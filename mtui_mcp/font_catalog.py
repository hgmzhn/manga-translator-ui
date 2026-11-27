"""Font catalog & CJK glyph-coverage validator for manga-translator-ui."""

from __future__ import annotations

import logging
import os
import sys
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from fontTools.ttLib import TTCollection, TTFont

from .config import get_runtime_settings

logger = logging.getLogger("mtui_mcp.fonts")
logging.getLogger("fontTools").setLevel(logging.ERROR)

FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc")


def strip_qt_foundry_brackets(family: str) -> str:
    """Match manga_translator.rendering.text_render._fonts.strip_qt_foundry_brackets."""
    return (family or "").replace("[", "").replace("]", "").strip()


@dataclass(frozen=True)
class FontEntry:
    """Metadata for an available font family."""

    qt_family: str
    display_name: str
    style: str
    selector: str
    file_path: str
    source: str  # "project" | "system"
    supports_cjk: bool


def _system_font_dirs() -> list[Path]:
    dirs: list[Path] = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(Path(windir) / "Fonts")
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            dirs.append(Path(local_app) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs.extend([Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library" / "Fonts"])
    else:
        dirs.extend(
            [
                Path("/usr/share/fonts"),
                Path("/usr/local/share/fonts"),
                Path.home() / ".local" / "share" / "fonts",
                Path.home() / ".fonts",
            ]
        )
    return [d for d in dirs if d.is_dir()]


_WHITESPACE_CPS = frozenset(
    {
        0x0020,
        0x00A0,
        0x2000,
        0x2001,
        0x2002,
        0x2003,
        0x2004,
        0x2005,
        0x2006,
        0x2007,
        0x2008,
        0x2009,
        0x200A,
        0x200B,
        0x3000,
        0xFEFF,
    }
)


def _extract_face_names_from_ttfont(tt: TTFont, file_path: Path) -> tuple[str, str, str]:
    """Extract `(qt_family, display_name, style_name)` reading ONLY the lightweight `name` table."""
    name_table = tt.get("name")
    en_family = ""
    zh_family = ""
    style_name = "Regular"

    if name_table is not None:
        for rec in name_table.names:
            if rec.nameID not in (1, 2, 16, 17):
                continue
            try:
                val = rec.toUnicode().strip()
            except Exception:
                continue
            if not val:
                continue
            is_cjk_lang = rec.langID in (0x0804, 0x0404, 0x0C04, 0x0411, 0x0412)
            if rec.nameID in (16, 1):
                if is_cjk_lang and not zh_family:
                    zh_family = val
                elif rec.langID == 0x0409 and (rec.nameID == 16 or not en_family):
                    en_family = val
                elif not en_family:
                    en_family = val
            elif rec.nameID in (17, 2) and rec.langID == 0x0409:
                style_name = val

    raw_primary = en_family or zh_family or file_path.stem
    qt_family = strip_qt_foundry_brackets(raw_primary)
    display_name = zh_family or raw_primary
    if zh_family and en_family and zh_family != en_family:
        display_name = f"{zh_family} ({en_family})"
    return qt_family, display_name, style_name


def _extract_faces_from_ttfont(tt: TTFont, file_path: Path, source: str) -> tuple[list[FontEntry], set[int]]:
    qt_family, display_name, style_name = _extract_face_names_from_ttfont(tt, file_path)
    codepoints: set[int] = set()
    try:
        cmap = tt.getBestCmap()
        if cmap:
            q_gids = {cmap.get(0x003F), cmap.get(0xFF1F), ".notdef"}
            q_gids.discard(None)
            has_glyf = ("loca" in tt) and ("glyf" in tt)
            locs = tt["loca"].locations if has_glyf else None
            glyf_tbl = tt["glyf"] if has_glyf else None
            gname_to_id = {g: i for i, g in enumerate(tt.getGlyphOrder())} if has_glyf else {}

            for cp, gname in cmap.items():
                if cp in _WHITESPACE_CPS:
                    codepoints.add(cp)
                    continue
                if not gname or gname == ".notdef":
                    continue
                if cp not in (0x003F, 0xFF1F) and gname in q_gids:
                    continue
                if locs is not None:
                    gid_idx = gname_to_id.get(gname)
                    if gid_idx is not None and (gid_idx + 1) < len(locs):
                        byte_len = int(locs[gid_idx + 1]) - int(locs[gid_idx])
                        # TrueType empty glyph slots (e.g. `文鼎古印体.ttf` `﹁﹂︻︼︵︶︴`, `SoukouMincho` `—·‼⁉`) have 0 bytes / 0 contours
                        if byte_len <= 0:
                            continue
                        if byte_len <= 12 and glyf_tbl is not None:
                            g_obj = glyf_tbl[gname]
                            if g_obj.numberOfContours == 0 and not g_obj.isComposite():
                                continue
                codepoints.add(cp)
    except Exception:
        pass

    supports_cjk = any(0x4E00 <= cp <= 0x9FFF for cp in (0x7684, 0x4E00, 0x662F, 0x6211, 0x4F60) if cp in codepoints)
    if not supports_cjk and codepoints:
        # Fallback check for Japanese/Traditional CJK range
        supports_cjk = any(cp in codepoints for cp in (0x3042, 0x6F22, 0x8A9E, 0x6771))

    selector = qt_family
    if style_name and style_name.lower() not in ("regular", "normal", "book", "roman"):
        selector = f"{qt_family}::{style_name}"

    entry = FontEntry(
        qt_family=qt_family,
        display_name=display_name,
        style=style_name,
        selector=selector,
        file_path=str(file_path),
        source=source,
        supports_cjk=supports_cjk,
    )
    return [entry], codepoints


@lru_cache(maxsize=4096)
def _match_font_names_only(file_path_str: str, mtime_ns: int) -> tuple[tuple[str, str], ...]:
    """Read ONLY the `name` table (never `cmap`/`loca`/`glyf`) for fast font-family matching."""
    _ = mtime_ns
    fp = Path(file_path_str)
    pairs: list[tuple[str, str]] = []
    try:
        if fp.suffix.lower() in (".ttc", ".otc"):
            ttc = TTCollection(fp, lazy=True)
            try:
                for font in ttc.fonts[:8]:
                    qt_fam, disp, _ = _extract_face_names_from_ttfont(font, fp)
                    pairs.append((qt_fam.casefold(), disp.casefold()))
            finally:
                ttc.close()
        else:
            tt = TTFont(fp, lazy=True)
            try:
                qt_fam, disp, _ = _extract_face_names_from_ttfont(tt, fp)
                pairs.append((qt_fam.casefold(), disp.casefold()))
            finally:
                tt.close()
    except Exception as exc:
        logger.debug("Skipping unreadable font header %s: %s", fp, exc)
    return tuple(pairs)


@lru_cache(maxsize=4096)
def _inspect_font_file(file_path_str: str, mtime_ns: int, source: str) -> tuple[tuple[FontEntry, ...], frozenset[int]]:
    _ = mtime_ns
    fp = Path(file_path_str)
    entries: list[FontEntry] = []
    all_cps: set[int] = set()
    try:
        if fp.suffix.lower() in (".ttc", ".otc"):
            ttc = TTCollection(fp, lazy=True)
            try:
                for font in ttc.fonts[:8]:
                    sub_entries, cps = _extract_faces_from_ttfont(font, fp, source)
                    entries.extend(sub_entries)
                    all_cps.update(cps)
            finally:
                ttc.close()
        else:
            tt = TTFont(fp, lazy=True)
            try:
                sub_entries, cps = _extract_faces_from_ttfont(tt, fp, source)
                entries.extend(sub_entries)
                all_cps.update(cps)
            finally:
                tt.close()
    except Exception as exc:
        logger.debug("Skipping unreadable font %s: %s", fp, exc)
    return tuple(entries), frozenset(all_cps)


def _project_fonts_signature(fonts_dir: Optional[Path]) -> tuple[str, int, int]:
    if fonts_dir is None or not fonts_dir.is_dir():
        return ("", 0, 0)
    count = 0
    mtime_xor = 0
    try:
        for root, _, files in os.walk(fonts_dir):
            for fname in files:
                if fname.lower().endswith(FONT_EXTENSIONS):
                    count += 1
                    try:
                        mtime_xor ^= (Path(root) / fname).stat().st_mtime_ns
                    except OSError:
                        pass
    except OSError:
        pass
    return (str(fonts_dir), count, mtime_xor)


@lru_cache(maxsize=2048)
def _resolve_target_font_codepoints(
    fonts_dir_str: str,
    project_count: int,
    project_mtime_xor: int,
    include_system: bool,
    target_norm: str,
) -> Optional[frozenset[int]]:
    _ = (project_count, project_mtime_xor)
    search_dirs: list[tuple[Path, str]] = []
    if fonts_dir_str:
        p_dir = Path(fonts_dir_str)
        if p_dir.is_dir():
            search_dirs.append((p_dir, "project"))
    if include_system:
        for sdir in _system_font_dirs():
            search_dirs.append((sdir, "system"))

    for base_dir, src_kind in search_dirs:
        try:
            candidates = list(base_dir.rglob("*")) if src_kind == "project" else list(base_dir.iterdir())
        except OSError:
            continue
        for fp in candidates:
            if not fp.is_file() or not fp.name.lower().endswith(FONT_EXTENSIONS):
                continue
            try:
                st = fp.stat()
            except OSError:
                continue
            matched_file = fp.stem.casefold() == target_norm
            if not matched_file:
                for qt_cf, disp_cf in _match_font_names_only(str(fp), st.st_mtime_ns):
                    if qt_cf == target_norm or target_norm in disp_cf:
                        matched_file = True
                        break
            if matched_file:
                _, cps = _inspect_font_file(str(fp), st.st_mtime_ns, src_kind)
                return cps
    return None


def list_available_fonts(
    *,
    include_system: bool = False,
    cjk_only: bool = False,
    query: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List fonts in `MTUI_ROOT/fonts/` and optionally system fonts."""
    settings = get_runtime_settings()
    results: list[FontEntry] = []

    if settings.fonts_dir is not None and settings.fonts_dir.is_dir():
        for root, _, files in os.walk(settings.fonts_dir):
            for fname in sorted(files):
                if not fname.lower().endswith(FONT_EXTENSIONS):
                    continue
                fpath = Path(root) / fname
                try:
                    st = fpath.stat()
                except OSError:
                    continue
                entries, _ = _inspect_font_file(str(fpath), st.st_mtime_ns, "project")
                results.extend(entries)

    if include_system:
        for sys_dir in _system_font_dirs():
            try:
                dir_entries = sorted(sys_dir.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                continue
            for fpath in dir_entries:
                if not fpath.is_file() or not fpath.name.lower().endswith(FONT_EXTENSIONS):
                    continue
                try:
                    st = fpath.stat()
                except OSError:
                    continue
                entries, _ = _inspect_font_file(str(fpath), st.st_mtime_ns, "system")
                results.extend(entries)

    q_norm = unicodedata.normalize("NFKC", query or "").casefold().strip()
    dedup: dict[str, FontEntry] = {}
    for item in results:
        if cjk_only and not item.supports_cjk:
            continue
        if q_norm:
            haystack = unicodedata.normalize(
                "NFKC", f"{item.qt_family} {item.display_name} {Path(item.file_path).name}"
            ).casefold()
            if q_norm not in haystack:
                continue
        key = item.selector.casefold()
        # Project fonts take priority over system fonts
        if key not in dedup or (dedup[key].source != "project" and item.source == "project"):
            dedup[key] = item

    sorted_items = sorted(
        dedup.values(),
        key=lambda e: (0 if e.source == "project" else 1, e.display_name.casefold()),
    )[: max(1, limit)]

    return [
        {
            "font_family": e.qt_family,
            "selector": e.selector,
            "display_name": e.display_name,
            "style": e.style,
            "source": e.source,
            "supports_cjk": e.supports_cjk,
            "file_name": Path(e.file_path).name,
        }
        for e in sorted_items
    ]


def check_missing_glyphs(
    font_family_or_selector: str,
    texts: str | Iterable[str],
    *,
    include_system: Optional[bool] = None,
) -> list[str]:
    """Return sorted unique printable characters in ``texts`` missing from the target font's cmap."""
    if not font_family_or_selector:
        return []
    family_only = font_family_or_selector.split("::")[0].strip()
    target_norm = strip_qt_foundry_brackets(family_only).casefold()
    if not target_norm:
        return []

    if include_system is None:
        include_system = not bool(os.environ.get("PYTEST_CURRENT_TEST"))

    settings = get_runtime_settings()
    f_dir_str, f_count, f_mtime_xor = _project_fonts_signature(settings.fonts_dir)
    matched_cps = _resolve_target_font_codepoints(
        f_dir_str,
        f_count,
        f_mtime_xor,
        include_system,
        target_norm,
    )

    if matched_cps is None:
        return []

    combined = texts if isinstance(texts, str) else "".join(texts)
    missing: set[str] = set()
    for ch in combined:
        if ch in ("\n", "\r", "\t", " ", "\u3000"):
            continue
        if unicodedata.category(ch).startswith("C"):
            continue
        if ord(ch) not in matched_cps:
            missing.add(ch)
    return sorted(missing)


def check_region_missing_glyphs(region: dict, default_font_family: str = "") -> list[str]:
    """Check missing glyphs in `region`, honoring per-inline `style.fontFamily` overrides in `translation_rich`."""
    base_ff = str(region.get("font_family") or default_font_family).strip()
    rich = region.get("translation_rich")
    if isinstance(rich, dict) and isinstance(rich.get("blocks"), list):
        missing: set[str] = set()

        def _walk_inlines(nodes: list) -> None:
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                ntype = node.get("type")
                if ntype == "text":
                    txt = str(node.get("text") or "")
                    st = node.get("style") if isinstance(node.get("style"), dict) else {}
                    run_ff = str(st.get("fontFamily") or base_ff).strip()
                    if run_ff and txt:
                        missing.update(check_missing_glyphs(run_ff, txt))
                elif ntype == "tcy":
                    _walk_inlines(node.get("content") or [])
                elif ntype == "ruby":
                    _walk_inlines(node.get("base") or [])
                    _walk_inlines(node.get("text") or [])

        for blk in rich.get("blocks") or []:
            if isinstance(blk, dict):
                _walk_inlines(blk.get("inlines") or [])
        return sorted(missing)

    from .richtext_builder import visible_text_of_region

    vis = visible_text_of_region(region)
    return check_missing_glyphs(base_ff, vis) if (base_ff and vis) else []
