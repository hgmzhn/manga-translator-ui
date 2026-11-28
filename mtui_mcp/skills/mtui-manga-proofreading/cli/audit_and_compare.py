"""End-to-End Headless Initial Translation, QA Audit, Font Slicing, Closed-Loop Centering & 3-Column Comparison CLI.

Usage:
  0. Silent headless initial translation on an untranslated (or partially translated) raw manga folder + automatic before_result snapshot:
     python audit_and_compare.py translate-raw --chapter-dir "D:/Manga/Chapter" --backup-before-dir "./before_result"

  1. Audit baseline sizes + generate 5x contrast-enhanced LaMa ghost sheets:
     python audit_and_compare.py audit --chapter-dir "D:/Manga/Chapter" --out-dir "./audit_out"

  2. Extract 1.6x native un-annotated raw character crops (including ALL '!' shout bubbles & large text) to inspect Japanese font changes:
     python audit_and_compare.py raw-fonts --chapter-dir "D:/Manga/Chapter" --out-dir "./audit_out"

  2b. Run bilingual translation & OCR-mistranslation radar + export <=4-bubble 1.6x Raw crops for all suspect/low-OCR regions:
     python audit_and_compare.py script-audit --chapter-dir "D:/Manga/Chapter" --out-dir "./audit_out"

  3. Closed-loop post-render optical bubble centering (Cusp-Bisector Multi-Lobe Split + Gestalt Ellipse + Orthogonal 2-Axis Ink Equal-Clearance):
     # Inspect/export computed centers to JSON:
     python audit_and_compare.py center-bubbles --chapter-dir "D:/Manga/Chapter" --out-json "./bubble_centers.json"
     # Or run 2-pass closed-loop update + Qt6 re-render directly via mtui-mcp:
     python audit_and_compare.py center-bubbles --chapter-dir "D:/Manga/Chapter" --apply-mcp

  3b. Mandatory post-proofreading true >=1.0x native-res per-region visual self-check sheets
      ([Raw | Rendered] for bubbles, [Raw | Inpainted | Rendered] for frameless text):
     python audit_and_compare.py verify-crops --chapter-dir "D:/Manga/Chapter" --out-dir "./verify_crops"

  3c. Gate A baseline / after-snapshot + zero-regression lock:
     python audit_and_compare.py qa-audit --chapter-dir "D:/Manga/Chapter" --out-json "./qa_before.json" --force-yuzumarker
     python audit_and_compare.py qa-audit --chapter-dir "D:/Manga/Chapter" --out-json "./qa_after.json"
     python audit_and_compare.py qa-diff --before-json "./qa_before.json" --after-json "./qa_after.json" --fail-on-regression

  4. Generate cache-busted 3-panel (Raw | Before | After) comparison PNGs:
     python audit_and_compare.py compare --raw-dir "D:/Manga/Chapter" --before-dir "./before_result" --after-dir "D:/Manga/Chapter/manga_translator_work/result" --out-dir "./comparisons" --prefix cmp_v1_
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

_DEFAULT_IMAGE_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".jfif",
    ".webp",
    ".avif",
    ".bmp",
    ".tiff",
    ".tif",
    ".heic",
    ".heif",
}
_ORDERED_PROBE_EXTS: tuple[str, ...] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".avif",
    ".bmp",
    ".jfif",
    ".tiff",
    ".tif",
    ".heic",
    ".heif",
)
IMAGE_EXTS: set[str] = set(_DEFAULT_IMAGE_EXTS)


def _natural_sort_key(p: Path) -> list[int | str]:
    return [int(tok) if tok.isdigit() else tok for tok in re.split(r"(\d+)", p.name.casefold())]


def _ensure_repo_src_on_path() -> Path | None:
    """Locate `mtui_mcp` package root by walking ancestor directories (or `MTUI_MCP_SRC`) without fixed `parents[N]` depth."""
    found: Path | None = None
    env_src = os.environ.get("MTUI_MCP_SRC", "").strip()
    if env_src:
        env_p = Path(env_src).expanduser().resolve()
        for cand in (env_p, env_p.parent):
            if (cand / "mtui_mcp" / "__init__.py").is_file():
                if str(cand) not in sys.path:
                    sys.path.insert(0, str(cand))
                found = cand
                break
    if found is None:
        cur = Path(__file__).resolve().parent
        for anc in (cur, *cur.parents):
            for cand in (anc, anc / "src"):
                if (cand / "mtui_mcp" / "__init__.py").is_file():
                    if str(cand) not in sys.path:
                        sys.path.insert(0, str(cand))
                    found = cand
                    break
            if found is not None:
                break
    if found is not None:
        try:
            from mtui_mcp.path_resolver import SUPPORTED_IMAGE_EXTENSIONS

            IMAGE_EXTS.clear()
            IMAGE_EXTS.update(SUPPORTED_IMAGE_EXTENSIONS)
        except Exception:
            pass
    return found


def _discover_mtui_runtime(explicit_root: Path | None = None) -> tuple[Path, Path]:
    _ensure_repo_src_on_path()
    try:
        from mtui_mcp.config import get_runtime_settings

        rt = get_runtime_settings()
        if explicit_root is not None:
            rt.refresh(mtui_root=str(explicit_root))
        if rt.mtui_root and rt.mtui_python and rt.mtui_python.is_file():
            return rt.mtui_root, rt.mtui_python
    except Exception:
        pass

    candidates: list[Path] = [explicit_root] if explicit_root else []
    env_r = os.environ.get("MTUI_ROOT", "").strip()
    if env_r:
        candidates.append(Path(env_r).expanduser())
    candidates.extend(Path(__file__).resolve().parents)
    for root in candidates:
        if root and (root / "manga_translator" / "manga_translator.py").is_file():
            for rel_py in (
                "packaging/python/python.exe",
                ".venv/Scripts/python.exe",
                "env/python.exe",
                "env/Scripts/python.exe",
                ".venv/bin/python",
            ):
                py = root / rel_py
                if py.is_file():
                    return root.resolve(), Path(os.path.abspath(str(py)))
            if not getattr(sys, "frozen", False) and sys.executable:
                cur_py = Path(sys.executable)
                if cur_py.is_file():
                    return root.resolve(), Path(os.path.abspath(str(cur_py)))
    raise RuntimeError("Could not auto-discover manga-translator-ui root & Python interpreter. Set MTUI_ROOT or pass --mtui-root.")


# ---------------------------------------------------------------------------
# 0. Silent Headless Initial Translation (UI-Identical Engine + Auto Backup)
# ---------------------------------------------------------------------------

_HEADLESS_TRANSLATE_RUNNER = r'''
import asyncio
import json
import os
import sys
import time

mtui_root = os.path.abspath(sys.argv[1])
_payload_arg = sys.argv[2]
if os.path.isfile(_payload_arg):
    with open(_payload_arg, "r", encoding="utf-8") as _pf:
        payload = json.load(_pf)
else:
    payload = json.loads(_payload_arg)
chapter_dir = os.path.abspath(payload["chapter_dir"])
target_files = [os.path.abspath(p) for p in payload["files"]]
overwrite = bool(payload.get("overwrite", False))
config_path = payload.get("config_path")

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TQDM_DISABLE"] = "1"
os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("QT_IMAGEIO_MAXALLOC", "1024")
os.environ.setdefault("MANGA_TRANSLATOR_ENV_PATH", os.path.join(mtui_root, ".env"))
font_dir = os.path.join(mtui_root, "fonts")
if os.path.isdir(font_dir):
    os.environ.setdefault("QT_QPA_FONTDIR", font_dir)

for p in (os.path.join(mtui_root, "desktop_qt_ui"), mtui_root):
    if p not in sys.path:
        sys.path.insert(0, p)
os.chdir(mtui_root)

# Import torch BEFORE PyQt6 on Windows to prevent DLL load conflict
import torch  # noqa: F401
from manga_translator.rendering.text_render._fonts import (
    _ensure_qt_runtime,
    _register_project_fonts,
    _register_system_fonts,
)
from manga_translator.utils.dotenv_utils import load_app_dotenv
from desktop_qt_ui.services.config_service import ConfigService
from manga_translator.config import (
    ColorizerConfig,
    Config,
    DetectorConfig,
    InpainterConfig,
    OcrConfig,
    RenderConfig,
    TranslatorConfig,
    UpscaleConfig,
)
from manga_translator.manga_translator import MangaTranslator

_ensure_qt_runtime()
_register_project_fonts()
_register_system_fonts()
load_app_dotenv()

cfg_svc = ConfigService(mtui_root)
try:
    if config_path:
        if not cfg_svc.load_config_file(config_path):
            raise RuntimeError(f"Failed to load config file: {config_path}")

    cfg_dict = cfg_svc.get_config().model_dump()
finally:
    cfg_svc.shutdown()
cli_cfg = cfg_dict.get("cli", {}).copy()
cli_cfg["save_text"] = True
cli_cfg["save_to_source_dir"] = True
cli_cfg["overwrite"] = overwrite
cli_cfg.setdefault("use_gpu", True)
for partial_flag in (
    "load_text",
    "template",
    "generate_and_export",
    "translate_json_only",
    "upscale_only",
    "colorize_only",
    "inpaint_only",
):
    cli_cfg[partial_flag] = False
cfg_dict["cli"] = cli_cfg

translator_params = cli_cfg.copy()
translator_params.update(cfg_dict)
font_family = (cfg_dict.get("render") or {}).get("font_family")
if font_family:
    translator_params["font_family"] = font_family

explicit_keys = {"render", "upscale", "translator", "detector", "colorizer", "inpainter", "ocr"}
remaining_cfg = {k: v for k, v in cfg_dict.items() if k in Config.model_fields and k not in explicit_keys}

render_data = (cfg_dict.get("render") or {}).copy()
if render_data.get("direction") == "h":
    render_data["direction"] = "horizontal"
elif render_data.get("direction") == "v":
    render_data["direction"] = "vertical"

trans_data = (cfg_dict.get("translator") or {}).copy()
hq_prompt = trans_data.get("high_quality_prompt_path")
if hq_prompt and not os.path.isabs(hq_prompt):
    cand_prompt = os.path.join(mtui_root, hq_prompt)
    if os.path.exists(cand_prompt):
        trans_data["high_quality_prompt_path"] = cand_prompt

upscale_data = (cfg_dict.get("upscale") or {}).copy()
if "upscale_ratio" in upscale_data:
    rv = upscale_data["upscale_ratio"]
    if rv == "不使用" or rv is None:
        upscale_data["upscale_ratio"] = None
    elif isinstance(rv, str) and rv in ("x2", "x4", "DAT2 x4"):
        upscale_data["upscale_ratio"] = rv
    else:
        try:
            upscale_data["upscale_ratio"] = int(rv)
        except (TypeError, ValueError):
            upscale_data["upscale_ratio"] = None

config_obj = Config(
    render=RenderConfig(**render_data),
    upscale=UpscaleConfig(**upscale_data),
    translator=TranslatorConfig(**trans_data),
    detector=DetectorConfig(**(cfg_dict.get("detector") or {})),
    colorizer=ColorizerConfig(**(cfg_dict.get("colorizer") or {})),
    inpainter=InpainterConfig(**(cfg_dict.get("inpainter") or {})),
    ocr=OcrConfig(**(cfg_dict.get("ocr") or {})),
    **remaining_cfg,
)

output_fmt = cli_cfg.get("format")
if not output_fmt or output_fmt == "不指定":
    output_fmt = None

out_result_dir = os.path.join(chapter_dir, "manga_translator_work", "result")
os.makedirs(out_result_dir, exist_ok=True)
save_info = {
    "output_folder": out_result_dir,
    "format": output_fmt,
    "overwrite": overwrite,
    "input_folders": {os.path.normpath(chapter_dir)},
    "save_to_source_dir": True,
}

async def _run():
    t0 = time.perf_counter()
    print(
        f"[INIT] Headless MTUI translator={cfg_dict['translator']['translator']} "
        f"ocr={cfg_dict['ocr']['ocr']} inpainter={cfg_dict['inpainter']['inpainter']} "
        f"pages={len(target_files)} overwrite={overwrite}",
        flush=True,
    )
    translator = MangaTranslator(params=translator_params)

    async def _prog(state: str, finished: bool):
        if state.startswith("batch:"):
            print(f"[PROGRESS] {state} (elapsed={time.perf_counter()-t0:.1f}s)", flush=True)

    translator.add_progress_hook(_prog)
    items = [(fp, config_obj) for fp in target_files]
    contexts = await translator.translate_batch(items, save_info=save_info)

    ok, skipped, failed = 0, 0, 0
    errors = []
    for ctx in contexts:
        if not ctx:
            failed += 1
            errors.append("Empty context")
            continue
        fn = os.path.basename(getattr(ctx, "image_name", "") or "unknown")
        if getattr(ctx, "skipped", False):
            skipped += 1
        elif getattr(ctx, "translation_error", None):
            failed += 1
            errors.append(f"{fn}: {ctx.translation_error}")
        elif getattr(ctx, "success", False) or getattr(ctx, "result", None) is not None:
            ok += 1
        else:
            # Pages with 0 detected text regions still save JSON + copy clean output
            ok += 1

    print(
        f"[SUMMARY] ok={ok} skipped={skipped} failed={failed} elapsed={time.perf_counter()-t0:.1f}s",
        flush=True,
    )
    if failed > 0:
        for e in errors:
            print(f"[ERROR] {e}", file=sys.stderr, flush=True)
        sys.exit(1)

asyncio.run(_run())
'''


def _find_rendered_page(res_dir: Path, src_p: Path) -> Path | None:
    if not res_dir.is_dir():
        return None
    exact = res_dir / src_p.name
    if exact.is_file():
        return exact
    for ext in _ORDERED_PROBE_EXTS:
        if ext in IMAGE_EXTS:
            cand = res_dir / f"{src_p.stem}{ext}"
            if cand.is_file():
                return cand
    for ext in sorted(IMAGE_EXTS - set(_ORDERED_PROBE_EXTS)):
        cand = res_dir / f"{src_p.stem}{ext}"
        if cand.is_file():
            return cand
    return None


def _resolve_page_rendered_status(src_p: Path) -> tuple[bool, Path | None]:
    """Resolve `(json_exists, rendered_output_path_if_exists)` via `mtui_mcp.path_resolver.resolve_page_paths`
    (supporting `last_output_path`, `last_export_dir`, `-translated`, and `manga_translator_work/result`)."""
    _ensure_repo_src_on_path()
    src_norm = os.path.normcase(os.path.abspath(str(src_p)))
    try:
        from mtui_mcp.path_resolver import resolve_page_paths

        rp = resolve_page_paths(src_p)
        if rp.rendered_output_exists and rp.rendered_output_path.is_file():
            if os.path.normcase(os.path.abspath(str(rp.rendered_output_path))) != src_norm:
                return rp.json_exists, rp.rendered_output_path
        if rp.json_exists and rp.json_path.is_file():
            try:
                raw = json.loads(rp.json_path.read_text(encoding="utf-8"))
                img_meta = next(iter(raw.values())) if isinstance(raw, dict) and raw else {}
                last_out = img_meta.get("last_output_path") if isinstance(img_meta, dict) else None
                if isinstance(last_out, str) and last_out.strip():
                    cand = Path(last_out)
                    if (
                        cand.is_file()
                        and cand.suffix.lower() in IMAGE_EXTS
                        and os.path.normcase(os.path.abspath(str(cand))) != src_norm
                    ):
                        return True, cand.resolve()
            except Exception:
                pass
        res_fallback = _find_rendered_page(src_p.parent / "manga_translator_work" / "result", src_p)
        return rp.json_exists, res_fallback
    except Exception:
        jp = src_p.parent / "manga_translator_work" / "json" / f"{src_p.stem}_translations.json"
        return jp.is_file(), _find_rendered_page(src_p.parent / "manga_translator_work" / "result", src_p)


def _copy_file_atomic(src: Path, dst: Path) -> None:
    """Copy `src` to `dst` via a same-directory temp file + atomic replace so partial writes never leave corrupt backups."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dst.stem}_", suffix=f"{dst.suffix}.tmp", dir=str(dst.parent))
    os.close(fd)
    try:
        shutil.copy2(src, tmp_name)
        try:
            from mtui_mcp.project_store import _atomic_replace_with_retry

            _atomic_replace_with_retry(tmp_name, dst)
        except ImportError:
            os.replace(tmp_name, dst)
        tmp_name = ""
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _backup_result_to_before(
    res_dir: Path,
    backup_before_dir: Path,
    overwrite: bool = False,
    *,
    source_pages: list[Path] | None = None,
) -> int:
    _ensure_repo_src_on_path()
    backup_before_dir.mkdir(parents=True, exist_ok=True)
    existing_by_stem: dict[str, Path] = {}
    for p in backup_before_dir.iterdir():
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            try:
                if p.stat().st_size > 0:
                    existing_by_stem.setdefault(p.stem.casefold(), p)
            except OSError:
                pass

    kept = 0
    copied = 0
    if source_pages is not None:
        for sp in sorted(source_pages, key=_natural_sort_key):
            stem_cf = sp.stem.casefold()
            prev = existing_by_stem.get(stem_cf)
            if prev is not None and not overwrite:
                kept += 1
                continue
            _, rendered_p = _resolve_page_rendered_status(sp)
            if rendered_p is not None and rendered_p.is_file():
                dst = backup_before_dir / f"{sp.stem}{rendered_p.suffix}"
                _copy_file_atomic(rendered_p, dst)
                if prev is not None and prev != dst and prev.exists():
                    try:
                        prev.unlink()
                    except OSError:
                        pass
                existing_by_stem[stem_cf] = dst
                copied += 1
        total = kept + copied
        if copied == 0 and kept > 0:
            print(f"[BACKUP] Keeping existing {kept} before_result snapshot(s) in {backup_before_dir}", flush=True)
        else:
            print(
                f"[BACKUP] Saved {copied} initial-translation page(s) (kept {kept} existing) -> {backup_before_dir}",
                flush=True,
            )
        return total

    if not res_dir.is_dir():
        return len(existing_by_stem)
    for rp in sorted((p for p in res_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS), key=_natural_sort_key):
        stem_cf = rp.stem.casefold()
        prev = existing_by_stem.get(stem_cf)
        if prev is not None and not overwrite:
            kept += 1
            continue
        dst = backup_before_dir / rp.name
        _copy_file_atomic(rp, dst)
        if prev is not None and prev != dst and prev.exists():
            try:
                prev.unlink()
            except OSError:
                pass
        existing_by_stem[stem_cf] = dst
        copied += 1
    total = kept + copied
    if copied == 0 and kept > 0:
        print(f"[BACKUP] Keeping existing {kept} before_result snapshot(s) in {backup_before_dir}", flush=True)
    else:
        print(
            f"[BACKUP] Saved {copied} initial-translation page(s) (kept {kept} existing) -> {backup_before_dir}",
            flush=True,
        )
    return total


def cmd_translate_raw(
    chapter_dir: Path,
    backup_before_dir: Path | None = None,
    overwrite: bool = False,
    mtui_root: Path | None = None,
    config_path: Path | None = None,
) -> None:
    """Run headless UI-identical initial translation on raw/missing pages and snapshot `before_result`."""
    _ensure_repo_src_on_path()
    chapter_dir = chapter_dir.resolve()
    if not chapter_dir.is_dir():
        raise FileNotFoundError(f"Chapter directory not found: {chapter_dir}")

    pages = sorted(
        (
            p
            for p in chapter_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in IMAGE_EXTS
            and not p.stem.lower().endswith("-translated")
        ),
        key=_natural_sort_key,
    )
    if not pages:
        raise RuntimeError(f"No image pages found in {chapter_dir}")

    work_dir = chapter_dir / "manga_translator_work"
    res_dir = work_dir / "result"

    if overwrite:
        pending_pages = pages
    else:
        pending_pages = []
        for p in pages:
            has_json, rendered_p = _resolve_page_rendered_status(p)
            if not has_json or rendered_p is None:
                pending_pages.append(p)

    if not pending_pages:
        print(f"[SKIP] All {len(pages)} page(s) in {chapter_dir} already have initial JSON + rendered result.", flush=True)
        if backup_before_dir is not None:
            _backup_result_to_before(res_dir, backup_before_dir.resolve(), overwrite=False, source_pages=pages)
        return

    resolved_root, resolved_py = _discover_mtui_runtime(mtui_root)
    print(
        f"[START] Translating {len(pending_pages)}/{len(pages)} raw page(s) in {chapter_dir} via {resolved_py} (headless offscreen)...",
        flush=True,
    )
    payload = {
        "chapter_dir": str(chapter_dir),
        "files": [str(p) for p in pending_pages],
        "overwrite": bool(overwrite),
        "config_path": str(config_path.resolve()) if config_path else None,
    }
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["QT_QPA_PLATFORM"] = "offscreen"

    runner_fd, runner_tmp = tempfile.mkstemp(prefix="mtui_translate_runner_", suffix=".py")
    os.close(runner_fd)
    payload_fd, payload_tmp = tempfile.mkstemp(prefix="mtui_translate_payload_", suffix=".json")
    os.close(payload_fd)
    try:
        Path(runner_tmp).write_text(_HEADLESS_TRANSLATE_RUNNER, encoding="utf-8")
        Path(payload_tmp).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(
            [str(resolved_py), runner_tmp, str(resolved_root), payload_tmp],
            cwd=str(resolved_root),
            env=env,
        )
    finally:
        for tmp_p in (runner_tmp, payload_tmp):
            try:
                if os.path.exists(tmp_p):
                    os.remove(tmp_p)
            except OSError:
                pass
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)

    if backup_before_dir is not None:
        _backup_result_to_before(res_dir, backup_before_dir.resolve(), overwrite=overwrite, source_pages=pages)


def _load_cjk_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ):
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# 1. Baseline & 5x Contrast LaMa Ghost Audit
# ---------------------------------------------------------------------------


def _region_world_bbox(r: dict) -> tuple[float, float, float, float] | None:
    pts = [pt for poly in (r.get("lines") or []) for pt in poly]
    if not pts:
        return None
    ang = float(r.get("angle", 0.0) or 0.0)
    c = r.get("center")
    if abs(ang) >= 0.5 and c and len(c) >= 2:
        cx, cy = float(c[0]), float(c[1])
        rad = math.radians(ang)
        ca, sa = math.cos(rad), math.sin(rad)
        wpts = [
            (cx + (float(p[0]) - cx) * ca - (float(p[1]) - cy) * sa, cy + (float(p[0]) - cx) * sa + (float(p[1]) - cy) * ca)
            for p in pts
        ]
    else:
        wpts = [(float(p[0]), float(p[1])) for p in pts]
    xs = [p[0] for p in wpts]
    ys = [p[1] for p in wpts]
    return min(xs), min(ys), max(xs), max(ys)


def _classify_page_regions_in_bubble(inp_gray: np.ndarray | None, regs: list[dict]) -> list[bool]:
    """Return True for each region sitting inside an enclosed white speech/burst bubble, False for out-of-bubble/floating text."""
    if inp_gray is None or not regs:
        return [True] * len(regs)
    h, w = inp_gray.shape[:2]
    bright = (inp_gray >= 232).astype(np.uint8)
    _, cc_l, cc_s, _ = cv2.connectedComponentsWithStats(bright, connectivity=4)
    out: list[bool] = []
    for r in regs:
        c = r.get("center")
        if not c:
            wb = _region_world_bbox(r)
            c = [(wb[0] + wb[2]) / 2.0, (wb[1] + wb[3]) / 2.0] if wb else [0.0, 0.0]
        ix = max(0, min(w - 1, int(round(float(c[0])))))
        iy = max(0, min(h - 1, int(round(float(c[1])))))
        # Sample small 9x9 window around center in inpainted base
        patch = inp_gray[max(0, iy - 4) : min(h, iy + 5), max(0, ix - 4) : min(w, ix + 5)]
        if patch.size == 0 or float(np.median(patch)) < 235.0:
            out.append(False)
            continue
        lbl = int(cc_l[iy, ix])
        if lbl == 0:
            out.append(False)
            continue
        x0, y0, bw, bh, area = cc_s[lbl]
        edges = int(x0 <= 2) + int(y0 <= 2) + int(x0 + bw >= w - 2) + int(y0 + bh >= h - 2)
        if area > 0.20 * w * h or edges >= 3 or (bw > 0.42 * w and bh > 0.22 * h):
            out.append(False)
            continue
        out.append(True)
    return out


_NON_MANGA_OR_PROPORTIONAL_SELECTORS = {
    "arial unicode ms",
    "ms gothic",
    "ms pgothic",
    "ms ui gothic",
    "microsoft yahei",
    "microsoft yahei ui",
    "dfpfangyuanw7-gb",
    "dfpzongyiw7-gb",
}


def _scan_local_cjk_whitelist() -> tuple[set[str], list[str]]:
    """Return (casefold_valid_selectors_and_families, ordered_display_selectors) strictly from MTUI_ROOT/fonts/."""
    try:
        _discover_mtui_runtime()
        from mtui_mcp.font_catalog import list_available_fonts

        items = list_available_fonts(include_system=False, cjk_only=True, limit=300)
        valid_cf: set[str] = set()
        ordered: list[str] = []
        for it in items:
            sel = str(it.get("selector") or "").strip()
            fam = str(it.get("font_family") or "").strip()
            if not sel or sel.casefold() in _NON_MANGA_OR_PROPORTIONAL_SELECTORS:
                continue
            valid_cf.add(sel.casefold())
            if fam:
                valid_cf.add(fam.casefold())
            ordered.append(sel)
        return valid_cf, ordered
    except Exception:
        return set(), []


def cmd_audit(chapter_dir: Path, out_dir: Path) -> None:
    _ensure_repo_src_on_path()
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = chapter_dir / "manga_translator_work"
    json_dir = work_dir / "json"
    inp_dir = work_dir / "inpainted"

    pages = sorted(
        (p for p in chapter_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS),
        key=_natural_sort_key,
    )

    tiles: list[Image.Image] = []
    summary_lines: list[str] = []
    valid_local_cf, local_selectors = _scan_local_cjk_whitelist()
    if local_selectors:
        summary_lines.append(
            f"[LOCAL_FONTS_WHITELIST ({len(local_selectors)} selectors strictly in MTUI fonts/)]: "
            + ", ".join(local_selectors)
        )

    for idx, src_p in enumerate(pages, 1):
        jp = json_dir / f"{src_p.stem}_translations.json"
        ip = _find_inpainted_file(inp_dir, src_p)

        if not jp.is_file():
            summary_lines.append(f"P{idx:02d} ({src_p.name}): NO JSON")
            continue

        inp = cv2.imdecode(np.fromfile(str(ip), dtype=np.uint8), cv2.IMREAD_GRAYSCALE) if ip is not None and ip.is_file() else None

        raw = json.loads(jp.read_text(encoding="utf-8"))
        img_data = next(iter(raw.values())) if raw else {}
        regs = img_data.get("regions") or []
        in_bubbles = _classify_page_regions_in_bubble(inp, regs)
        summary_lines.append(f"=== P{idx:02d} ({src_p.name}) regions={len(regs)} ===")
        for r_i, r in enumerate(regs):
            fs = r.get("font_size")
            ff = r.get("font_family")
            ang = float(r.get("angle", 0.0) or 0.0)
            in_b = in_bubbles[r_i]
            ctx_tag = "BUBBLE" if in_b else "OUT-BUBBLE"
            txt = r.get("text", "")
            tr_raw = str(r.get("translation", "")).replace("[BR]", "\n")
            tr = tr_raw.replace("\n", "\\n")
            summary_lines.append(f"  #{r_i} [{ctx_tag}] fs={fs} ang={ang:+.1f}° font={ff!r} src={txt!r} -> {tr!r}")
            if ff and str(ff).strip() and valid_local_cf and str(ff).strip().casefold() not in valid_local_cf:
                summary_lines.append(
                    f"    [NON_LOCAL_FONT ALERT] font_family={ff!r} is NOT an approved CJK manga font in local MTUI fonts/ (or is proportional DFP*)! Replace with a verified local selector!"
                )
            cols = [c for c in tr_raw.split("\n") if c.strip()]
            if ff and "dffangyuan" in str(ff).casefold():
                if len(cols) >= 3 or len("".join(cols)) > 14:
                    summary_lines.append(
                        f"    [FANGYUAN_ABUSE ALERT] DFFangYuanW7-GB used on {len(cols)}-col / {len(''.join(cols))}-char bubble! Re-inspect raw strokes: if serifed -> FZYaSong-DB-GBK; if 90° square bold -> Source Han Sans SC::Bold; if clean round gothic -> FZZhunYuan-M02 / FZLanTingYuan-M-GBK / Resource Han Rounded CN!"
                    )
            d_raw = str(r.get("direction") or "auto").lower()
            if d_raw not in ("h", "horizontal", "hr"):
                bad_v = sorted({ch for ch in tr_raw if ch in "—─━═–―…⋯（）()【】《》〈〉〔〕「」『』〜〰"})
                if bad_v:
                    summary_lines.append(
                        f"    [UNROTATED_VERTICAL_SYMBOL ALERT] Vertical region contains horizontal symbol(s) {''.join(bad_v)!r}! Convert to ││, ︙, ︻︼, ︵︶, ﹁﹂, ﹃﹄, ︽︾, ︿﹀, ︴!"
                    )
            if len(cols) >= 2:
                lens = [len(c) for c in cols]
                if max(lens) >= 7 and (min(lens) / max(lens)) < 0.45:
                    summary_lines.append(
                        f"    [COLUMN_IMBALANCE ALERT] lens={lens} (stub column {min(lens)} vs {max(lens)}) — merge lead-in phrase with comma to balance column heights!"
                    )
            if in_b and abs(ang) >= 1.0:
                summary_lines.append(
                    f"    [SPURIOUS_BUBBLE_TILT ALERT] angle={ang:+.2f}° inside enclosed bubble! Check raw kanji horizontal strokes — if upright in raw (common OBB false tilt on unequal column lengths), MUST reset angle=0.0!"
                )
            elif not in_b:
                summary_lines.append(
                    f"    [OUT_OF_BUBBLE_CHECK] json_angle={ang:+.2f}° (floating/borderless text) — inspect raw_font_grid to match: (1) true slant angle (+deg = '/' top-right/bottom-left, -deg = '\\' top-left/bottom-right, 0° if upright), (2) handwriting/aside font (Xiaolai/Yozai), (3) restrained font_size!"
                )

        from mtui_mcp.project_store import decode_mask_base64

        mask_raw = img_data.get("mask_raw")
        mask = decode_mask_base64(mask_raw, inp.shape[:2]) if (mask_raw and inp is not None) else None
        if inp is not None:
            try:
                _discover_mtui_runtime()
                from mtui_mcp.qa_auditor import _detect_structural_and_border_cv_issues

                src_rgb = np.array(Image.open(src_p).convert("RGB"))
                inp_rgb = np.array(Image.open(ip).convert("RGB"))
                struct_issues = _detect_structural_and_border_cv_issues(src_rgb, inp_rgb, mask, regs)
                for si in struct_issues:
                    summary_lines.append(f"  [{si['category'].upper()} ALERT] r={si.get('region_index')}: {si['message']}")
            except Exception:
                pass

        if inp is None or mask is None or mask.shape != inp.shape:
            continue

        num_c, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8))
        for cid in range(1, num_c):
            x, y, w, h, area = stats[cid]
            if area < 20:
                continue
            comp = labels == cid
            pad = 8
            x1, y1 = max(0, x - pad), max(0, y - pad)
            x2, y2 = min(inp.shape[1], x + w + pad), min(inp.shape[0], y + h + pad)
            sub_comp = comp[y1:y2, x1:x2]
            dil = cv2.dilate(sub_comp.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))) > 0
            ring = dil & (~sub_comp)
            sub_inp = inp[y1:y2, x1:x2]
            if not np.any(ring):
                continue
            if float(np.mean(sub_inp[ring] >= 250)) >= 0.35:
                outside_dark = (~sub_comp) & (sub_inp < 220)
                if np.any(outside_dark):
                    dist_ext = cv2.distanceTransform((~outside_dark).astype(np.uint8), cv2.DIST_L2, 5)
                    safe_interior = sub_comp & (dist_ext >= 3.0)
                else:
                    safe_interior = sub_comp
                non_white = safe_interior & (sub_inp < 248)
                nw_count = int(np.sum(non_white))
                if nw_count >= 10:
                    min_g = int(sub_inp[non_white].min())
                    ys, xs = np.where(non_white)
                    gbox = [int(x1 + xs.min()), int(y1 + ys.min()), int(x1 + xs.max() + 1), int(y1 + ys.max() + 1)]
                    summary_lines.append(
                        f"  [GHOST/BORDER ALERT] ghost_px={nw_count} min_gray={min_g} box={gbox}"
                    )
                    im_o = Image.open(src_p).convert("RGB").crop((x1, y1, x2, y2))
                    im_i = Image.open(ip).convert("RGB").crop((x1, y1, x2, y2))
                    arr = np.array(im_i, dtype=np.int16)
                    im_e = Image.fromarray(np.clip(255 - (255 - arr) * 5, 0, 255).astype(np.uint8))
                    cw, ch = im_o.size
                    row = Image.new("RGB", (cw * 3 + 16, ch + 26), (30, 30, 35))
                    d = ImageDraw.Draw(row)
                    d.text((4, 4), f"P{idx:02d} {src_p.name} min={min_g} box={gbox}", fill=(255, 220, 120))
                    row.paste(im_o, (0, 24))
                    row.paste(im_i, (cw + 8, 24))
                    row.paste(im_e, (cw * 2 + 16, 24))
                    tiles.append(row)

    report_file = out_dir / "baseline_and_ghost_report.txt"
    report_file.write_text("\n".join(summary_lines), encoding="utf-8")
    print(f"Saved audit report to {report_file}")

    for s_idx in range(0, len(tiles), 8):
        chunk = tiles[s_idx : s_idx + 8]
        sheet = Image.new("RGB", (max(t.width for t in chunk), sum(t.height + 6 for t in chunk)), (20, 20, 24))
        cy = 0
        for t in chunk:
            sheet.paste(t, (0, cy))
            cy += t.height + 6
        sp = out_dir / f"ghost_sheet_{s_idx // 8 + 1}.png"
        sheet.save(sp)
        print(f"Saved 5x contrast ghost sheet to {sp}")


# ---------------------------------------------------------------------------
# 2. 1.6x Raw Font Skeleton & Exclamation Shout Audit
# ---------------------------------------------------------------------------


def cmd_raw_fonts(chapter_dir: Path, out_dir: Path) -> None:
    """Extract 1.6x un-annotated native crops of raw text regions to audit font skeletons AND true tilt angles.
    When chapter total regions <= 140 (almost all standard chapters), includes 100% of regions so mid-page
    normal-sized bubbles without '!' (such as 太丸ゴシック, 古印体, 古籍糸柳) are never missed."""
    _ensure_repo_src_on_path()
    try:
        from mtui_mcp.richtext_builder import region_is_bold as _region_is_bold
    except Exception:
        def _region_is_bold(reg: dict) -> bool:
            return bool(reg.get("bold"))

    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = chapter_dir / "manga_translator_work"
    json_dir = work_dir / "json"
    inp_dir = work_dir / "inpainted"
    label_font = _load_cjk_font(14)
    cards: list[Image.Image] = []

    pages = sorted(
        (p for p in chapter_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS),
        key=_natural_sort_key,
    )
    page_records: list[tuple[int, Path, Path, list[dict]]] = []
    total_regions = 0
    for pi, src_p in enumerate(pages, 1):
        jp = json_dir / f"{src_p.stem}_translations.json"
        if not jp.is_file():
            continue
        raw = json.loads(jp.read_text(encoding="utf-8"))
        img_data = next(iter(raw.values())) if raw else {}
        regs = img_data.get("regions") or []
        if not regs:
            continue
        page_records.append((pi, src_p, jp, regs))
        total_regions += len(regs)

    # Strictly 1:1 native pixels (ZERO resize()!) and compact <=920px 2x2 sheets so remote Vision LLMs
    # NEVER suffer from local resampling artifacts or server-side downscaling aliasing!
    full_chapter_coverage = True
    for pi, src_p, _jp, regs in page_records:
        ip = _find_inpainted_file(inp_dir, src_p)
        inp = cv2.imdecode(np.fromfile(str(ip), dtype=np.uint8), cv2.IMREAD_GRAYSCALE) if ip is not None else None

        in_bubbles = _classify_page_regions_in_bubble(inp, regs)
        im = Image.open(src_p).convert("RGB")
        picked = set(range(len(regs)))

        for ri in sorted(picked):
            r = regs[ri]
            wb = _region_world_bbox(r)
            if wb is None:
                continue
            wx1, wy1, wx2, wy2 = wb
            x1, y1 = max(0, int(wx1) - 18), max(0, int(wy1) - 18)
            x2, y2 = min(im.width, int(wx2) + 18), min(im.height, int(wy2) + 18)
            # 100% 1:1 native pixels — NO resize(), NO bottom truncation!
            z = im.crop((x1, y1, x2, y2))
            ang = float(r.get("angle", 0.0) or 0.0)
            in_b = in_bubbles[ri]
            is_shout = any(ch in str(r.get("text", "")) + str(r.get("translation", "")) for ch in ("!", "！"))
            cur_ff = str(r.get("font_family") or "")
            is_bold = _region_is_bold(r)
            tags = [f"{cur_ff}{'+B' if is_bold else ''}"]
            if not in_b:
                tags.append("[OUT]")
            if abs(ang) >= 1.0:
                tags.append(f"[ang={ang:+.1f}°]")
            if is_shout:
                tags.append("[!]")

            if not in_b:
                hdr_color = (135, 220, 255)
            elif abs(ang) >= 1.0:
                hdr_color = (255, 165, 110)
            elif is_shout:
                hdr_color = (140, 245, 175)
            else:
                hdr_color = (255, 225, 130)

            card_w = max(240, z.width + 16)
            card_h = z.height + 34
            card = Image.new("RGB", (card_w, card_h), (32, 34, 40))
            d = ImageDraw.Draw(card)
            d.text((6, 5), f"P{pi:02d} #{ri} fs={r.get('font_size')} {' '.join(tags)}", fill=hdr_color, font=label_font)
            # Draw subtle 0° orthogonal reference ticks along crop frame to make tilt angle vs upright unmistakable
            ox, oy = 8, 28
            d.rectangle([ox - 1, oy - 1, ox + z.width, oy + z.height], outline=(72, 78, 92), width=1)
            for ty in range(oy, oy + z.height, 48):
                d.line([(ox - 4, ty), (ox - 1, ty)], fill=(120, 130, 150), width=1)
            for tx in range(ox, ox + z.width, 48):
                d.line([(tx, oy - 4), (tx, oy - 1)], fill=(120, 130, 150), width=1)
            card.paste(z, (ox, oy))
            cards.append(card)

    per_row = 5 if len(cards) > 32 else 4
    per_sheet = per_row * 4
    for s_i in range(0, len(cards), per_sheet):
        chunk = cards[s_i : s_i + per_sheet]
        grid_rows = [chunk[j : j + per_row] for j in range(0, len(chunk), per_row)]
        W = max(sum(c.width + 8 for c in gr) for gr in grid_rows) + 16
        H = sum(max(c.height for c in gr) + 10 for gr in grid_rows) + 16
        canvas = Image.new("RGB", (W, H), (20, 22, 26))
        cy = 8
        for gr in grid_rows:
            cx = 8
            rh = max(c.height for c in gr)
            for c in gr:
                canvas.paste(c, (cx, cy))
                cx += c.width + 8
            cy += rh + 10
        out_p = out_dir / f"raw_font_grid_{s_i // per_sheet + 1}.png"
        canvas.save(out_p)
        print(f"Saved {out_p} (coverage={'100%-all' if full_chapter_coverage else 'sampled'}, cards={len(chunk)})")


# ---------------------------------------------------------------------------
# 3. Cusp-Bisector Multi-Lobe Split & Closed-Loop Orthogonal Ink Centering
# ---------------------------------------------------------------------------


def detect_all_concave_cusps(sub_mask: np.ndarray, pts_arr: np.ndarray) -> list[tuple[int, np.ndarray, float]]:
    """Detect ALL inward concavity cusps along an external contour (even when multiple cusps share one convex-hull pocket)."""
    n = len(pts_arr)
    sh, sw = sub_mask.shape
    depths = np.zeros(n, dtype=np.float32)
    bisectors = np.zeros((n, 2), dtype=np.float32)

    for k in (10, 18, 28):
        if n <= 2 * k + 5:
            continue
        p_prev = np.roll(pts_arr, k, axis=0).astype(np.float32)
        p_next = np.roll(pts_arr, -k, axis=0).astype(np.float32)
        p_cur = pts_arr.astype(np.float32)

        mid = 0.5 * (p_prev + p_next)
        va = p_prev - p_cur
        vb = p_next - p_cur
        la = np.hypot(va[:, 0], va[:, 1]) + 1e-6
        lb = np.hypot(vb[:, 0], vb[:, 1]) + 1e-6
        v_sum = (va / la[:, None]) + (vb / lb[:, None])
        v_norm = np.hypot(v_sum[:, 0], v_sum[:, 1])

        chord = p_next - p_prev
        clen = np.hypot(chord[:, 0], chord[:, 1]) + 1e-6
        perp_d = np.abs(chord[:, 0] * (p_prev[:, 1] - p_cur[:, 1]) - (p_prev[:, 0] - p_cur[:, 0]) * chord[:, 1]) / clen

        for i in range(n):
            if perp_d[i] < 2.2 or v_norm[i] < 0.25:
                continue
            out_dir = v_sum[i] / v_norm[i]
            in_dir = -out_dir
            ox, oy = int(round(p_cur[i, 0] + 4.0 * out_dir[0])), int(round(p_cur[i, 1] + 4.0 * out_dir[1]))
            ix, iy = int(round(p_cur[i, 0] + 4.0 * in_dir[0])), int(round(p_cur[i, 1] + 4.0 * in_dir[1]))
            mx, my = int(round(mid[i, 0])), int(round(mid[i, 1]))
            out_black = (not (0 <= ox < sw and 0 <= oy < sh)) or (sub_mask[oy, ox] == 0)
            mid_black = (not (0 <= mx < sw and 0 <= my < sh)) or (sub_mask[my, mx] == 0)
            in_white = (0 <= ix < sw and 0 <= iy < sh) and (sub_mask[iy, ix] > 0)
            if out_black and mid_black and in_white and perp_d[i] > depths[i]:
                depths[i] = perp_d[i]
                bisectors[i] = in_dir

    cusps = []
    win = 12
    for i in range(n):
        if depths[i] < 2.5:
            continue
        idxs = [(i + d) % n for d in range(-win, win + 1)]
        if depths[i] >= np.max(depths[idxs]) and (i == min(j for j in idxs if depths[j] == depths[i])):
            cusps.append((i, bisectors[i], float(depths[i])))
    return cusps


def split_multilobe_cusp_bisector(comp_mask: np.ndarray, reg_pts: list[tuple[float, float]]) -> np.ndarray:
    """Split connected multi-lobe speech bubbles along true waist necks guided by cusp inward angle bisectors."""
    ys_all, xs_all = np.nonzero(comp_mask > 0)
    if xs_all.size < 80:
        return comp_mask
    pad_b = 4
    x0, x1 = max(0, int(xs_all.min())), min(comp_mask.shape[1], int(xs_all.max()) + 1)
    y0, y1 = max(0, int(ys_all.min())), min(comp_mask.shape[0], int(ys_all.max()) + 1)

    sub_raw = np.pad(comp_mask[y0:y1, x0:x1], pad_b, mode="constant", constant_values=0)
    local_pts = [(cx - x0 + pad_b, cy - y0 + pad_b) for cx, cy in reg_pts]
    sh, sw = sub_raw.shape

    # Strip outward triangular speech pointer tails FIRST so pointer armpits are never mistaken for waist cusps
    kern_tail = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    sub = cv2.morphologyEx(sub_raw, cv2.MORPH_OPEN, kern_tail)
    if any(sub[max(0, min(sh - 1, int(round(cy)))), max(0, min(sw - 1, int(round(cx))))] == 0 for cx, cy in local_pts):
        sub = sub_raw.copy()

    contours, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return comp_mask
    cnt = max(contours, key=cv2.contourArea)
    n_cnt = len(cnt)
    if n_cnt < 40:
        return comp_mask

    pts_arr = cnt[:, 0, :]
    cusps = detect_all_concave_cusps(sub, pts_arr)
    if not cusps:
        return comp_mask

    cusp_idxs = np.array([c[0] for c in cusps], dtype=np.int32)
    seeds_arr = np.array(local_pts, dtype=np.float32)
    target_idxs = np.unique(np.concatenate([np.arange(0, n_cnt, 3), cusp_idxs]))

    cands = []
    for f_idx, in_bis, _ in cusps:
        p1 = pts_arr[f_idx].astype(np.float32)
        for j_idx in target_idxs:
            j_idx = int(j_idx)
            if min(abs(f_idx - j_idx), n_cnt - abs(f_idx - j_idx)) < 35:
                continue
            p2 = pts_arr[j_idx].astype(np.float32)
            dv = p2 - p1
            clen = float(np.hypot(dv[0], dv[1]))
            if clen < 14.0 or clen > 295.0:
                continue
            d_unit = dv / clen
            dot1 = float(d_unit[0] * in_bis[0] + d_unit[1] * in_bis[1])
            if dot1 < 0.52:
                continue

            rel = seeds_arr - p1[None, :]
            cross = dv[0] * rel[:, 1] - dv[1] * rel[:, 0]
            if not (np.any(cross > 0) and np.any(cross < 0)):
                continue
            t_proj = np.clip((rel[:, 0] * dv[0] + rel[:, 1] * dv[1]) / (clen * clen), 0.0, 1.0)
            proj = p1[None, :] + t_proj[:, None] * dv[None, :]
            if float(np.hypot(seeds_arr[:, 0] - proj[:, 0], seeds_arr[:, 1] - proj[:, 1]).min()) < 18.0:
                continue

            n_samp = int(clen) + 1
            xs = np.linspace(p1[0], p2[0], n_samp).astype(int)
            ys = np.linspace(p1[1], p2[1], n_samp).astype(int)
            if n_samp > 8 and float(np.mean(sub[ys[3:-3], xs[3:-3]] > 0)) < 0.92:
                continue

            best_opp_bonus = 1.0
            for c2_idx, bis2, _ in cusps:
                if c2_idx != f_idx and min(abs(j_idx - c2_idx), n_cnt - abs(j_idx - c2_idx)) <= 10:
                    if float((-d_unit[0]) * bis2[0] + (-d_unit[1]) * bis2[1]) >= 0.40:
                        best_opp_bonus = min(best_opp_bonus, 0.58)

            score = (clen / (dot1 ** 1.6)) * best_opp_bonus
            cands.append((score, (int(round(p1[0])), int(round(p1[1]))), (int(round(p2[0])), int(round(p2[1])))))

    cands.sort(key=lambda x: x[0])
    work = sub_raw.copy()

    def seed_labels(mask_img: np.ndarray) -> list[int]:
        _, l_img = cv2.connectedComponents(mask_img, connectivity=4)
        return [
            int(l_img[max(0, min(sh - 1, int(round(cy)))), max(0, min(sw - 1, int(round(cx))))])
            for cx, cy in local_pts
        ]

    for _, p1, p2 in cands:
        cur_s = seed_labels(work)
        prev_k = len(set(cur_s) - {0})
        if prev_k >= len(local_pts):
            break
        dv = np.array([p2[0] - p1[0], p2[1] - p1[1]], dtype=np.float32)
        L = float(np.hypot(dv[0], dv[1])) + 1e-6
        q1 = (int(round(p1[0] - 2.5 * dv[0] / L)), int(round(p1[1] - 2.5 * dv[1] / L)))
        q2 = (int(round(p2[0] + 2.5 * dv[0] / L)), int(round(p2[1] + 2.5 * dv[1] / L)))

        thin = work.copy()
        cv2.line(thin, q1, q2, 0, thickness=4)
        new_s = seed_labels(thin)
        new_k = len(set(new_s) - {0})
        if 0 not in new_s and new_k > prev_k:
            buf = work.copy()
            cv2.line(buf, q1, q2, 0, thickness=8)
            buf_s = seed_labels(buf)
            work = buf if (0 not in buf_s and len(set(buf_s) - {0}) == new_k) else thin

    out = comp_mask.copy()
    out[y0:y1, x0:x1] = work[pad_b : sh - pad_b, pad_b : sw - pad_b]
    return out


def strip_pointer_tails(lobe_mask: np.ndarray) -> np.ndarray:
    """Amputate narrow outward speech pointer tails while keeping flat walls, curved arcs, and inward spikes."""
    ys_all, xs_all = np.nonzero(lobe_mask > 0)
    if xs_all.size < 100:
        return lobe_mask
    pad_b = 3
    x0, x1 = max(0, int(xs_all.min())), min(lobe_mask.shape[1], int(xs_all.max()) + 1)
    y0, y1 = max(0, int(ys_all.min())), min(lobe_mask.shape[0], int(ys_all.max()) + 1)
    sub = np.pad(lobe_mask[y0:y1, x0:x1], pad_b, mode="constant", constant_values=0)
    dist = cv2.distanceTransform(sub, cv2.DIST_L2, 5)
    max_d = float(dist.max())
    if max_d < 12.0:
        return lobe_mask
    r = max(4, min(14, int(round(0.22 * max_d))))
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    opened = cv2.morphologyEx(sub, cv2.MORPH_OPEN, kern)
    if int(opened.sum()) < 150:
        return lobe_mask
    num_b, b_l, b_s, _ = cv2.connectedComponentsWithStats(opened, connectivity=4)
    if num_b > 1:
        best = 1 + int(np.argmax(b_s[1:, cv2.CC_STAT_AREA]))
        opened = (b_l == best).astype(np.uint8)
    out = np.zeros_like(lobe_mask)
    sh, sw = sub.shape
    out[y0:y1, x0:x1] = opened[pad_b : sh - pad_b, pad_b : sw - pad_b]
    return out


def _run_span_1d(arr_1d: np.ndarray, ref_idx: int) -> tuple[int, int] | None:
    padded = np.concatenate(([0], arr_1d, [0]))
    diffs = np.diff(padded)
    starts = np.nonzero(diffs == 1)[0]
    ends = np.nonzero(diffs == -1)[0] - 1
    if starts.size == 0:
        return None
    for s, e in zip(starts, ends):
        if s <= ref_idx <= e and (e - s) >= 6:
            return int(s), int(e)
    best = int(np.argmax(ends - starts))
    if (ends[best] - starts[best]) >= 6:
        return int(starts[best]), int(ends[best])
    return None


def _safe_clamp(val: float, min_neg_room: float, min_pos_room: float, floor_px: float) -> float:
    lo = -(min_neg_room - floor_px)
    hi = +(min_pos_room - floor_px)
    if lo > hi:
        return 0.5 * (min_pos_room - min_neg_room)
    return float(min(max(val, lo), hi))


def fill_small_interior_holes(mask: np.ndarray, max_hole_area: int = 500) -> np.ndarray:
    """Fill tiny interior dark specks/pinholes inside a white bubble BEFORE morphological opening
    so a 1px speck never gets enlarged into a 30px crater by MORPH_OPEN."""
    inv = (mask == 0).astype(np.uint8)
    num_l, lbls, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    if num_l <= 2:
        return mask
    out = mask.copy()
    h, w = mask.shape
    for cid in range(1, num_l):
        x0, y0, bw, bh, area = stats[cid]
        if area <= max_hole_area and x0 > 0 and y0 > 0 and (x0 + bw) < w and (y0 + bh) < h:
            out[lbls == cid] = 1
    return out


def gestalt_complete_lobe(lobe_mask: np.ndarray, real_comp_mask: np.ndarray) -> np.ndarray:
    """Reconstruct each multi-lobe bubble's complete underlying oval via `cv2.fitEllipse` on its unclipped outer curved arc
    (`(E_k ∪ bm_k) ∩ real_comp_mask`), restoring the cap sliced off by straight waist-split chords."""
    clean_lobe = fill_small_interior_holes(lobe_mask)
    clean_real = fill_small_interior_holes(real_comp_mask)
    bm = strip_pointer_tails(clean_lobe)
    if np.array_equal(lobe_mask, real_comp_mask):
        return bm

    contours, _ = cv2.findContours(bm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return bm
    cnt = max(contours, key=cv2.contourArea)[:, 0, :]
    dist_to_real_wall = cv2.distanceTransform(clean_real, cv2.DIST_L2, 3)

    ys_r, xs_r = np.nonzero(clean_real > 0)
    rx0, rx1, ry0, ry1 = int(xs_r.min()), int(xs_r.max()), int(ys_r.min()), int(ys_r.max())

    arc_pts = []
    for x, y in cnt:
        if dist_to_real_wall[y, x] > 4.5:
            continue
        if (abs(x - rx0) <= 3 and np.sum(clean_real[:, rx0 : rx0 + 4]) > 45) or \
           (abs(x - rx1) <= 3 and np.sum(clean_real[:, max(0, rx1 - 3) : rx1 + 1]) > 45) or \
           (abs(y - ry0) <= 3 and np.sum(clean_real[ry0 : ry0 + 4, :]) > 45) or \
           (abs(y - ry1) <= 3 and np.sum(clean_real[max(0, ry1 - 3) : ry1 + 1, :]) > 45):
            continue
        arc_pts.append([float(x), float(y)])

    if len(arc_pts) < 25:
        return bm
    try:
        (ecx, ecy), (ma, MA), angle = cv2.fitEllipse(np.array(arc_pts, dtype=np.float32))
    except cv2.error:
        return bm
    ys_b, xs_b = np.nonzero(bm > 0)
    bw, bh = float(xs_b.max() - xs_b.min()), float(ys_b.max() - ys_b.min())
    if ma <= 10 or MA <= 10 or MA > 2.2 * max(bw, bh) or ma > 1.8 * max(bw, bh):
        return bm
    ell_mask = np.zeros_like(bm)
    cv2.ellipse(ell_mask, ((ecx, ecy), (ma, MA), angle), 1, thickness=-1)
    dist_from_bm = cv2.distanceTransform((bm == 0).astype(np.uint8), cv2.DIST_L2, 5)
    completed = ((bm > 0) | ((ell_mask > 0) & (dist_from_bm <= 55.0))).astype(np.uint8) & clean_real
    return strip_pointer_tails(completed)


def compute_orthogonal_equal_margin_center(
    raw_lobe_mask: np.ndarray,
    real_comp_mask: np.ndarray,
    hard_safe_mask: np.ndarray,
    ink_mask: np.ndarray,
    cur_cx: float,
    cur_cy: float,
    fs: float = 26.0,
    has_trailing_light_punct: bool = False,
) -> tuple[float, float] | None:
    """Gestalt Oval Completion + Full-Ink P2 Rectangular-Corner 5-Iteration 2D Fixed-Point Centering."""
    gm = gestalt_complete_lobe(raw_lobe_mask, real_comp_mask)
    hs_clean = fill_small_interior_holes(hard_safe_mask, max_hole_area=120)
    iys0, ixs0 = np.nonzero(ink_mask > 0)
    if ixs0.size < 15:
        return None

    h, w = gm.shape
    iy_min0, iy_max0 = int(iys0.min()), int(iys0.max())
    ix_min0, ix_max0 = int(ixs0.min()), int(ixs0.max())

    dx, dy = 0.0, 0.0
    for _ in range(5):
        sx_min = max(0, min(w - 1, int(round(ix_min0 + dx))))
        sx_max = max(0, min(w - 1, int(round(ix_max0 + dx))))
        sy_min = max(0, min(h - 1, int(round(iy_min0 + dy))))
        sy_max = max(0, min(h - 1, int(round(iy_max0 + dy))))

        ref_x = int(round(0.5 * (sx_min + sx_max)))
        pls, prs = [], []
        for y_row in range(sy_min, sy_max + 1):
            sp = _run_span_1d(gm[y_row, :], ref_x)
            if sp is not None:
                pls.append(float(sx_min - sp[0]))
                prs.append(float(sp[1] - sx_max))
        if len(pls) >= 4:
            eff_L = 0.88 * float(np.percentile(pls, 2)) + 0.12 * float(np.median(pls))
            eff_R = 0.88 * float(np.percentile(prs, 2)) + 0.12 * float(np.median(prs))
            dx += 0.5 * (eff_R - eff_L)

        h_pl, h_pr = [], []
        for y_row in range(sy_min, sy_max + 1):
            sp = _run_span_1d(hs_clean[y_row, :], ref_x)
            if sp is not None:
                h_pl.append(float((ix_min0 + dx) - sp[0]))
                h_pr.append(float(sp[1] - (ix_max0 + dx)))
        if h_pl and h_pr:
            dx += _safe_clamp(0.0, float(np.percentile(h_pl, 2)), float(np.percentile(h_pr, 2)), 14.0)

        sx_min = max(0, min(w - 1, int(round(ix_min0 + dx))))
        sx_max = max(0, min(w - 1, int(round(ix_max0 + dx))))

        ref_y = int(round(0.5 * (sy_min + sy_max)))
        pts_t, pts_b = [], []
        for x_col in range(sx_min, sx_max + 1):
            sp = _run_span_1d(gm[:, x_col], ref_y)
            if sp is not None:
                pts_t.append(float(sy_min - sp[0]))
                pts_b.append(float(sp[1] - sy_max))
        if len(pts_t) >= 4:
            eff_T = 0.55 * float(np.percentile(pts_t, 10)) + 0.45 * float(np.median(pts_t))
            eff_B = 0.55 * float(np.percentile(pts_b, 10)) + 0.45 * float(np.median(pts_b))
            dy += 0.5 * (eff_B - eff_T)

        h_pt, h_pb = [], []
        for x_col in range(sx_min, sx_max + 1):
            sp = _run_span_1d(hs_clean[:, x_col], ref_y)
            if sp is not None:
                h_pt.append(float((iy_min0 + dy) - sp[0]))
                h_pb.append(float(sp[1] - (iy_max0 + dy)))
        if h_pt and h_pb:
            dy += _safe_clamp(0.0, float(np.percentile(h_pt, 2)), float(np.percentile(h_pb, 2)), 16.0)

    return round(cur_cx + dx, 1), round(cur_cy + dy, 1)


def _estimate_empty_lobe_center(raw_lobe_mask: np.ndarray, reg: dict) -> tuple[float, float] | None:
    """Pre-render fallback when rendered result ink is unavailable: uses synthetic text block dimensions."""
    body_mask = strip_pointer_tails(raw_lobe_mask)
    ys_all, xs_all = np.nonzero(body_mask > 0)
    if xs_all.size < 150:
        return None
    h, w = body_mask.shape
    cx, cy = float(np.median(xs_all)), float(np.median(ys_all))

    tr = str(reg.get("translation", "")).replace("[BR]", "\n")
    cols = [c for c in tr.split("\n") if c.strip()] or ["汉"]
    fs = float(reg.get("font_size") or 26)
    est_w = max(fs, len(cols) * fs * 1.05)
    est_h = max(fs * 1.5, max(len(c) for c in cols) * fs * 1.0)

    for _ in range(2):
        x1 = max(0, int(round(cx - 0.5 * est_w)))
        x2 = min(w - 1, int(round(cx + 0.5 * est_w)))
        pts_t, pts_b = [], []
        for x in range(x1, x2 + 1):
            sp = _run_span_1d(body_mask[:, x], int(round(cy)))
            if sp:
                pts_t.append((cy - 0.5 * est_h) - sp[0])
                pts_b.append(sp[1] - (cy + 0.5 * est_h))
        if len(pts_t) >= 3:
            et = 0.65 * float(np.percentile(pts_t, 15)) + 0.35 * float(np.median(pts_t))
            eb = 0.65 * float(np.percentile(pts_b, 15)) + 0.35 * float(np.median(pts_b))
            cy += 0.5 * (eb - et)

        y1 = max(0, int(round(cy - 0.5 * est_h)))
        y2 = min(h - 1, int(round(cy + 0.5 * est_h)))
        pls, prs = [], []
        for y in range(y1, y2 + 1):
            sp = _run_span_1d(body_mask[y, :], int(round(cx)))
            if sp:
                pls.append((cx - 0.5 * est_w) - sp[0])
                prs.append(sp[1] - (cx + 0.5 * est_w))
        if len(pls) >= 3:
            el = 0.70 * float(np.percentile(pls, 12)) + 0.30 * float(np.median(pls))
            er = 0.70 * float(np.percentile(prs, 12)) + 0.30 * float(np.median(prs))
            cx += 0.5 * (er - el)

    return round(cx, 1), round(cy, 1)


def _is_perspective_quad(val: object) -> bool:
    """Return True if `val` is a 4-point `[[x, y], ...]` quad (`white_frame_quad_local` / `render_box_quad_local`)."""
    if not isinstance(val, (list, tuple)) or len(val) != 4:
        return False
    for pt in val:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            return False
        try:
            float(pt[0])
            float(pt[1])
        except (TypeError, ValueError):
            return False
    return True


def _perspective_quad_world_bbox(r: dict) -> tuple[float, float, float, float] | None:
    """Project 4-point local perspective quad (`white_frame_quad_local` / `render_box_quad_local`) to world AABB,
    matching `EditorControllerExportService._perspective_quad_world` (`controller_export_service.py:440-474`)."""
    if not isinstance(r, dict):
        return None
    quad = None
    if r.get("has_custom_white_frame") and _is_perspective_quad(r.get("white_frame_quad_local")):
        quad = r.get("white_frame_quad_local")
    elif _is_perspective_quad(r.get("render_box_quad_local")):
        quad = r.get("render_box_quad_local")
    elif _is_perspective_quad(r.get("white_frame_quad_local")):
        quad = r.get("white_frame_quad_local")
    if quad is None:
        return None

    c = r.get("center")
    if isinstance(c, (list, tuple)) and len(c) >= 2:
        cx, cy = float(c[0]), float(c[1])
    else:
        wb = _region_world_bbox(r)
        if wb is None:
            return None
        cx, cy = 0.5 * (wb[0] + wb[2]), 0.5 * (wb[1] + wb[3])

    ang = float(r.get("angle", 0.0) or 0.0)
    rad = math.radians(ang)
    ca, sa = math.cos(rad), math.sin(rad)
    wxs = [cx + float(p[0]) * ca - float(p[1]) * sa for p in quad[:4]]
    wys = [cy + float(p[0]) * sa + float(p[1]) * ca for p in quad[:4]]
    return min(wxs), min(wys), max(wxs), max(wys)


def partition_page_white_bubbles(inp_gray: np.ndarray, regs: list[dict]) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Return `{region_index: (lobe_mask, real_unsplit_bubble_mask)}` for all white-bubble vertical regions on a page."""
    h, w = inp_gray.shape
    sealed = cv2.dilate((inp_gray < 225).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1)
    _, cc_l, cc_s, _ = cv2.connectedComponentsWithStats((sealed == 0).astype(np.uint8), connectivity=4)

    cc_map: dict[int, list[int]] = {}
    rc_map: dict[int, tuple[float, float]] = {}
    for i, r in enumerate(regs):
        if str(r.get("direction", "")).lower().startswith("h") or not r.get("center"):
            continue
        # Skip intentionally tilted regions (|angle| >= 3.0°) or 4-point perspective-quad regions
        if abs(float(r.get("angle", 0.0) or 0.0)) >= 3.0:
            continue
        if _is_perspective_quad(r.get("white_frame_quad_local")) or _is_perspective_quad(r.get("render_box_quad_local")):
            continue
        cx, cy = float(r["center"][0]), float(r["center"][1])
        ix, iy = max(0, min(w - 1, int(round(cx)))), max(0, min(h - 1, int(round(cy))))
        if inp_gray[iy, ix] < 235:
            continue
        lbl = int(cc_l[iy, ix])
        if lbl == 0:
            continue
        x0, y0, bw, bh, area = cc_s[lbl]
        edges = int(x0 <= 2) + int(y0 <= 2) + int(x0 + bw >= w - 2) + int(y0 + bh >= h - 2)
        if area <= 0.22 * w * h and edges < 3:
            rc_map[i] = (cx, cy)
            cc_map.setdefault(lbl, []).append(i)

    lobe_by_ri: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for lbl, ridxs in cc_map.items():
        cm = (cc_l == lbl).astype(np.uint8)
        if len(ridxs) == 1:
            lobe_by_ri[ridxs[0]] = (cm, cm)
        else:
            sm = split_multilobe_cusp_bisector(cm, [rc_map[ri] for ri in ridxs])
            _, sl = cv2.connectedComponents(sm, connectivity=4)
            groups: dict[int, list[int]] = {}
            for ri in ridxs:
                cx, cy = rc_map[ri]
                k = int(sl[int(round(cy)), int(round(cx))])
                if k > 0:
                    groups.setdefault(k, []).append(ri)
            for k, sub in groups.items():
                if len(sub) == 1:
                    lobe_by_ri[sub[0]] = ((sl == k).astype(np.uint8), cm)
    return lobe_by_ri


def build_region_hard_safe_mask(real_comp_mask: np.ndarray, excl_inks: dict[int, np.ndarray], ri: int, gap_px: int = 14) -> np.ndarray:
    other = np.zeros_like(real_comp_mask)
    for k, m in excl_inks.items():
        if k != ri:
            other |= m
    if not np.any(other):
        return real_comp_mask
    ys, xs = np.nonzero(other > 0)
    pad = gap_px + 2
    x0, x1 = max(0, int(xs.min()) - pad), min(real_comp_mask.shape[1], int(xs.max()) + pad + 1)
    y0, y1 = max(0, int(ys.min()) - pad), min(real_comp_mask.shape[0], int(ys.max()) + pad + 1)
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gap_px + 1, 2 * gap_px + 1))
    dil = cv2.dilate(other[y0:y1, x0:x1], kern)
    safe = real_comp_mask.copy()
    safe[y0:y1, x0:x1] = safe[y0:y1, x0:x1] & (dil == 0).astype(np.uint8)
    return safe


def assign_exclusive_page_ink_masks(ink_full: np.ndarray, regs: list[dict]) -> dict[int, np.ndarray]:
    """Partition `ink_full` mutually exclusively among regions using each region's layout box around `center`."""
    ys, xs = np.nonzero(ink_full > 0)
    if xs.size == 0 or not regs:
        return {}
    boxes = []
    valid_ris = []
    for i, r in enumerate(regs):
        if not r.get("center"):
            continue
        cx, cy = float(r["center"][0]), float(r["center"][1])
        tr = str(r.get("translation", "")).replace("[BR]", "\n")
        cols = [c for c in tr.split("\n") if c] or ["汉"]
        fs = float(r.get("font_size") or 26)
        ls = float(r.get("line_spacing") or 1.02)
        if str(r.get("direction", "")).lower().startswith("h"):
            hw = 0.5 * (max(len(c) for c in cols) * fs * 1.05) + 0.65 * fs
            hh = 0.5 * (len(cols) * fs * ls) + 0.55 * fs
        else:
            hw = 0.5 * (len(cols) * fs * ls) + 0.55 * fs
            hh = 0.5 * (max(len(c) for c in cols) * fs * 1.04) + 0.65 * fs
        boxes.append((cx, cy, max(16.0, hw), max(16.0, hh)))
        valid_ris.append(i)
    if not boxes:
        return {}
    b_arr = np.array(boxes, dtype=np.float32)
    px, py = xs.astype(np.float32)[:, None], ys.astype(np.float32)[:, None]
    norm_cheb = np.maximum(np.abs(px - b_arr[None, :, 0]) / b_arr[None, :, 2], np.abs(py - b_arr[None, :, 1]) / b_arr[None, :, 3])
    best_k = np.argmin(norm_cheb, axis=1)
    best_val = norm_cheb[np.arange(xs.size), best_k]
    out: dict[int, np.ndarray] = {}
    for k_idx, ri in enumerate(valid_ris):
        sel = (best_k == k_idx) & (best_val <= 1.18)
        if np.any(sel):
            m = np.zeros_like(ink_full)
            m[ys[sel], xs[sel]] = 1
            out[ri] = m
    return out


def _find_inpainted_file(inp_dir: Path, src_p: Path) -> Path | None:
    if not inp_dir.is_dir():
        return None
    cand0 = inp_dir / f"{src_p.stem}_inpainted{src_p.suffix}"
    if cand0.is_file():
        return cand0
    for ext in _ORDERED_PROBE_EXTS:
        if ext in IMAGE_EXTS:
            cand = inp_dir / f"{src_p.stem}_inpainted{ext}"
            if cand.is_file():
                return cand
    for ext in sorted(IMAGE_EXTS - set(_ORDERED_PROBE_EXTS)):
        cand = inp_dir / f"{src_p.stem}_inpainted{ext}"
        if cand.is_file():
            return cand
    return None


def _apply_closed_loop_direct(chapter_dir: Path, passes: int = 2) -> dict[str, list[dict]]:
    """Run closed-loop optical centering directly via `mtui_mcp` and shut down the temporary CLI worker cleanly in `finally:`."""
    _ensure_repo_src_on_path()
    from mtui_mcp.bubble_centering import run_closed_loop_bubble_centering
    from mtui_mcp.server import _render_single_page_via_worker
    from mtui_mcp.worker.client import get_worker_client

    try:
        return run_closed_loop_bubble_centering(
            chapter_dir,
            passes=passes,
            render_page_fn=_render_single_page_via_worker,
        )
    finally:
        get_worker_client().shutdown()


def cmd_center_bubbles(chapter_dir: Path, out_json: Path | None, apply_mcp: bool = False, passes: int = 2) -> None:
    """Compute (and optionally closed-loop apply + re-render) orthogonal ink-clearance centers for single & multi-lobe white speech bubbles."""
    _ensure_repo_src_on_path()
    from mtui_mcp.bubble_centering import compute_chapter_optical_centers

    if apply_mcp:
        report = _apply_closed_loop_direct(chapter_dir, passes=passes)
        print(f"Applied {passes}-pass closed-loop optical centering across {len(report)} pages.")
    else:
        report = compute_chapter_optical_centers(chapter_dir)

    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved optical centers for {len(report)} pages -> {out_json}")


# ---------------------------------------------------------------------------
# 4. Bilingual Script & Raw-Crop Mistranslation Auditor (`script-audit`)
# ---------------------------------------------------------------------------


def cmd_script_audit(chapter_dir: Path, out_dir: Path, glossary_file: Path | None = None) -> dict:
    """Export full `P001 -> P_end` RTL bilingual script ledger + 1.6x Raw crops for all OCR-suspect / mistranslation-risk bubbles."""
    _ensure_repo_src_on_path()
    from mtui_mcp.path_resolver import list_manga_pages_in_path
    from mtui_mcp.project_store import (
        _reading_order_sort_key,
        format_region_summary,
        load_page_json,
        region_bbox_xyxy,
    )
    from mtui_mcp.translation_auditor import (
        audit_region_translation_accuracy,
        load_active_glossary_terms,
    )

    chapter_dir = chapter_dir.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    crops_dir = out_dir / "suspect_ocr_raw_crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    glossary_terms = load_active_glossary_terms(glossary_file)
    pages = list_manga_pages_in_path(chapter_dir)
    label_font = _load_cjk_font(18)

    md_lines: list[str] = [
        f"# Bilingual Translation & Mistranslation Audit — `{chapter_dir.name}`",
        "",
        f"- **Pages**: {len(pages)}",
        f"- **Loaded Glossary Terms**: {len(glossary_terms)}",
        "",
    ]

    kata_occurrences: dict[str, list[tuple[str, int, str]]] = {}
    kata_re = re.compile(r"[\u30a0-\u30ffー]{4,}")
    suspect_cards: list[Image.Image] = []
    total_regions = 0
    flagged_regions = 0

    page_sections: list[str] = []
    for p in pages:
        if not p.json_exists:
            continue
        _, pdata = load_page_json(p)
        raw_regs = pdata.get("regions", [])
        if not raw_regs:
            continue
        raw_pil = Image.open(p.display_base_path).convert("RGB") if p.display_base_path.is_file() else None
        img_w, img_h = raw_pil.size if raw_pil else (1600, 2200)

        indexed_summaries = [(idx, reg, format_region_summary(idx, reg)) for idx, reg in enumerate(raw_regs)]
        indexed_summaries.sort(key=lambda item: _reading_order_sort_key(item[2], img_w, rtl=True))

        p_lines = [f"## `{p.source_path.name}` ({len(indexed_summaries)} regions)", ""]
        for idx, reg, summ in indexed_summaries:
            total_regions += 1
            src = summ["source_text"].replace("\n", " ").strip()
            vis = summ["translation"].replace("\n", " / ").strip()
            prob = float(summ["ocr_prob"])

            for km in kata_re.finditer(src):
                kt = km.group(0)
                kata_occurrences.setdefault(kt, []).append((p.source_path.name, idx, vis))

            acc_issues = audit_region_translation_accuracy(idx, reg, glossary_terms=glossary_terms)
            low_ocr = bool(src and prob < 0.78)
            tags = [iss["category"] for iss in acc_issues]
            if low_ocr and "low_ocr_prob" not in tags:
                tags.append(f"low_ocr={prob:.2f}")

            flag_prefix = f"⚠️ **[{', '.join(tags)}]** " if tags else ""
            p_lines.append(f"- `#{idx:02d}` (ocr={prob:.2f}) {flag_prefix}**JP**: `{src}`  ==>  **ZH**: `{vis}`")
            for iss in acc_issues:
                p_lines.append(f"  - ↳ `{iss['severity'].upper()}`: {iss['message']}")

            if (acc_issues or low_ocr) and raw_pil is not None:
                flagged_regions += 1
                x1, y1, x2, y2 = region_bbox_xyxy(reg)
                pad_x, pad_y = 28, 28
                cx1 = max(0, int(math.floor(x1)) - pad_x)
                cy1 = max(0, int(math.floor(y1)) - pad_y)
                cx2 = min(img_w, int(math.ceil(x2)) + pad_x)
                cy2 = min(img_h, int(math.ceil(y2)) + pad_y)
                if cx2 > cx1 + 8 and cy2 > cy1 + 8:
                    crop = raw_pil.crop((cx1, cy1, cx2, cy2))
                    nw = max(120, min(900, int(round(crop.width * 1.6))))
                    nh = max(80, min(900, int(round(crop.height * 1.6))))
                    crop_up = crop.resize((nw, nh), Image.Resampling.LANCZOS)
                    card_w = max(nw + 20, 520)
                    hdr_h = 62
                    card = Image.new("RGB", (card_w, nh + hdr_h + 12), (28, 30, 36))
                    dr = ImageDraw.Draw(card)
                    dr.text(
                        (10, 6),
                        f"{p.source_path.name} #{idx} (ocr={prob:.2f}) [{'|'.join(tags[:2])}]",
                        fill=(255, 215, 110),
                        font=label_font,
                    )
                    dr.text((10, 32), f"ZH: {vis[:34]}", fill=(185, 235, 200), font=label_font)
                    card.paste(crop_up, (10, hdr_h))
                    suspect_cards.append(card)
        p_lines.append("")
        page_sections.extend(p_lines)

    # Group <= 4 suspect Raw crops per sheet so each sheet has high duty cycle and zero thumbnail downscaling!
    sheet_paths: list[str] = []
    for s_idx in range(0, len(suspect_cards), 4):
        batch = suspect_cards[s_idx : s_idx + 4]
        sw = max(c.width for c in batch) + 24
        sh = sum(c.height + 12 for c in batch) + 12
        sheet = Image.new("RGB", (sw, sh), (18, 20, 24))
        cy_off = 12
        for c in batch:
            sheet.paste(c, (12, cy_off))
            cy_off += c.height + 12
        sp = crops_dir / f"suspect_raw_{s_idx // 4 + 1:03d}.png"
        sheet.save(sp, compress_level=3)
        sheet_paths.append(str(sp))

    # Recurring katakana proper-noun cross-page table (catches intra-chapter transliteration drift even without glossary)
    recurring = {k: v for k, v in sorted(kata_occurrences.items()) if len(v) >= 2}
    if recurring:
        md_lines.extend(["## Recurring Katakana Terms Cross-Check (Verify Intra-Chapter Consistency)", ""])
        for kt, hits in list(recurring.items())[:80]:
            sample_str = " ; ".join(f"`{pg}#{ri}`→{zh}" for pg, ri, zh in hits[:5])
            md_lines.append(f"- **`{kt}`** ({len(hits)}x): {sample_str}")
        md_lines.append("")

    md_lines.append(f"- **Total Regions Audited**: {total_regions}")
    md_lines.append(f"- **Flagged Suspect / Low-OCR Regions**: {flagged_regions} ({len(sheet_paths)} Raw crop sheets in `{crops_dir}`)")
    md_lines.append("")
    md_lines.extend(page_sections)

    report_md = out_dir / "script_bilingual_audit.md"
    report_md.write_text("\n".join(md_lines), encoding="utf-8")
    summary = {
        "total_regions": total_regions,
        "flagged_suspect_regions": flagged_regions,
        "raw_crop_sheets": len(sheet_paths),
        "report_markdown": str(report_md),
        "crops_dir": str(crops_dir),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


# ---------------------------------------------------------------------------
# 4b. Mandatory Post-Proofreading [Raw Bubble | Rendered Bubble] Self-Check Crops
# ---------------------------------------------------------------------------


_CORE_VERIFIED_KAISHO_KEYS: tuple[str, ...] = (
    "kaishomcbk",
    "特太楷書",
    "gfkaisho",
    "太行書体",
)


def _call_recommend_fn_compat(recommend_fn, ym_pred: dict, **extra_kwargs):
    """Invoke `recommend_fn(ym_pred, ...)` while keeping 1-arg mock lambdas in unit tests compatible."""
    try:
        sig = inspect.signature(recommend_fn)
        params = sig.parameters
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return recommend_fn(ym_pred, **extra_kwargs)
        filtered = {k: v for k, v in extra_kwargs.items() if k in params}
        if filtered:
            return recommend_fn(ym_pred, **filtered)
    except (TypeError, ValueError):
        pass
    return recommend_fn(ym_pred)


def _format_verify_crop_ym_line(
    ym_pred: dict | None,
    *,
    ff: str,
    shw: float,
    reg_hw: float,
    recommend_fn,
    classify_fn,
    stem_hw: float | None = None,
    bbox_wh: tuple[float, float] | None = None,
    source_text: str | None = None,
    page_wh: tuple[int, int] | None = None,
) -> tuple[str, tuple[int, int, int], bool]:
    """Build the YM4x diagnostic line for a `verify-crops` card header.

    Returns `(ym_line, ym_color_rgb, has_ym_hi_mismatch)`.
    """
    sw_str = f"raw_sw={2.0 * shw:.1f}px (reg≈{2.0 * reg_hw:.1f}px)" if shw > 0 else f"raw_sw=n/a (reg≈{2.0 * reg_hw:.1f}px)"
    if ym_pred is None:
        return f"YM4x: n/a (no cached scan) | n_clean=n/a | {sw_str}", (180, 190, 205), False

    has_prop = bool(ym_pred.get("neighbor_propagated_family"))
    ym_fam = str(ym_pred.get("neighbor_propagated_family") or ym_pred.get("pred_family") or "?")
    ym_conf = float(ym_pred.get("pred_conf") or 0.0)
    t_fonts = ym_pred.get("top_fonts") or []
    t1_name = str(t_fonts[0].get("font") if t_fonts else "?")
    t1_prob = float(t_fonts[0].get("prob") if t_fonts else 0.0)
    b_prob = float(ym_pred.get("bold_prob") or 0.0)
    h_prob = float(ym_pred.get("heavy_prob") or 0.0)
    n_clean_raw = ym_pred.get("n_clean_chars")
    n_clean_val = int(n_clean_raw) if n_clean_raw is not None else None
    n_clean_str = f"n_clean={n_clean_val}" if n_clean_val is not None else "n_clean=?"

    rec_ff, rec_b, acc_fams = _call_recommend_fn_compat(
        recommend_fn,
        ym_pred,
        raw_stroke_hw=(shw if shw > 0.0 else None),
        raw_stem_hw=stem_hw,
        bbox_wh=bbox_wh,
        source_text=source_text,
        page_wh=page_wh,
    )
    cur_fam = classify_fn(ff if ff != "default" else "FZHei-B01")

    narrow_kana_stretch = False
    if bbox_wh and source_text:
        _ensure_repo_src_on_path()
        from mtui_mcp.font_detector import is_narrow_kana_stretch_suspect

        narrow_kana_stretch = is_narrow_kana_stretch_suspect(
            ym_pred,
            bbox_wh=bbox_wh,
            source_text=source_text,
            raw_stem_hw=(stem_hw if (stem_hw is not None and stem_hw > 0.0) else None),
            page_wh=page_wh,
        )

    thin_stem_downgraded = False
    if not narrow_kana_stretch and ym_fam == "MINCHO" and rec_ff != "FZCuSong-B09":
        if (stem_hw is not None and stem_hw > 0.0) or (stem_hw is None and shw > 0.0):
            base_rec_ff, _, _ = recommend_fn(ym_pred)
            if base_rec_ff == "FZCuSong-B09":
                thin_stem_downgraded = True

    is_low_conf_or_short = bool(
        ym_conf < 0.55
        or t1_prob < 0.25
        or (n_clean_val is not None and n_clean_val <= 2 and not has_prop)
    )
    t1_lower = t1_name.lower()
    kaisho_lock_allowed = ym_fam != "KAISHO_BRUSH" or any(k in t1_lower for k in _CORE_VERIFIED_KAISHO_KEYS)

    has_ym_hi_mismatch = False
    ym_col = (180, 190, 205)
    lock_tag = ""
    if narrow_kana_stretch:
        lock_tag = " [NARROW-KANA-STRETCH: verify 1:1 raw strokes]"
        ym_col = (255, 200, 120)
    elif thin_stem_downgraded:
        stem_disp = 2.0 * stem_hw if (stem_hw is not None and stem_hw > 0.0) else 2.0 * shw
        if ym_conf >= 0.90 and not is_low_conf_or_short and cur_fam not in acc_fams:
            has_ym_hi_mismatch = True
            lock_tag = f" [THIN-STEM: raw_stem={stem_disp:.1f}px, no CuSong lock] [⚠ HIGH-CONF YM MISMATCH!]"
            ym_col = (255, 110, 110)
        else:
            lock_tag = f" [THIN-STEM: raw_stem={stem_disp:.1f}px, no CuSong lock]"
            ym_col = (255, 200, 120)
    elif ym_conf >= 0.90 and not is_low_conf_or_short:
        if cur_fam not in acc_fams:
            has_ym_hi_mismatch = True
            lock_tag = " [⚠ HIGH-CONF YM MISMATCH!]"
            ym_col = (255, 110, 110)
        elif kaisho_lock_allowed:
            lock_tag = " [LOCKED>=0.90]"
            ym_col = (155, 225, 255)

    bold_tag = f"bold_p={b_prob:.2f}(ANTIQUE-normal)" if ym_fam == "ANTIQUE" else f"bold_p={b_prob:.2f}"
    eff_rec_b = bool(rec_b)
    if ym_fam == "ANTIQUE" and eff_rec_b and (shw <= 0.0 or (shw < 2.50 and shw <= reg_hw * 1.12)):
        eff_rec_b = False
    if is_low_conf_or_short and not narrow_kana_stretch:
        rec_tag = "[LOW-CONF/SHORT: judge by raw_sw vs reg]"
    else:
        rec_tag = f"rec={rec_ff}{'+B' if eff_rec_b else ''}{lock_tag}"

    ym_line = (
        f"YM4x: {ym_fam}({ym_conf:.2f}) {t1_name}({t1_prob:.2f}) | "
        f"{n_clean_str} | {sw_str} | {bold_tag} heavy_p={h_prob:.2f} | {rec_tag}"
    )
    return ym_line, ym_col, has_ym_hi_mismatch


def cmd_verify_crops(
    chapter_dir: Path,
    out_dir: Path,
    *,
    page_start: int = 1,
    page_end: int = 999999,
    pages: list[str] | set[str] | None = None,
    cards_per_sheet: int = 3,
) -> dict:
    """Export true >=1.0x native-resolution side-by-side verification sheets for P001->P_end covering BOTH:
    - Enclosed bubbles (`2-panel [RAW 原图局部 | RENDERED 成品局部]` + exterior artwork margin);
    - Frameless / out-of-bubble regions (`3-panel [RAW 原图 | INPAINTED 消字修图底图 | RENDERED 嵌字成品]`, the #1 inpainting disaster zone,
      exposing naked inpainted artwork impact unobscured by new text!) + orphan masked zones!
    Every card header displays `align=...` and cached `YuzuMarker 4x` ground-truth (`family`, `conf`, `top1`, `bold_p`, `rec`)
    and NEVER downscales below 1.0x so fine Mincho serifs and stroke weights stay crisp for VLM inspection.
    """
    _ensure_repo_src_on_path()
    from mtui_mcp.config import get_runtime_settings
    from mtui_mcp.font_detector import (
        classify_current_mtui_font,
        load_font_detection_cache,
        lookup_cached_region_prediction,
        recommend_mtui_font_for_prediction,
    )
    from mtui_mcp.path_resolver import list_manga_pages_in_path
    from mtui_mcp.project_store import (
        decode_mask_base64,
        load_page_json,
        open_rgb_image,
        region_bbox_xyxy,
        region_center,
        visible_text_of_region,
    )
    from mtui_mcp.qa_auditor import (
        _count_vertical_cells_in_line,
        _estimate_raw_main_char_size,
        measure_page_stroke_metrics,
    )
    from mtui_mcp.richtext_builder import region_is_bold

    out_dir.mkdir(parents=True, exist_ok=True)
    all_pages = list_manga_pages_in_path(chapter_dir)
    label_font = _load_cjk_font(14)
    sub_font = _load_cjk_font(13)

    by_name_or_stem: dict[str, set[int]] = {}
    for p_idx, pg in enumerate(all_pages, 1):
        by_name_or_stem.setdefault(pg.source_path.name.lower(), set()).add(p_idx)
        by_name_or_stem.setdefault(pg.source_path.stem.lower(), set()).add(p_idx)

    matched_page_nums: set[int] = set()
    has_page_filter = False
    if pages:
        for tok in pages:
            t = str(tok).strip()
            if not t:
                continue
            has_page_filter = True
            exact_hits = by_name_or_stem.get(t.lower(), set()) | by_name_or_stem.get(Path(t).stem.lower(), set())
            if exact_hits:
                # Exact filename/stem match takes priority over 1-based page index (e.g. `--pages 007` -> `007.png`)
                matched_page_nums.update(exact_hits)
            elif t.isdigit():
                matched_page_nums.add(int(t))

    cfg_default_align = ""
    try:
        mtui_cfg = get_runtime_settings().load_mtui_config_dict()
        cfg_default_align = str((mtui_cfg.get("render") or {}).get("alignment") or "").strip().lower()
    except Exception:
        cfg_default_align = ""

    ym_caches_by_work: dict[Path, dict] = {}
    pair_cards: list[Image.Image] = []
    selected_pages: list[str] = []
    exported_pages: list[str] = []
    skipped_pages: list[dict] = []
    missing_artifact_pages: list[dict] = []
    total_bubbles = 0
    frameless_exported = 0
    orphan_mask_exported = 0
    flagged_attention = 0
    alignment_flagged = 0
    ym_mismatch_flagged = 0
    min_scale_used = 999.0

    for p_no, p in enumerate(all_pages, 1):
        if p_no < page_start or p_no > page_end:
            continue
        if has_page_filter and p_no not in matched_page_nums:
            continue
        selected_pages.append(p.source_path.name)
        missing_parts: list[str] = []
        if not p.json_exists:
            missing_parts.append("missing_json")
        if not p.display_base_path.is_file():
            missing_parts.append("missing_display_base")
        if not p.rendered_output_exists:
            missing_parts.append("missing_rendered_output")
        if missing_parts:
            skip_rec = {
                "page": p.source_path.name,
                "page_number": p_no,
                "reason": ",".join(missing_parts),
            }
            skipped_pages.append(skip_rec)
            missing_artifact_pages.append(skip_rec)
            continue
        cards_before_page = len(pair_cards)
        if p.work_dir not in ym_caches_by_work:
            ym_caches_by_work[p.work_dir] = load_font_detection_cache(p.work_dir)
        page_ym_cache = ((ym_caches_by_work[p.work_dir].get("pages") or {}).get(p.source_path.stem))

        _, img_data = load_page_json(p)
        regs = img_data.get("regions") or []

        raw_im = Image.open(p.display_base_path).convert("RGB")
        ren_im = Image.open(p.rendered_output_path).convert("RGB")
        if ren_im.size != raw_im.size:
            ren_im = ren_im.resize(raw_im.size, Image.Resampling.LANCZOS)
        iw, ih = raw_im.size
        raw_np = np.asarray(raw_im)
        ren_np = np.asarray(ren_im)
        base_gray = cv2.cvtColor(raw_np, cv2.COLOR_RGB2GRAY)

        inp_im: Image.Image | None = None
        inp_gray: np.ndarray | None = None
        if p.inpainted_exists:
            inp_rgb = open_rgb_image(p.inpainted_path)
            inp_im = Image.fromarray(inp_rgb)
            if inp_im.size != raw_im.size:
                inp_im = inp_im.resize(raw_im.size, Image.Resampling.LANCZOS)
                inp_rgb = np.asarray(inp_im)
            inp_gray = cv2.cvtColor(inp_rgb, cv2.COLOR_RGB2GRAY)

        mask_u8 = decode_mask_base64(img_data.get("mask_raw"), (ih, iw))
        in_bubbles = _classify_page_regions_in_bubble(inp_gray, regs) if regs else []
        reg_stroke_hw, reg_stem_hw, regular_ref_hw = measure_page_stroke_metrics(base_gray, inp_gray, regs) if regs else ({}, {}, 2.20)

        # Sort regions in manga reading order (top-to-bottom bands, right-to-left within band)
        ordered: list[tuple[int, dict, float, float]] = []
        active_boxes: list[tuple[float, float, float, float]] = []
        for r_i, r in enumerate(regs):
            vis = visible_text_of_region(r).strip()
            if not vis:
                continue
            cx, cy = region_center(r)
            ordered.append((r_i, r, cx, cy))
            abx1, aby1, abx2, aby2 = region_bbox_xyxy(r)
            pquad_ab = _perspective_quad_world_bbox(r)
            if pquad_ab is not None:
                abx1 = min(abx1, pquad_ab[0])
                aby1 = min(aby1, pquad_ab[1])
                abx2 = max(abx2, pquad_ab[2])
                aby2 = max(aby2, pquad_ab[3])
            active_boxes.append((abx1, aby1, abx2, aby2))
        ordered.sort(key=lambda t: (int(t[3] // max(140.0, ih * 0.14)), -t[2]))

        for r_i, r, cx, cy in ordered:
            total_bubbles += 1
            is_in_bub = in_bubbles[r_i] if r_i < len(in_bubbles) else True
            if not is_in_bub:
                frameless_exported += 1
            vis = visible_text_of_region(r).strip()
            lns = [ln.strip() for ln in vis.split("\n") if ln.strip()]
            col_lens = [_count_vertical_cells_in_line(ln) for ln in lns]
            fs = int(round(float(r.get("font_size") or 16)))
            ff = str(r.get("font_family") or "default").strip()
            is_b = region_is_bold(r)
            reg_align = str(r.get("alignment") or "auto").strip().lower()
            bx1, by1, bx2, by2 = region_bbox_xyxy(r)
            raw_bbox_wh = (max(1.0, bx2 - bx1), max(1.0, by2 - by1))
            pquad_bb = _perspective_quad_world_bbox(r)
            has_persp_quad = pquad_bb is not None
            lsp = max(0.85, float(r.get("line_spacing", 1.0) or 1.0))
            csp = max(0.85, float(r.get("letter_spacing", 1.0) or 1.0))
            d_r = str(r.get("direction") or "auto").lower()
            is_v = d_r in ("v", "vertical", "vr") or (
                d_r == "auto" and not ((bx2 - bx1) / max(1.0, by2 - by1) > 1.45 and len(lns) == 1)
            )
            if is_v and lns:
                ew = len(lns) * fs * lsp * 1.18
                eh = max(col_lens) * fs * csp * 1.06
            else:
                ew = max((len(ln) for ln in lns), default=1) * fs * csp * 1.06
                eh = max(1, len(lns)) * fs * lsp * 1.18

            if pquad_bb is not None:
                bx1 = min(bx1, pquad_bb[0])
                by1 = min(by1, pquad_bb[1])
                bx2 = max(bx2, pquad_bb[2])
                by2 = max(by2, pquad_bb[3])

            pad_c = 52 if not is_in_bub else 46
            ux1 = max(0, int(math.floor(min(bx1, cx - ew * 0.52))) - pad_c)
            uy1 = max(0, int(math.floor(min(by1, cy - eh * 0.52))) - pad_c)
            ux2 = min(iw, int(math.ceil(max(bx2, cx + ew * 0.52))) + pad_c)
            uy2 = min(ih, int(math.ceil(max(by2, cy + eh * 0.52))) + pad_c)
            if ux2 - ux1 < 24 or uy2 - uy1 < 24:
                continue

            show_3panel = (not is_in_bub) and (inp_im is not None)
            c_raw = raw_im.crop((ux1, uy1, ux2, uy2))
            c_ren = ren_im.crop((ux1, uy1, ux2, uy2))
            c_inp = inp_im.crop((ux1, uy1, ux2, uy2)) if show_3panel and inp_im is not None else None

            cw0, ch0 = c_raw.size
            # NEVER downscale below 1.0x native resolution so Mincho serifs and stroke weights stay crisp!
            max_panel_w = 340.0 if show_3panel else 480.0
            scale = min(1.45, max_panel_w / max(cw0, 1), 440.0 / max(ch0, 1))
            scale = max(1.0, scale)
            min_scale_used = min(min_scale_used, scale)
            tw = max(16, int(round(cw0 * scale)))
            th = max(16, int(round(ch0 * scale)))
            if abs(scale - 1.0) >= 0.04:
                c_raw = c_raw.resize((tw, th), Image.Resampling.LANCZOS)
                c_ren = c_ren.resize((tw, th), Image.Resampling.LANCZOS)
                if c_inp is not None:
                    c_inp = c_inp.resize((tw, th), Image.Resampling.LANCZOS)

            raw_cw = _estimate_raw_main_char_size(base_gray, inp_gray, r)
            ratio_str = f"raw≈{raw_cw:.0f}px ({fs / raw_cw:.2f}x)" if raw_cw >= 14.0 else "raw=n/a"
            has_size_warn = raw_cw >= 16.0 and (fs / raw_cw <= 0.74 and raw_cw - fs >= 8)
            has_col_warn = (
                len(col_lens) >= 2
                and (
                    (col_lens[-1] == 1 and max(col_lens) >= 4)
                    or (max(col_lens) >= 7 and min(col_lens[:-1]) <= 3 and max(col_lens) >= 2.15 * min(col_lens[:-1]))
                    or any(
                        col_lens[k] <= 3 and col_lens[k] <= 0.52 * min(col_lens[k - 1], col_lens[k + 1])
                        for k in range(1, len(col_lens) - 1)
                    )
                )
            ) or (len(col_lens) == 2 and sum(col_lens) >= 16 and max(col_lens) >= 9)

            has_align_warn = bool(
                is_v
                and cfg_default_align != "center"
                and reg_align in ("center", "right")
                and (cfg_default_align == "left" or len(lns) >= 2)
            )
            if has_align_warn:
                alignment_flagged += 1

            # Resolve cached YuzuMarker 4x prediction for card header visibility & high-conf guard!
            ym_pred = (
                lookup_cached_region_prediction(page_ym_cache, r, r_i, total_regions=len(regs))
                if page_ym_cache
                else None
            )
            ym_line, ym_col, has_ym_hi_mismatch = _format_verify_crop_ym_line(
                ym_pred,
                ff=ff,
                shw=reg_stroke_hw.get(r_i, 0.0),
                reg_hw=regular_ref_hw,
                recommend_fn=recommend_mtui_font_for_prediction,
                classify_fn=classify_current_mtui_font,
                stem_hw=reg_stem_hw.get(r_i, 0.0),
                bbox_wh=raw_bbox_wh,
                source_text=str(r.get("text") or "").strip(),
                page_wh=(iw, ih),
            )
            if has_ym_hi_mismatch:
                ym_mismatch_flagged += 1

            if has_ym_hi_mismatch or has_align_warn:
                flagged_attention += 1
                hdr_col = (255, 115, 115) if has_ym_hi_mismatch else (255, 155, 95)
                ctx_badge = "[FRAMELESS 框外无框字·修图重灾区]" if not is_in_bub else "[BUBBLE 气泡框]"
            elif not is_in_bub:
                flagged_attention += 1
                hdr_col = (255, 195, 95) if not (has_size_warn or has_col_warn) else (255, 125, 95)
                ctx_badge = "[FRAMELESS 框外无框字·修图重灾区]"
            elif has_size_warn or has_col_warn:
                flagged_attention += 1
                hdr_col = (255, 145, 95)
                ctx_badge = "[BUBBLE 气泡框]"
            else:
                hdr_col = (145, 235, 185)
                ctx_badge = "[BUBBLE 气泡框]"
            if has_persp_quad:
                ctx_badge = f"{ctx_badge} [PERSP-QUAD]"

            hdr_h = 74
            gap = 10
            n_panels = 3 if c_inp is not None else 2
            card_w = max(tw * n_panels + gap * (n_panels - 1) + 24, 960)
            card = Image.new("RGB", (card_w, th + hdr_h + 12), (34, 28, 38) if not is_in_bub else (28, 30, 36))
            dr = ImageDraw.Draw(card)
            align_tag = f"align={reg_align}{'[!=left!]' if has_align_warn else ''}"
            line1 = (
                f"P{p_no:03d} ({p.source_path.name}) #{r_i:02d} {ctx_badge} | "
                f"font={ff}{'+B' if is_b else ''} | {align_tag} | fs={fs}px [{ratio_str}] | cols={col_lens} | {scale:.2f}x"
            )
            zh_flat = " / ".join(lns)
            if len(zh_flat) > 48:
                zh_flat = zh_flat[:47] + "…"
            if c_inp is not None:
                line3 = f"左:RAW原图 | 中:INPAINTED修图底图(核查原画影响最小化!) | 右:RENDERED成品 | 译: {zh_flat}"
            else:
                line3 = f"左:RAW原图(含框外周边原画) | 右:RENDERED成品 | 译: {zh_flat}"
            dr.text((10, 5), line1, fill=hdr_col, font=label_font)
            dr.text((10, 27), ym_line, fill=ym_col, font=sub_font)
            dr.text((10, 48), line3, fill=(220, 225, 235), font=sub_font)
            card.paste(c_raw, (10, hdr_h))
            if c_inp is not None:
                card.paste(c_inp, (10 + tw + gap, hdr_h))
                card.paste(c_ren, (10 + (tw + gap) * 2, hdr_h))
            else:
                card.paste(c_ren, (10 + tw + gap, hdr_h))
            pair_cards.append(card)

        # Also inspect orphan masked patches (`mask_raw > 0` far from any active region where Rendered differs from Raw, e.g. improperly cleaned deleted regions over artwork!)
        if mask_u8 is not None and np.any(mask_u8 > 0):
            nc_m, _, m_stats, m_cents = cv2.connectedComponentsWithStats((mask_u8 > 0).astype(np.uint8), connectivity=8)
            for mid in range(1, nc_m):
                mx, my, mw, mh, marea = m_stats[mid]
                if marea < 450:
                    continue
                mcx, mcy = float(m_cents[mid][0]), float(m_cents[mid][1])
                if any(bx1 - 30 <= mcx <= bx2 + 30 and by1 - 30 <= mcy <= by2 + 30 for bx1, by1, bx2, by2 in active_boxes):
                    continue
                ox1, oy1 = max(0, int(mx) - 36), max(0, int(my) - 36)
                ox2, oy2 = min(iw, int(mx + mw) + 36), min(ih, int(my + mh) + 36)
                diff_patch = np.abs(raw_np[oy1:oy2, ox1:ox2].astype(np.int16) - ren_np[oy1:oy2, ox1:ox2].astype(np.int16))
                if int(np.sum(np.max(diff_patch, axis=2) >= 20)) < 120:
                    continue
                orphan_mask_exported += 1
                flagged_attention += 1
                o_raw = raw_im.crop((ox1, oy1, ox2, oy2))
                o_ren = ren_im.crop((ox1, oy1, ox2, oy2))
                ocw, och = o_raw.size
                oscale = max(1.0, min(1.40, 440.0 / max(ocw, 1), 380.0 / max(och, 1)))
                min_scale_used = min(min_scale_used, oscale)
                otw, oth = max(16, int(round(ocw * oscale))), max(16, int(round(och * oscale)))
                if abs(oscale - 1.0) >= 0.04:
                    o_raw = o_raw.resize((otw, oth), Image.Resampling.LANCZOS)
                    o_ren = o_ren.resize((otw, oth), Image.Resampling.LANCZOS)
                ocard = Image.new("RGB", (max(otw * 2 + 36, 780), oth + 68), (42, 26, 28))
                odr = ImageDraw.Draw(ocard)
                odr.text(
                    (10, 6),
                    f"P{p_no:03d} ({p.source_path.name}) [ORPHAN-MASK 无框残留遮罩区 {mx},{my},{mx+mw},{my+mh}] | {oscale:.2f}x",
                    fill=(255, 115, 115),
                    font=label_font,
                )
                odr.text(
                    (10, 29),
                    "左:RAW原图 vs 右:RENDERED成品 — 检查已删区域是否用 restore_artwork 100% 还原原图！",
                    fill=(235, 215, 215),
                    font=sub_font,
                )
                ocard.paste(o_raw, (10, 56))
                ocard.paste(o_ren, (10 + otw + 12, 56))
                pair_cards.append(ocard)

        if len(pair_cards) > cards_before_page:
            exported_pages.append(p.source_path.name)
        else:
            skipped_pages.append(
                {
                    "page": p.source_path.name,
                    "page_number": p_no,
                    "reason": "no_visible_regions" if not ordered else "crops_too_small",
                }
            )

    per_sheet = max(1, int(cards_per_sheet))
    sheet_paths: list[str] = []
    for s_idx in range(0, len(pair_cards), per_sheet):
        batch = pair_cards[s_idx : s_idx + per_sheet]
        sw = max(c.width for c in batch) + 20
        sh = sum(c.height + 10 for c in batch) + 10
        sheet = Image.new("RGB", (sw, sh), (18, 20, 24))
        cy_off = 10
        for c in batch:
            sheet.paste(c, (10, cy_off))
            cy_off += c.height + 10
        sp = out_dir / f"verify_sheet_{s_idx // per_sheet + 1:03d}.png"
        sheet.save(sp, compress_level=3)
        sheet_paths.append(str(sp))

    manifest = {
        "page_range": [page_start, page_end],
        "page_filter": sorted(str(x) for x in pages) if pages else None,
        "selected_page_count": len(selected_pages),
        "selected_pages": selected_pages,
        "exported_pages": exported_pages,
        "skipped_pages": skipped_pages,
        "missing_artifact_pages": missing_artifact_pages,
        "cards_per_sheet": per_sheet,
        "min_scale_used": round(min_scale_used, 3) if min_scale_used < 999.0 else 1.0,
        "total_bubbles_exported": total_bubbles,
        "frameless_regions_exported": frameless_exported,
        "orphan_mask_zones_exported": orphan_mask_exported,
        "alignment_flagged_bubbles": alignment_flagged,
        "ym_high_conf_mismatch_flagged": ym_mismatch_flagged,
        "attention_highlighted_bubbles": flagged_attention,
        "verify_sheet_count": len(sheet_paths),
        "out_dir": str(out_dir),
        "sheets": sheet_paths,
    }
    (out_dir / "verify_crops_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def cmd_qa_audit(chapter_dir: Path, out_json: Path, *, force_yuzumarker: bool = False, glossary_file: Path | None = None) -> dict:
    """Run the full `run_qa_audit` headlessly and persist the report JSON (summary printed to stdout)."""
    _ensure_repo_src_on_path()
    from mtui_mcp.qa_auditor import run_qa_audit

    report = run_qa_audit(chapter_dir, force_yuzumarker=force_yuzumarker, glossary_file=glossary_file)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: v for k, v in report.items() if k != "pages"}
    summary["saved_json_path"] = str(out_json)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return report


def cmd_qa_diff(before_json: Path, after_json: Path, *, pages: list[str] | None = None, fail_on_regression: bool = False) -> dict:
    """Compare before/after `mtui_qa_audit` JSON reports to enforce a zero-regression closed-loop Gate B lock."""
    _ensure_repo_src_on_path()
    from mtui_mcp.qa_auditor import diff_qa_audits

    before_rep = json.loads(before_json.read_text(encoding="utf-8"))
    after_rep = json.loads(after_json.read_text(encoding="utf-8"))
    page_set = {p.strip() for p in pages if p.strip()} if pages else None
    diff = diff_qa_audits(before_rep, after_rep, pages=page_set)
    print(json.dumps(diff, ensure_ascii=False, indent=2))
    if fail_on_regression and not diff["passed"]:
        sys.exit(1)
    return diff


# ---------------------------------------------------------------------------
# 5. 3-Column (Raw | Before | After) Cache-Busted Comparison Sheets
# ---------------------------------------------------------------------------


def cmd_compare(
    raw_dir: Path | None,
    before_dir: Path,
    after_dir: Path | None,
    out_dir: Path,
    prefix: str = "cmp_",
) -> None:
    """Generate 3-panel (Raw | Before | After) comparison PNGs (falls back to 2-panel if raw_dir omitted).
    When `after_dir` is omitted (`None`) or lacks a matching file, automatically resolves each page's rendered output
    (`last_output_path` / `last_export_dir` / `-translated` / `manga_translator_work/result`) via `resolve_page_paths`."""
    _ensure_repo_src_on_path()
    out_dir.mkdir(parents=True, exist_ok=True)
    title_font = _load_cjk_font(24)
    before_files = sorted(
        (p for p in before_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS),
        key=_natural_sort_key,
    )
    count = 0
    for idx, bp in enumerate(before_files, 1):
        rp: Path | None = None
        if raw_dir is not None and raw_dir.is_dir():
            rp = _find_rendered_page(raw_dir, bp)

        ap: Path | None = None
        if after_dir is not None and after_dir.is_dir():
            ap = _find_rendered_page(after_dir, bp)
        if (ap is None or not ap.is_file()) and rp is not None and rp.is_file():
            _, ap = _resolve_page_rendered_status(rp)
        if (ap is None or not ap.is_file()) and after_dir is not None:
            chapter_cand = (
                after_dir.parent.parent
                if after_dir.parent.name == "manga_translator_work"
                else after_dir.parent
            )
            if chapter_cand.is_dir():
                src_cand = _find_rendered_page(chapter_cand, bp)
                if src_cand is not None and src_cand.is_file():
                    _, ap = _resolve_page_rendered_status(src_cand)

        if ap is None or not ap.is_file():
            continue
        ib = Image.open(bp).convert("RGB")
        ia = Image.open(ap).convert("RGB")
        if np.array_equal(np.asarray(ib), np.asarray(ia)):
            continue
        w, h = ib.size
        if ia.size != (w, h):
            ia = ia.resize((w, h), Image.Resampling.LANCZOS)

        ir = None
        if rp is not None and rp.is_file():
            ir = Image.open(rp).convert("RGB")
            if ir.size != (w, h):
                ir = ir.resize((w, h), Image.Resampling.LANCZOS)

        header_h, gap, pad = 56, 16, 14
        ncols = 3 if ir is not None else 2
        canvas = Image.new("RGB", (pad * 2 + w * ncols + gap * (ncols - 1), header_h + h + pad), (24, 26, 32))
        draw = ImageDraw.Draw(canvas)

        if ir is not None:
            draw.text((pad + 8, 14), f"第 {idx:02d} 页 ({bp.name}) — 日文原图 (Raw)", fill=(255, 220, 130), font=title_font)
            draw.text((pad + w + gap + 8, 14), f"第 {idx:02d} 页 — 初译处理前 (Before)", fill=(210, 215, 225), font=title_font)
            draw.text((pad + (w + gap) * 2 + 8, 14), f"第 {idx:02d} 页 — 精修处理后 (After)", fill=(130, 230, 175), font=title_font)
            canvas.paste(ir, (pad, header_h))
            canvas.paste(ib, (pad + w + gap, header_h))
            canvas.paste(ia, (pad + (w + gap) * 2, header_h))
        else:
            draw.text((pad + 8, 14), f"第 {idx:02d} 页 ({bp.name}) — 处理前 (Before)", fill=(210, 215, 225), font=title_font)
            draw.text((pad + w + gap + 8, 14), f"第 {idx:02d} 页 ({bp.name}) — 处理后 (After)", fill=(130, 230, 175), font=title_font)
            canvas.paste(ib, (pad, header_h))
            canvas.paste(ia, (pad + w + gap, header_h))

        out_p = out_dir / f"{prefix}p{idx:02d}_{bp.stem}.png"
        canvas.save(out_p, compress_level=3)
        count += 1
    print(f"Generated {count} comparison sheets in {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MTUI Manga Headless Initial Translation, QA Audit, Centering & Comparison CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tr = sub.add_parser("translate-raw", help="Run silent headless UI-identical initial translation on raw/missing pages & snapshot before_result")
    p_tr.add_argument("--chapter-dir", type=Path, required=True)
    p_tr.add_argument("--backup-before-dir", type=Path, default=None, help="Auto-snapshot initial rendered pages here right after initial translation")
    p_tr.add_argument("--overwrite", action="store_true", help="Re-translate even if JSON + result already exist")
    p_tr.add_argument("--mtui-root", type=Path, default=None)
    p_tr.add_argument("--config", type=Path, default=None)

    p_aud = sub.add_parser("audit")
    p_aud.add_argument("--chapter-dir", type=Path, required=True)
    p_aud.add_argument("--out-dir", type=Path, required=True)

    p_rf = sub.add_parser("raw-fonts")
    p_rf.add_argument("--chapter-dir", type=Path, required=True)
    p_rf.add_argument("--out-dir", type=Path, required=True)

    p_df = sub.add_parser("detect-fonts", help="Run local YuzuMarker 4x Ensemble font detection & build .yuzumarker_font_cache.json")
    p_df.add_argument("--chapter-dir", type=Path, required=True)
    p_df.add_argument("--force-refresh", action="store_true")

    p_sa = sub.add_parser(
        "script-audit",
        help="Export P001->P_end RTL bilingual script ledger + <=4-bubble 1.6x Raw crops for OCR/mistranslation verification",
    )
    p_sa.add_argument("--chapter-dir", type=Path, required=True)
    p_sa.add_argument("--out-dir", type=Path, required=True)
    p_sa.add_argument("--glossary", type=Path, default=None)

    p_cb = sub.add_parser("center-bubbles")
    p_cb.add_argument("--chapter-dir", type=Path, required=True)
    p_cb.add_argument("--out-json", type=Path, default=None)
    p_cb.add_argument("--apply-mcp", action="store_true", help="Run 2-pass closed-loop update + Qt6 re-render via mtui-mcp")
    p_cb.add_argument("--passes", type=int, default=2)

    p_vc = sub.add_parser(
        "verify-crops",
        help="Generate true >=1.0x native-res [Raw | Rendered] per-region verification sheets with align & YM4x headers",
    )
    p_vc.add_argument("--chapter-dir", type=Path, required=True)
    p_vc.add_argument("--out-dir", type=Path, required=True)
    p_vc.add_argument("--page-start", type=int, default=1)
    p_vc.add_argument("--page-end", type=int, default=999999)
    p_vc.add_argument(
        "--pages",
        type=str,
        default="",
        help="Optional comma-separated page stems/filenames or 1-based page numbers (exact stem/filename match takes priority over 1-based page index, e.g. 007,13)",
    )
    p_vc.add_argument("--cards-per-sheet", type=int, default=3, help="Max region cards stacked per PNG sheet (default 3; use 2 for extra-large bubbles)")

    p_qa = sub.add_parser(
        "qa-audit",
        help="Run the full mtui_qa_audit headlessly and save the report JSON (Gate A baseline / after-snapshot for qa-diff)",
    )
    p_qa.add_argument("--chapter-dir", type=Path, required=True)
    p_qa.add_argument("--out-json", type=Path, required=True)
    p_qa.add_argument("--force-yuzumarker", action="store_true")
    p_qa.add_argument("--glossary", type=Path, default=None)

    p_qd = sub.add_parser(
        "qa-diff",
        help="Compare before/after mtui_qa_audit JSON reports and block any newly introduced errors (Gate B regression lock)",
    )
    p_qd.add_argument("--before-json", type=Path, required=True)
    p_qd.add_argument("--after-json", type=Path, required=True)
    p_qd.add_argument("--pages", type=str, default="", help="Optional comma-separated page names/stems to scope diff")
    p_qd.add_argument("--fail-on-regression", action="store_true")

    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("--raw-dir", type=Path, default=None)
    p_cmp.add_argument("--before-dir", type=Path, required=True)
    p_cmp.add_argument("--after-dir", type=Path, default=None, help="Optional explicit rendered dir (auto-resolved via resolve_page_paths when omitted)")
    p_cmp.add_argument("--out-dir", type=Path, required=True)
    p_cmp.add_argument("--prefix", type=str, default="cmp_", help="Filename prefix (bump version e.g. cmp_v2_ to bust UI image cache)")

    args = parser.parse_args()
    if args.cmd == "translate-raw":
        cmd_translate_raw(
            args.chapter_dir,
            backup_before_dir=args.backup_before_dir,
            overwrite=args.overwrite,
            mtui_root=args.mtui_root,
            config_path=args.config,
        )
    elif args.cmd == "audit":
        cmd_audit(args.chapter_dir, args.out_dir)
    elif args.cmd == "raw-fonts":
        cmd_raw_fonts(args.chapter_dir, args.out_dir)
    elif args.cmd == "detect-fonts":
        _ensure_repo_src_on_path()
        from mtui_mcp.worker.client import get_worker_client

        w = get_worker_client()
        try:
            res = w.detect_fonts(args.chapter_dir, force_refresh=bool(args.force_refresh))
            print(json.dumps(res, ensure_ascii=False, indent=2))
        finally:
            w.shutdown()
    elif args.cmd == "script-audit":
        cmd_script_audit(args.chapter_dir, args.out_dir, glossary_file=args.glossary)
    elif args.cmd == "center-bubbles":
        cmd_center_bubbles(args.chapter_dir, args.out_json, apply_mcp=args.apply_mcp, passes=args.passes)
    elif args.cmd == "verify-crops":
        page_list = [tok.strip() for tok in args.pages.split(",") if tok.strip()] if args.pages else None
        cmd_verify_crops(
            args.chapter_dir,
            args.out_dir,
            page_start=args.page_start,
            page_end=args.page_end,
            pages=page_list,
            cards_per_sheet=args.cards_per_sheet,
        )
    elif args.cmd == "qa-audit":
        cmd_qa_audit(args.chapter_dir, args.out_json, force_yuzumarker=bool(args.force_yuzumarker), glossary_file=args.glossary)
    elif args.cmd == "qa-diff":
        page_list = [tok.strip() for tok in args.pages.split(",") if tok.strip()] if args.pages else None
        cmd_qa_diff(
            args.before_json,
            args.after_json,
            pages=page_list,
            fail_on_regression=bool(args.fail_on_regression),
        )
    elif args.cmd == "compare":
        cmd_compare(args.raw_dir, args.before_dir, args.after_dir, args.out_dir, args.prefix)


if __name__ == "__main__":
    main()
