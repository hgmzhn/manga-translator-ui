import base64
import io
import json
import re
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

from PIL import Image

from .openai_compat import resolve_openai_compatible_api_key
from .retry import summarize_exception_message, summarize_response_text

_OPENAI_IMAGE_INTERFACE_CACHE: dict[tuple[str, str], str] = {}
_OPENAI_IMAGE_INTERFACES = ("images/edits", "images/generations", "chat/completions")
_ENDPOINT_FALLBACK_STATUS_CODES = {404, 405, 501}
_DATA_URL_RE = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=_-]+")
_IMAGE_API_BACKEND_DEFAULT = "default"
_IMAGE_API_BACKEND_SILICONFLOW = "siliconflow"
_IMAGE_API_BACKEND_VOLCENGINE = "volcengine"
_IMAGE_API_BACKEND_DASHSCOPE = "dashscope"
_IMAGE_API_BACKEND_XAI = "xai"
_IMAGE_API_BACKEND_OPENROUTER = "openrouter"
_OPENROUTER_DEFAULT_MAX_TOKENS = 2048


async def request_openai_image_with_fallback(
    *,
    session,
    base_url: str,
    api_key: str,
    default_headers: Optional[dict],
    model_name: str,
    prompt_text: str,
    image_bytes: bytes,
    filename: str,
    timeout: float,
    fetch_remote_image: Callable[[str], Awaitable[Image.Image]],
    provider_name: str,
    logger,
    extra_images: Optional[list[dict]] = None,
    extra_request_params: Optional[dict] = None,
) -> Image.Image:
    from curl_cffi import CurlMime

    base_url = base_url.rstrip("/")
    resolved_api_key = resolve_openai_compatible_api_key(api_key, base_url)
    headers = {}
    if resolved_api_key:
        headers["Authorization"] = f"Bearer {resolved_api_key}"
    if default_headers:
        headers.update(default_headers)

    extra_images = extra_images or []
    extra_request_params = _normalize_extra_request_params(extra_request_params)
    cache_key = (base_url, model_name)
    candidate_interfaces = _build_candidate_interfaces(
        cache_key=cache_key,
        has_extra_images=bool(extra_images),
        base_url=base_url,
    )

    errors: list[str] = []

    for index, interface_name in enumerate(candidate_interfaces):
        next_interface_name = candidate_interfaces[index + 1] if index + 1 < len(candidate_interfaces) else None
        if interface_name == "images/edits":
            backend = _detect_image_api_backend(base_url)
            if extra_images and backend != _IMAGE_API_BACKEND_XAI:
                errors.append("images/edits skipped because reference images require chat/completions")
                continue
            if backend == _IMAGE_API_BACKEND_XAI:
                request_json = _build_edits_request_json(
                    base_url=base_url,
                    model_name=model_name,
                    prompt_text=prompt_text,
                    image_bytes=image_bytes,
                    extra_images=extra_images,
                    extra_request_params=extra_request_params,
                )
                response = await session.post(
                    f"{base_url}/images/edits",
                    headers=headers,
                    json=request_json,
                    timeout=timeout,
                )
            else:
                multipart = CurlMime()
                multipart.addpart(
                    name="image",
                    filename=filename,
                    content_type="image/png",
                    data=image_bytes,
                )
                try:
                    request_data = {
                        "model": model_name,
                        "prompt": prompt_text,
                        "response_format": "b64_json",
                    }
                    if extra_request_params:
                        request_data.update(
                            {
                                key: json.dumps(value, ensure_ascii=False)
                                if isinstance(value, (dict, list))
                                else value
                                for key, value in extra_request_params.items()
                            }
                        )
                    response = await session.post(
                        f"{base_url}/images/edits",
                        headers=headers,
                        data=request_data,
                        multipart=multipart,
                        timeout=timeout,
                    )
                finally:
                    multipart.close()

            if response.status_code == 200:
                try:
                    payload = response.json()
                except Exception as exc:
                    raise RuntimeError(
                        f"{provider_name} {base_url}/images/edits returned invalid JSON: "
                        f"{summarize_exception_message(exc)}"
                    ) from exc
                image = await _extract_image_from_images_payload(
                    payload=payload,
                    fetch_remote_image=fetch_remote_image,
                )
                if image is not None:
                    _OPENAI_IMAGE_INTERFACE_CACHE[cache_key] = interface_name
                    return image
                errors.append("images/edits returned 200 but did not contain image data")
                continue

            if _should_try_next_interface(response.status_code, response.text):
                _log_fallback(
                    logger=logger,
                    provider_name=provider_name,
                    endpoint=f"{base_url}/images/edits",
                    status_code=response.status_code,
                    next_interface_name=next_interface_name,
                )
                errors.append(f"images/edits HTTP {response.status_code}")
                continue

            raise RuntimeError(
                f"{provider_name} request failed at {base_url}/images/edits "
                f"with status {response.status_code}: "
                f"{_response_text_preview(response.text)}"
            )

        if interface_name == "images/generations":
            generation_candidates = _build_generation_request_candidates(
                base_url=base_url,
                model_name=model_name,
                prompt_text=prompt_text,
                image_bytes=image_bytes,
                extra_images=extra_images,
                extra_request_params=extra_request_params,
            )
            generation_attempt_errors: list[str] = []

            for generation_index, (payload_variant, request_json) in enumerate(generation_candidates):
                next_payload_variant = (
                    generation_candidates[generation_index + 1][0]
                    if generation_index + 1 < len(generation_candidates)
                    else None
                )
                generation_endpoint = _resolve_generation_endpoint(base_url, payload_variant)
                response = await session.post(
                    generation_endpoint,
                    headers=headers,
                    json=request_json,
                    timeout=timeout,
                )

                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except Exception as exc:
                        raise RuntimeError(
                            f"{provider_name} {base_url}/images/generations returned invalid JSON: "
                            f"{summarize_exception_message(exc)}"
                        ) from exc
                    image = await _extract_image_from_images_payload(
                        payload=payload,
                        fetch_remote_image=fetch_remote_image,
                    )
                    if image is None:
                        image = await _extract_image_from_chat_payload(
                            payload=payload,
                            fetch_remote_image=fetch_remote_image,
                        )
                    if image is not None:
                        _OPENAI_IMAGE_INTERFACE_CACHE[cache_key] = interface_name
                        return image
                    errors.append("images/generations returned 200 but did not contain image data")
                    break

                if next_payload_variant and _should_try_next_generation_payload_variant(
                    response.status_code,
                    response.text,
                ):
                    _log_generation_payload_fallback(
                        logger=logger,
                        provider_name=provider_name,
                        endpoint=generation_endpoint,
                        status_code=response.status_code,
                        payload_variant=payload_variant,
                        next_payload_variant=next_payload_variant,
                    )
                    generation_attempt_errors.append(
                        f"{payload_variant} HTTP {response.status_code}"
                    )
                    continue

                if _should_try_next_interface(
                    response.status_code,
                    response.text,
                    interface_name=interface_name,
                ):
                    _log_fallback(
                        logger=logger,
                        provider_name=provider_name,
                        endpoint=generation_endpoint,
                        status_code=response.status_code,
                        next_interface_name=next_interface_name,
                    )
                    variant_prefix = ""
                    if generation_attempt_errors:
                        variant_prefix = (
                            f"payload variants tried: {', '.join(generation_attempt_errors + [f'{payload_variant} HTTP {response.status_code}'])}; "
                        )
                    errors.append(
                        f"images/generations {variant_prefix}HTTP {response.status_code}".strip()
                    )
                    break

                raise RuntimeError(
                    f"{provider_name} request failed at {generation_endpoint} "
                    f"with status {response.status_code}: "
                    f"{_response_text_preview(response.text)}"
                )
            else:
                continue

            continue

        message_content = [
            {"type": "text", "text": prompt_text},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"
                },
            },
        ]
        for idx, item in enumerate(extra_images, start=1):
            label = str(item.get("label") or f"Reference image {idx}")
            extra_image_bytes = item.get("image_bytes")
            if not isinstance(extra_image_bytes, (bytes, bytearray)):
                continue
            message_content.append({"type": "text", "text": f"Reference image {idx}: {label}"})
            message_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{base64.b64encode(extra_image_bytes).decode('ascii')}"
                    },
                }
            )

        request_json = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": message_content,
                }
            ],
        }
        if _detect_image_api_backend(base_url) == _IMAGE_API_BACKEND_OPENROUTER:
            request_json["modalities"] = ["image", "text"]
            request_json["max_tokens"] = _OPENROUTER_DEFAULT_MAX_TOKENS
        if extra_request_params:
            request_json.update(extra_request_params)

        response = await session.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=request_json,
            timeout=timeout,
        )

        if response.status_code == 200:
            try:
                payload = response.json()
            except Exception as exc:
                raise RuntimeError(
                    f"{provider_name} {base_url}/chat/completions returned invalid JSON: "
                    f"{summarize_exception_message(exc)}"
                ) from exc
            image = await _extract_image_from_chat_payload(
                payload=payload,
                fetch_remote_image=fetch_remote_image,
            )
            if image is not None:
                _OPENAI_IMAGE_INTERFACE_CACHE[cache_key] = interface_name
                return image

            text_preview = _extract_text_preview(payload)
            message = "chat/completions returned 200 but did not contain an image"
            if text_preview:
                message = f"{message}; text preview: {summarize_response_text(text_preview)}"
            errors.append(message)
            continue

        if _should_try_next_interface(response.status_code, response.text):
            _log_fallback(
                logger=logger,
                provider_name=provider_name,
                endpoint=f"{base_url}/chat/completions",
                status_code=response.status_code,
                next_interface_name=next_interface_name,
            )
            errors.append(f"chat/completions HTTP {response.status_code}")
            continue

        raise RuntimeError(
            f"{provider_name} request failed at {base_url}/chat/completions "
            f"with status {response.status_code}: "
            f"{_response_text_preview(response.text)}"
        )

    attempts = ", ".join(errors) if errors else "no compatible image interface responded"
    raise RuntimeError(
        f"{provider_name} could not find a compatible image output interface under {base_url}. "
        f"Tried /images/edits, /images/generations, and /chat/completions. Details: {attempts}. "
        f"This API base may only support text chat and vision input, not image generation/editing output."
    )


def _build_candidate_interfaces(
    *,
    cache_key: tuple[str, str],
    has_extra_images: bool,
    base_url: Optional[str] = None,
) -> list[str]:
    preferred_interface = _OPENAI_IMAGE_INTERFACE_CACHE.get(cache_key)
    backend = _detect_image_api_backend(base_url or cache_key[0])
    if preferred_interface:
        default_order = [preferred_interface]
    elif backend == _IMAGE_API_BACKEND_OPENROUTER:
        default_order = ["chat/completions", "images/generations", "images/edits"]
    elif backend == _IMAGE_API_BACKEND_DASHSCOPE:
        default_order = ["images/generations", "chat/completions", "images/edits"]
    elif backend == _IMAGE_API_BACKEND_XAI:
        default_order = ["images/edits", "images/generations", "chat/completions"]
    elif has_extra_images:
        default_order = ["images/generations", "chat/completions", "images/edits"]
    else:
        default_order = ["images/edits", "images/generations", "chat/completions"]

    candidate_interfaces = list(default_order)
    candidate_interfaces.extend(
        interface for interface in _OPENAI_IMAGE_INTERFACES if interface not in candidate_interfaces
    )
    return candidate_interfaces


def _normalize_extra_request_params(extra_request_params: Optional[dict]) -> dict:
    normalized = dict(extra_request_params or {})
    extra_body = normalized.pop("extra_body", None)
    if isinstance(extra_body, dict):
        for key, value in extra_body.items():
            normalized.setdefault(key, value)
    return normalized


def _detect_image_api_backend(base_url: str) -> str:
    normalized_base_url = (base_url or "").strip().lower()
    parsed = urlparse(normalized_base_url)
    host = (parsed.netloc or parsed.path).lower()
    path = parsed.path.lower() if parsed.netloc else ""
    combined = f"{host}{path}"

    if "siliconflow.cn" in combined:
        return _IMAGE_API_BACKEND_SILICONFLOW

    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        return _IMAGE_API_BACKEND_OPENROUTER

    if host == "api.x.ai" or host.startswith("api.x.ai:"):
        return _IMAGE_API_BACKEND_XAI

    dashscope_hosts = {
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
    }
    if host in dashscope_hosts and "/compatible-mode/" not in path:
        if (
            path.endswith("/api/v1")
            or path.endswith("/api/v1/")
            or path.endswith("/services/aigc/multimodal-generation/generation")
        ):
            return _IMAGE_API_BACKEND_DASHSCOPE

    volcengine_host_markers = (
        "volces.com",
        "volcengineapi.com",
    )
    if (
        any(marker in host for marker in volcengine_host_markers)
        or host.startswith("ark.")
        or path.endswith("/v3")
        or "/v3/" in path
    ):
        return _IMAGE_API_BACKEND_VOLCENGINE

    return _IMAGE_API_BACKEND_DEFAULT


def _encode_generation_image(image_bytes: bytes) -> str:
    return f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"


def _build_edits_request_json(
    *,
    base_url: str,
    model_name: str,
    prompt_text: str,
    image_bytes: bytes,
    extra_images: Optional[list[dict]] = None,
    extra_request_params: Optional[dict] = None,
) -> dict:
    backend = _detect_image_api_backend(base_url)
    if backend == _IMAGE_API_BACKEND_XAI:
        encoded_images = [_encode_generation_image(image_bytes)]
        encoded_images.extend(
            _encode_generation_image(item["image_bytes"])
            for item in (extra_images or [])
            if isinstance(item.get("image_bytes"), (bytes, bytearray))
        )
        image_items = [
            {
                "url": image_value,
                "type": "image_url",
            }
            for image_value in encoded_images
        ]
        request_json = {
            "model": model_name,
            "prompt": prompt_text,
        }
        if len(image_items) == 1:
            request_json["image"] = image_items[0]
        else:
            request_json["images"] = image_items
        if extra_request_params:
            request_json.update(extra_request_params)
        return request_json

    request_json = {
        "model": model_name,
        "prompt": prompt_text,
        "response_format": "b64_json",
    }
    if extra_request_params:
        request_json.update(extra_request_params)
    return request_json


def _build_generation_request_json(
    *,
    base_url: str,
    model_name: str,
    prompt_text: str,
    image_bytes: bytes,
    extra_images: Optional[list[dict]] = None,
    extra_request_params: Optional[dict] = None,
    backend_override: Optional[str] = None,
) -> dict:
    backend = backend_override or _detect_image_api_backend(base_url)
    generation_images = [_encode_generation_image(image_bytes)]
    generation_images.extend(
        _encode_generation_image(item["image_bytes"])
        for item in (extra_images or [])
        if isinstance(item.get("image_bytes"), (bytes, bytearray))
    )

    request_json = {
        "model": model_name,
        "prompt": prompt_text,
    }
    if backend != _IMAGE_API_BACKEND_SILICONFLOW:
        request_json["response_format"] = "b64_json"
    if extra_request_params:
        request_json.update(extra_request_params)

    if not generation_images:
        return request_json

    if backend == _IMAGE_API_BACKEND_SILICONFLOW:
        request_json.setdefault("image", generation_images[0])
        for index, image_value in enumerate(generation_images[1:], start=2):
            request_json.setdefault(f"image{index}", image_value)
        return request_json

    if backend == _IMAGE_API_BACKEND_DASHSCOPE:
        content_parts = [{"image": image_value} for image_value in generation_images]
        content_parts.append({"text": prompt_text})
        request_json = {
            "model": model_name,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": content_parts,
                    }
                ]
            },
            "parameters": dict(extra_request_params or {}),
        }
        return request_json

    request_json.setdefault(
        "image",
        generation_images if len(generation_images) > 1 else generation_images[0],
    )
    if len(generation_images) > 1:
        request_json.setdefault("sequential_image_generation", "disabled")
    return request_json


def _build_generation_request_candidates(
    *,
    base_url: str,
    model_name: str,
    prompt_text: str,
    image_bytes: bytes,
    extra_images: Optional[list[dict]] = None,
    extra_request_params: Optional[dict] = None,
) -> list[tuple[str, dict]]:
    backend = _detect_image_api_backend(base_url)
    if backend == _IMAGE_API_BACKEND_SILICONFLOW:
        variant_order = [_IMAGE_API_BACKEND_SILICONFLOW]
    elif backend == _IMAGE_API_BACKEND_DASHSCOPE:
        variant_order = [_IMAGE_API_BACKEND_DASHSCOPE]
    elif backend == _IMAGE_API_BACKEND_VOLCENGINE:
        variant_order = [_IMAGE_API_BACKEND_DEFAULT]
    else:
        variant_order = [_IMAGE_API_BACKEND_DEFAULT, _IMAGE_API_BACKEND_SILICONFLOW]

    candidates: list[tuple[str, dict]] = []
    seen_payloads: set[str] = set()
    for variant in variant_order:
        payload = _build_generation_request_json(
            base_url=base_url,
            model_name=model_name,
            prompt_text=prompt_text,
            image_bytes=image_bytes,
            extra_images=extra_images,
            extra_request_params=extra_request_params,
            backend_override=variant,
        )
        payload_key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if payload_key in seen_payloads:
            continue
        seen_payloads.add(payload_key)
        candidates.append((variant, payload))
    return candidates


def _log_generation_payload_fallback(
    *,
    logger,
    provider_name: str,
    endpoint: str,
    status_code: int,
    payload_variant: str,
    next_payload_variant: str,
):
    logger.warning(
        f"{provider_name}: {endpoint} payload variant '{payload_variant}' failed "
        f"(HTTP {status_code}), trying '{next_payload_variant}'."
    )


def _resolve_generation_endpoint(base_url: str, payload_variant: str) -> str:
    normalized_base_url = (base_url or "").rstrip("/")
    if payload_variant != _IMAGE_API_BACKEND_DASHSCOPE:
        return f"{normalized_base_url}/images/generations"

    dashscope_suffix = "/services/aigc/multimodal-generation/generation"
    if normalized_base_url.endswith(dashscope_suffix):
        return normalized_base_url
    return f"{normalized_base_url}{dashscope_suffix}"


def _log_fallback(*, logger, provider_name: str, endpoint: str, status_code: int, next_interface_name: Optional[str]):
    if not next_interface_name:
        return
    logger.warning(
        f"{provider_name}: {endpoint} unavailable (HTTP {status_code}), "
        f"trying /{next_interface_name}."
    )


def _should_try_next_generation_payload_variant(status_code: int, response_text: str) -> bool:
    if status_code not in {400, 415, 422}:
        return False

    text = (response_text or "").lower()
    non_payload_markers = (
        "model does not exist",
        "model not found",
        "unknown model",
        "invalid api key",
        "unauthorized",
        "authentication",
        "insufficient quota",
        "insufficient balance",
        "rate limit",
        "too many requests",
        "content policy",
        "safety",
        "moderation",
        "prompt blocked",
    )
    return not any(marker in text for marker in non_payload_markers)


def _should_try_next_interface(status_code: int, response_text: str, interface_name: Optional[str] = None) -> bool:
    if status_code in _ENDPOINT_FALLBACK_STATUS_CODES:
        return True
    text = (response_text or "").lower()
    fallback_markers = (
        "not found",
        "unknown url",
        "unknown path",
        "unsupported endpoint",
        "unsupported route",
        "does not support",
    )
    if any(marker in text for marker in fallback_markers):
        return True

    if status_code in {400, 415, 422} and interface_name == "images/generations":
        generation_markers = (
            "unknown parameter",
            "unknown field",
            "unrecognized field",
            "extra fields not permitted",
            "additional properties are not allowed",
            "reference_images",
            "does not support image",
            "does not support reference image",
        )
        return any(marker in text for marker in generation_markers)

    return False


def _response_text_preview(text: str, limit: Optional[int] = None) -> str:
    return summarize_response_text(text, limit=limit)


async def _extract_image_from_images_payload(
    payload: dict,
    fetch_remote_image: Callable[[str], Awaitable[Image.Image]],
) -> Optional[Image.Image]:
    data = payload.get("data") or []
    for item in data:
        image = await _image_from_candidate(item, fetch_remote_image)
        if image is not None:
            return image
    return None


async def _extract_image_from_chat_payload(
    payload: dict,
    fetch_remote_image: Callable[[str], Awaitable[Image.Image]],
) -> Optional[Image.Image]:
    for candidate in (
        payload.get("image"),
        *(payload.get("images") or []),
    ):
        image = await _image_from_candidate(candidate, fetch_remote_image)
        if image is not None:
            return image

    choices = payload.get("choices") or []
    for choice in choices:
        message = choice.get("message") or {}
        for candidate in (
            message.get("image"),
            *(message.get("images") or []),
        ):
            image = await _image_from_candidate(candidate, fetch_remote_image)
            if image is not None:
                return image

        content = message.get("content")
        image = await _extract_image_from_content(content, fetch_remote_image)
        if image is not None:
            return image

    image = await _extract_image_from_images_payload(payload, fetch_remote_image)
    if image is not None:
        return image

    return await _extract_image_from_content(payload.get("output"), fetch_remote_image)


async def _extract_image_from_content(
    content,
    fetch_remote_image: Callable[[str], Awaitable[Image.Image]],
) -> Optional[Image.Image]:
    if isinstance(content, list):
        for item in content:
            image = await _image_from_candidate(item, fetch_remote_image)
            if image is not None:
                return image
    elif isinstance(content, dict):
        image = await _image_from_candidate(content, fetch_remote_image)
        if image is not None:
            return image
    elif isinstance(content, str):
        return await _image_from_candidate(content, fetch_remote_image)
    return None


async def _image_from_candidate(
    candidate,
    fetch_remote_image: Callable[[str], Awaitable[Image.Image]],
) -> Optional[Image.Image]:
    if isinstance(candidate, str):
        return await _image_from_string(candidate, fetch_remote_image)

    if not isinstance(candidate, dict):
        return None

    for key in ("text", "content"):
        value = candidate.get(key)
        if isinstance(value, str):
            image = await _image_from_string(value, fetch_remote_image)
            if image is not None:
                return image

    for key in ("b64_json", "image_base64", "b64"):
        value = candidate.get(key)
        if isinstance(value, str):
            image = _load_image_from_base64(value)
            if image is not None:
                return image

    inline_data = candidate.get("inlineData") or candidate.get("inline_data")
    if isinstance(inline_data, dict):
        data = inline_data.get("data")
        if isinstance(data, str):
            image = _load_image_from_base64(data)
            if image is not None:
                return image

    if isinstance(candidate.get("image_url"), dict):
        url = candidate["image_url"].get("url")
        if isinstance(url, str):
            return await _image_from_string(url, fetch_remote_image)

    for key in ("url", "image_url"):
        value = candidate.get(key)
        if isinstance(value, str):
            return await _image_from_string(value, fetch_remote_image)

    for nested_key in ("image", "content", "output", "result", "choices", "message", "messages"):
        nested_value = candidate.get(nested_key)
        image = await _extract_image_from_content(nested_value, fetch_remote_image)
        if image is not None:
            return image

    return None


async def _image_from_string(
    value: str,
    fetch_remote_image: Callable[[str], Awaitable[Image.Image]],
) -> Optional[Image.Image]:
    image = _load_image_from_data_url(value)
    if image is not None:
        return image
    if value.startswith("http://") or value.startswith("https://"):
        return await fetch_remote_image(value)
    return None


def _load_image_from_data_url(value: str) -> Optional[Image.Image]:
    match = _DATA_URL_RE.search(value)
    if match is None:
        return None
    _, encoded = match.group(0).split(";base64,", 1)
    return _load_image_from_base64(encoded)


def _load_image_from_base64(value: str) -> Optional[Image.Image]:
    try:
        return Image.open(io.BytesIO(base64.b64decode(value))).convert("RGB")
    except Exception:
        return None


def _extract_text_preview(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts).strip()
    return ""
