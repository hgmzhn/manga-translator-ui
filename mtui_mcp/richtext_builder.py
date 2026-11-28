"""Strict `richtext.v1` document builder, style synchronizer, and rules engine.

Fully adheres to `manga_translator/rendering/rich_text.py` and
`desktop_qt_ui/editor/rich_text_editing.py` contracts:
- Top-level keys: ONLY `{"format": "richtext.v1", "blocks": [...]}`
- Block keys: ONLY `{"type": "paragraph", "inlines": [...]}`
- Inline types:
  - `{"type": "text", "text": str, "style": {...}}`
  - `{"type": "ruby", "base": [text_run, ...], "text": [text_run, ...]}`
  - `{"type": "tcy", "content": [text_run, ...]}`
- Style keys (strict camelCase):
  `bold`, `italic`, `underline`, `strikethrough`, `color`, `scale`,
  `fontSize`, `fontFamily`, `stroke` (`{color, width}`),
  `outerStroke` (`{color, width}`), `glow` (`{color, blur}`),
  `blur`, `blurAngle`, `blurRadial`, `blurMotion`, `blurMotionDistance`,
  `rubyInherit`, `emphasis`, `noTcy`, `verticalAdvance` (`"half"` | `"full"`),
  `kerning`, `preKerning`, `lineKerning`, `nextKerning`,
  `transform` (`{offsetX, offsetY, rotation, mirrorX, mirrorY, scaleX, scaleY}`).
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

RICH_TEXT_FORMAT = "richtext.v1"
_BR_RE = re.compile(r"(?:\[BR\]|<br\s*/?>|【BR】|\r\n|\r|\n)", re.IGNORECASE)
ALREADY_VERTICAL_GLYPHS = frozenset("│︱┃║︲︙︰︵︶︷︸︹︺︻︼︽︾︿﹀﹁﹂﹃﹄︴")


def strip_rotation_from_style(style: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Return a normalized copy of ``style`` with ``transform.rotation`` removed (preventing double-rotation on vertical glyphs)."""
    if not style or not isinstance(style, dict):
        return {}
    out = copy.deepcopy(style)
    tf = out.get("transform")
    if isinstance(tf, dict) and "rotation" in tf:
        tf.pop("rotation", None)
        if not tf:
            out.pop("transform", None)
    return validate_and_normalize_style(out)


ALLOWED_STYLE_KEYS = {
    "bold",
    "italic",
    "underline",
    "strikethrough",
    "color",
    "scale",
    "fontSize",
    "fontFamily",
    "stroke",
    "outerStroke",
    "glow",
    "blur",
    "blurAngle",
    "blurRadial",
    "blurMotion",
    "blurMotionDistance",
    "rubyInherit",
    "emphasis",
    "noTcy",
    "verticalAdvance",
    "kerning",
    "preKerning",
    "lineKerning",
    "nextKerning",
    "transform",
}

ALLOWED_TRANSFORM_KEYS = {
    "offsetX",
    "offsetY",
    "rotation",
    "mirrorX",
    "mirrorY",
    "scaleX",
    "scaleY",
}

STYLE_ALIAS_MAP = {
    "font_size": "fontSize",
    "font_family": "fontFamily",
    "outer_stroke": "outerStroke",
    "no_tcy": "noTcy",
    "vertical_advance": "verticalAdvance",
    "pre_kerning": "preKerning",
    "line_kerning": "lineKerning",
    "next_kerning": "nextKerning",
    "blur_angle": "blurAngle",
    "blur_radial": "blurRadial",
    "blur_motion": "blurMotion",
    "blur_motion_distance": "blurMotionDistance",
    "ruby_inherit": "rubyInherit",
    "offset_x": "offsetX",
    "offset_y": "offsetY",
    "mirror_x": "mirrorX",
    "mirror_y": "mirrorY",
    "scale_x": "scaleX",
    "scale_y": "scaleY",
}


def normalize_hex_color(value: Any) -> Optional[str]:
    """Convert `#RGB`, `#RRGGBB`, or `[R, G, B]` into `#rrggbb`."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.startswith("#"):
            hex_part = s[1:]
            if len(hex_part) == 3 and all(c in "0123456789abcdefABCDEF" for c in hex_part):
                hex_part = "".join(ch * 2 for ch in hex_part)
            if len(hex_part) == 6 and all(c in "0123456789abcdefABCDEF" for c in hex_part):
                return f"#{hex_part.lower()}"
        raise ValueError(f"Invalid hex color {value!r}; expected '#rrggbb' or [R, G, B]")
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        r, g, b = (max(0, min(255, int(round(float(c))))) for c in value[:3])
        return f"#{r:02x}{g:02x}{b:02x}"
    raise ValueError(f"Unsupported color format: {value!r}")


def color_to_rgb_list(value: Any) -> Optional[list[int]]:
    hex_str = normalize_hex_color(value)
    if not hex_str:
        return None
    return [int(hex_str[1:3], 16), int(hex_str[3:5], 16), int(hex_str[5:7], 16)]


def plain_to_storage_br(text: str) -> str:
    """Convert multiline plain text (`\\n`) into MTUI storage format (`[BR]`)."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"(?:\[BR\]|<br\s*/?>|【BR】)", "\n", normalized, flags=re.IGNORECASE)
    return normalized.replace("\n", "[BR]")


def storage_br_to_plain(text: str) -> str:
    """Convert MTUI storage `translation` (`[BR]`) into clean `\\n`-separated text."""
    return _BR_RE.sub("\n", str(text or ""))


def _drop_none_or_default(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def validate_and_normalize_style(raw_style: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Validate and normalize a style dict strictly to `richtext.v1` camelCase protocol."""
    if not raw_style:
        return {}
    if not isinstance(raw_style, dict):
        raise TypeError("Rich text style must be a dict")

    s: dict[str, Any] = {}
    transform_extra: dict[str, Any] = {}
    for key, val in raw_style.items():
        mapped = STYLE_ALIAS_MAP.get(key, key)
        if mapped in ALLOWED_TRANSFORM_KEYS and mapped not in ALLOWED_STYLE_KEYS:
            transform_extra[mapped] = val
        elif mapped in ALLOWED_STYLE_KEYS:
            s[mapped] = val
        else:
            raise ValueError(f"Unknown richtext.v1 style field: {key!r}")

    out: dict[str, Any] = {}
    if bool(s.get("bold", False)):
        out["bold"] = True

    italic = s.get("italic", False)
    if isinstance(italic, bool):
        if italic:
            out["italic"] = True
    elif isinstance(italic, (int, float)) and math.isfinite(float(italic)) and float(italic) != 0.0:
        out["italic"] = float(italic)

    if bool(s.get("underline", False)):
        out["underline"] = True
    if bool(s.get("strikethrough", False)):
        out["strikethrough"] = True
    if bool(s.get("emphasis", False)):
        out["emphasis"] = True
    if bool(s.get("noTcy", False)):
        out["noTcy"] = True

    if s.get("color") is not None:
        out["color"] = normalize_hex_color(s["color"])

    if s.get("scale") is not None:
        scale_val = float(s["scale"])
        if scale_val > 0 and not math.isclose(scale_val, 1.0):
            out["scale"] = scale_val

    if s.get("fontSize") is not None:
        fs = float(s["fontSize"])
        if fs > 0:
            out["fontSize"] = fs

    if s.get("fontFamily"):
        out["fontFamily"] = str(s["fontFamily"]).strip()

    for stroke_key in ("stroke", "outerStroke"):
        st = s.get(stroke_key)
        if isinstance(st, dict) and st:
            st_out: dict[str, Any] = {}
            if st.get("color") is not None:
                st_out["color"] = normalize_hex_color(st["color"])
            if st.get("width") is not None:
                st_out["width"] = max(0.0, float(st["width"]))
            if st_out:
                out[stroke_key] = st_out

    glow = s.get("glow")
    if isinstance(glow, dict) and glow:
        glow_out: dict[str, Any] = {}
        if glow.get("color") is not None:
            glow_out["color"] = normalize_hex_color(glow["color"])
        if glow.get("blur") is not None:
            glow_out["blur"] = max(0.0, float(glow["blur"]))
        if glow_out:
            out["glow"] = glow_out

    if s.get("blur") is not None:
        blur_val = float(s["blur"])
        if not math.isfinite(blur_val):
            raise ValueError("blur must be finite")
        blur_clamped = min(max(0.0, blur_val), 1.0)
        if blur_clamped > 0.0:
            out["blur"] = blur_clamped

    if s.get("blurAngle") is not None:
        ba_val = float(s["blurAngle"])
        if not math.isfinite(ba_val):
            raise ValueError("blurAngle must be finite")
        out["blurAngle"] = ba_val % 360.0

    if bool(s.get("blurRadial", False)):
        out["blurRadial"] = True
    if bool(s.get("blurMotion", False)):
        out["blurMotion"] = True

    if s.get("blurMotionDistance") is not None:
        bmd_val = float(s["blurMotionDistance"])
        if not math.isfinite(bmd_val):
            raise ValueError("blurMotionDistance must be finite")
        bmd_clamped = min(max(0.0, bmd_val), 500.0)
        if bmd_clamped > 0.0:
            out["blurMotionDistance"] = bmd_clamped

    if "rubyInherit" in s and s["rubyInherit"] is not None:
        if not bool(s["rubyInherit"]):
            out["rubyInherit"] = False

    va = s.get("verticalAdvance")
    if va is not None:
        if va not in ("half", "full"):
            raise ValueError("verticalAdvance must be 'half' or 'full'")
        out["verticalAdvance"] = va

    for kern_key in ("kerning", "preKerning", "lineKerning", "nextKerning"):
        kv = s.get(kern_key)
        if kv is not None:
            kf = float(kv)
            if kern_key in ("lineKerning", "nextKerning") or kf != 0.0:
                out[kern_key] = kf

    raw_transform = dict(s.get("transform") or {})
    for tk, tv in list(raw_transform.items()):
        mapped_tk = STYLE_ALIAS_MAP.get(tk, tk)
        if mapped_tk != tk:
            raw_transform.pop(tk)
            raw_transform[mapped_tk] = tv
    raw_transform.update(transform_extra)

    if raw_transform:
        unknown_tf = set(raw_transform.keys()) - ALLOWED_TRANSFORM_KEYS
        if unknown_tf:
            raise ValueError(f"Unknown transform keys: {sorted(unknown_tf)}")
        tf_out: dict[str, Any] = {}
        for off_k in ("offsetX", "offsetY", "rotation"):
            if raw_transform.get(off_k) is not None:
                fv = float(raw_transform[off_k])
                if fv != 0.0:
                    tf_out[off_k] = fv
        for mir_k in ("mirrorX", "mirrorY"):
            if bool(raw_transform.get(mir_k, False)):
                tf_out[mir_k] = True
        for sc_k in ("scaleX", "scaleY"):
            if raw_transform.get(sc_k) is not None:
                sv = float(raw_transform[sc_k])
                if not math.isfinite(sv) or sv <= 0.0:
                    raise ValueError(f"transform.{sc_k} must be finite and > 0")
                if not math.isclose(sv, 1.0):
                    tf_out[sc_k] = sv
        if tf_out:
            out["transform"] = tf_out

    return out


def _merge_raw_style_patch(
    existing_style: Optional[dict[str, Any]],
    raw_patch: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Merge a raw style patch onto an existing style before default-stripping normalization.

    Mirrors ``desktop_qt_ui/editor/rich_text_editing.py:_merge_style`` so explicit resets
    (e.g. ``rubyInherit=True``, ``blur=0.0``, ``blurAngle=None``, ``bold=False``, ``scale=1.0``)
    cleanly clear prior non-default cell styles before ``validate_and_normalize_style`` runs.
    """
    merged = validate_and_normalize_style(existing_style)
    if not raw_patch:
        return merged
    if not isinstance(raw_patch, dict):
        raise TypeError("Rich text style must be a dict")

    tf_extra: dict[str, Any] = {}
    for key, val in raw_patch.items():
        mapped = STYLE_ALIAS_MAP.get(key, key)
        if mapped in ALLOWED_TRANSFORM_KEYS and mapped not in ALLOWED_STYLE_KEYS:
            tf_extra[mapped] = val
        elif mapped in ALLOWED_STYLE_KEYS:
            if mapped in ("stroke", "outerStroke", "glow", "transform"):
                if val is None:
                    merged.pop(mapped, None)
                    continue
                if not isinstance(val, dict):
                    raise TypeError(f"{mapped} style must be a dict or None")
                nested = copy.deepcopy(merged.get(mapped)) if isinstance(merged.get(mapped), dict) else {}
                for nk, nv in val.items():
                    mapped_nk = STYLE_ALIAS_MAP.get(nk, nk) if mapped == "transform" else nk
                    if mapped == "transform" and mapped_nk not in ALLOWED_TRANSFORM_KEYS:
                        raise ValueError(f"Unknown transform keys: {[nk]}")
                    if nv is None:
                        nested.pop(mapped_nk, None)
                    else:
                        nested[mapped_nk] = nv
                if nested:
                    merged[mapped] = nested
                else:
                    merged.pop(mapped, None)
            elif val is None:
                merged.pop(mapped, None)
            else:
                merged[mapped] = val
        else:
            raise ValueError(f"Unknown richtext.v1 style field: {key!r}")

    if tf_extra:
        nested_tf = copy.deepcopy(merged.get("transform")) if isinstance(merged.get("transform"), dict) else {}
        for tk, tv in tf_extra.items():
            if tv is None:
                nested_tf.pop(tk, None)
            else:
                nested_tf[tk] = tv
        if nested_tf:
            merged["transform"] = nested_tf
        else:
            merged.pop("transform", None)

    return validate_and_normalize_style(merged)


def is_rich_text_document(value: Any) -> bool:
    return isinstance(value, dict) and value.get("format") == RICH_TEXT_FORMAT and isinstance(value.get("blocks"), list)


def _validate_richtext_text_run(run: Any, field_path: str) -> dict[str, Any]:
    if not isinstance(run, dict):
        raise ValueError(f"{field_path} must be a text run dict, got {type(run).__name__}")
    unknown = set(run.keys()) - {"type", "text", "style"}
    if unknown:
        raise ValueError(f"{field_path} has unknown keys: {sorted(unknown)}")
    rtype = run.get("type", "text")
    if rtype != "text":
        raise ValueError(f"{field_path}.type must be 'text', got {rtype!r}")
    raw_txt = run.get("text", "")
    if not isinstance(raw_txt, str):
        raise ValueError(f"{field_path}.text must be a string, got {type(raw_txt).__name__}")
    raw_st = run.get("style")
    if raw_st is not None and not isinstance(raw_st, dict):
        raise ValueError(f"{field_path}.style must be a dict, got {type(raw_st).__name__}")
    try:
        norm_st = validate_and_normalize_style(raw_st)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_path}.style: {exc}") from exc
    return {"type": "text", "text": raw_txt, "style": norm_st}


def validate_richtext_document(doc: Any, *, field_name: str = "translation_rich") -> dict[str, Any]:
    """Strictly validate and normalize a `richtext.v1` document structure (`blocks`, `inlines`, and nested runs)."""
    if not isinstance(doc, dict):
        raise ValueError(f"{field_name} must be a richtext.v1 dict, got {type(doc).__name__}")
    if doc.get("format") != RICH_TEXT_FORMAT:
        raise ValueError(f"{field_name}.format must be {RICH_TEXT_FORMAT!r}, got {doc.get('format')!r}")
    unknown_top = set(doc.keys()) - {"format", "blocks"}
    if unknown_top:
        raise ValueError(f"{field_name} has unknown top-level keys: {sorted(unknown_top)}")
    blocks = doc.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError(f"{field_name}.blocks must be a list, got {type(blocks).__name__}")

    norm_blocks: list[dict[str, Any]] = []
    for b_idx, block in enumerate(blocks):
        b_path = f"{field_name}.blocks[{b_idx}]"
        if not isinstance(block, dict):
            raise ValueError(f"{b_path} must be a paragraph dict, got {type(block).__name__}")
        unknown_blk = set(block.keys()) - {"type", "inlines"}
        if unknown_blk:
            raise ValueError(f"{b_path} has unknown keys: {sorted(unknown_blk)}")
        btype = block.get("type", "paragraph")
        if btype != "paragraph":
            raise ValueError(f"{b_path}.type must be 'paragraph', got {btype!r}")
        inlines = block.get("inlines", [])
        if not isinstance(inlines, list):
            raise ValueError(f"{b_path}.inlines must be a list, got {type(inlines).__name__}")

        norm_inlines: list[dict[str, Any]] = []
        for i_idx, inline in enumerate(inlines):
            i_path = f"{b_path}.inlines[{i_idx}]"
            if not isinstance(inline, dict):
                raise ValueError(f"{i_path} must be an inline dict, got {type(inline).__name__}")
            itype = inline.get("type", "text")
            if itype == "text":
                norm_inlines.append(_validate_richtext_text_run(inline, i_path))
            elif itype == "tcy":
                unknown_tcy = set(inline.keys()) - {"type", "content"}
                if unknown_tcy:
                    raise ValueError(f"{i_path} has unknown keys: {sorted(unknown_tcy)}")
                content = inline.get("content")
                if not isinstance(content, list):
                    raise ValueError(f"{i_path}.content must be a list, got {type(content).__name__}")
                norm_content = [
                    _validate_richtext_text_run(r, f"{i_path}.content[{r_idx}]")
                    for r_idx, r in enumerate(content)
                ]
                norm_inlines.append({"type": "tcy", "content": norm_content})
            elif itype == "ruby":
                unknown_ruby = set(inline.keys()) - {"type", "base", "text"}
                if unknown_ruby:
                    raise ValueError(f"{i_path} has unknown keys: {sorted(unknown_ruby)}")
                base_runs = inline.get("base")
                ruby_runs = inline.get("text")
                if not isinstance(base_runs, list):
                    raise ValueError(f"{i_path}.base must be a list, got {type(base_runs).__name__}")
                if not isinstance(ruby_runs, list):
                    raise ValueError(f"{i_path}.text must be a list, got {type(ruby_runs).__name__}")
                norm_base = [
                    _validate_richtext_text_run(r, f"{i_path}.base[{r_idx}]")
                    for r_idx, r in enumerate(base_runs)
                ]
                norm_ruby = [
                    _validate_richtext_text_run(r, f"{i_path}.text[{r_idx}]")
                    for r_idx, r in enumerate(ruby_runs)
                ]
                norm_inlines.append({"type": "ruby", "base": norm_base, "text": norm_ruby})
            else:
                raise ValueError(f"{i_path}.type must be 'text', 'ruby', or 'tcy', got {itype!r}")

        norm_blocks.append({"type": "paragraph", "inlines": norm_inlines})

    return {"format": RICH_TEXT_FORMAT, "blocks": norm_blocks}


def plain_to_richtext_document(plain_text: str, base_style: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Build a valid `richtext.v1` document from multiline text (`\\n` or `[BR]`)."""
    norm_style = validate_and_normalize_style(base_style)
    clean_text = storage_br_to_plain(plain_text)
    lines = clean_text.split("\n")
    blocks: list[dict[str, Any]] = []
    for line in lines:
        inlines = [{"type": "text", "text": line, "style": copy.deepcopy(norm_style)}] if line else []
        blocks.append({"type": "paragraph", "inlines": inlines})
    return {"format": RICH_TEXT_FORMAT, "blocks": blocks}


@dataclass(slots=True)
class _CharCell:
    char: str
    style: dict[str, Any]
    node: Optional[dict[str, Any]]  # ruby or tcy dict reference


def _flatten_document(doc: dict[str, Any]) -> list[_CharCell]:
    cells: list[_CharCell] = []
    if not is_rich_text_document(doc):
        return cells
    blocks = doc.get("blocks", [])
    for b_idx, block in enumerate(blocks):
        inlines = block.get("inlines", []) if isinstance(block, dict) else []
        for inline in inlines:
            if not isinstance(inline, dict):
                continue
            itype = inline.get("type", "text")
            if itype == "ruby":
                norm_ruby_text = [
                    {
                        "type": "text",
                        "text": str(r.get("text", "")),
                        "style": validate_and_normalize_style(r.get("style")),
                    }
                    for r in inline.get("text", [])
                    if isinstance(r, dict)
                ]
                norm_inline = {"type": "ruby", "base": [], "text": norm_ruby_text}
                for run in inline.get("base", []):
                    st = validate_and_normalize_style(run.get("style")) if isinstance(run, dict) else {}
                    for ch in str(run.get("text", "")):
                        cells.append(_CharCell(ch, st, norm_inline))
            elif itype == "tcy":
                for run in inline.get("content", []):
                    st = validate_and_normalize_style(run.get("style")) if isinstance(run, dict) else {}
                    for ch in str(run.get("text", "")):
                        cells.append(_CharCell(ch, st, inline))
            elif itype == "text":
                st = validate_and_normalize_style(inline.get("style"))
                for ch in str(inline.get("text", "")):
                    cells.append(_CharCell(ch, st, None))
        if b_idx < len(blocks) - 1:
            cells.append(_CharCell("\n", {}, None))
    return cells


def _group_runs(cells: list[_CharCell], start: int, end: int) -> list[dict[str, Any]]:
    if start >= end:
        return []
    runs: list[dict[str, Any]] = []
    run_start = start
    cur_style = cells[start].style
    for i in range(start + 1, end):
        if cells[i].style == cur_style:
            continue
        runs.append(
            {
                "type": "text",
                "text": "".join(cells[k].char for k in range(run_start, i)),
                "style": copy.deepcopy(cur_style),
            }
        )
        run_start = i
        cur_style = cells[i].style
    runs.append(
        {
            "type": "text",
            "text": "".join(cells[k].char for k in range(run_start, end)),
            "style": copy.deepcopy(cur_style),
        }
    )
    return runs


def _rebuild_document(cells: list[_CharCell]) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    line_start = 0
    while True:
        line_end = line_start
        while line_end < len(cells) and cells[line_end].char != "\n":
            line_end += 1

        inlines: list[dict[str, Any]] = []
        idx = line_start
        while idx < line_end:
            node = cells[idx].node
            grp_start = idx
            idx += 1
            while idx < line_end and cells[idx].node is node:
                idx += 1
            runs = _group_runs(cells, grp_start, idx)
            if node is None:
                inlines.extend(runs)
            elif runs:
                if node.get("type") == "ruby":
                    ruby_runs = [
                        {
                            "type": "text",
                            "text": str(r.get("text", "")),
                            "style": validate_and_normalize_style(r.get("style")),
                        }
                        for r in node.get("text", [])
                        if isinstance(r, dict)
                    ]
                    inlines.append({"type": "ruby", "base": runs, "text": ruby_runs})
                elif node.get("type") == "tcy":
                    inlines.append({"type": "tcy", "content": runs})
        blocks.append({"type": "paragraph", "inlines": inlines})
        if line_end == len(cells):
            break
        line_start = line_end + 1
    return {"format": RICH_TEXT_FORMAT, "blocks": blocks}


def visible_text_of_region(region: dict[str, Any]) -> str:
    """Return visible multiline text (`\\n`) from a region, preferring `translation_rich`."""
    rich = region.get("translation_rich")
    if is_rich_text_document(rich):
        cells = _flatten_document(rich)
        return "".join(c.char for c in cells)
    return storage_br_to_plain(str(region.get("translation", "") or ""))


def document_has_nontrivial_styling(doc: Optional[dict[str, Any]], plain_storage_text: str) -> bool:
    """Return False if `doc` has zero styles/ruby/tcy and matches `plain_storage_text`."""
    if not is_rich_text_document(doc):
        return False
    for block in doc.get("blocks", []):
        if not isinstance(block, dict):
            continue
        inlines = block.get("inlines")
        if not isinstance(inlines, list):
            continue
        for inline in inlines:
            if not isinstance(inline, dict):
                continue
            itype = inline.get("type", "text")
            if itype in ("ruby", "tcy"):
                return True
            if inline.get("style"):
                return True
    cells = _flatten_document(doc)
    doc_plain = "".join(c.char for c in cells)
    return doc_plain != storage_br_to_plain(plain_storage_text)


def region_is_bold(region: dict[str, Any]) -> bool:
    """Return True if the region renders bold (via `richtext.v1` inline runs or `region['bold']`)."""
    rich = region.get("translation_rich")
    if is_rich_text_document(rich):
        cells = [c for c in _flatten_document(rich) if c.char != "\n" and not c.char.isspace()]
        if cells:
            return all(bool(c.style.get("bold", False)) for c in cells)
    return bool(region.get("bold", False))


def set_region_whole_bold(region: dict[str, Any], bold: bool) -> None:
    """Enable or disable whole-region bold in `richtext.v1` (`translation_rich`) + `region['bold']`."""
    storage_txt = str(region.get("translation", "") or plain_to_storage_br(str(region.get("text", "") or "")))
    rich = region.get("translation_rich")
    if bold:
        region["bold"] = True
        if is_rich_text_document(rich):
            cells = _flatten_document(rich)
            for c in cells:
                if c.char != "\n":
                    merged = copy.deepcopy(c.style)
                    merged["bold"] = True
                    c.style = validate_and_normalize_style(merged)
            region["translation_rich"] = _rebuild_document(cells)
        else:
            region["translation_rich"] = plain_to_richtext_document(storage_txt, base_style={"bold": True})
    else:
        region.pop("bold", None)
        if is_rich_text_document(rich):
            cells = _flatten_document(rich)
            for c in cells:
                if "bold" in c.style:
                    cleaned = copy.deepcopy(c.style)
                    cleaned.pop("bold", None)
                    c.style = validate_and_normalize_style(cleaned)
            rebuilt = _rebuild_document(cells)
            if document_has_nontrivial_styling(rebuilt, storage_txt):
                region["translation_rich"] = rebuilt
            else:
                region.pop("translation_rich", None)


def sync_region_text_update(
    region: dict[str, Any],
    new_text: str,
    *,
    clear_rich_text: bool = False,
) -> None:
    """Update `region['translation']` (and `translation_raw`) while preserving existing rich-text styles via minimal edit-op diff."""
    clean_new = storage_br_to_plain(new_text)
    storage_new = plain_to_storage_br(clean_new)
    was_whole_bold = region_is_bold(region)

    if clear_rich_text:
        region.pop("translation_rich", None)
        region.pop("bold", None)
        region["translation"] = storage_new
        region["translation_raw"] = storage_new
        return

    rich = region.get("translation_rich")
    if is_rich_text_document(rich):
        cells = _flatten_document(rich)
        old_plain = "".join(c.char for c in cells)
        if old_plain != clean_new:
            # Multi-span opcode diff (inspired by SequenceMatcher opcodes) so non-adjacent edits
            # preserve rich-text spans (Ruby, TCY, bold, color, stroke) on all surviving segments!
            matcher = SequenceMatcher(None, old_plain, clean_new, autojunk=False)
            updated_cells: list[_CharCell] = []
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    updated_cells.extend(cells[i1:i2])
                elif tag in ("replace", "insert"):
                    # Inherit style from replaced span if uniform, else from adjacent character
                    if tag == "replace" and i2 > i1:
                        non_nl = [c for c in cells[i1:i2] if c.char != "\n"]
                        inh_style = copy.deepcopy(non_nl[0].style) if non_nl else {}
                        same_node = non_nl[0].node if (non_nl and all(c.node is non_nl[0].node for c in non_nl)) else None
                    else:
                        ref_idx = i1 - 1 if i1 > 0 else (i1 if i1 < len(cells) else -1)
                        inh_style = (
                            copy.deepcopy(cells[ref_idx].style)
                            if (0 <= ref_idx < len(cells) and cells[ref_idx].char != "\n")
                            else {}
                        )
                        same_node = (
                            cells[ref_idx].node
                            if (0 < i1 < len(cells) and cells[i1 - 1].node is cells[i1].node)
                            else None
                        )
                    for ch in clean_new[j1:j2]:
                        cell_style = (
                            {}
                            if ch == "\n"
                            else (
                                strip_rotation_from_style(inh_style)
                                if ch in ALREADY_VERTICAL_GLYPHS
                                else copy.deepcopy(inh_style)
                            )
                        )
                        updated_cells.append(
                            _CharCell(
                                ch,
                                cell_style,
                                None if ch == "\n" else same_node,
                            )
                        )
                # tag == "delete": omit cells[i1:i2]
            new_doc = _rebuild_document(updated_cells)
            if document_has_nontrivial_styling(new_doc, storage_new):
                region["translation_rich"] = new_doc
            else:
                region.pop("translation_rich", None)

    region["translation"] = storage_new
    region["translation_raw"] = storage_new
    if was_whole_bold:
        set_region_whole_bold(region, True)


def sanitize_vertical_richtext_rotations(region: dict[str, Any]) -> bool:
    """Strip ``transform.rotation`` from any already-vertical presentation glyph in ``region['translation_rich']``."""
    rich = region.get("translation_rich")
    if not is_rich_text_document(rich):
        return False
    cells = _flatten_document(rich)
    changed = False
    for c in cells:
        if c.char in ALREADY_VERTICAL_GLYPHS and isinstance(c.style.get("transform"), dict) and "rotation" in c.style["transform"]:
            c.style = strip_rotation_from_style(c.style)
            changed = True
    if not changed:
        return False
    storage_txt = str(region.get("translation", "") or "")
    rebuilt = _rebuild_document(cells)
    if document_has_nontrivial_styling(rebuilt, storage_txt):
        region["translation_rich"] = rebuilt
    else:
        region.pop("translation_rich", None)
    return True


def apply_missing_glyph_inline_fallbacks(region: dict[str, Any], char_to_fallback_font: dict[str, str]) -> bool:
    """Assign inline ``style.fontFamily`` fallbacks to characters missing/empty in ``region['font_family']``, preserving bold/TCY/ruby."""
    if not char_to_fallback_font:
        return False
    storage_txt = str(region.get("translation", "") or plain_to_storage_br(str(region.get("text", "") or "")))
    rich = region.get("translation_rich")
    if is_rich_text_document(rich):
        base_doc = copy.deepcopy(rich)
    else:
        base_st = {"bold": True} if region_is_bold(region) else None
        base_doc = plain_to_richtext_document(storage_txt, base_style=base_st)
    cells = _flatten_document(base_doc)
    changed = False
    for c in cells:
        fb_ff = char_to_fallback_font.get(c.char)
        if fb_ff and not c.style.get("fontFamily"):
            merged = copy.deepcopy(c.style)
            merged["fontFamily"] = fb_ff
            c.style = validate_and_normalize_style(merged)
            changed = True
    if not changed:
        return False
    rebuilt = _rebuild_document(cells)
    if document_has_nontrivial_styling(rebuilt, storage_txt):
        region["translation_rich"] = rebuilt
    return True


def region_max_richtext_blur(region: dict[str, Any]) -> float:
    """Return the maximum ``richtext.v1`` inline ``blur`` (``0.0..1.0``) across all runs in ``region``."""
    rich = region.get("translation_rich")
    if not is_rich_text_document(rich):
        return 0.0
    max_b = 0.0
    for cell in _flatten_document(rich):
        b = cell.style.get("blur")
        if isinstance(b, (int, float)) and float(b) > max_b:
            max_b = float(b)
    return max_b


def apply_span_styles_to_region(
    region: dict[str, Any],
    *,
    whole_style: Optional[dict[str, Any]] = None,
    spans: Optional[list[dict[str, Any]]] = None,
    reset_existing: bool = False,
) -> dict[str, Any]:
    """Apply whole-region and/or targeted substring/regex/index spans (`style`, `ruby`, `tcy`) to a region."""
    rich = region.get("translation_rich")
    if reset_existing or not is_rich_text_document(rich):
        base_doc = plain_to_richtext_document(str(region.get("translation", "") or ""))
    else:
        base_doc = copy.deepcopy(rich)

    cells = _flatten_document(base_doc)
    full_text = "".join(c.char for c in cells)

    if whole_style is not None:
        if not isinstance(whole_style, dict):
            raise TypeError("Rich text style must be a dict")
        if whole_style:
            # Validate upfront even on empty text
            _merge_raw_style_patch({}, whole_style)
            for cell in cells:
                if cell.char != "\n":
                    cell.style = _merge_raw_style_patch(cell.style, whole_style)

    for span_spec in spans or []:
        if not isinstance(span_spec, dict):
            continue
        spec = copy.deepcopy(span_spec)
        match_str = spec.pop("match", None)
        regex_str = spec.pop("regex", None)
        start_idx = spec.pop("start", None)
        end_idx = spec.pop("end", None)
        occurrence = spec.pop("occurrence", None)  # Optional 0-based match index; None = all matches
        ruby_text = spec.pop("ruby", None)
        raw_ruby_style = spec.pop("ruby_style", None)
        ruby_style = validate_and_normalize_style(raw_ruby_style)
        ruby_inherit_flag = spec.pop("ruby_inherit", None)
        tcy_flag = spec.pop("tcy", None)
        raw_style_sub = spec.pop("style", None)
        raw_patch = raw_style_sub if raw_style_sub is not None else spec
        if raw_patch:
            _merge_raw_style_patch({}, raw_patch)

        explicit_ruby_inherit_in_patch = isinstance(raw_patch, dict) and (
            "rubyInherit" in raw_patch or "ruby_inherit" in raw_patch
        )

        ranges: list[tuple[int, int]] = []
        if start_idx is not None and end_idx is not None:
            s_i = max(0, min(int(start_idx), len(cells)))
            e_i = max(s_i, min(int(end_idx), len(cells)))
            ranges.append((s_i, e_i))
        elif match_str:
            needle = str(match_str)
            pos = 0
            hits: list[tuple[int, int]] = []
            while needle:
                found = full_text.find(needle, pos)
                if found < 0:
                    break
                hits.append((found, found + len(needle)))
                pos = found + len(needle)
            if occurrence is not None:
                occ_idx = int(occurrence)
                if 0 <= occ_idx < len(hits):
                    ranges.append(hits[occ_idx])
            else:
                ranges.extend(hits)
        elif regex_str:
            hits = [(m.start(), m.end()) for m in re.finditer(str(regex_str), full_text) if m.end() > m.start()]
            if occurrence is not None:
                occ_idx = int(occurrence)
                if 0 <= occ_idx < len(hits):
                    ranges.append(hits[occ_idx])
            else:
                ranges.extend(hits)

        has_nonempty_ruby = ruby_text is not None and bool(str(ruby_text))
        has_empty_ruby = ruby_text is not None and not bool(str(ruby_text))

        # Reject partial-node ruby_style patches so unmatched base chars of a multi-char ruby node are never mutated
        if raw_ruby_style is not None and ruby_text is None and tcy_flag is not True:
            covered_indices = {
                idx_c
                for r_start, r_end in ranges
                for idx_c in range(r_start, r_end)
                if cells[idx_c].char != "\n"
            }
            seen_check_nodes: set[int] = set()
            for idx_c in sorted(covered_indices):
                existing_node = cells[idx_c].node
                if isinstance(existing_node, dict) and existing_node.get("type") == "ruby":
                    nid = id(existing_node)
                    if nid in seen_check_nodes:
                        continue
                    seen_check_nodes.add(nid)
                    node_indices = [i for i, c in enumerate(cells) if c.node is existing_node]
                    if not all(i in covered_indices for i in node_indices):
                        base_str = "".join(cells[i].char for i in node_indices)
                        raise ValueError(
                            f"ruby_style span only partially covers ruby node {base_str!r}; "
                            "match the full ruby base text or set ruby explicitly on the target slice"
                        )

        mutated_ruby_nodes: set[int] = set()
        for r_start, r_end in ranges:
            # Split across line breaks so nodes never cross paragraphs
            seg_start = r_start
            while seg_start < r_end:
                while seg_start < r_end and cells[seg_start].char == "\n":
                    seg_start += 1
                if seg_start >= r_end:
                    break
                seg_end = seg_start
                while seg_end < r_end and cells[seg_end].char != "\n":
                    seg_end += 1

                new_node: Optional[dict[str, Any]] = None
                if has_nonempty_ruby:
                    new_node = {
                        "type": "ruby",
                        "base": [],
                        "text": [{"type": "text", "text": str(ruby_text), "style": copy.deepcopy(ruby_style)}],
                    }
                elif tcy_flag is True:
                    new_node = {"type": "tcy", "content": []}

                for k in range(seg_start, seg_end):
                    if raw_patch:
                        cells[k].style = _merge_raw_style_patch(cells[k].style, raw_patch)
                    if ruby_inherit_flag is not None:
                        cells[k].style = _merge_raw_style_patch(
                            cells[k].style,
                            {"rubyInherit": bool(ruby_inherit_flag)},
                        )
                    elif ruby_style and not explicit_ruby_inherit_in_patch:
                        # Upstream ruby_text_style_for() ignores ruby[0].style unless base style has rubyInherit=False
                        cells[k].style = _merge_raw_style_patch(cells[k].style, {"rubyInherit": False})

                    if has_nonempty_ruby or tcy_flag is True:
                        cells[k].node = new_node
                    else:
                        if has_empty_ruby and isinstance(cells[k].node, dict) and cells[k].node.get("type") == "ruby":
                            cells[k].node = None
                        if tcy_flag is False and isinstance(cells[k].node, dict) and cells[k].node.get("type") == "tcy":
                            cells[k].node = None
                        if (
                            raw_ruby_style is not None
                            and ruby_text is None
                            and isinstance(cells[k].node, dict)
                            and cells[k].node.get("type") == "ruby"
                        ):
                            nid = id(cells[k].node)
                            if nid not in mutated_ruby_nodes:
                                mutated_ruby_nodes.add(nid)
                                for r_run in cells[k].node.get("text", []):
                                    if isinstance(r_run, dict):
                                        r_run["style"] = _merge_raw_style_patch(r_run.get("style"), raw_ruby_style)

                seg_start = seg_end + 1

    final_doc = _rebuild_document(cells)
    storage_text = plain_to_storage_br(full_text)
    region["translation"] = storage_text
    if "translation_raw" not in region or not region["translation_raw"]:
        region["translation_raw"] = storage_text
    if document_has_nontrivial_styling(final_doc, storage_text):
        region["translation_rich"] = final_doc
    else:
        region.pop("translation_rich", None)

    non_ws_cells = [c for c in cells if c.char != "\n" and not c.char.isspace()]
    if non_ws_cells:
        if all(bool(c.style.get("bold", False)) for c in non_ws_cells):
            region["bold"] = True
        else:
            region.pop("bold", None)
    elif reset_existing or (isinstance(whole_style, dict) and "bold" in whole_style and not whole_style.get("bold")):
        region.pop("bold", None)
    elif isinstance(whole_style, dict) and whole_style.get("bold"):
        region["bold"] = True

    return final_doc


def apply_project_richtext_rules(
    region: dict[str, Any],
    rules_yaml_path: Optional[Path] = None,
) -> bool:
    """Apply `config/rich_text_rules.yaml` (e.g. vertical symbol rotation & `!?` TCY) to a region."""
    if rules_yaml_path is None or not rules_yaml_path.is_file():
        # Built-in MTUI default vertical rules fallback
        rules_data = {
            "common": [],
            "horizontal": [],
            "vertical": [
                {
                    "enabled": True,
                    "pattern": r'["\'ー⸺～﹏●•〖〗［］]',
                    "regex": True,
                    "style": {"transform": {"rotation": -90}},
                    "ruby": "",
                    "tcy": False,
                },
                {
                    "enabled": True,
                    "pattern": r"[!?！？]{2,4}",
                    "regex": True,
                    "style": {},
                    "ruby": "",
                    "tcy": True,
                },
            ],
        }
    else:
        import yaml

        with open(rules_yaml_path, "r", encoding="utf-8") as f:
            rules_data = yaml.safe_load(f) or {}

    direction = str(region.get("direction") or "auto").lower()
    is_vert = direction in ("v", "vertical", "vr")
    if direction == "auto":
        # Infer from region polygon aspect ratio just like TextBlock
        lines = region.get("lines") or []
        pts = [pt for poly in lines for pt in poly if isinstance(pt, (list, tuple)) and len(pt) >= 2]
        if pts:
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            is_vert = h > w * 1.15

    active_groups = list(rules_data.get("common") or []) + list(
        rules_data.get("vertical" if is_vert else "horizontal") or []
    )
    spans_to_apply: list[dict[str, Any]] = []
    for rule in active_groups:
        if not isinstance(rule, dict) or not rule.get("enabled", True):
            continue
        pat = rule.get("pattern", "")
        if not pat:
            continue
        spec: dict[str, Any] = {
            "regex" if rule.get("regex", False) else "match": pat,
            "style": rule.get("style") or {},
        }
        if rule.get("ruby"):
            spec["ruby"] = str(rule["ruby"])
        if rule.get("tcy"):
            spec["tcy"] = True
        spans_to_apply.append(spec)

    if not spans_to_apply:
        return False
    apply_span_styles_to_region(region, spans=spans_to_apply, reset_existing=False)
    return True
