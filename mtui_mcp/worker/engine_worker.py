#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone warm engine worker executed inside `MTUI_ROOT`'s Python environment.

Provides fast (<0.3s) native Qt6 offscreen text rendering, PSD export,
incremental neural inpainting (`lama_large` / `lama_mpe`), smart bubble layout
& HanLP semantic line-breaking, and region OCR / translation.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import contextlib
import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

_WINDOWS_TRANSIENT_REPLACE_WINERRORS = frozenset({5, 32})
_ATOMIC_REPLACE_RETRY_DELAYS = (0.02, 0.05, 0.1, 0.15, 0.2, 0.3)
_SHUTDOWN_DRAIN_TIMEOUT_SEC = 10.0
_TRANSLATION_MAP_LOCK = threading.Lock()


@contextlib.contextmanager
def _acquire_page_edit_lock(
    target: str | Path,
    timeout_sec: float = 30.0,
) -> Iterator[Any]:
    lock_fn = None
    try:
        from mtui_mcp import project_store as _ps

        lock_fn = getattr(_ps, "page_edit_lock", None)
    except Exception:
        lock_fn = None
    if callable(lock_fn):
        with lock_fn(target, timeout_sec=timeout_sec) as locked:
            yield locked
    else:
        yield target


def _regions_list_from_entry(val: Any) -> Optional[list[dict[str, Any]]]:
    if isinstance(val, dict):
        regs = val.get("regions", [])
        return regs if isinstance(regs, list) else []
    if isinstance(val, list):
        return val
    return None


def _json_belongs_to_source_page(abs_src: str, json_path: Optional[str | Path]) -> bool:
    """Return True iff `json_path` is the dedicated per-page `*_translations.json` for `abs_src`."""
    if not json_path:
        return False
    jp_abs = os.path.abspath(str(json_path))
    src_stem = os.path.splitext(os.path.basename(abs_src))[0]
    if os.path.normcase(os.path.basename(jp_abs)) != os.path.normcase(f"{src_stem}_translations.json"):
        return False
    src_dir = os.path.dirname(abs_src)
    try:
        jp_dir_real = Path(os.path.realpath(os.path.dirname(jp_abs)))
        src_dir_real = Path(os.path.realpath(src_dir))
        trans_dir_real = Path(os.path.realpath(f"{src_dir}-translated"))
    except OSError:
        return False
    for allowed_root in (src_dir_real, trans_dir_real):
        try:
            if jp_dir_real == allowed_root or jp_dir_real.is_relative_to(allowed_root):
                return True
        except ValueError:
            continue
    return False


def _select_page_entry_from_raw_json(
    raw_json: Any,
    source_path: str | Path,
    *,
    json_path: Optional[str | Path] = None,
) -> tuple[Optional[str], list[dict[str, Any]]]:
    """Match ONLY the entry belonging to ``source_path`` (supporting dict & legacy list entries)."""
    if not isinstance(raw_json, dict) or not raw_json:
        return None, []

    abs_src = os.path.abspath(str(source_path))
    if abs_src in raw_json:
        regs = _regions_list_from_entry(raw_json[abs_src])
        if regs is not None:
            return abs_src, regs

    norm_target = os.path.normcase(os.path.normpath(abs_src))
    norm_real = os.path.normcase(os.path.realpath(abs_src))

    rel_bases: list[str] = []
    if json_path is not None:
        jp_abs = os.path.abspath(str(json_path))
        jp_dir = os.path.dirname(jp_abs)
        rel_bases.append(jp_dir)
        if (
            os.path.normcase(os.path.basename(jp_dir)) == "json"
            and os.path.normcase(os.path.basename(os.path.dirname(jp_dir))) == "manga_translator_work"
        ):
            proj_dir = os.path.dirname(os.path.dirname(jp_dir))
            rel_bases.append(proj_dir)
            if proj_dir.lower().endswith("-translated"):
                rel_bases.append(proj_dir[: -len("-translated")])

    # 1. Canonical path match (exact absolute path or relative key resolved against json_path's root)
    for k, v in raw_json.items():
        if not isinstance(k, str):
            continue
        regs = _regions_list_from_entry(v)
        if regs is None:
            continue
        try:
            if os.path.isabs(k):
                if (
                    os.path.normcase(os.path.normpath(k)) == norm_target
                    or os.path.normcase(os.path.realpath(k)) == norm_real
                ):
                    return k, regs
            else:
                for base_dir in rel_bases:
                    cand_abs = os.path.abspath(os.path.join(base_dir, k))
                    if (
                        os.path.normcase(os.path.normpath(cand_abs)) == norm_target
                        or os.path.normcase(os.path.realpath(cand_abs)) == norm_real
                    ):
                        return k, regs
        except Exception:
            continue

    # 2. Migrated / relative key fallback — ONLY when page ownership is verifiable and key does not belong to another page
    is_dedicated_page_json = _json_belongs_to_source_page(abs_src, json_path)
    if not is_dedicated_page_json:
        return None, []

    target_base = os.path.normcase(os.path.basename(abs_src))
    src_stem = os.path.normcase(os.path.splitext(os.path.basename(abs_src))[0])
    src_parent_name = os.path.normcase(os.path.basename(os.path.dirname(abs_src)))

    def _is_safe_migrated_or_relative_key(key_str: str, *, require_exact_basename: bool) -> bool:
        key_base = os.path.normcase(os.path.basename(key_str))
        key_stem = os.path.normcase(os.path.splitext(key_base)[0])
        if require_exact_basename and key_base != target_base:
            return False
        if not require_exact_basename and key_stem != src_stem:
            return False
        if os.path.isabs(key_str):
            # Never match an absolute path that still resolves to a different existing page on disk
            try:
                if os.path.exists(key_str) and os.path.normcase(os.path.realpath(key_str)) != norm_real:
                    return False
            except OSError:
                return False
            return True
        key_dir = os.path.dirname(key_str)
        if not key_dir or key_dir in (".",):
            return True
        return os.path.normcase(os.path.basename(key_dir)) == src_parent_name

    base_matches = [
        k
        for k, v in raw_json.items()
        if isinstance(k, str)
        and _regions_list_from_entry(v) is not None
        and _is_safe_migrated_or_relative_key(k, require_exact_basename=True)
    ]
    if len(base_matches) == 1:
        matched_k = base_matches[0]
        return matched_k, _regions_list_from_entry(raw_json[matched_k]) or []

    if len(raw_json) == 1:
        only_k = next(iter(raw_json.keys()))
        only_regs = _regions_list_from_entry(raw_json[only_k])
        if (
            isinstance(only_k, str)
            and only_regs is not None
            and _is_safe_migrated_or_relative_key(only_k, require_exact_basename=False)
        ):
            return only_k, only_regs

    return None, []


def _run_subprocess_bounded_tails(
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout_sec: int,
    max_chars: int = 4000,
    runtime: Optional["EngineRuntime"] = None,
) -> tuple[int, str, str]:
    """Run subprocess while streaming stdout/stderr into bounded tail ring buffers."""
    if runtime is not None and getattr(runtime, "shutting_down", False):
        raise RuntimeError("MTUI Worker is shutting down")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    if runtime is not None:
        runtime.register_subprocess(proc)

    def _make_tail_reader(pipe: Any, sink: dict[str, str], key: str) -> threading.Thread:
        def _reader() -> None:
            chunks: collections.deque[str] = collections.deque()
            total_len = 0
            try:
                if pipe is not None:
                    while True:
                        piece = pipe.read(4096)
                        if not piece:
                            break
                        chunks.append(piece)
                        total_len += len(piece)
                        while len(chunks) > 1 and (total_len - len(chunks[0])) >= max_chars:
                            total_len -= len(chunks.popleft())
            except Exception:
                pass
            sink[key] = "".join(chunks)[-max_chars:]

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        return t

    tails: dict[str, str] = {"stdout": "", "stderr": ""}
    t_out = _make_tail_reader(proc.stdout, tails, "stdout")
    t_err = _make_tail_reader(proc.stderr, tails, "stderr")
    try:
        proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5.0)
        except Exception:
            pass
        t_out.join(timeout=2.0)
        t_err.join(timeout=2.0)
        raise
    finally:
        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=2.0)
        t_out.join(timeout=5.0)
        t_err.join(timeout=5.0)
        for p in (proc.stdout, proc.stderr):
            if p is not None:
                try:
                    p.close()
                except Exception:
                    pass
        if runtime is not None:
            runtime.unregister_subprocess(proc)

    return int(proc.returncode or 0), tails["stdout"], tails["stderr"]


def _atomic_replace_file(
    src: str,
    dst: str,
    *,
    retry_delays: tuple[float, ...] = _ATOMIC_REPLACE_RETRY_DELAYS,
) -> None:
    """Atomically replace `dst` with `src`, retrying transient Windows access/sharing locks (`WinError 5`/`32`)."""
    for delay in (*retry_delays, None):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            is_transient_windows_lock = os.name == "nt" and (
                isinstance(exc, PermissionError)
                or winerror in _WINDOWS_TRANSIENT_REPLACE_WINERRORS
            )
            if not is_transient_windows_lock or delay is None:
                raise
            time.sleep(delay)


def _setup_environment(mtui_root: str) -> None:
    os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")
    os.environ.setdefault("QT_IMAGEIO_MAXALLOC", "1024")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("TQDM_DISABLE", "1")
    font_dir = os.path.join(mtui_root, "fonts")
    if os.path.isdir(font_dir):
        os.environ.setdefault("QT_QPA_FONTDIR", font_dir)

    desktop_dir = os.path.join(mtui_root, "desktop_qt_ui")
    mcp_src_dir = str(Path(__file__).resolve().parents[2])
    for p in (mcp_src_dir, desktop_dir, mtui_root):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    os.chdir(mtui_root)


class EngineRuntime:
    """Thread-safe holder for manga-translator-ui services inside the worker process."""

    def __init__(self, mtui_root: str):
        self.mtui_root = os.path.abspath(mtui_root)
        self.lock = threading.RLock()
        self._state_cond = threading.Condition(threading.Lock())
        self.shutting_down = False
        self._in_flight_requests = 0
        self._subproc_lock = threading.Lock()
        self._active_subprocesses: set[Any] = set()
        self.last_used = time.time()
        self._initialized = False
        self._export_service = None
        self._bubble_mask_cache: dict[tuple[str, int], Any] = {}

    def _get_subproc_registry(self) -> tuple[threading.Lock, set[Any]]:
        lck = getattr(self, "_subproc_lock", None)
        if lck is None:
            lck = threading.Lock()
            self._subproc_lock = lck
        procs = getattr(self, "_active_subprocesses", None)
        if procs is None:
            procs = set()
            self._active_subprocesses = procs
        return lck, procs

    def register_subprocess(self, proc: Any) -> None:
        lck, procs = self._get_subproc_registry()
        with lck:
            procs.add(proc)
            already_stopping = bool(getattr(self, "shutting_down", False))
        if already_stopping and proc.poll() is None:
            terminate_fn = getattr(proc, "terminate", None) or getattr(proc, "kill", None)
            if callable(terminate_fn):
                with contextlib.suppress(Exception):
                    terminate_fn()

    def unregister_subprocess(self, proc: Any) -> None:
        lck, procs = self._get_subproc_registry()
        with lck:
            procs.discard(proc)

    def terminate_active_subprocesses(self, grace_sec: float = 1.0) -> int:
        lck, procs = self._get_subproc_registry()
        with lck:
            active = list(procs)
        if not active:
            return 0
        reaped = 0
        for proc in active:
            if proc.poll() is None:
                terminate_fn = getattr(proc, "terminate", None) or getattr(proc, "kill", None)
                if callable(terminate_fn):
                    with contextlib.suppress(Exception):
                        terminate_fn()
        deadline = time.monotonic() + max(0.0, float(grace_sec))
        for proc in active:
            if proc.poll() is None:
                rem = max(0.0, deadline - time.monotonic())
                with contextlib.suppress(Exception):
                    proc.wait(timeout=rem)
            if proc.poll() is None:
                kill_fn = getattr(proc, "kill", None)
                if callable(kill_fn):
                    with contextlib.suppress(Exception):
                        kill_fn()
                with contextlib.suppress(Exception):
                    proc.wait(timeout=1.5)
            if proc.poll() is not None:
                reaped += 1
        with lck:
            for proc in active:
                if proc.poll() is not None:
                    procs.discard(proc)
        return reaped

    def _runtime_lock(self) -> threading.RLock:
        lck = getattr(self, "lock", None)
        if lck is None:
            lck = threading.RLock()
            self.lock = lck
        return lck

    def _get_state_cond(self) -> threading.Condition:
        cond = getattr(self, "_state_cond", None)
        if cond is None:
            cond = threading.Condition(threading.Lock())
            self._state_cond = cond
            if not hasattr(self, "shutting_down"):
                self.shutting_down = False
            if not hasattr(self, "_in_flight_requests"):
                self._in_flight_requests = 0
        return cond

    def begin_request(self) -> bool:
        cond = self._get_state_cond()
        with cond:
            if getattr(self, "shutting_down", False):
                return False
            self._in_flight_requests = int(getattr(self, "_in_flight_requests", 0)) + 1
            return True

    def end_request(self) -> None:
        cond = self._get_state_cond()
        with cond:
            self._in_flight_requests = max(0, int(getattr(self, "_in_flight_requests", 0)) - 1)
            if self._in_flight_requests == 0:
                cond.notify_all()

    def mark_shutting_down(self) -> None:
        cond = self._get_state_cond()
        with cond:
            self.shutting_down = True
            cond.notify_all()

    def wait_for_inflight_drain(self, timeout_sec: float = _SHUTDOWN_DRAIN_TIMEOUT_SEC) -> bool:
        cond = self._get_state_cond()
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        with cond:
            self.shutting_down = True
            while int(getattr(self, "_in_flight_requests", 0)) > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                cond.wait(timeout=remaining)
            return True

    def ensure_initialized(self) -> None:
        if getattr(self, "_initialized", False):
            return
        with self._runtime_lock():
            if getattr(self, "_initialized", False):
                return
            # Load PyTorch before PyQt6 to prevent Windows DLL search-path collision (`c10.dll`, pytorch#166628)
            try:
                import torch  # noqa: F401
            except ImportError:
                pass

            from manga_translator.rendering.text_render._fonts import (
                _ensure_qt_runtime,
                _register_project_fonts,
                _register_system_fonts,
            )

            _ensure_qt_runtime()
            _register_project_fonts()
            _register_system_fonts()

            from services import ServiceManager
            from services.export_service import ExportService

            if not ServiceManager.initialize(self.mtui_root, root_widget=None):
                raise RuntimeError(f"ServiceManager.initialize failed for mtui_root={self.mtui_root!r}")
            self._export_service = ExportService()
            self._initialized = True

    def ping(self) -> dict[str, Any]:
        self.ensure_initialized()
        self.last_used = time.time()
        import torch
        from PyQt6.QtGui import QFontDatabase
        from services import get_config_service

        cuda_ok = bool(torch.cuda.is_available())
        gpu_name = torch.cuda.get_device_name(0) if cuda_ok else None
        cfg_svc = get_config_service()
        cfg_dict = cfg_svc.get_config().model_dump() if cfg_svc else {}

        return {
            "status": "ok",
            "mtui_root": self.mtui_root,
            "python_executable": sys.executable,
            "python_version": sys.version.split()[0],
            "torch_version": torch.__version__,
            "cuda_available": cuda_ok,
            "gpu_name": gpu_name,
            "qt_font_family_count": len(QFontDatabase.families()),
            "active_translator": (cfg_dict.get("translator") or {}).get("translator"),
            "active_target_lang": (cfg_dict.get("translator") or {}).get("target_lang"),
            "active_inpainter": (cfg_dict.get("inpainter") or {}).get("inpainter"),
            "active_layout_mode": (cfg_dict.get("render") or {}).get("layout_mode"),
            "default_font_family": (cfg_dict.get("render") or {}).get("font_family"),
        }

    def _get_config_dict(self) -> dict[str, Any]:
        from services import get_config_service

        cfg_svc = get_config_service()
        if cfg_svc is not None:
            # Reload user config from disk in case user edited settings in UI
            try:
                cfg_svc._load_configs_with_priority()
            except Exception:
                pass
            cfg_obj = cfg_svc.get_config()
            if hasattr(cfg_obj, "model_dump"):
                return cfg_obj.model_dump()
        return {}

    @staticmethod
    def _update_translation_map(output_path: str, source_path: str) -> None:
        out_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(out_dir, exist_ok=True)
        map_file = os.path.join(out_dir, "translation_map.json")
        with _TRANSLATION_MAP_LOCK:
            mapping: dict[str, str] = {}
            if os.path.isfile(map_file):
                try:
                    with open(map_file, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        mapping = loaded
                except Exception:
                    mapping = {}
            mapping[os.path.normpath(output_path)] = os.path.normpath(source_path)
            fd, tmp = tempfile.mkstemp(prefix=".translation_map.", suffix=".tmp", dir=out_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(mapping, f, ensure_ascii=False, indent=2)
                _atomic_replace_file(tmp, map_file)
                tmp = ""
            finally:
                if tmp and os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

    @staticmethod
    def _persist_last_export_dir(
        json_path: str,
        export_dir: str,
        output_path: Optional[str] = None,
        source_path: Optional[str] = None,
    ) -> bool:
        if not json_path or not os.path.isfile(json_path):
            return False
        lock_target = source_path or json_path
        with _acquire_page_edit_lock(lock_target):
            if not os.path.isfile(json_path):
                return False
            tmp = ""
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict) or not data:
                    return False

                target_keys: list[str] = []
                if source_path:
                    matched_k, _ = _select_page_entry_from_raw_json(
                        data,
                        source_path,
                        json_path=json_path,
                    )
                    if matched_k is not None and isinstance(data.get(matched_k), (dict, list)):
                        target_keys = [matched_k]
                else:
                    entry_keys = [
                        k for k, v in data.items() if isinstance(k, str) and isinstance(v, (dict, list))
                    ]
                    if len(entry_keys) == 1:
                        target_keys = entry_keys
                    elif output_path:
                        out_stem = os.path.normcase(os.path.splitext(os.path.basename(output_path))[0])
                        stem_matches = [
                            k
                            for k in entry_keys
                            if os.path.normcase(os.path.splitext(os.path.basename(k))[0]) == out_stem
                        ]
                        if len(stem_matches) == 1:
                            target_keys = stem_matches

                if not target_keys:
                    return False

                changed = False
                wrote_any = False
                norm_dir = os.path.normpath(export_dir)
                norm_out = os.path.normpath(output_path) if output_path else None
                for k in target_keys:
                    val = data.get(k)
                    if isinstance(val, list):
                        val = {"regions": val}
                        data[k] = val
                        changed = True
                    elif not isinstance(val, dict):
                        continue
                    wrote_any = True
                    if val.get("last_export_dir") != norm_dir:
                        val["last_export_dir"] = norm_dir
                        changed = True
                    if norm_out and val.get("last_output_path") != norm_out:
                        val["last_output_path"] = norm_out
                        changed = True
                if not wrote_any:
                    return False
                if changed:
                    fd, tmp = tempfile.mkstemp(
                        prefix=f".{os.path.basename(json_path)}.",
                        suffix=".tmp",
                        dir=os.path.dirname(json_path),
                    )
                    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    _atomic_replace_file(tmp, json_path)
                    tmp = ""
                return True
            finally:
                if tmp and os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass

    def render_page(self, params: dict[str, Any]) -> dict[str, Any]:
        """Render a page using manga-translator-ui's exact ExportService + Qt6 offscreen engine."""
        self.ensure_initialized()
        with self._runtime_lock():
            self.last_used = time.time()
            t0 = time.perf_counter()

            import cv2
            import numpy as np
            from editor.controller_export_service import (
                EditorControllerExportService,
                ExportJob,
            )
            from editor.document_state import ExportBase
            from editor.inpaint_state import InpaintKey
            from PIL import Image
            from services import get_file_service, get_render_parameter_service

            source_path = os.path.abspath(params["source_path"])
            display_base_path = os.path.abspath(params.get("display_base_path") or source_path)
            inpainted_path = os.path.abspath(params["inpainted_path"])
            output_path = os.path.abspath(params["output_path"])
            json_path = os.path.abspath(params["json_path"])
            force_backend_inpaint = bool(params.get("force_backend_inpaint", False))
            export_psd = bool(params.get("export_psd", False))
            psd_script_only = bool(params.get("psd_script_only", False))
            inpainter_override = params.get("inpainter")
            render_overrides = params.get("render_overrides") or {}

            config = self._get_config_dict()
            if inpainter_override:
                config.setdefault("inpainter", {})["inpainter"] = str(inpainter_override)
            if export_psd or psd_script_only:
                config.setdefault("cli", {})["export_editable_psd"] = True
            if psd_script_only:
                config.setdefault("cli", {})["psd_script_only"] = True
            render_cfg = config.setdefault("render", {})
            for rk, rv in render_overrides.items():
                render_cfg[rk] = rv
            # Strictly lock off ALL native auto-wrap / semantic line-break heuristics:
            # line breaking (`\n` -> `[BR]`) is 100% judged and controlled by the Agent!
            render_cfg["disable_auto_wrap"] = True
            render_cfg["semantic_linebreak"] = False
            render_cfg["optimize_line_breaks"] = False

            with Image.open(display_base_path) as im:
                source_pil = im.convert("RGB")
            w, h = source_pil.size

            file_svc = get_file_service()
            regions, raw_mask, _, overlays = file_svc.load_translation_json(source_path)

            # Align mask to display_base dimensions
            mask_u8: Optional[np.ndarray] = None
            if raw_mask is not None:
                arr_m = np.asarray(raw_mask)
                if arr_m.ndim == 3:
                    arr_m = arr_m[..., 0]
                if arr_m.shape != (h, w):
                    arr_m = cv2.resize(arr_m, (w, h), interpolation=cv2.INTER_NEAREST)
                mask_u8 = np.where(arr_m > 0, 255, 0).astype(np.uint8)

            # Build enhanced regions just like EditorControllerExportService._build_enhanced_regions
            render_param_svc = get_render_parameter_service()
            render_param_svc.clear_cache()
            quad_fn = getattr(EditorControllerExportService, "_perspective_quad_world", None)
            enhanced_regions: list[dict[str, Any]] = []
            for idx, reg in enumerate(regions):
                rc = copy.deepcopy(reg)
                quad_world = quad_fn(rc) if callable(quad_fn) else None
                if quad_world is not None:
                    rc["perspective_quad"] = quad_world
                    rc["center"] = [
                        float(np.mean([p[0] for p in quad_world])),
                        float(np.mean([p[1] for p in quad_world])),
                    ]
                else:
                    EditorControllerExportService.apply_white_frame_center(rc)
                rc.update(render_param_svc.export_parameters_for_backend(idx, rc))
                rc["disable_auto_wrap"] = True
                rc["semantic_linebreak"] = False
                rc["optimize_line_breaks"] = False
                enhanced_regions.append(rc)

            has_nonempty_mask = mask_u8 is not None and bool(np.any(mask_u8))
            inpaint_key = InpaintKey(document_id=1, mask_revision=1)

            if not has_nonempty_mask:
                export_base = ExportBase(
                    kind="source",
                    source_image=source_pil,
                    render_image=source_pil,
                    mask=None,
                    inpaint_key=None,
                )
            elif os.path.isfile(inpainted_path) and not force_backend_inpaint:
                with Image.open(inpainted_path) as inp_im:
                    inp_rgb = np.array(inp_im.convert("RGB"), dtype=np.uint8, copy=True)
                if inp_rgb.shape[:2] != (h, w):
                    inp_rgb = cv2.resize(inp_rgb, (w, h), interpolation=cv2.INTER_LANCZOS4)
                export_base = ExportBase(
                    kind="paired",
                    source_image=source_pil,
                    render_image=inp_rgb,
                    mask=mask_u8,
                    inpaint_key=inpaint_key,
                )
            else:
                export_base = ExportBase(
                    kind="backend_inpaint",
                    source_image=source_pil,
                    render_image=source_pil,
                    mask=mask_u8,
                    inpaint_key=inpaint_key,
                )

            paint_overlay = overlays.get("paint")
            if paint_overlay is None:
                from manga_translator.utils.path_manager import find_paint_overlay_path

                legacy_po = find_paint_overlay_path(source_path)
                if legacy_po and os.path.isfile(legacy_po):
                    with Image.open(legacy_po) as po_im:
                        paint_overlay = np.array(po_im.convert("RGBA"), dtype=np.uint8, copy=True)

            job = ExportJob(
                automatic=False,
                source_path=source_path,
                output_path=output_path,
                export_base=export_base,
                regions=enhanced_regions,
                config=config,
                paint_overlay=paint_overlay,
                stamp_overlay=overlays.get("stamp"),
                paste_overlays=tuple(overlays.get("paste_overlays") or ()),
            )
            try:
                res = self._export_service.execute_export_job(job)
            finally:
                job.release_resources()

            if res.error:
                raise RuntimeError(res.error)

            export_base_kind = export_base.kind
            enhanced_region_count = len(enhanced_regions)
            psd_script_only_cfg = bool(config.get("cli", {}).get("psd_script_only", False))

        # Release `_runtime_lock` BEFORE acquiring `page_edit_lock` in `_persist_last_export_dir`
        # so global lock hierarchy stays strictly `page_edit_lock -> _runtime_lock` (R2-F01).
        metadata_saved = True
        metadata_error: Optional[str] = None
        if not bool(params.get("proof_only", False)):
            meta_errors: list[str] = []
            try:
                self._update_translation_map(output_path, source_path)
            except Exception as exc:
                meta_errors.append(f"translation_map: {exc}")
            try:
                persisted = self._persist_last_export_dir(
                    json_path,
                    os.path.dirname(output_path),
                    output_path=output_path,
                    source_path=source_path,
                )
                if not persisted:
                    meta_errors.append(f"page_json: no matching writable page entry in {json_path}")
            except Exception as exc:
                meta_errors.append(f"page_json: {exc}")
            if meta_errors:
                metadata_saved = False
                metadata_error = "; ".join(meta_errors)

        out_payload: dict[str, Any] = {
            "status": "ok" if metadata_saved else "partial",
            "output_path": output_path,
            "export_base_kind": export_base_kind,
            "region_count": enhanced_region_count,
            "export_psd": bool(export_psd or psd_script_only),
            "psd_script_only": psd_script_only_cfg,
            "metadata_saved": metadata_saved,
            "elapsed_sec": round(time.perf_counter() - t0, 3),
        }
        if metadata_error is not None:
            out_payload["metadata_error"] = metadata_error
        return out_payload

    def incremental_neural_inpaint(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run incremental crop-box neural inpainting (`lama_large` / `lama_mpe`) matching `EditorControllerInpaintService`."""
        self.ensure_initialized()
        with self._runtime_lock():
            self.last_used = time.time()
            t0 = time.perf_counter()

            import cv2
            import numpy as np
            import torch
            from PIL import Image

            from manga_translator.config import (
                Inpainter,
                InpainterConfig,
                InpaintPrecision,
            )
            from manga_translator.inpainting import dispatch as inpaint_dispatch

            display_base_path = os.path.abspath(params["display_base_path"])
            inpainted_path = os.path.abspath(params["inpainted_path"])
            padding = max(16, int(params.get("padding", 50)))
            full_reinpaint = bool(params.get("full_reinpaint", False))
            prev_mask_synced = bool(params.get("prev_mask_synced", False))

            with Image.open(display_base_path) as im:
                base_rgb = np.array(im.convert("RGB"), dtype=np.uint8, copy=True)
            h, w = base_rgb.shape[:2]

            def _decode_b64_mask(b64_str: Optional[str]) -> np.ndarray:
                if not b64_str:
                    return np.zeros((h, w), dtype=np.uint8)
                buf = np.frombuffer(base64.b64decode(b64_str), dtype=np.uint8)
                m = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
                if m is None:
                    return np.zeros((h, w), dtype=np.uint8)
                if m.ndim == 3:
                    m = m[..., 0]
                if m.shape != (h, w):
                    m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
                return np.where(m > 0, 255, 0).astype(np.uint8)

            current_mask = _decode_b64_mask(params.get("current_mask_b64"))
            added_mask = _decode_b64_mask(params.get("added_mask_b64"))
            removed_mask = _decode_b64_mask(params.get("removed_mask_b64"))
            if params.get("prev_inpainted_mask_b64") is not None:
                prev_inp_mask = _decode_b64_mask(params.get("prev_inpainted_mask_b64"))
                added_mask = np.where((added_mask > 0) | ((current_mask > 0) & (prev_inp_mask == 0)), 255, 0).astype(
                    np.uint8
                )
                removed_mask = np.where(
                    (removed_mask > 0) | ((prev_inp_mask > 0) & (current_mask == 0)), 255, 0
                ).astype(np.uint8)

            cfg = self._get_config_dict()
            inp_cfg_dict = cfg.get("inpainter") or {}
            cli_cfg_dict = cfg.get("cli") or {}

            raw_inp = params.get("inpainter") or inp_cfg_dict.get("inpainter") or "lama_large"
            inp_name = str(getattr(raw_inp, "value", raw_inp)).strip()
            if "." in inp_name:
                inp_name = inp_name.split(".", 1)[1]
            try:
                inpainter_enum = Inpainter(inp_name)
            except ValueError:
                try:
                    inpainter_enum = Inpainter[inp_name]
                except KeyError:
                    inpainter_enum = Inpainter.lama_large

            inp_config = InpainterConfig()
            prec_str = str(inp_cfg_dict.get("inpainting_precision") or "fp32").strip()
            if "." in prec_str:
                prec_str = prec_str.split(".", 1)[1]
            try:
                inp_config.inpainting_precision = InpaintPrecision(prec_str)
            except ValueError:
                if hasattr(InpaintPrecision, prec_str):
                    inp_config.inpainting_precision = InpaintPrecision[prec_str]
            if hasattr(inp_config, "force_use_torch_inpainting"):
                inp_config.force_use_torch_inpainting = bool(
                    inp_cfg_dict.get("force_use_torch_inpainting", False)
                )
            raw_blur = (
                params.get("blur_radius")
                if params.get("blur_radius") is not None
                else inp_cfg_dict.get("blur_radius", 0)
            )
            clamped_blur = max(0, min(200, int(raw_blur or 0)))
            if hasattr(inp_config, "blur_radius"):
                inp_config.blur_radius = clamped_blur

            inpaint_size = int(params.get("inpainting_size") or inp_cfg_dict.get("inpainting_size") or 2048)
            use_gpu = bool(cli_cfg_dict.get("use_gpu", True)) and torch.cuda.is_available()
            device = "cuda" if use_gpu else "cpu"

            def _run_dispatch(crop_img: np.ndarray, crop_mask: np.ndarray) -> np.ndarray:
                loop = asyncio.new_event_loop()
                try:
                    res = loop.run_until_complete(
                        inpaint_dispatch(
                            inpainter_key=inpainter_enum,
                            image=crop_img,
                            mask=crop_mask,
                            config=inp_config,
                            inpainting_size=inpaint_size,
                            device=device,
                        )
                    )
                    return np.asarray(res, dtype=np.uint8)[..., :3].copy()
                finally:
                    loop.close()

            blur_enum = getattr(Inpainter, "blur", None)
            is_blur_inpainter = (
                (blur_enum is not None and inpainter_enum == blur_enum)
                or str(getattr(inpainter_enum, "value", inpainter_enum)).lower() == "blur"
            )
            has_prev = (
                os.path.isfile(inpainted_path)
                and not full_reinpaint
                and not is_blur_inpainter
                and prev_mask_synced
            )
            prev_rgb: Optional[np.ndarray] = None
            if has_prev:
                with Image.open(inpainted_path) as pim:
                    prev_rgb = np.array(pim.convert("RGB"), dtype=np.uint8, copy=True)
                if prev_rgb.shape[:2] != (h, w):
                    prev_rgb = None

            crop_box_used: Optional[list[int]] = None
            if prev_rgb is None:
                if np.any(current_mask):
                    final_rgb = _run_dispatch(base_rgb.copy(), current_mask.copy())
                    crop_box_used = [0, 0, w, h]
                else:
                    final_rgb = base_rgb.copy()
            else:
                final_rgb = prev_rgb.copy()
                if np.any(removed_mask):
                    unmasked = current_mask == 0
                    final_rgb[unmasked] = base_rgb[unmasked]

                if np.any(added_mask):
                    ys, xs = np.where(added_mask > 0)
                    y1 = max(0, int(np.min(ys)) - padding)
                    y2 = min(h, int(np.max(ys)) + padding + 1)
                    x1 = max(0, int(np.min(xs)) - padding)
                    x2 = min(w, int(np.max(xs)) + padding + 1)
                    repaired_crop = _run_dispatch(
                        final_rgb[y1:y2, x1:x2].copy(),
                        added_mask[y1:y2, x1:x2].copy(),
                    )
                    final_rgb[y1:y2, x1:x2] = repaired_crop
                    crop_box_used = [x1, y1, x2, y2]

                if np.any(removed_mask):
                    unmasked = current_mask == 0
                    final_rgb[unmasked] = base_rgb[unmasked]

            saved_inpainted_path = self._export_service.save_inpainted_image(display_base_path, final_rgb, cfg)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            return {
                "status": "ok",
                "inpainted_path": str(saved_inpainted_path or inpainted_path),
                "inpainter": inpainter_enum.value,
                "blur_radius": clamped_blur,
                "device": device,
                "crop_box_xyxy": crop_box_used,
                "elapsed_sec": round(time.perf_counter() - t0, 3),
            }

    def ocr_or_translate_regions(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run native OCR and/or Translator on specified regions or custom boxes."""
        self.ensure_initialized()
        with self._runtime_lock():
            self.last_used = time.time()
            t0 = time.perf_counter()

            import numpy as np
            from PIL import Image
            from services import (
                get_config_service,
                get_ocr_service,
                get_translation_service,
            )

            from manga_translator.utils import Quadrilateral

            display_base_path = os.path.abspath(params["display_base_path"])
            raw_source_param = params.get("source_path")
            if raw_source_param:
                source_ref_path = os.path.abspath(str(raw_source_param))
            else:
                try:
                    from mtui_mcp.path_resolver import resolve_source_image_path

                    source_ref_path = str(resolve_source_image_path(display_base_path))
                except Exception:
                    source_ref_path = display_base_path

            json_path = params.get("json_path")
            run_ocr = bool(params.get("run_ocr", True))
            run_translate = bool(params.get("run_translate", False))
            ocr_override = params.get("ocr")
            translator_name = params.get("translator")
            target_lang = params.get("target_lang")
            region_indices = params.get("region_indices") or []
            custom_boxes = params.get("custom_boxes_xyxy") or []
            commit = bool(params.get("commit", False))

            with Image.open(display_base_path) as im:
                pil_img = im.convert("RGB")
                img_rgb = np.array(pil_img, dtype=np.uint8, copy=True)
            im_h, im_w = img_rgb.shape[:2]

            raw_json = None
            matched_page_key: Optional[str] = None
            regions_list: list[dict[str, Any]] = []
            if json_path and os.path.isfile(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    raw_json = json.load(f)
                matched_page_key, regions_list = _select_page_entry_from_raw_json(
                    raw_json,
                    source_ref_path,
                    json_path=json_path,
                )

            def _geom_identity_key(r_obj: dict[str, Any]) -> str:
                return json.dumps(
                    [
                        r_obj.get("lines"),
                        r_obj.get("center"),
                        round(float(r_obj.get("angle", 0.0) or 0.0), 4),
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                )

            geom_key_counts: collections.Counter[str] = collections.Counter(
                _geom_identity_key(r_obj) for r_obj in regions_list if isinstance(r_obj, dict)
            )

            items_to_process: list[dict[str, Any]] = []
            for r_idx in region_indices:
                idx = int(r_idx)
                if 0 <= idx < len(regions_list):
                    reg = regions_list[idx]
                    if not isinstance(reg, dict):
                        continue
                    lines = reg.get("lines") or []
                    pts_all = [
                        (float(pt[0]), float(pt[1]))
                        for poly in lines
                        for pt in poly
                        if isinstance(pt, (list, tuple)) and len(pt) >= 2
                    ]
                    if not pts_all:
                        continue
                    ang = float(reg.get("angle", 0.0) or 0.0)
                    if abs(ang) > 1e-6:
                        raw_center = reg.get("center")
                        if isinstance(raw_center, (list, tuple)) and len(raw_center) >= 2:
                            rcx, rcy = float(raw_center[0]), float(raw_center[1])
                        else:
                            raw_xs = [p[0] for p in pts_all]
                            raw_ys = [p[1] for p in pts_all]
                            rcx = (min(raw_xs) + max(raw_xs)) / 2.0
                            rcy = (min(raw_ys) + max(raw_ys)) / 2.0
                        rad = math.radians(ang)
                        ca, sa = math.cos(rad), math.sin(rad)
                        world_pts = [
                            (
                                rcx + (px - rcx) * ca - (py - rcy) * sa,
                                rcy + (px - rcx) * sa + (py - rcy) * ca,
                            )
                            for px, py in pts_all
                        ]
                    else:
                        world_pts = pts_all
                    xs = [max(0.0, min(float(im_w), p[0])) for p in world_pts]
                    ys = [max(0.0, min(float(im_h), p[1])) for p in world_pts]
                    x1 = min(xs)
                    y1 = min(ys)
                    x2 = max(x1 + 1.0, max(xs))
                    y2 = max(y1 + 1.0, max(ys))
                    box_pts = np.array(
                        [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                        dtype=np.float32,
                    )
                    existing_prob = reg.get("prob")
                    g_key = _geom_identity_key(reg)
                    items_to_process.append(
                        {
                            "region_index": idx,
                            "region_uid": str(reg.get("region_uid") or "").strip() or None,
                            "orig_lines": copy.deepcopy(lines),
                            "orig_center": copy.deepcopy(reg.get("center")),
                            "orig_angle": ang,
                            "orig_geom_key": g_key,
                            "orig_geom_unique": geom_key_counts[g_key] == 1,
                            "orig_text": str(reg.get("text", "")),
                            "orig_texts": copy.deepcopy(reg.get("texts")),
                            "orig_translation": reg.get("translation"),
                            "orig_translation_raw": reg.get("translation_raw"),
                            "orig_translation_rich": copy.deepcopy(reg.get("translation_rich")),
                            "pts": box_pts,
                            "text": str(reg.get("text", "")),
                            "prob": 1.0 if existing_prob is None else float(existing_prob),
                        }
                    )

            for cbox in custom_boxes:
                x1, y1, x2, y2 = (float(v) for v in cbox[:4])
                box_pts = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)
                items_to_process.append(
                    {
                        "region_index": None,
                        "region_uid": None,
                        "orig_lines": None,
                        "pts": box_pts,
                        "text": "",
                        "prob": 1.0,
                    }
                )

            if not items_to_process:
                return {"status": "ok", "committed": False, "results": [], "elapsed_sec": 0.0}

            cfg_svc = get_config_service()
            runtime_cfg = None
            if cfg_svc is not None:
                try:
                    cfg_svc._load_configs_with_priority()
                except Exception:
                    pass
                runtime_cfg = cfg_svc.get_config()

            loop = asyncio.new_event_loop()
            try:
                if run_ocr:
                    from manga_translator.config import Ocr
                    from manga_translator.ocr import dispatch as dispatch_ocr

                    ocr_svc = get_ocr_service()
                    ocr_cfg = ocr_svc._get_current_config()

                    def _parse_ocr_choice(raw_val: Any, *, strict: bool) -> Optional[Any]:
                        if raw_val is None:
                            return None
                        if isinstance(raw_val, Ocr):
                            return raw_val
                        s = str(getattr(raw_val, "value", raw_val)).strip()
                        if not s:
                            return None
                        if "." in s:
                            s = s.split(".", 1)[1]
                        try:
                            return Ocr(s)
                        except ValueError:
                            if hasattr(Ocr, s):
                                return Ocr[s]
                        if strict:
                            raise ValueError(f"Unsupported OCR model: {raw_val!r}")
                        return None

                    explicit_ocr = _parse_ocr_choice(ocr_override, strict=True)
                    if explicit_ocr is not None:
                        ocr_cfg.ocr = explicit_ocr
                    else:
                        editor_ocr_raw = getattr(getattr(runtime_cfg, "app", None), "editor_ocr", None)
                        editor_ocr_enum = _parse_ocr_choice(editor_ocr_raw, strict=False)
                        if editor_ocr_enum is not None:
                            ocr_cfg.ocr = editor_ocr_enum

                    loop.run_until_complete(ocr_svc.prepare_model(ocr_cfg.ocr))

                    quads = [Quadrilateral(pts=it["pts"], text="", prob=1.0) for it in items_to_process]
                    ocr_out = loop.run_until_complete(
                        dispatch_ocr(
                            ocr_cfg.ocr,
                            img_rgb,
                            quads,
                            ocr_cfg,
                            ocr_svc.device,
                            False,
                            runtime_config=runtime_cfg,
                        )
                    )
                    for it, q_res in zip(items_to_process, ocr_out):
                        it["text"] = str(getattr(q_res, "text", "") or "")
                        raw_prob = getattr(q_res, "prob", None)
                        it["prob"] = 1.0 if raw_prob is None else float(raw_prob)

                if run_translate:
                    trans_svc = get_translation_service()
                    from manga_translator.config import Translator

                    tr_enum = None
                    if translator_name:
                        tr_str = str(getattr(translator_name, "value", translator_name)).strip()
                        if "." in tr_str:
                            tr_str = tr_str.split(".", 1)[1]
                        if tr_str:
                            try:
                                tr_enum = Translator(tr_str)
                            except ValueError:
                                if hasattr(Translator, tr_str):
                                    tr_enum = Translator[tr_str]
                                else:
                                    raise ValueError(f"Unsupported translator: {translator_name!r}")
                    texts_in = [it["text"] for it in items_to_process]
                    tr_results = loop.run_until_complete(
                        trans_svc.translate_text_batch(
                            texts_in,
                            translator=tr_enum,
                            target_lang=target_lang,
                            image=pil_img,
                            regions=regions_list or None,
                        )
                    )
                    tr_list = list(tr_results or [])
                    if len(tr_list) < len(items_to_process):
                        tr_list.extend([None] * (len(items_to_process) - len(tr_list)))
                    for it, tr_item in zip(items_to_process, tr_list):
                        tr_text = getattr(tr_item, "translated_text", None) if tr_item is not None else None
                        if tr_text is not None:
                            it["translation"] = str(tr_text)
                            it["translated"] = True
                        else:
                            it["translation"] = None
                            it["translated"] = False
            finally:
                loop.close()
                try:
                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass

        # Release `_runtime_lock` BEFORE acquiring `page_edit_lock` (`page_edit_lock -> _runtime_lock`, R2-F01).
        changed_json = False
        has_region_commits = any(
            it["region_index"] is not None
            and ((run_ocr and bool(it.get("text"))) or (run_translate and it.get("translation") is not None))
            for it in items_to_process
        )
        if commit and has_region_commits:
            if not json_path or matched_page_key is None:
                for it in items_to_process:
                    if it["region_index"] is not None:
                        it["committed"] = False
                        it["conflict"] = True
                        it["conflict_reason"] = "page_entry_missing"
            else:
                with _acquire_page_edit_lock(source_ref_path or json_path):
                    if not os.path.isfile(json_path):
                        for it in items_to_process:
                            if it["region_index"] is not None:
                                it["committed"] = False
                                it["conflict"] = True
                                it["conflict_reason"] = "json_file_missing"
                    else:
                        with open(json_path, "r", encoding="utf-8") as f:
                            latest_json = json.load(f)
                        latest_key, latest_regions = _select_page_entry_from_raw_json(
                            latest_json,
                            source_ref_path,
                            json_path=json_path,
                        )
                        if latest_key is None:
                            for it in items_to_process:
                                if it["region_index"] is not None:
                                    it["committed"] = False
                                    it["conflict"] = True
                                    it["conflict_reason"] = "page_entry_missing"
                        else:
                            for it in items_to_process:
                                r_idx = it["region_index"]
                                if r_idx is None:
                                    continue
                                wants_ocr = bool(run_ocr and it.get("text"))
                                wants_tr = bool(run_translate and it.get("translation") is not None)
                                if not (wants_ocr or wants_tr):
                                    it["committed"] = False
                                    continue

                                # R2-F16: Strict UID matching when `region_uid` existed; unique geometry verification when UID-less
                                target_reg: Optional[dict[str, Any]] = None
                                r_uid = it.get("region_uid")
                                if r_uid:
                                    for cand_reg in latest_regions:
                                        if (
                                            isinstance(cand_reg, dict)
                                            and str(cand_reg.get("region_uid") or "").strip() == r_uid
                                        ):
                                            target_reg = cand_reg
                                            break
                                elif it.get("orig_lines") is not None and it.get("orig_geom_unique"):
                                    geom_cands = [
                                        cand_reg
                                        for cand_reg in latest_regions
                                        if isinstance(cand_reg, dict)
                                        and _geom_identity_key(cand_reg) == it.get("orig_geom_key")
                                    ]
                                    if len(geom_cands) == 1:
                                        target_reg = geom_cands[0]

                                if target_reg is None:
                                    it["committed"] = False
                                    it["conflict"] = True
                                    it["conflict_reason"] = "region_deleted_or_ambiguous"
                                    continue

                                # R2-F15: Verify fields read for inference and fields to be overwritten have not changed concurrently
                                cur_src_matches = (
                                    str(target_reg.get("text", "")) == it.get("orig_text", "")
                                    and target_reg.get("texts") == it.get("orig_texts")
                                )
                                if wants_ocr:
                                    cur_geom_matches = _geom_identity_key(target_reg) == it.get("orig_geom_key")
                                    if not (cur_src_matches and cur_geom_matches):
                                        it["committed"] = False
                                        it["conflict"] = True
                                        it["conflict_reason"] = "source_or_geometry_modified_during_ocr"
                                        continue

                                if wants_tr:
                                    cur_tr_matches = (
                                        target_reg.get("translation") == it.get("orig_translation")
                                        and target_reg.get("translation_rich") == it.get("orig_translation_rich")
                                    )
                                    if (
                                        it.get("orig_translation") is None
                                        and it.get("orig_translation_raw") is not None
                                    ):
                                        cur_tr_matches = cur_tr_matches and (
                                            target_reg.get("translation_raw") == it.get("orig_translation_raw")
                                        )
                                    if not (cur_src_matches and cur_tr_matches):
                                        it["committed"] = False
                                        it["conflict"] = True
                                        it["conflict_reason"] = "source_or_translation_modified_during_translate"
                                        continue

                                reg_updated = False
                                if wants_ocr:
                                    target_reg["text"] = it["text"]
                                    target_reg["texts"] = [it["text"]]
                                    target_reg["prob"] = float(it.get("prob", 1.0))
                                    reg_updated = True
                                if wants_tr:
                                    br_t = str(it["translation"]).replace("\n", "[BR]")
                                    target_reg["translation"] = br_t
                                    target_reg["translation_raw"] = br_t
                                    target_reg.pop("translation_rich", None)
                                    reg_updated = True
                                it["committed"] = reg_updated
                                if reg_updated:
                                    changed_json = True

                            if changed_json:
                                fd, tmp = tempfile.mkstemp(
                                    prefix=f".{os.path.basename(json_path)}.",
                                    suffix=".tmp",
                                    dir=os.path.dirname(json_path),
                                )
                                try:
                                    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                                        json.dump(latest_json, f, ensure_ascii=False, indent=2)
                                        f.flush()
                                        os.fsync(f.fileno())
                                    _atomic_replace_file(tmp, json_path)
                                    tmp = ""
                                finally:
                                    if tmp and os.path.exists(tmp):
                                        try:
                                            os.remove(tmp)
                                        except OSError:
                                            pass

        out_records: list[dict[str, Any]] = []
        conflict_count = 0
        for it in items_to_process:
            rec: dict[str, Any] = {
                "region_index": it["region_index"],
                "source_text": it.get("text", ""),
                "ocr_prob": round(float(it.get("prob", 1.0)), 3),
                "translation": it.get("translation"),
            }
            if run_translate:
                rec["translated"] = bool(it.get("translated", False))
            if commit and it["region_index"] is not None:
                rec["committed"] = bool(it.get("committed", False))
            if it.get("conflict"):
                conflict_count += 1
                rec["conflict"] = True
                rec["conflict_reason"] = it.get("conflict_reason", "conflict")
            out_records.append(rec)

        resp_payload: dict[str, Any] = {
            "status": "partial" if conflict_count > 0 else "ok",
            "committed": bool(commit and changed_json),
            "results": out_records,
            "elapsed_sec": round(time.perf_counter() - t0, 3),
        }
        if conflict_count > 0:
            resp_payload["conflict_count"] = conflict_count
        return resp_payload

    def release_vram(self) -> dict[str, Any]:
        """Unload cached neural inpainter / OCR models inside the warm worker and empty PyTorch CUDA cache."""
        import gc

        import torch

        with self._runtime_lock():
            unloaded: list[str] = []
            try:
                from manga_translator import inpainting as mt_inpainting

                cache = getattr(mt_inpainting, "inpainter_cache", None)
                if isinstance(cache, dict) and cache:
                    loop = asyncio.new_event_loop()
                    try:
                        for k, inst in list(cache.items()):
                            unload_fn = getattr(inst, "unload", None)
                            if callable(unload_fn):
                                try:
                                    res = unload_fn()
                                    if asyncio.iscoroutine(res):
                                        loop.run_until_complete(res)
                                except Exception:
                                    pass
                            unloaded.append(f"inpainter:{k}")
                        cache.clear()
                    finally:
                        loop.close()
            except Exception:
                pass

            if getattr(self, "_initialized", False):
                try:
                    from services import get_ocr_service

                    ocr_svc = get_ocr_service()
                    if ocr_svc is not None:
                        unload_cur = getattr(ocr_svc, "unload_current_model", None)
                        cur_ocr = getattr(ocr_svc, "current_prepared_ocr", None)
                        if callable(unload_cur):
                            loop = asyncio.new_event_loop()
                            try:
                                res = unload_cur()
                                if asyncio.iscoroutine(res):
                                    loop.run_until_complete(res)
                            finally:
                                loop.close()
                            if cur_ocr is not None:
                                unloaded.append(f"ocr_service:{getattr(cur_ocr, 'value', cur_ocr)}")
                        ocr_svc.model_prepared = False
                        ocr_svc.current_prepared_ocr = None
                        if getattr(ocr_svc, "_yolo_detector", None) is not None:
                            ocr_svc._yolo_detector = None
                except Exception:
                    pass

            try:
                from manga_translator import ocr as mt_ocr

                ocache = getattr(mt_ocr, "ocr_cache", None)
                if isinstance(ocache, dict) and ocache:
                    loop = asyncio.new_event_loop()
                    try:
                        for k, inst in list(ocache.items()):
                            unload_fn = getattr(inst, "unload", None)
                            if callable(unload_fn):
                                try:
                                    res = unload_fn()
                                    if asyncio.iscoroutine(res):
                                        loop.run_until_complete(res)
                                except Exception:
                                    pass
                            unloaded.append(f"ocr:{k}")
                        ocache.clear()
                    finally:
                        loop.close()
            except Exception:
                pass

            gc.collect()
            cuda_ok = bool(torch.cuda.is_available())
            if cuda_ok:
                torch.cuda.empty_cache()
            alloc_mb = round(torch.cuda.memory_allocated() / (1024 * 1024), 1) if cuda_ok else 0.0
            reserved_mb = round(torch.cuda.memory_reserved() / (1024 * 1024), 1) if cuda_ok else 0.0
            return {
                "status": "ok",
                "unloaded": unloaded,
                "cuda_allocated_mb": alloc_mb,
                "cuda_reserved_mb": reserved_mb,
            }

    def run_cli_pipeline(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute `python -m manga_translator local` in a clean child process for batch/fresh translation."""
        with self._runtime_lock():
            self.last_used = time.time()
            t0 = time.perf_counter()

            # R2-F53: Validate inputs and all CLI arguments BEFORE unloading warm GPU models!
            inputs = params.get("inputs") or []
            if isinstance(inputs, str):
                inputs = [inputs]
            if not isinstance(inputs, (list, tuple)):
                raise ValueError("inputs must be a list of paths")
            cleaned_inputs = [str(x).strip() for x in inputs if str(x).strip()]
            if not cleaned_inputs:
                raise ValueError("inputs list is required")

            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--mtui-cli-local", "local", "-i", *cleaned_inputs]
            else:
                cmd = [sys.executable, "-m", "manga_translator", "local", "-i", *cleaned_inputs]
            if params.get("output_dir"):
                cmd.extend(["-o", str(params["output_dir"])])
            if params.get("config_path"):
                cmd.extend(["--config", str(params["config_path"])])
            if params.get("overwrite", True):
                cmd.append("--overwrite")
            if params.get("use_gpu") is True:
                cmd.append("--use-gpu")
            if params.get("disable_onnx_gpu") is True:
                cmd.append("--disable-onnx-gpu")
            if params.get("output_format"):
                cmd.extend(["--format", str(params["output_format"])])
            if params.get("batch_size") is not None:
                batch_sz = int(params["batch_size"])
                if batch_sz <= 0:
                    raise ValueError(f"batch_size must be positive, got {batch_sz}")
                cmd.extend(["--batch-size", str(batch_sz)])
            if params.get("attempts") is not None:
                attempts_val = int(params["attempts"])
                if attempts_val < -1:
                    raise ValueError(f"attempts must be >= -1, got {attempts_val}")
                cmd.extend(["--attempts", str(attempts_val)])
            if bool(params.get("subprocess") or params.get("use_subprocess")):
                cmd.append("--subprocess")
            if params.get("memory_limit") is not None:
                mem_lim = int(params["memory_limit"])
                if mem_lim <= 0:
                    raise ValueError(f"memory_limit must be positive, got {mem_lim}")
                cmd.extend(["--memory-limit", str(mem_lim)])
            if params.get("memory_percent") is not None:
                mem_pct = float(params["memory_percent"])
                if not math.isfinite(mem_pct) or not (0.0 < mem_pct <= 100.0):
                    raise ValueError(f"memory_percent must be in (0, 100], got {params['memory_percent']!r}")
                cmd.extend(["--memory-percent", str(int(round(mem_pct)))])
            if params.get("batch_per_restart") is not None:
                bpr = int(params["batch_per_restart"])
                if bpr < 0:
                    raise ValueError(f"batch_per_restart must be >= 0, got {bpr}")
                cmd.extend(["--batch-per-restart", str(bpr)])

            timeout_sec = int(params.get("timeout_sec", 1800))
            if timeout_sec <= 0:
                raise ValueError(f"timeout_sec must be positive, got {timeout_sec}")

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["TQDM_DISABLE"] = "1"

            # Flush warm worker GPU models only after validation succeeds so invalid calls never evict warm models
            try:
                self.release_vram()
            except Exception:
                pass

            returncode, tail_out, tail_err = _run_subprocess_bounded_tails(
                cmd,
                cwd=self.mtui_root,
                env=env,
                timeout_sec=timeout_sec,
                max_chars=4000,
                runtime=self,
            )

            if returncode != 0:
                raise RuntimeError(
                    f"manga_translator local exited with code {returncode}:\nSTDOUT:\n{tail_out}\nSTDERR:\n{tail_err}"
                )
            return {
                "status": "ok",
                "returncode": returncode,
                "elapsed_sec": round(time.perf_counter() - t0, 2),
                "stdout_tail": tail_out,
            }

    def detect_fonts(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run local YuzuMarker ResNet-50 4x Ensemble font detection across a page or chapter and free GPU VRAM."""
        import gc

        import numpy as np
        import torch
        import torch.nn as nn
        from PIL import Image
        from torchvision.models import resnet50

        from mtui_mcp.font_detector import (
            CACHE_VERSION,
            CLEAN_STRIP_RE,
            FAMILY_NAMES,
            FONT_COUNT,
            INPUT_SIZE,
            WEIGHT_NAMES,
            classify_vcb_font_entry,
            load_font_detection_cache,
            recommend_mtui_font_for_prediction,
            region_bbox_signature,
            save_font_detection_cache_atomic,
        )
        from mtui_mcp.path_resolver import list_manga_pages_in_path
        from mtui_mcp.project_store import (
            load_page_json,
            region_bbox_xyxy,
            region_center,
        )

        with self._runtime_lock():
            self.last_used = time.time()
            t0 = time.perf_counter()
            target_path = str(params.get("target_path") or "").strip()
            if not target_path:
                raise ValueError("target_path is required for detect_fonts")
            force_refresh = bool(params.get("force_refresh", False))

            mdir = Path(self.mtui_root) / "models" / "yuzumarker"
            labels_path = mdir / "font-labels-ex.json"
            ckpt_entries: list[tuple[Path, int, int]] = []
            if mdir.is_dir():
                for p in mdir.glob("*.ckpt"):
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    if st.st_size > 300_000_000:
                        ckpt_entries.append((p, int(st.st_size), int(st.st_mtime_ns)))
            ckpt_entries.sort(key=lambda item: item[0].name)
            ckpts = [item[0] for item in ckpt_entries]
            if not labels_path.is_file() or not ckpts:
                raise FileNotFoundError(
                    f"YuzuMarker models not found under {mdir}. Expected font-labels-ex.json and >=1 ResNet-50 4x .ckpt."
                )

            try:
                labels_st = labels_path.stat()
                labels_sig: Optional[list[int]] = [int(labels_st.st_size), int(labels_st.st_mtime_ns)]
            except OSError:
                labels_sig = None
            ensemble_sig = [[p.name, sz, mt] for p, sz, mt in ckpt_entries]

            pages = list_manga_pages_in_path(target_path)
            if not pages:
                return {"status": "ok", "scanned_pages": 0, "scanned_regions": 0, "cached_hits": 0, "elapsed_sec": 0.0}

            # Group pages by work_dir so each work_dir gets its own `.yuzumarker_font_cache.json`
            caches_by_work: dict[Path, dict[str, Any]] = {}
            dirty_work_dirs: set[Path] = set()
            pages_needing_gpu: list[tuple[Any, list[int]]] = []
            cached_hits = 0

            def _build_page_regions_font_sig(regs_in: list[Any]) -> list[Any]:
                sig_items: list[Any] = []
                for ridx_i, r_i in enumerate(regs_in):
                    if not isinstance(r_i, dict):
                        sig_items.append([ridx_i, "invalid"])
                        continue
                    bb = region_bbox_xyxy(r_i)
                    if bb[2] - bb[0] < 4 or bb[3] - bb[1] < 4:
                        sig_items.append([ridx_i, "tiny"])
                        continue
                    stxt = str(r_i.get("text") or "").strip()
                    sclean = CLEAN_STRIP_RE.sub("", stxt)
                    cx_i, cy_i = region_center(r_i)
                    sig_items.append(
                        [
                            ridx_i,
                            region_bbox_signature(bb),
                            round(bb[2] - bb[0], 1),
                            round(bb[3] - bb[1], 1),
                            stxt,
                            len(sclean),
                            [round(cx_i, 1), round(cy_i, 1)],
                            str(r_i.get("region_uid") or ""),
                        ]
                    )
                return sig_items

            def _finalize_page_font_recommendations(
                page_recs: list[dict[str, Any]],
                meta_by_ridx: dict[int, tuple[list[float], str]],
                page_wh_cur: tuple[int, int],
            ) -> None:
                # Same-panel short-box neighbor propagation (`<=2` clean chars & low top1_prob `<0.35` within `195px`)
                for rec in page_recs:
                    rec.pop("neighbor_propagated_family", None)
                    rec.pop("neighbor_source_ridx", None)
                    rec.pop("neighbor_dist", None)
                    t1_p = float(rec["top_fonts"][0]["prob"]) if rec["top_fonts"] else 0.0
                    if rec["n_clean_chars"] <= 2 and rec["pred_conf"] < 0.48 and t1_p < 0.35:
                        cx, cy = rec["center"]
                        best_nb = None
                        best_dist = 195.0
                        for other in page_recs:
                            if other["ridx"] == rec["ridx"]:
                                continue
                            ot1 = float(other["top_fonts"][0]["prob"]) if other["top_fonts"] else 0.0
                            if other["n_clean_chars"] >= 3 and other["pred_conf"] >= 0.80 and ot1 >= 0.35:
                                d = math.hypot(cx - other["center"][0], cy - other["center"][1])
                                if d < best_dist:
                                    best_dist = d
                                    best_nb = other
                        if best_nb is not None and best_nb["pred_family"] != rec["pred_family"]:
                            rec["neighbor_propagated_family"] = best_nb["pred_family"]
                            rec["neighbor_source_ridx"] = best_nb["ridx"]
                            rec["neighbor_dist"] = round(best_dist, 1)

                    rec_bbox, rec_src_txt = meta_by_ridx[rec["ridx"]]
                    rec_ff, rec_b, _ = recommend_mtui_font_for_prediction(
                        rec,
                        bbox_wh=(max(1.0, rec_bbox[2] - rec_bbox[0]), max(1.0, rec_bbox[3] - rec_bbox[1])),
                        source_text=rec_src_txt,
                        page_wh=page_wh_cur,
                    )
                    rec["recommended_font_family"] = rec_ff
                    rec["recommended_bold"] = rec_b

            raw_cnn_keys = ("pred_family", "pred_conf", "family_probs", "bold_prob", "heavy_prob", "top_fonts")

            for pp in pages:
                if not pp.json_exists or not pp.display_base_path.is_file():
                    continue
                try:
                    bst = pp.display_base_path.stat()
                    base_sig = [int(bst.st_size), int(bst.st_mtime_ns)]
                except OSError:
                    continue

                if pp.work_dir not in caches_by_work:
                    loaded = load_font_detection_cache(pp.work_dir)
                    try:
                        loaded_ver = int(loaded.get("version") or 1)
                    except (TypeError, ValueError):
                        loaded_ver = 1
                    ensemble_matches = (
                        loaded.get("ensemble_sig") == ensemble_sig
                        and loaded.get("labels_sig") == labels_sig
                    )
                    if not isinstance(loaded.get("pages"), dict) or loaded_ver < CACHE_VERSION or not ensemble_matches:
                        loaded = {
                            "version": CACHE_VERSION,
                            "ensemble_ckpts": [c.name for c in ckpts],
                            "ensemble_sig": ensemble_sig,
                            "labels_sig": labels_sig,
                            "pages": {},
                        }
                    else:
                        loaded["version"] = CACHE_VERSION
                        loaded["ensemble_ckpts"] = [c.name for c in ckpts]
                        loaded["ensemble_sig"] = ensemble_sig
                        loaded["labels_sig"] = labels_sig
                    caches_by_work[pp.work_dir] = loaded

                _, img_data = load_page_json(pp)
                regs = img_data.get("regions", [])
                if not regs:
                    continue
                p_cache = caches_by_work[pp.work_dir]["pages"].get(pp.source_path.stem) or {}
                by_sig = p_cache.get("by_bbox_sig") or {}
                base_matches = (
                    p_cache.get("base_image_sig") == base_sig
                    and p_cache.get("display_base_name") == pp.display_base_path.name
                )
                cur_regions_sig = _build_page_regions_font_sig(regs)
                all_bboxes_cached = base_matches and all(
                    (not isinstance(r, dict))
                    or (region_bbox_xyxy(r)[2] - region_bbox_xyxy(r)[0] < 4)
                    or (region_bbox_xyxy(r)[3] - region_bbox_xyxy(r)[1] < 4)
                    or (
                        isinstance(by_sig.get(region_bbox_signature(region_bbox_xyxy(r))), dict)
                        and all(
                            rk in by_sig[region_bbox_signature(region_bbox_xyxy(r))]
                            for rk in raw_cnn_keys
                        )
                    )
                    for r in regs
                )
                if not force_refresh and all_bboxes_cached:
                    if p_cache.get("regions_state_sig") == cur_regions_sig:
                        cached_hits += len(regs)
                    else:
                        # R2-F13: OCR text or region layout/neighbor eligibility changed while image & bbox crops stayed identical:
                        # recompute neighbor propagation + font recommendations directly from cached raw CNN predictions!
                        with Image.open(pp.display_base_path) as hdr_im:
                            page_wh_cached = (int(hdr_im.width), int(hdr_im.height))
                        rebuilt_recs: list[dict[str, Any]] = []
                        rebuilt_meta: dict[int, tuple[list[float], str]] = {}
                        for ridx_c, reg_c in enumerate(regs):
                            if not isinstance(reg_c, dict):
                                continue
                            bb_c = region_bbox_xyxy(reg_c)
                            if bb_c[2] - bb_c[0] < 4 or bb_c[3] - bb_c[1] < 4:
                                continue
                            bsig_c = region_bbox_signature(bb_c)
                            raw_hit = by_sig[bsig_c]
                            src_txt_c = str(reg_c.get("text") or "").strip()
                            src_clean_c = CLEAN_STRIP_RE.sub("", src_txt_c)
                            cent_c = region_center(reg_c)
                            rebuilt_meta[ridx_c] = (bb_c, src_txt_c)
                            rec_c: dict[str, Any] = {
                                "ridx": ridx_c,
                                "bbox_sig": bsig_c,
                                "n_clean_chars": len(src_clean_c),
                                "center": [round(cent_c[0], 1), round(cent_c[1], 1)],
                                "pred_family": raw_hit["pred_family"],
                                "pred_conf": raw_hit["pred_conf"],
                                "family_probs": copy.deepcopy(raw_hit["family_probs"]),
                                "bold_prob": raw_hit["bold_prob"],
                                "heavy_prob": raw_hit["heavy_prob"],
                                "top_fonts": copy.deepcopy(raw_hit["top_fonts"]),
                            }
                            if reg_c.get("region_uid"):
                                rec_c["region_uid"] = str(reg_c["region_uid"])
                            rebuilt_recs.append(rec_c)
                        _finalize_page_font_recommendations(rebuilt_recs, rebuilt_meta, page_wh_cached)
                        caches_by_work[pp.work_dir]["pages"][pp.source_path.stem] = {
                            "base_image_sig": base_sig,
                            "display_base_name": pp.display_base_path.name,
                            "total_regions": len(regs),
                            "region_count": len(regs),
                            "regions_state_sig": cur_regions_sig,
                            "by_bbox_sig": {r["bbox_sig"]: r for r in rebuilt_recs},
                            "by_ridx": {str(r["ridx"]): r for r in rebuilt_recs},
                        }
                        dirty_work_dirs.add(pp.work_dir)
                        cached_hits += len(regs)
                else:
                    pages_needing_gpu.append((pp, base_sig))

            scanned_regions = 0
            nets: list[Any] = []
            fam_mat = None
            wt_mat = None

            def _crop_exact_data_py(rgb_arr: np.ndarray, bbox: list[float], delta: float) -> torch.Tensor:
                ih, iw = rgb_arr.shape[:2]
                x0 = max(0.0, min(float(iw - 1), float(bbox[0])))
                y0 = max(0.0, min(float(ih - 1), float(bbox[1])))
                x1 = max(x0 + 1.0, min(float(iw), float(bbox[2])))
                y1 = max(y0 + 1.0, min(float(ih), float(bbox[3])))
                bw, bh = x1 - x0, y1 - y0
                x0 = max(0, int(round(x0 - delta * bw)))
                y0 = max(0, int(round(y0 - delta * bh)))
                x1 = min(iw, int(round(x1 + delta * bw)))
                y1 = min(ih, int(round(y1 + delta * bh)))
                pil = Image.fromarray(rgb_arr[y0:y1, x0:x1]).resize((INPUT_SIZE, INPUT_SIZE), Image.Resampling.BILINEAR)
                arr = np.asarray(pil, dtype=np.float32).transpose(2, 0, 1) / 255.0
                return torch.from_numpy(arr)

            try:
                if pages_needing_gpu:
                    # Release cached LaMa/OCR GPU weights before loading dual ResNet-50 4x checkpoints
                    try:
                        self.release_vram()
                    except Exception:
                        pass
                    raw_labels = json.loads(labels_path.read_text(encoding="utf-8"))
                    f2i = {f: i for i, f in enumerate(FAMILY_NAMES)}
                    w2i = {w: i for i, w in enumerate(WEIGHT_NAMES)}
                    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

                    fam_mat = torch.zeros((FONT_COUNT, len(FAMILY_NAMES)), dtype=torch.float32, device=device)
                    wt_mat = torch.zeros((FONT_COUNT, len(WEIGHT_NAMES)), dtype=torch.float32, device=device)
                    fam_idx_arr: list[int] = []
                    for i, entry in enumerate(raw_labels):
                        fn, wn = classify_vcb_font_entry(entry)
                        fam_idx_arr.append(f2i[fn])
                        fam_mat[i, f2i[fn]] = 1.0
                        wt_mat[i, w2i[wn]] = 1.0

                    safe_globals_fn = getattr(getattr(torch, "serialization", None), "safe_globals", None)
                    for ckpt_p in ckpts:
                        safe_ctx = (
                            safe_globals_fn([argparse.Namespace])
                            if callable(safe_globals_fn)
                            else contextlib.nullcontext()
                        )
                        with safe_ctx:
                            raw_ckpt = torch.load(str(ckpt_p), map_location="cpu", weights_only=True)
                        if not isinstance(raw_ckpt, dict):
                            raise ValueError(f"Unexpected checkpoint payload type in {ckpt_p.name}")
                        sd = raw_ckpt["state_dict"] if isinstance(raw_ckpt.get("state_dict"), dict) else raw_ckpt
                        clean_sd = {}
                        for k, v in sd.items():
                            nk = k
                            if nk.startswith("model._orig_mod."):
                                nk = nk[len("model._orig_mod.") :]
                            elif nk.startswith("model."):
                                nk = nk[len("model.") :]
                            if nk.startswith("model."):
                                clean_sd[nk] = v
                        out_dim = int(clean_sd["model.fc.weight"].shape[0])
                        holder = nn.Module()
                        m_net = resnet50(weights=None)
                        m_net.fc = nn.Linear(2048, out_dim)
                        holder.model = m_net
                        holder.load_state_dict(clean_sd, strict=True)
                        m_net.eval().to(device)
                        nets.append(m_net)
                        del raw_ckpt, sd, clean_sd

                    for pp, base_sig in pages_needing_gpu:
                        _, img_data = load_page_json(pp)
                        regs = img_data.get("regions", [])
                        with Image.open(pp.display_base_path) as base_im:
                            rgb = np.asarray(base_im.convert("RGB"))
                        crops_tensors: list[torch.Tensor] = []
                        valid_meta: list[tuple[int, dict[str, Any], list[float], tuple[float, float]]] = []
                        for ridx, reg in enumerate(regs):
                            bbox = region_bbox_xyxy(reg)
                            if bbox[2] - bbox[0] < 4 or bbox[3] - bbox[1] < 4:
                                continue
                            crops_tensors.append(_crop_exact_data_py(rgb, bbox, 0.03))
                            crops_tensors.append(_crop_exact_data_py(rgb, bbox, 0.08))
                            valid_meta.append((ridx, reg, bbox, region_center(reg)))

                        if not valid_meta:
                            caches_by_work[pp.work_dir]["pages"][pp.source_path.stem] = {
                                "base_image_sig": base_sig,
                                "display_base_name": pp.display_base_path.name,
                                "total_regions": len(regs),
                                "region_count": len(regs),
                                "regions_state_sig": _build_page_regions_font_sig(regs),
                                "by_bbox_sig": {},
                                "by_ridx": {},
                            }
                            dirty_work_dirs.add(pp.work_dir)
                            continue

                        batch = torch.stack(crops_tensors, dim=0).to(device)
                        with torch.inference_mode():
                            prob_sum = None
                            for m_net in nets:
                                pr = torch.softmax(m_net(batch)[:, :FONT_COUNT], dim=-1)
                                prob_sum = pr if prob_sum is None else (prob_sum + pr)
                            p_ens = prob_sum / float(len(nets))  # type: ignore[operator]
                            p_reg = p_ens.view(len(valid_meta), 2, FONT_COUNT).mean(dim=1)
                            f_probs = torch.matmul(p_reg, fam_mat).cpu().numpy()
                            w_probs = torch.matmul(p_reg, wt_mat).cpu().numpy()
                            top_v, top_i = torch.topk(p_reg, 5, dim=-1)
                            top_v_np = top_v.cpu().numpy()
                            top_i_np = top_i.cpu().numpy()

                        page_recs: list[dict[str, Any]] = []
                        meta_by_ridx: dict[int, tuple[list[float], str]] = {}
                        for k, (ridx, reg, bbox, cent) in enumerate(valid_meta):
                            scanned_regions += 1
                            fp = {FAMILY_NAMES[j]: round(float(f_probs[k, j]), 4) for j in range(len(FAMILY_NAMES))}
                            wp = {WEIGHT_NAMES[j]: round(float(w_probs[k, j]), 4) for j in range(len(WEIGHT_NAMES))}
                            best_fam = max(fp.items(), key=lambda kv: kv[1])[0]
                            tfonts = [
                                {
                                    "font": raw_labels[int(top_i_np[k, j])]["path"].split("/")[-1],
                                    "family": FAMILY_NAMES[fam_idx_arr[int(top_i_np[k, j])]],
                                    "prob": round(float(top_v_np[k, j]), 4),
                                }
                                for j in range(5)
                            ]
                            src_txt = str(reg.get("text") or "").strip()
                            src_clean = CLEAN_STRIP_RE.sub("", src_txt)
                            meta_by_ridx[ridx] = (bbox, src_txt)
                            rec_item: dict[str, Any] = {
                                "ridx": ridx,
                                "bbox_sig": region_bbox_signature(bbox),
                                "n_clean_chars": len(src_clean),
                                "center": [round(cent[0], 1), round(cent[1], 1)],
                                "pred_family": best_fam,
                                "pred_conf": fp[best_fam],
                                "family_probs": fp,
                                "bold_prob": round(float(wp["bold"] + wp["heavy"]), 4),
                                "heavy_prob": round(float(wp["heavy"]), 4),
                                "top_fonts": tfonts,
                            }
                            if reg.get("region_uid"):
                                rec_item["region_uid"] = str(reg["region_uid"])
                            page_recs.append(rec_item)

                        page_wh_cur = (int(rgb.shape[1]), int(rgb.shape[0]))
                        _finalize_page_font_recommendations(page_recs, meta_by_ridx, page_wh_cur)

                        by_sig_out = {r["bbox_sig"]: r for r in page_recs}
                        by_ridx_out = {str(r["ridx"]): r for r in page_recs}
                        caches_by_work[pp.work_dir]["pages"][pp.source_path.stem] = {
                            "base_image_sig": base_sig,
                            "display_base_name": pp.display_base_path.name,
                            "total_regions": len(regs),
                            "region_count": len(regs),
                            "regions_state_sig": _build_page_regions_font_sig(regs),
                            "by_bbox_sig": by_sig_out,
                            "by_ridx": by_ridx_out,
                        }
                        dirty_work_dirs.add(pp.work_dir)

                for wdir in dirty_work_dirs:
                    save_font_detection_cache_atomic(wdir, caches_by_work[wdir])
            finally:
                nets.clear()
                del fam_mat, wt_mat
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

            return {
                "status": "ok",
                "ensemble_models": [c.name for c in ckpts],
                "scanned_pages": len(pages_needing_gpu),
                "scanned_regions": scanned_regions,
                "cached_hits": cached_hits,
                "cache_files": [str(w / ".yuzumarker_font_cache.json") for w in caches_by_work],
                "elapsed_sec": round(time.perf_counter() - t0, 2),
            }


def main() -> None:
    parser = argparse.ArgumentParser(description="MTUI-MCP Warm Engine Worker")
    parser.add_argument("--mtui-engine-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--mtui-root", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    # Keep original stdout fd solely for the 1-line startup handshake, then redirect stdout -> stderr!
    handshake_stream = sys.stdout
    sys.stdout = sys.stderr

    _setup_environment(args.mtui_root)
    runtime = EngineRuntime(args.mtui_root)
    expected_token = args.token

    class WorkerHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *log_args: Any) -> None:
            return  # Silence HTTP access logs

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            token = self.headers.get("X-MTUI-Worker-Token", "")
            if token != expected_token:
                self._send_json(403, {"ok": False, "error": "Invalid worker token"})
                return

            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                req = json.loads(body)
                action = req.get("action")
                params = req.get("params") or {}

                if action == "shutdown":
                    runtime.mark_shutting_down()
                    self._send_json(200, {"ok": True, "data": {"status": "shutting_down"}})

                    def _drain_and_shutdown() -> None:
                        drained = runtime.wait_for_inflight_drain(timeout_sec=_SHUTDOWN_DRAIN_TIMEOUT_SEC)
                        if not drained:
                            runtime.terminate_active_subprocesses()
                        self.server.shutdown()

                    threading.Thread(target=_drain_and_shutdown, daemon=True).start()
                    return

                if not runtime.begin_request():
                    self._send_json(503, {"ok": False, "error": "MTUI Worker is shutting down"})
                    return

                try:
                    if action == "ping":
                        data = runtime.ping()
                    elif action == "render_page":
                        data = runtime.render_page(params)
                    elif action == "incremental_neural_inpaint":
                        data = runtime.incremental_neural_inpaint(params)
                    elif action == "ocr_or_translate_regions":
                        data = runtime.ocr_or_translate_regions(params)
                    elif action == "run_cli_pipeline":
                        data = runtime.run_cli_pipeline(params)
                    elif action == "detect_fonts":
                        data = runtime.detect_fonts(params)
                    elif action == "release_vram":
                        data = runtime.release_vram()
                    else:
                        self._send_json(400, {"ok": False, "error": f"Unknown action: {action!r}"})
                        return

                    self._send_json(200, {"ok": True, "data": data})
                finally:
                    runtime.end_request()
            except Exception as exc:
                tb = traceback.format_exc()
                sys.stderr.write(f"[MTUI-Worker Error] {tb}\n")
                self._send_json(500, {"ok": False, "error": str(exc), "traceback": tb[-3000:]})

    server = ThreadingHTTPServer(("127.0.0.1", args.port), WorkerHandler)
    actual_port = server.server_address[1]
    handshake_stream.write(f"MTUI_WORKER_READY {json.dumps({'port': actual_port})}\n")
    handshake_stream.flush()

    try:
        server.serve_forever()
    finally:
        runtime.terminate_active_subprocesses()
        server.server_close()


if __name__ == "__main__":
    main()
