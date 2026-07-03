from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Iterable, Optional, TypeVar

from .utils.retry import (
    get_retry_attempts_from_config,
    normalize_retry_attempts,
    resolve_total_attempts,
    summarize_exception_message,
)


DEFAULT_ROTATION_SLOTS = 3
MAX_ROTATION_SLOTS = 30
DEFAULT_ROTATION_STRATEGY = "failover"
DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 60
MAX_RATE_LIMIT_COOLDOWN_SECONDS = 600
ROTATION_STRATEGIES = {
    "failover": "按顺序故障切换",
    "round_robin": "轮询",
}

_STATUS_RE = re.compile(r"\b(400|402|404|429)\b")
_INDEXED_ENV_RE = re.compile(r"^(?P<base>.+)_(?P<index>[1-9]\d*)$")
_STATUS_LOCK = threading.RLock()
_API_STATUS: dict[str, dict[str, Any]] = {}
_ROUND_ROBIN_CURSORS: dict[str, int] = {}

T = TypeVar("T")


class APIRotationExhaustedError(RuntimeError):
    """Raised after all API candidates have exhausted their configured attempts."""


@dataclass(frozen=True)
class APIEndpoint:
    feature: str
    provider: str
    slot: int
    api_key: Optional[str]
    base_url: str
    model_name: str
    status_key: str
    label: str


def normalize_rotation_strategy(value: str | None) -> str:
    strategy = str(value or "").strip().lower()
    if strategy in ROTATION_STRATEGIES:
        return strategy
    return DEFAULT_ROTATION_STRATEGY


def get_api_group_prefix(api_key_env: str | None) -> str:
    key = str(api_key_env or "").strip().upper()
    for suffix in ("_API_KEY", "_AUTH_KEY", "_TOKEN"):
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def get_strategy_env_key(api_key_env: str | None) -> str | None:
    prefix = get_api_group_prefix(api_key_env)
    return f"{prefix}_API_ROTATION_STRATEGY" if prefix else None


def get_indexed_env_key(env_key: str | None, index: int) -> str | None:
    if not env_key:
        return None
    return env_key if index <= 1 else f"{env_key}_{index}"


def get_rotation_slot_count(
    env_vars: dict[str, Any] | None,
    keys: Iterable[str | None],
    *,
    default: int = DEFAULT_ROTATION_SLOTS,
    maximum: int = MAX_ROTATION_SLOTS,
) -> int:
    count = max(1, int(default or 1))
    normalized_keys = {str(key).strip().upper() for key in keys if key}
    for key in (env_vars or {}).keys():
        normalized_key = str(key or "").strip().upper()
        match = _INDEXED_ENV_RE.match(normalized_key)
        if not match:
            continue
        if match.group("base") not in normalized_keys:
            continue
        try:
            count = max(count, int(match.group("index")))
        except ValueError:
            continue
    return min(count, maximum)


def get_rotation_env_keys(
    api_key_env: str | None,
    api_base_env: str | None,
    model_env: str | None,
    *,
    slots: int = DEFAULT_ROTATION_SLOTS,
) -> list[str]:
    keys: list[str] = []
    strategy_key = get_strategy_env_key(api_key_env)
    if strategy_key:
        keys.append(strategy_key)
    for index in range(1, max(1, slots) + 1):
        for env_key in (api_key_env, model_env, api_base_env):
            indexed_key = get_indexed_env_key(env_key, index)
            if indexed_key and indexed_key not in keys:
                keys.append(indexed_key)
    return keys


def env_has_any_indexed_value(env_vars: dict[str, Any], key: str, *, maximum: int = MAX_ROTATION_SLOTS) -> bool:
    if str(env_vars.get(key, "") or "").strip():
        return True
    for index in range(2, maximum + 1):
        if str(env_vars.get(f"{key}_{index}", "") or "").strip():
            return True
    return False


def make_endpoint_status_key(
    feature: str,
    provider: str,
    slot: int,
    base_url: str,
    model_name: str,
    api_key: str | None = None,
) -> str:
    api_key_text = str(api_key or "").strip()
    api_key_fingerprint = (
        hashlib.sha256(api_key_text.encode("utf-8")).hexdigest()[:12]
        if api_key_text
        else "no-key"
    )
    return f"{feature}:{provider}:{slot}:{base_url}:{model_name}:{api_key_fingerprint}"


def _now() -> float:
    return time.time()


def _endpoint_identity(endpoint: APIEndpoint) -> dict[str, Any]:
    return {
        "feature": endpoint.feature,
        "provider": endpoint.provider,
        "slot": endpoint.slot,
        "label": endpoint.label,
        "base_url": endpoint.base_url,
        "model_name": endpoint.model_name,
        "has_api_key": bool(str(endpoint.api_key or "").strip()),
    }


def _get_status(status_key: str) -> dict[str, Any] | None:
    with _STATUS_LOCK:
        status = _API_STATUS.get(status_key)
        return dict(status) if status else None


def is_endpoint_unavailable(endpoint: APIEndpoint) -> bool:
    status = _get_status(endpoint.status_key)
    if not status:
        return False
    if status.get("state") == "unavailable":
        return True
    if status.get("state") == "cooldown":
        return float(status.get("cooldown_until") or 0) > _now()
    return False


def record_api_success(endpoint: APIEndpoint) -> None:
    with _STATUS_LOCK:
        payload = _endpoint_identity(endpoint)
        payload.update(
            {
                "state": "available",
                "last_error": "",
                "last_status_code": None,
                "updated_at": _now(),
            }
        )
        _API_STATUS[endpoint.status_key] = payload


def _extract_status_code(error: Exception) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(error, attr, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    response = getattr(error, "response", None)
    if response is not None:
        try:
            return int(getattr(response, "status_code", None))
        except (TypeError, ValueError):
            pass
    if isinstance(error, (RuntimeError, ValueError, TypeError, KeyError, AttributeError)):
        return None
    match = _STATUS_RE.search(str(error or ""))
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _message_contains(error: Exception, markers: Iterable[str]) -> bool:
    message = str(error or "").lower()
    return any(marker in message for marker in markers)


def _is_bad_request_api_unavailable_error(error: Exception) -> bool:
    return _message_contains(
        error,
        (
            "api key not valid",
            "api_key_invalid",
            "invalid api key",
            "invalid api_key",
            "api key expired",
            "api key has expired",
            "api key revoked",
            "invalid authentication",
            "invalid credentials",
            "permission denied",
            "access denied",
            "model not found",
            "not found for api version",
        ),
    )


def is_permanent_api_unavailable_error(error: Exception) -> bool:
    status_code = _extract_status_code(error)
    if status_code == 400:
        return _is_bad_request_api_unavailable_error(error)
    if status_code in (402, 404):
        return True
    return _message_contains(
        error,
        (
            "insufficient_quota",
            "insufficient quota",
            "quota exceeded",
            "billing",
            "payment required",
        ),
    )


def _extract_retry_after_seconds(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if not headers:
        return None

    retry_after = None
    if hasattr(headers, "get"):
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
    if retry_after is None:
        return None

    retry_after_text = str(retry_after).strip()
    if not retry_after_text:
        return None
    try:
        seconds = int(float(retry_after_text))
    except ValueError:
        try:
            retry_time = parsedate_to_datetime(retry_after_text)
            seconds = int(retry_time.timestamp() - _now())
        except Exception:
            return None
    return max(1, min(seconds, MAX_RATE_LIMIT_COOLDOWN_SECONDS))


def is_rate_limit_cooldown_error(error: Exception) -> bool:
    status_code = _extract_status_code(error)
    if status_code == 429:
        return True
    if is_permanent_api_unavailable_error(error):
        return False
    return _message_contains(
        error,
        (
            "rate limit",
            "too many requests",
        ),
    )


def record_api_failure(endpoint: APIEndpoint, error: Exception) -> None:
    permanently_unavailable = is_permanent_api_unavailable_error(error)
    rate_limited = (not permanently_unavailable) and is_rate_limit_cooldown_error(error)
    cooldown_seconds = _extract_retry_after_seconds(error) or DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
    with _STATUS_LOCK:
        payload = _endpoint_identity(endpoint)
        state = "failed"
        if permanently_unavailable:
            state = "unavailable"
        elif rate_limited:
            state = "cooldown"
        payload.update(
            {
                "state": state,
                "last_error": str(error or ""),
                "last_status_code": _extract_status_code(error),
                "cooldown_until": _now() + cooldown_seconds if rate_limited else None,
                "updated_at": _now(),
            }
        )
        _API_STATUS[endpoint.status_key] = payload


def get_api_status_snapshot() -> list[dict[str, Any]]:
    with _STATUS_LOCK:
        return [dict(item) for item in _API_STATUS.values()]


def _rotation_group_key(endpoints: tuple[APIEndpoint, ...]) -> str:
    if not endpoints:
        return ""
    first = endpoints[0]
    return f"{first.feature}:{first.provider}"


def iter_api_candidates(endpoints: tuple[APIEndpoint, ...], strategy: str) -> list[APIEndpoint]:
    available = [endpoint for endpoint in endpoints if not is_endpoint_unavailable(endpoint)]
    if not available:
        return []
    strategy = normalize_rotation_strategy(strategy)
    if strategy == "round_robin" and len(available) > 1:
        group_key = _rotation_group_key(endpoints)
        with _STATUS_LOCK:
            cursor = _ROUND_ROBIN_CURSORS.get(group_key, 0) % len(available)
            _ROUND_ROBIN_CURSORS[group_key] = cursor + 1
        return available[cursor:] + available[:cursor]
    return available


async def run_with_api_candidates(
    *,
    endpoints: tuple[APIEndpoint, ...],
    strategy: str,
    operation: Callable[[APIEndpoint], Awaitable[T]],
    provider_name: str,
    operation_name: str,
    logger,
    runtime_config: Any = None,
    retry_attempts: int | None = None,
    on_candidate_error: Optional[Callable[[APIEndpoint, Exception], Awaitable[None]]] = None,
) -> T:
    candidates = iter_api_candidates(endpoints, strategy)
    if not candidates:
        raise RuntimeError(f"{provider_name} has no available API candidates for {operation_name}.")

    if retry_attempts is None:
        retry_attempts = get_retry_attempts_from_config(
            runtime_config,
            logger=logger,
            fallback=-1,
        )
    else:
        retry_attempts = normalize_retry_attempts(retry_attempts, logger=logger, default=-1)
    max_total_attempts = resolve_total_attempts(retry_attempts)

    last_error: Exception | None = None
    for candidate_index, endpoint in enumerate(candidates, start=1):
        candidate_attempt = 0
        while True:
            candidate_attempt += 1
            try:
                result = await operation(endpoint)
                record_api_success(endpoint)
                return result
            except Exception as exc:
                last_error = exc
                can_retry_same_candidate = (
                    not is_permanent_api_unavailable_error(exc)
                    and (max_total_attempts == -1 or candidate_attempt < max_total_attempts)
                )

                if on_candidate_error is not None:
                    await on_candidate_error(endpoint, exc)

                if can_retry_same_candidate:
                    attempt_limit = "inf" if max_total_attempts == -1 else str(max_total_attempts)
                    logger.warning(
                        f"{provider_name}: {operation_name} failed on {endpoint.label} "
                        f"attempt {candidate_attempt}/{attempt_limit}: "
                        f"{summarize_exception_message(exc)}. Retrying same API candidate..."
                    )
                    await asyncio.sleep(min(1.0 * candidate_attempt, 3.0))
                    continue

                record_api_failure(endpoint, exc)

                if candidate_index >= len(candidates):
                    raise APIRotationExhaustedError(
                        f"{provider_name} {operation_name} failed after exhausting "
                        f"{len(candidates)} API candidate(s): {summarize_exception_message(exc)}"
                    ) from exc

                attempts_text = (
                    "unlimited attempts"
                    if max_total_attempts == -1
                    else f"{candidate_attempt}/{max_total_attempts} attempts"
                )
                logger.warning(
                    f"{provider_name}: {operation_name} exhausted {endpoint.label} "
                    f"after {attempts_text}: {summarize_exception_message(exc)}. "
                    f"Trying next API candidate..."
                )
                break

    if last_error is not None:
        raise APIRotationExhaustedError(
            f"{provider_name} {operation_name} failed after exhausting API candidates: "
            f"{summarize_exception_message(last_error)}"
        ) from last_error
    raise APIRotationExhaustedError(f"{provider_name} {operation_name} failed without an error.")
