"""Local YuzuMarker (ResNet-50 4x Ensemble) Japanese Manga Font Taxonomy, Cache & MTUI Font Mapper.

PyTorch CUDA inference executes inside `MTUI_ROOT`'s Python environment via `engine_worker.py`
(`action='detect_fonts'`), keeping `mtui-mcp`'s own lightweight environment free of heavy CUDA dependencies
and freeing GPU VRAM (`0 MB` idle) immediately after each chapter scan.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

from .config import get_runtime_settings
from .project_store import _atomic_replace_with_retry, region_bbox_xyxy, region_center

FONT_COUNT = 6150
INPUT_SIZE = 512
CACHE_FILENAME = ".yuzumarker_font_cache.json"
CACHE_VERSION = 2

FAMILY_NAMES: tuple[str, ...] = (
    "ANTIQUE",
    "KAKU_GOTHIC",
    "ROUND_GOTHIC",
    "MINCHO",
    "GUYIN",
    "KAISHO_BRUSH",
    "REISHO",
    "KYOKASHO_PEN",
    "POP_CHISEL",
)

WEIGHT_NAMES: tuple[str, ...] = ("light", "regular", "bold", "heavy")

# Standard Japanese `筑紫アンチック` / dialogue fonts whose kana/furigana often match TsukuARdGothic / DFEnKai / DigiKyokasho / HOT-FKaisho / DFSoKaiSho
_ANTIQUE_AMBIGUOUS_SUBSTRS: tuple[str, ...] = (
    "tsukuardgothic",
    "tsukubrdgothic",
    "dfenkai",
    "uddigikyokasho",
    "dftegakisumi",
    "dfcomic",
    "rodinhimawari",
    "koburina",
    "こぶりな",
    "hot-fkaisho",
    "dfsokaisho",
)

# Morisawa KyokaICA + elementary stroke-order ICA siblings (学参 筆順 教科書ICA) and standard textbook scripts
_KYOKAICA_SUBSTRS: tuple[str, ...] = (
    "kyokaica",
    "hitujyun",
    "hitujyo",
    "hitu2jyo",
)
_TEXTBOOK_KYOKASHO_SUBSTRS: tuple[str, ...] = (
    *_KYOKAICA_SUBSTRS,
    "kyokasho",
    "教科",
    "klee",
)

# Punctuation/symbol stripper shared with `engine_worker.py` (`n_clean_chars` & narrow-kana stretch detection)
CLEAN_STRIP_RE = re.compile(r"[\s…‥・！？!?‼⁉⁈⁇、。,.「」『』（）()【】―─ー〜～︙│]+")
_KANA_ONLY_RE = re.compile(r"^[\u3040-\u309f\u30a0-\u30ffー]+$")

THIN_STEM_CUSONG_THRESHOLD_PX = 2.45
THIN_STROKE_CUSONG_FALLBACK_PX = 2.15


def page_resolution_scale(page_wh: Optional[tuple[int, int]]) -> float:
    """Return single-page horizontal scale relative to a 1350x1920 tankobon page (spread-, webtoon-, and unit-test-safe)."""
    if not page_wh:
        return 1.0
    w, h = float(page_wh[0]), float(page_wh[1])
    if w <= 0.0 or h <= 0.0 or max(w, h) < 900.0:
        return 1.0
    eff_w = min(w / 2.0, h * (1350.0 / 1920.0)) if w > 1.15 * h else w
    return max(0.60, min(2.50, eff_w / 1350.0))


def is_narrow_kana_stretch_suspect(
    pred: dict[str, Any],
    *,
    bbox_wh: Optional[tuple[float, float]] = None,
    source_text: Optional[str] = None,
    raw_stem_hw: Optional[float] = None,
    page_wh: Optional[tuple[int, int]] = None,
) -> bool:
    """Return True when a short pure-kana narrow strip risks anisotropic 512x512 stretch distortion in YuzuMarker."""
    if pred.get("neighbor_propagated_family"):
        return False
    if not bbox_wh or not source_text:
        return False
    bw, bh = float(bbox_wh[0]), float(bbox_wh[1])
    if bw <= 0.0 or bh <= 0.0:
        return False
    scale = page_resolution_scale(page_wh)
    aspect = max(bh / bw, bw / bh)
    min_side = min(bw, bh)
    if aspect < 3.5 or min_side > 44.0 * scale:
        return False
    src_clean = CLEAN_STRIP_RE.sub("", str(source_text).strip())
    if not src_clean or len(src_clean) > 3:
        return False
    if not _KANA_ONLY_RE.match(src_clean):
        return False
    if raw_stem_hw is not None and float(raw_stem_hw) >= 2.50 * scale:
        return False
    # Spare genuine thin/medium Mincho strips (e.g. `RyuminPr6N-Medium`, `bold_prob < 0.25`, `heavy_prob < 0.10`)
    if (
        str(pred.get("pred_family") or "") == "MINCHO"
        and "bold_prob" in pred
        and float(pred.get("bold_prob") or 0.0) < 0.25
        and float(pred.get("heavy_prob") or 0.0) < 0.10
    ):
        return False
    return True


_LIGHT_WEIGHT_RE = re.compile(
    r"((?:-w1|-w2|-w3)(?!\d)|thin|hairline|extralight|extra[-_\s]?light|ultralight|ultra[-_\s]?light|semilight|light|-el\b|-ul\b|-l\b|_l\.|细|纤|极细|細|纖|極細)"
)
_HEAVY_WEIGHT_RE = re.compile(
    r"(-w9|-w10|-w12|-w14|heavy|exheavy|ultrablack|ultrabold|ultra(?!\s*[-_]?\s*light)|black|superbold|-eb|-ub|-hv|-u\b|-h\b|_h\.|_u\.|特粗|超粗|极粗|特太)"
)
_BOLD_WEIGHT_RE = re.compile(
    r"(-w7|-w8|bold|semibold|demibold|debold|-db|-b\b|_b\.|粗|中粗|准|大黑|大标|太)"
)


def _classify_font_filename_weight(fname_lower: str) -> str:
    """Classify a lowercased font filename/selector into ``light | regular | bold | heavy``."""
    if _LIGHT_WEIGHT_RE.search(fname_lower):
        return "light"
    if _HEAVY_WEIGHT_RE.search(fname_lower):
        return "heavy"
    if _BOLD_WEIGHT_RE.search(fname_lower):
        return "bold"
    return "regular"


def classify_vcb_font_entry(entry: dict[str, Any]) -> tuple[str, str]:
    """Deterministically categorize each of the 6,150 VCB-Studio font labels into `(manga_family, weight_tier)`."""
    p = str(entry.get("path") or "")
    pl = p.lower()
    is_serif = bool(entry.get("serif", False))
    fname = p.split("/")[-1].lower()

    weight = _classify_font_filename_weight(fname)

    # 1. GUYIN (古印体 — broken/worm-eaten strokes)
    if any(k in pl for k in ("koin", "kointai", "古印", "古籍木兰", "斑驳")):
        return "GUYIN", weight

    # 2. ANTIQUE (日漫標準アンチック体: Mincho kanji + Gothic kana)
    if any(k in pl for k in ("antic", "antique", "アンチック", "アンチ", "shueianti", "kantique")):
        return "ANTIQUE", weight

    # 3. Thin stick-pen `TakeStd-Light` -> KYOKASHO_PEN (`Xiaolai`), whereas bold `TakeStd-Bold` -> POP_CHISEL
    if ("takestd" in pl or "takepro" in pl) and weight == "light":
        return "KYOKASHO_PEN", weight

    # 4. POP_CHISEL (創英角ポップ / ComicReggae / Take-Bold / SoGei / 剪纸 / 综艺)
    if any(
        k in pl
        for k in (
            "comicreggae",
            "reggae",
            "takestd",
            "takepro",
            "sogei",
            "pop",
            "剪纸",
            "剪紙",
            "综艺",
            "綜藝",
            "zongyi",
            "singeitai",
            "新艺",
            "新藝",
            "kantei",
            "勘亭",
            "kanting",
            "slump",
            "raglan",
            "rocknroll",
            "kurokane",
            "comictai",
            "ita-tai",
            "胖头鱼",
        )
    ):
        return "POP_CHISEL", weight

    # 5. REISHO (隷書 / 陸隷 / 豊隷 — Clerical Script for solemn gods, magic chants & deadpan tsukkomi)
    if any(k in pl for k in ("reisho", "leisho", "likurei", "hourei", "kakurei", "隶", "隸")):
        return "REISHO", weight

    # 6. Heavy comic shout `RoBrush` -> KAKU_GOTHIC (heavy)
    if "robrush" in pl:
        return "KAKU_GOTHIC", "heavy"

    # 7. ROUND_GOTHIC (丸ゴシック / 圆体 / RodinHappy / Seurat / TsukuARdGothic)
    if (
        "丸ゴシック" in p
        or any(
            k in pl
            for k in (
                "marugothic",
                "marugo",
                "shinmgo",
                "udshinmgo",
                "-mgo",
                "mgo-",
                "round-gothic",
                "roundgothic",
                "tsukuard",
                "tsukubrd",
                "ardgothic",
                "rdgothic",
                "seurat",
                "capie",
                "tsukumaru",
                "udmarugo",
                "junpro",
                "junstd",
                "jun101",
                "jun201",
                "jun34",
                "jun501",
                "rodinhappy",
                "圆",
                "圓",
                "准圆",
                "幼圆",
                "琥珀",
                "mamelon",
                "kosugimaru",
                "zenmaru",
                "kiwimaru",
                "humming",
            )
        )
        and "圆章" not in p
    ):
        return "ROUND_GOTHIC", weight

    # 8. KYOKASHO_PEN (教科書体 / KyokaICA / Hitujyun / ComicMystery / 硬笔 / 手写圆笔)
    if any(
        k in pl
        for k in (
            "comicmystery",
            "mystery",
            "kyokaica",
            "hitujyun",
            "hitujyo",
            "hitu2jyo",
            "kyokasho",
            "教科",
            "penkaisho",
            "pengyo",
            "hanzipen",
            "hannotate",
            "takahand",
            "moon font",
            "钢笔",
            "鋼筆",
            "硬笔",
            "硬筆",
            "签字",
            "簽字",
            "稚艺",
            "稚藝",
            "zhiyi",
            "xiaolai",
            "yozai",
            "手写",
            "手寫",
            "marker",
            "crayon",
            "felt",
            "klee",
        )
    ):
        return "KYOKASHO_PEN", weight

    # 9. KAISHO_BRUSH (正楷 / 特太楷書 / 魏碑 / 行书 / 书道)
    if any(
        k in pl
        for k in (
            "kaisho",
            "kaiti",
            "kaishu",
            "ankai",
            "yankai",
            "xingkai",
            "biaokai",
            "zhongkai",
            "stdkai",
            "dfkai",
            "arkai",
            "mkai",
            "fzkai",
            "enkai",
            "gyosho",
            "gyokaisho",
            "socho",
            "tenshou",
            "brush",
            "sumi",
            "fuun",
            "ryuseki",
            "gihi",
            "hige",
            "楷",
            "魏",
            "碑",
            "行书",
            "行書",
            "草书",
            "草書",
            "毛笔",
            "毛筆",
            "书道",
            "書道",
            "飞白",
            "飛白",
            "白舟",
            "hakusyu",
            "舒体",
            "行体",
        )
    ):
        return "KAISHO_BRUSH", weight

    # 10. Hybrid Kaku-Gothic (`カクミン` Kakumin)
    if "kakumin" in pl:
        return "KAKU_GOTHIC", weight

    # 11. MINCHO (明朝体 / 宋体 / 麗雅宋 LeiGaSo / 雅宋 YuGaSo)
    if (
        is_serif
        or "明朝体" in p
        or any(
            k in pl
            for k in (
                "mincho",
                "myungjo",
                "matisse",
                "ryumin",
                "shueimin",
                "shueishogo",
                "udreimin",
                "tsukumin",
                "tsukuaoldmin",
                "tenmincho",
                "kokumin",
                "futomin",
                "midashimin",
                "modemin",
                "leigaso",
                "yugaso",
                "kaimin",
                "kinbun",
                "古籍",
                "糸柳",
                "宋",
                "明體",
                "明体",
                "serif",
            )
        )
    ):
        return "MINCHO", weight

    return "KAKU_GOTHIC", weight


def classify_current_mtui_font(font_family: str) -> str:
    """Classify a local MTUI Chinese font selector into one of the canonical manga font families."""
    ff = (font_family or "FZHei-B01").strip() or "FZHei-B01"
    fl = ff.lower()
    base_fl = fl.split("::")[0].strip()
    if any(k in fl for k in ("tankuin", "guyin")):
        return "GUYIN"
    if any(k in fl for k in ("lisu", "stliti", "lishu", "隶", "willow", "糸柳")):
        return "REISHO"
    if any(k in fl for k in ("jianzhi", "zongyi", "fangyuan", "huangyou", "kuaile", "zhsrxt")):
        return "POP_CHISEL"
    if any(k in fl for k in ("zhunyuan", "rounded", "lantingyuan", "youyuan", "swei gothic")):
        return "ROUND_GOTHIC"
    if any(k in fl for k in ("wenkai", "zhiyi", "xiaolai", "yozai")):
        return "KYOKASHO_PEN"
    if any(k in fl for k in ("kai", "weibei", "cangsang", "shuti", "xing", "cao")):
        return "KAISHO_BRUSH"
    if any(k in fl for k in ("yasong", "cusong", "serif", "huiwen", "soukou", "simsun", "song", "fangsong")):
        return "MINCHO"
    if base_fl == "fzhei-b01" and "::bold" not in fl and "::heavy" not in fl:
        return "ANTIQUE"
    return "KAKU_GOTHIC"


def _recommend_textbook_kyokasho(
    top1_font: str,
    top_fonts: list[dict[str, Any]],
) -> tuple[str, bool, set[str]]:
    """Map Morisawa KyokaICA / Hitujyun / non-ambiguous textbook scripts to `LXGW WenKai` (`bold=False`)."""
    is_regular_or_light = bool(re.search(r"(-regular|-reg\b|-light|\blight\b|-l\b|-w3\b|-icar)", top1_font))
    rec_ff = "LXGW WenKai" if is_regular_or_light else "LXGW WenKai::Medium"
    has_ambig_top3 = any(
        float(tf.get("prob") or 0.0) >= 0.08
        and any(amb in str(tf.get("font") or "").lower() for amb in _ANTIQUE_AMBIGUOUS_SUBSTRS)
        for tf in top_fonts[:3]
    )
    textbook_prob = sum(
        float(tf.get("prob") or 0.0)
        for tf in top_fonts
        if any(k in str(tf.get("font") or "").lower() for k in _TEXTBOOK_KYOKASHO_SUBSTRS)
        and not any(amb in str(tf.get("font") or "").lower() for amb in _ANTIQUE_AMBIGUOUS_SUBSTRS)
    )
    if not has_ambig_top3 and textbook_prob >= 0.70:
        return rec_ff, False, {"KYOKASHO_PEN", "KAISHO_BRUSH"}
    return rec_ff, False, {"KYOKASHO_PEN", "KAISHO_BRUSH", "ROUND_GOTHIC", "ANTIQUE"}


def recommend_mtui_font_for_prediction(
    pred: dict[str, Any],
    *,
    raw_stroke_hw: Optional[float] = None,
    raw_stem_hw: Optional[float] = None,
    bbox_wh: Optional[tuple[float, float]] = None,
    source_text: Optional[str] = None,
    page_wh: Optional[tuple[int, int]] = None,
) -> tuple[str, bool, set[str]]:
    """Return `(recommended_font_family, recommended_bold, acceptable_families_set)` for a YuzuMarker prediction."""
    eff_fam = str(pred.get("neighbor_propagated_family") or pred.get("pred_family") or "ANTIQUE")
    bold_p = float(pred.get("bold_prob") or 0.0)
    heavy_p = float(pred.get("heavy_prob") or 0.0)
    top_fonts = pred.get("top_fonts") or []
    top1_font = (top_fonts[0].get("font") if top_fonts else "" or "").lower()
    top1_prob = float(top_fonts[0].get("prob") if top_fonts else 0.0)

    # Check if top-1 is a `筑紫アンチック` kana-neighbor font where standard `FZHei-B01` (ANTIQUE) is normal dialogue
    if not pred.get("neighbor_propagated_family") and any(k in top1_font for k in _ANTIQUE_AMBIGUOUS_SUBSTRS):
        return "FZHei-B01", False, {"ANTIQUE", "ROUND_GOTHIC", "KAKU_GOTHIC", "KYOKASHO_PEN", "KAISHO_BRUSH"}
    # Read-time safety net for legacy v1 caches whose top-1 is Morisawa KyokaICA / Hitujyun (cached under ROUND_GOTHIC/KAKU_GOTHIC)
    if not pred.get("neighbor_propagated_family") and any(k in top1_font for k in _KYOKAICA_SUBSTRS):
        return _recommend_textbook_kyokasho(top1_font, top_fonts)
    if not pred.get("neighbor_propagated_family") and "kaimin" in top1_font:
        return "FZHei-B01", False, {"ANTIQUE", "MINCHO", "KAKU_GOTHIC"}
    if not pred.get("neighbor_propagated_family") and "greco" in top1_font:
        return "FZYaSong-DB-GBK", False, {"MINCHO", "ANTIQUE", "KAKU_GOTHIC"}
    # `KakuminPro-Bold` (`カクミン Bold`) is bold Kaku-Gothic dialogue/narration — check BEFORE `eff_fam == "MINCHO"`
    # so coin-flip splits (e.g. `KakuminPro-Bold` top1=0.491 vs summed `MINCHO=0.502`) never recommend Songti!
    if (
        not pred.get("neighbor_propagated_family")
        and "kakumin" in top1_font
        and (
            eff_fam == "KAKU_GOTHIC"
            or top1_prob >= 0.25
            or float((pred.get("family_probs") or {}).get("KAKU_GOTHIC") or 0.0) >= 0.30
        )
    ):
        return "FZHei-B01", bool(bold_p >= 0.55), {"ANTIQUE", "KAKU_GOTHIC", "ROUND_GOTHIC"}

    if eff_fam == "GUYIN":
        return "Arphic Tankuin Bold GB", False, {"GUYIN"}

    if eff_fam == "REISHO":
        return "LiSu", bool(bold_p >= 0.70 or heavy_p >= 0.50), {"REISHO"}

    if eff_fam == "POP_CHISEL":
        if any(k in top1_font for k in ("sogei", "zongyi", "综艺", "綜藝", "singeitai", "新艺", "新藝")):
            return "ZHSRXT2011-GB", False, {"POP_CHISEL"}
        return "FZJianZhi-M23", False, {"POP_CHISEL"}

    if eff_fam == "ROUND_GOTHIC":
        if bold_p >= 0.55:
            return "FZLanTingYuan-B-GBK", False, {"ROUND_GOTHIC", "KAKU_GOTHIC"}
        return "FZZhunYuan-M02", False, {"ROUND_GOTHIC", "KAKU_GOTHIC"}

    if eff_fam == "MINCHO":
        top1_weight = _classify_font_filename_weight(top1_font)
        is_explicit_heavy_mincho = top1_weight != "light" and (
            top1_weight == "heavy"
            or any(k in top1_font for k in ("leigaso", "shueishogo", "futo"))
        )
        if heavy_p >= 0.35 or is_explicit_heavy_mincho:
            scale = page_resolution_scale(page_wh)
            # Explicit `raw_stem_hw == 0.0` means unmeasured (frameless/mixed-bg) and MUST NOT fall back to `raw_stroke_hw`.
            eff_stem_for_nk: Optional[float] = None
            if raw_stem_hw is not None:
                eff_stem_for_nk = float(raw_stem_hw) if float(raw_stem_hw) > 0.0 else None
            elif raw_stroke_hw is not None and float(raw_stroke_hw) > 0.0:
                eff_stem_for_nk = float(raw_stroke_hw)

            narrow_stretch = is_narrow_kana_stretch_suspect(
                pred,
                bbox_wh=bbox_wh,
                source_text=source_text,
                raw_stem_hw=eff_stem_for_nk,
                page_wh=page_wh,
            )
            thin_stem_guard = (
                raw_stem_hw is not None
                and 0.0 < float(raw_stem_hw) < THIN_STEM_CUSONG_THRESHOLD_PX * scale
            )
            thin_stroke_fallback = (
                raw_stem_hw is None
                and raw_stroke_hw is not None
                and 0.0 < float(raw_stroke_hw) < THIN_STROKE_CUSONG_FALLBACK_PX * scale
                and (bbox_wh is None or min(float(bbox_wh[0]), float(bbox_wh[1])) < 55.0 * scale)
            )
            if narrow_stretch or thin_stem_guard or thin_stroke_fallback:
                return "FZYaSong-DB-GBK", False, {"MINCHO"}
            return "FZCuSong-B09", False, {"MINCHO"}
        if bold_p < 0.25 and heavy_p < 0.10 and "medium" in top1_font:
            return "FZYaSong-M-GBK", False, {"MINCHO"}
        return "FZYaSong-DB-GBK", False, {"MINCHO"}

    if eff_fam == "KYOKASHO_PEN":
        if "takestd-light" in top1_font:
            return "Xiaolai", False, {"KYOKASHO_PEN"}
        if "comicmystery" in top1_font or "mystery" in top1_font:
            return "LXGW WenKai::Medium", True, {"KYOKASHO_PEN", "KAISHO_BRUSH"}
        if any(k in top1_font for k in _TEXTBOOK_KYOKASHO_SUBSTRS):
            return _recommend_textbook_kyokasho(top1_font, top_fonts)
        return "LXGW WenKai::Medium", bool(bold_p >= 0.60), {"KYOKASHO_PEN", "ROUND_GOTHIC", "ANTIQUE"}

    if eff_fam == "KAISHO_BRUSH":
        if any(k in top1_font for k in ("kaishomcbk", "特太楷書", "gfkaisho")) and top1_prob >= 0.45:
            return "FZBeiWeiKaiShu-S19", False, {"KAISHO_BRUSH"}
        return "FZBeiWeiKaiShu-S19", False, {"KAISHO_BRUSH", "KYOKASHO_PEN", "ANTIQUE", "KAKU_GOTHIC"}

    if eff_fam == "KAKU_GOTHIC":
        # `KakuminPro-Bold` (`カクミン Bold`) is bold emphasis dialogue / narration (`FZHei-B01+B`, `FZZhunYuan-M02+B`, `Source Han Sans`)
        if "kakumin" in top1_font:
            return "FZHei-B01", bool(bold_p >= 0.55), {"ANTIQUE", "KAKU_GOTHIC", "ROUND_GOTHIC"}
        # `GothicBBB*-Medium` is pure serifless monologue/telepathy sans-serif — NEVER Songti (`MINCHO`)!
        if "gothicbbb" in top1_font:
            return "FZZhunYuan-M02", False, {"ROUND_GOTHIC", "KAKU_GOTHIC", "ANTIQUE"}
        if heavy_p >= 0.55:
            return "Source Han Sans SC::Heavy", False, {"KAKU_GOTHIC", "ANTIQUE", "ROUND_GOTHIC", "POP_CHISEL"}
        if bold_p >= 0.60:
            return "Source Han Sans SC::Bold", False, {"KAKU_GOTHIC", "ANTIQUE", "ROUND_GOTHIC"}
        return "FZZhunYuan-M02", False, {"KAKU_GOTHIC", "ROUND_GOTHIC", "ANTIQUE"}

    # ANTIQUE (`-DB`/`-B` commercial dialogue fonts naturally score bold_prob=0.88~1.00; only heavy_p>=0.45 is truly bold)
    return "FZHei-B01", bool(heavy_p >= 0.45), {"ANTIQUE", "KAKU_GOTHIC", "ROUND_GOTHIC"}


def region_bbox_signature(bbox: list[float]) -> str:
    s = ",".join(f"{v:.1f}" for v in bbox)
    return hashlib.md5(s.encode("ascii")).hexdigest()[:12]


def yuzumarker_models_available() -> bool:
    rt = get_runtime_settings()
    if not rt.mtui_root:
        return False
    mdir = rt.mtui_root / "models" / "yuzumarker"
    try:
        if not (mdir / "font-labels-ex.json").is_file():
            return False
        candidates = list(mdir.glob("*.ckpt"))
    except OSError:
        return False
    for p in candidates:
        try:
            if p.stat().st_size > 300_000_000:
                return True
        except OSError:
            continue
    return False


def font_cache_path_for_work_dir(work_dir: Path) -> Path:
    return work_dir / CACHE_FILENAME


def load_font_detection_cache(work_dir: Path) -> dict[str, Any]:
    cp = font_cache_path_for_work_dir(work_dir)
    if not cp.is_file():
        return {}
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        pages_dict = data.get("pages")
        if pages_dict is not None and not isinstance(pages_dict, dict):
            return {}
        if isinstance(pages_dict, dict):
            for p_entry in pages_dict.values():
                if isinstance(p_entry, dict):
                    p_entry.setdefault("_from_disk_cache", True)
        return data
    except Exception:
        return {}


def save_font_detection_cache_atomic(work_dir: Path, payload: dict[str, Any]) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    cp = font_cache_path_for_work_dir(work_dir)
    clean_payload = dict(payload)
    if isinstance(clean_payload.get("pages"), dict):
        clean_pages: dict[str, Any] = {}
        for k, v in clean_payload["pages"].items():
            if isinstance(v, dict) and "_from_disk_cache" in v:
                clean_pages[k] = {pk: pv for pk, pv in v.items() if pk != "_from_disk_cache"}
            else:
                clean_pages[k] = v
        clean_payload["pages"] = clean_pages
    fd, tmp = tempfile.mkstemp(prefix=".yuzumarker_cache.", suffix=".tmp", dir=str(work_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(clean_payload, f, ensure_ascii=False, indent=2)
        _atomic_replace_with_retry(tmp, cp)
        tmp = ""
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return cp


def _parse_nonneg_int(val: Any) -> Optional[int]:
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, int):
        return val if val >= 0 else None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            parsed = int(s)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def lookup_cached_region_prediction(
    page_cache: Optional[dict[str, Any]],
    reg: dict[str, Any],
    region_index: int,
    *,
    total_regions: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    if not page_cache or not isinstance(page_cache, dict):
        return None
    if total_regions is not None:
        total_regions = _parse_nonneg_int(total_regions)
        if total_regions is None:
            return None
    bbox = region_bbox_xyxy(reg)
    sig = region_bbox_signature(bbox)
    reg_uid = str(reg.get("region_uid") or "").strip()

    by_sig = page_cache.get("by_bbox_sig") or {}
    if isinstance(by_sig, dict) and sig in by_sig:
        hit = by_sig[sig]
        if isinstance(hit, dict):
            hit_uid = str(hit.get("region_uid") or "").strip()
            if not (reg_uid and hit_uid and reg_uid != hit_uid):
                return hit

    by_idx = page_cache.get("by_ridx") or {}
    if not isinstance(by_idx, dict):
        return None
    cand = by_idx.get(str(region_index))
    if reg_uid and (not isinstance(cand, dict) or str(cand.get("region_uid") or "").strip() != reg_uid):
        for alt_cand in by_idx.values():
            if isinstance(alt_cand, dict) and str(alt_cand.get("region_uid") or "").strip() == reg_uid:
                cand = alt_cand
                break
    if not isinstance(cand, dict):
        return None

    cand_sig = str(cand.get("bbox_sig") or "").strip()
    cand_center = cand.get("center")
    cand_cx = 0.0
    cand_cy = 0.0
    has_cand_center = False
    if (
        isinstance(cand_center, (list, tuple))
        and len(cand_center) >= 2
        and not isinstance(cand_center[0], bool)
        and not isinstance(cand_center[1], bool)
    ):
        try:
            cand_cx = float(cand_center[0])
            cand_cy = float(cand_center[1])
            has_cand_center = math.isfinite(cand_cx) and math.isfinite(cand_cy)
        except (TypeError, ValueError, OverflowError):
            has_cand_center = False

    # 1. Explicit stable region_uid match/mismatch — still requires matching crop identity (`R3-F05`)
    cand_uid = str(cand.get("region_uid") or "").strip()
    if reg_uid and cand_uid:
        if reg_uid != cand_uid:
            return None
        if cand_sig:
            return cand if cand_sig == sig else None
        if has_cand_center:
            cx, cy = region_center(reg)
            return cand if math.hypot(cx - cand_cx, cy - cand_cy) <= 24.0 else None
        return None
    if cand_uid and not reg_uid:
        return None

    # 2. Cached bbox_sig / center identity verification (present on all real engine_worker cache entries)
    if cand_sig and cand_sig == sig:
        return cand

    # Reject invalid or stale region_count, or cached indices exceeding current region list
    if "region_count" in page_cache and page_cache.get("region_count") is not None:
        cached_count = _parse_nonneg_int(page_cache.get("region_count"))
        if cached_count is None:
            return None
        if total_regions is not None and cached_count != total_regions:
            return None
    if total_regions is not None:
        if any(int(k) >= total_regions for k in by_idx if str(k).isdigit()):
            return None
        if (page_cache.get("_from_disk_cache") or by_sig) and len(by_idx) != total_regions:
            return None

    if has_cand_center:
        cx, cy = region_center(reg)
        if math.hypot(cx - cand_cx, cy - cand_cy) > 24.0:
            return None
        if total_regions is not None and len(by_idx) != total_regions:
            return None
        return cand

    # Never accept unverified bare by_ridx hits lacking region_uid, bbox_sig, and center
    return None
