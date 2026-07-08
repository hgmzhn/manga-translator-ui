from __future__ import annotations

import atexit
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_VERSION = 1
_TYPE = "editor_canvas_lag_debug"
_MAX_RECORDS = 3000
_WRITE_DELAY_SECONDS = 0.15
_SESSION_TIMESTAMP = time.strftime("%Y%m%d_%H%M%S", time.localtime())
_SESSION_PID = os.getpid()

_lock = threading.RLock()
_records: list[dict[str, Any]] = []
_write_timer: threading.Timer | None = None
_last_rate_limited: dict[str, float] = {}
_last_interaction: dict[str, Any] | None = None


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def debug_file_path() -> Path:
    override = os.environ.get("EDITOR_CANVAS_LAG_DEBUG_PATH")
    if override:
        return Path(override)
    filename = f"editor_canvas_lag_debug_{_SESSION_TIMESTAMP}_{_SESSION_PID}.json"
    return _workspace_root() / "result" / "diagnostics" / "editor_canvas_lag_debug" / filename


def is_enabled() -> bool:
    value = os.environ.get("EDITOR_CANVAS_LAG_DEBUG", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return {
                "type": "ndarray",
                "shape": list(value.shape),
                "dtype": str(value.dtype),
                "nbytes": int(value.nbytes),
            }
    except Exception:
        pass
    try:
        from PyQt6.QtCore import QRect, QRectF, QSize

        if isinstance(value, (QRect, QRectF)):
            return {
                "x": float(value.x()),
                "y": float(value.y()),
                "width": float(value.width()),
                "height": float(value.height()),
            }
        if isinstance(value, QSize):
            return {"width": int(value.width()), "height": int(value.height())}
    except Exception:
        pass
    return str(value)


def _mb(value: int | float | None) -> float | None:
    if value is None:
        return None
    return round(float(value) / (1024 * 1024), 2)


def process_memory_snapshot() -> dict[str, Any]:
    try:
        import psutil

        info = psutil.Process(os.getpid()).memory_info()
        return {
            "rss_mb": _mb(getattr(info, "rss", None)),
            "wset_mb": _mb(getattr(info, "wset", None)),
            "vms_mb": _mb(getattr(info, "vms", None)),
            "private_mb": _mb(getattr(info, "private", None)),
            "pagefile_mb": _mb(getattr(info, "pagefile", None)),
        }
    except Exception as exc:
        return {"error": str(exc)}


def cuda_memory_snapshot() -> dict[str, Any]:
    torch = sys.modules.get("torch")
    if torch is None:
        return {"available": False, "reason": "torch_not_imported"}
    try:
        if not torch.cuda.is_available():
            return {"available": False, "reason": "cuda_not_available"}
        device = torch.cuda.current_device()
        allocated = torch.cuda.memory_allocated(device)
        reserved = torch.cuda.memory_reserved(device)
        max_allocated = torch.cuda.max_memory_allocated(device)
        max_reserved = torch.cuda.max_memory_reserved(device)
        free_bytes, total_bytes = torch.cuda.mem_get_info(device)
        return {
            "available": True,
            "device": int(device),
            "device_name": torch.cuda.get_device_name(device),
            "allocated_mb": _mb(allocated),
            "reserved_mb": _mb(reserved),
            "max_allocated_mb": _mb(max_allocated),
            "max_reserved_mb": _mb(max_reserved),
            "free_mb": _mb(free_bytes),
            "total_mb": _mb(total_bytes),
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": _VERSION,
        "type": _TYPE,
        "path": str(debug_file_path()),
        "records": records,
    }


def _write_now() -> None:
    global _write_timer
    with _lock:
        _write_timer = None
        records = list(_records)

    if not records:
        return

    path = debug_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(_payload(records), handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        pass


def _schedule_write() -> None:
    global _write_timer
    if not is_enabled():
        return
    try:
        with _lock:
            if _write_timer is not None:
                return
            _write_timer = threading.Timer(_WRITE_DELAY_SECONDS, _write_now)
            _write_timer.daemon = True
            _write_timer.start()
    except Exception:
        _write_timer = None


def flush_canvas_debug() -> None:
    try:
        with _lock:
            timer = _write_timer
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        _write_now()
    except Exception:
        pass


atexit.register(flush_canvas_debug)


def record_canvas_debug(
    stage: str,
    *,
    include_system: bool = False,
    force_flush: bool = False,
    **data: Any,
) -> None:
    try:
        if not is_enabled():
            return

        now = time.perf_counter()
        record = {
            "stage": str(stage),
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "monotonic_ms": round(now * 1000.0, 3),
            "thread": threading.current_thread().name,
            "pid": os.getpid(),
        }
        record.update({key: _to_jsonable(value) for key, value in data.items()})
        if include_system:
            record["process_memory"] = process_memory_snapshot()
            record["cuda_memory"] = cuda_memory_snapshot()

        with _lock:
            _records.append(record)
            if len(_records) > _MAX_RECORDS:
                del _records[: len(_records) - _MAX_RECORDS]

        if force_flush:
            flush_canvas_debug()
        else:
            _schedule_write()
    except Exception:
        pass


def record_canvas_debug_rate_limited(
    stage: str,
    *,
    min_interval_ms: float = 1000.0,
    include_system: bool = False,
    **data: Any,
) -> None:
    try:
        now = time.perf_counter()
        with _lock:
            last = _last_rate_limited.get(stage, 0.0)
            if (now - last) * 1000.0 < min_interval_ms:
                return
            _last_rate_limited[stage] = now
        record_canvas_debug(stage, include_system=include_system, **data)
    except Exception:
        pass


def mark_canvas_interaction(kind: str, **data: Any) -> None:
    global _last_interaction
    try:
        now = time.perf_counter()
        payload = {
            "kind": str(kind),
            "monotonic_ms": round(now * 1000.0, 3),
            "data": {key: _to_jsonable(value) for key, value in data.items()},
        }
        with _lock:
            _last_interaction = payload
    except Exception:
        pass


def last_canvas_interaction() -> dict[str, Any] | None:
    try:
        with _lock:
            if _last_interaction is None:
                return None
            return dict(_last_interaction)
    except Exception:
        return None


def record_canvas_duration(
    stage: str,
    elapsed_ms: float,
    *,
    threshold_ms: float = 50.0,
    force: bool = False,
    include_system: bool = False,
    **data: Any,
) -> None:
    try:
        if not force and elapsed_ms < threshold_ms:
            return
        record_canvas_debug(
            stage,
            include_system=include_system or elapsed_ms >= max(threshold_ms * 2.0, 100.0),
            elapsed_ms=round(float(elapsed_ms), 3),
            threshold_ms=float(threshold_ms),
            slow=elapsed_ms >= threshold_ms,
            **data,
        )
    except Exception:
        pass


@contextmanager
def canvas_debug_timer(
    stage: str,
    *,
    threshold_ms: float = 50.0,
    force: bool = False,
    include_system: bool = False,
    **data: Any,
) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        record_canvas_debug(
            stage,
            include_system=True,
            elapsed_ms=round(elapsed_ms, 3),
            threshold_ms=float(threshold_ms),
            exception=repr(exc),
            **data,
        )
        raise
    else:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        record_canvas_duration(
            stage,
            elapsed_ms,
            threshold_ms=threshold_ms,
            force=force,
            include_system=include_system,
            **data,
        )
