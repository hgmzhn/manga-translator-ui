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
_DEFAULT_REGULAR_STYLES = frozenset({"regular", "normal", "book", "roman"})


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


def _iter_font_files(base_dir: Path) -> list[tuple[Path, int, int]]:
    """Recursively enumerate font files and their ``(st_size, st_mtime_ns)`` under ``base_dir`` in deterministic order."""
    out: list[tuple[Path, int, int]] = []
    visited_dirs: set[str] = set()

    def _walk(cur_dir: Path) -> None:
        try:
            real_key = os.path.normcase(os.path.realpath(cur_dir))
        except OSError:
            return
        if real_key in visited_dirs:
            return
        visited_dirs.add(real_key)

        try:
            with os.scandir(cur_dir) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            return

        subdirs: list[Path] = []
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=True):
                    subdirs.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=True) and entry.name.lower().endswith(FONT_EXTENSIONS):
                    st = entry.stat(follow_symlinks=True)
                    out.append((Path(entry.path), int(st.st_size), int(st.st_mtime_ns)))
            except OSError:
                continue

        for sub in subdirs:
            _walk(sub)

    if base_dir.is_dir():
        _walk(base_dir)
    return out


def _extract_face_names_from_ttfont(tt: TTFont, file_path: Path) -> tuple[str, str, str, str, str]:
    """Extract ``(qt_family, zh_family, display_name, style_name, selector)`` reading ONLY the ``name`` table."""
    name_table = tt.get("name")
    en_family = ""
    zh_family = ""
    en_style = ""
    fallback_style = ""

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
            elif rec.nameID in (17, 2):
                if rec.langID == 0x0409 and (rec.nameID == 17 or not en_style):
                    en_style = val
                elif not fallback_style:
                    fallback_style = val

    style_name = en_style or fallback_style or "Regular"
    raw_primary = en_family or zh_family or file_path.stem
    qt_family = strip_qt_foundry_brackets(raw_primary)
    zh_clean = strip_qt_foundry_brackets(zh_family) if zh_family else ""
    display_name = zh_family or raw_primary
    if zh_family and en_family and zh_family != en_family:
        display_name = f"{zh_family} ({en_family})"

    selector = qt_family
    if style_name and style_name.lower() not in _DEFAULT_REGULAR_STYLES:
        selector = f"{qt_family}::{style_name}"

    return qt_family, zh_clean, display_name, style_name, selector


def _extract_face_from_ttfont(
    tt: TTFont,
    file_path: Path,
    source: str,
) -> tuple[FontEntry, Optional[frozenset[int]]]:
    """Extract a single ``FontEntry`` and its isolated codepoint coverage (``None`` if ``cmap`` is unreadable)."""
    qt_family, _, display_name, style_name, selector = _extract_face_names_from_ttfont(tt, file_path)
    coverage: Optional[frozenset[int]] = None

    try:
        cmap = tt.getBestCmap()
    except Exception as exc:
        logger.debug("Unreadable cmap in %s (%s): %s", file_path, selector, exc)
        cmap = None

    if cmap is not None:
        codepoints: set[int] = set()
        q_gids = {cmap.get(0x003F), cmap.get(0xFF1F), ".notdef"}
        q_gids.discard(None)

        locs = None
        glyf_tbl = None
        gname_to_id: dict[str, int] = {}
        try:
            if ("loca" in tt) and ("glyf" in tt):
                locs = tt["loca"].locations
                glyf_tbl = tt["glyf"]
                gname_to_id = {g: i for i, g in enumerate(tt.getGlyphOrder())}
        except Exception as exc:
            logger.debug("Skipping loca/glyf table header in %s: %s", file_path, exc)
            locs = None
            glyf_tbl = None
            gname_to_id = {}

        for cp, gname in cmap.items():
            try:
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
            except Exception as exc:
                logger.debug("Skipping malformed glyph %r (U+%04X) in %s: %s", gname, cp, file_path, exc)
                continue

        coverage = frozenset(codepoints)

    supports_cjk = False
    if coverage:
        supports_cjk = any(0x4E00 <= cp <= 0x9FFF for cp in (0x7684, 0x4E00, 0x662F, 0x6211, 0x4F60) if cp in coverage)
        if not supports_cjk:
            # Fallback check for Japanese/Traditional CJK range
            supports_cjk = any(cp in coverage for cp in (0x3042, 0x6F22, 0x8A9E, 0x6771))

    entry = FontEntry(
        qt_family=qt_family,
        display_name=display_name,
        style=style_name,
        selector=selector,
        file_path=str(file_path),
        source=source,
        supports_cjk=supports_cjk,
    )
    return entry, coverage


_FontDirFileSig = tuple[str, str, int, int]
_FontDirSig = tuple[str, tuple[_FontDirFileSig, ...]]


@lru_cache(maxsize=4096)
def _match_font_names_only(
    file_path_str: str,
    file_size: int,
    mtime_ns: int,
) -> tuple[tuple[int, str, str, str, str, str], ...]:
    """Read ONLY the ``name`` table (never ``cmap``/``loca``/``glyf``) for fast per-face matching."""
    _ = (file_size, mtime_ns)
    fp = Path(file_path_str)
    faces: list[tuple[int, str, str, str, str, str]] = []
    try:
        if fp.suffix.lower() in (".ttc", ".otc"):
            ttc = TTCollection(fp, lazy=True)
            try:
                for face_idx, font in enumerate(ttc.fonts[:64]):
                    try:
                        qt_fam, zh_fam, disp, style, sel = _extract_face_names_from_ttfont(font, fp)
                        faces.append(
                            (face_idx, qt_fam.casefold(), zh_fam.casefold(), disp.casefold(), style.casefold(), sel.casefold())
                        )
                    except Exception as exc:
                        logger.debug("Skipping unreadable collection face %d header in %s: %s", face_idx, fp, exc)
            finally:
                ttc.close()
        else:
            tt = TTFont(fp, lazy=True)
            try:
                qt_fam, zh_fam, disp, style, sel = _extract_face_names_from_ttfont(tt, fp)
                faces.append((0, qt_fam.casefold(), zh_fam.casefold(), disp.casefold(), style.casefold(), sel.casefold()))
            finally:
                tt.close()
    except Exception as exc:
        logger.debug("Skipping unreadable font header %s: %s", fp, exc)
    return tuple(faces)


@lru_cache(maxsize=4096)
def _inspect_font_file(
    file_path_str: str,
    file_size: int,
    mtime_ns: int,
    source: str,
) -> tuple[tuple[int, FontEntry, Optional[frozenset[int]]], ...]:
    """Inspect all faces in a font file, preserving original face index and isolated per-face glyph coverage."""
    _ = (file_size, mtime_ns)
    fp = Path(file_path_str)
    face_records: list[tuple[int, FontEntry, Optional[frozenset[int]]]] = []
    try:
        if fp.suffix.lower() in (".ttc", ".otc"):
            ttc = TTCollection(fp, lazy=True)
            try:
                for face_idx, font in enumerate(ttc.fonts[:64]):
                    try:
                        entry, cov = _extract_face_from_ttfont(font, fp, source)
                        face_records.append((face_idx, entry, cov))
                    except Exception as exc:
                        logger.debug("Skipping unreadable collection face %d in %s: %s", face_idx, fp, exc)
            finally:
                ttc.close()
        else:
            tt = TTFont(fp, lazy=True)
            try:
                entry, cov = _extract_face_from_ttfont(tt, fp, source)
                face_records.append((0, entry, cov))
            finally:
                tt.close()
    except Exception as exc:
        logger.debug("Skipping unreadable font %s: %s", fp, exc)
    return tuple(face_records)


def _fonts_dirs_signature(dirs: Iterable[Path]) -> tuple[_FontDirSig, ...]:
    sigs: list[_FontDirSig] = []
    for d in dirs:
        if d is None or not d.is_dir():
            continue
        files = _iter_font_files(d)
        file_sigs = tuple(
            sorted(
                (os.path.normcase(os.path.abspath(str(fp))), str(fp), fsize, mtime_ns)
                for fp, fsize, mtime_ns in files
            )
        )
        sigs.append((os.path.normcase(os.path.abspath(str(d))), file_sigs))
    return tuple(sigs)


def _parse_font_spec(font_family_or_selector: str) -> tuple[str, Optional[str]]:
    raw = (font_family_or_selector or "").strip()
    if not raw:
        return "", None
    if "::" in raw:
        fam_part, style_part = raw.split("::", 1)
        explicit_style = style_part.strip().casefold() or None
    else:
        fam_part = raw
        explicit_style = None
    target_fam = strip_qt_foundry_brackets(fam_part.strip()).casefold()
    return target_fam, explicit_style


def _face_match_rank(
    *,
    qt_cf: str,
    zh_cf: str,
    disp_cf: str,
    style_cf: str,
    sel_cf: str,
    stem_cf: str,
    source_rank: int,
    target_fam: str,
    explicit_style: Optional[str],
) -> Optional[tuple[int, int, int]]:
    if explicit_style is not None:
        full_sel = f"{target_fam}::{explicit_style}"
        if style_cf == explicit_style or sel_cf == full_sel:
            style_rank = 0
        elif explicit_style in _DEFAULT_REGULAR_STYLES and style_cf in _DEFAULT_REGULAR_STYLES:
            style_rank = 1
        else:
            return None
    else:
        style_rank = 0 if style_cf in _DEFAULT_REGULAR_STYLES else 1

    if qt_cf == target_fam or disp_cf == target_fam or (zh_cf and zh_cf == target_fam):
        name_tier = 0
    elif stem_cf == target_fam:
        name_tier = 1
    elif target_fam in disp_cf:
        name_tier = 2
    else:
        return None

    return (name_tier, source_rank, style_rank)


@lru_cache(maxsize=2048)
def _resolve_target_font_codepoints(
    project_sig: tuple[_FontDirSig, ...],
    system_sig: tuple[_FontDirSig, ...],
    include_system: bool,
    target_fam: str,
    explicit_style: Optional[str],
) -> Optional[frozenset[int]]:
    if not target_fam:
        return None

    candidates: list[tuple[tuple[int, int, int], int, str, int, int, str, int]] = []
    order_seq = 0

    def _collect_from_dirs(dir_sigs: tuple[_FontDirSig, ...], src_kind: str, src_rank: int) -> bool:
        nonlocal order_seq
        for _, file_sigs in dir_sigs:
            for _, fp_str, fsize, mtime_ns in file_sigs:
                stem_cf = Path(fp_str).stem.casefold()
                face_names = _match_font_names_only(fp_str, fsize, mtime_ns)
                for face_idx, qt_cf, zh_cf, disp_cf, style_cf, sel_cf in face_names:
                    rank = _face_match_rank(
                        qt_cf=qt_cf,
                        zh_cf=zh_cf,
                        disp_cf=disp_cf,
                        style_cf=style_cf,
                        sel_cf=sel_cf,
                        stem_cf=stem_cf,
                        source_rank=src_rank,
                        target_fam=target_fam,
                        explicit_style=explicit_style,
                    )
                    if rank is not None:
                        candidates.append((rank, order_seq, fp_str, fsize, mtime_ns, src_kind, face_idx))
                        order_seq += 1
                        if rank == (0, 0, 0):
                            return True
        return False

    found_best_project = _collect_from_dirs(project_sig, "project", 0)
    if include_system and not found_best_project:
        _collect_from_dirs(system_sig, "system", 1)

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    _, _, best_fp_str, best_fsize, best_mtime_ns, best_src_kind, best_face_idx = candidates[0]
    inspected_faces = _inspect_font_file(best_fp_str, best_fsize, best_mtime_ns, best_src_kind)
    for f_idx, _, face_cps in inspected_faces:
        if f_idx == best_face_idx:
            return face_cps
    return None


def _lookup_font_codepoints(
    font_family_or_selector: str,
    *,
    include_system: Optional[bool] = None,
) -> Optional[frozenset[int]]:
    target_fam, explicit_style = _parse_font_spec(font_family_or_selector)
    if not target_fam:
        return None

    if include_system is None:
        include_system = not bool(os.environ.get("PYTEST_CURRENT_TEST"))

    settings = get_runtime_settings()
    project_dirs = [settings.fonts_dir] if (settings.fonts_dir is not None and settings.fonts_dir.is_dir()) else []
    project_sig = _fonts_dirs_signature(project_dirs)
    system_sig = _fonts_dirs_signature(_system_font_dirs()) if include_system else ()

    return _resolve_target_font_codepoints(
        project_sig,
        system_sig,
        bool(include_system),
        target_fam,
        explicit_style,
    )


def has_font(font_family: str, *, include_system: Optional[bool] = None) -> bool:
    """Return ``True`` iff ``font_family`` or ``Family::Style`` resolves to an installed font face with readable coverage."""
    return _lookup_font_codepoints(font_family, include_system=include_system) is not None


def list_available_fonts(
    *,
    include_system: bool = False,
    cjk_only: bool = False,
    query: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List fonts in ``MTUI_ROOT/fonts/`` and optionally system fonts (including subdirectories)."""
    settings = get_runtime_settings()
    results: list[FontEntry] = []
    q_norm = unicodedata.normalize("NFKC", query or "").casefold().strip()

    def _scan_dir(base_dir: Path, source_kind: str) -> None:
        for fpath, fsize, mtime_ns in _iter_font_files(base_dir):
            if q_norm:
                face_names = _match_font_names_only(str(fpath), fsize, mtime_ns)
                if face_names and not any(
                    q_norm in unicodedata.normalize("NFKC", f"{qt_cf} {disp_cf} {fpath.name}").casefold()
                    for _, qt_cf, _, disp_cf, _, _ in face_names
                ):
                    continue
            for _, entry, _ in _inspect_font_file(str(fpath), fsize, mtime_ns, source_kind):
                results.append(entry)

    if settings.fonts_dir is not None and settings.fonts_dir.is_dir():
        _scan_dir(settings.fonts_dir, "project")

    if include_system:
        for sys_dir in _system_font_dirs():
            _scan_dir(sys_dir, "system")

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
    """Return sorted unique printable characters in ``texts`` missing from the target font face's cmap."""
    matched_cps = _lookup_font_codepoints(font_family_or_selector, include_system=include_system)
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


def check_region_missing_glyphs_by_font(
    region: dict[str, Any],
    default_font_family: str = "",
    *,
    include_system: Optional[bool] = None,
) -> dict[str, list[str]]:
    """Return ``{font_selector: sorted_missing_chars}`` across ``region``, honoring inline ``style.fontFamily`` spans."""
    base_ff = str(region.get("font_family") or default_font_family).strip()
    rich = region.get("translation_rich")
    if isinstance(rich, dict) and isinstance(rich.get("blocks"), list):
        chars_by_font: dict[str, set[str]] = {}

        def _walk_inlines(nodes: Any, inherited_ff: str, *, force_ff: Optional[str] = None) -> None:
            if not isinstance(nodes, list):
                return
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                ntype = node.get("type", "text")
                st = node.get("style") if isinstance(node.get("style"), dict) else {}
                run_ff = force_ff if force_ff is not None else str(st.get("fontFamily") or inherited_ff).strip()
                if ntype == "text":
                    txt = str(node.get("text") or "")
                    if run_ff and txt:
                        chars_by_font.setdefault(run_ff, set()).update(txt)
                elif ntype == "tcy":
                    _walk_inlines(node.get("content") or [], run_ff, force_ff=force_ff)
                elif ntype == "ruby":
                    base_nodes = node.get("base") or []
                    first_base = (
                        base_nodes[0]
                        if isinstance(base_nodes, list) and base_nodes and isinstance(base_nodes[0], dict)
                        else {}
                    )
                    base_st = first_base.get("style") if isinstance(first_base.get("style"), dict) else {}
                    ruby_runs = node.get("text") or []
                    first_ruby = (
                        ruby_runs[0]
                        if isinstance(ruby_runs, list) and ruby_runs and isinstance(ruby_runs[0], dict)
                        else {}
                    )
                    ruby_st = first_ruby.get("style") if isinstance(first_ruby.get("style"), dict) else {}
                    if base_st.get("rubyInherit", True) is False:
                        rt_ff = str(ruby_st.get("fontFamily") or run_ff).strip()
                    else:
                        rt_ff = str(base_st.get("fontFamily") or run_ff).strip()
                    _walk_inlines(base_nodes, run_ff)
                    _walk_inlines(ruby_runs, rt_ff, force_ff=rt_ff)

        for blk in rich.get("blocks") or []:
            if isinstance(blk, dict):
                _walk_inlines(blk.get("inlines") or [], base_ff)

        by_font: dict[str, list[str]] = {}
        for ff, chars in chars_by_font.items():
            if chars:
                miss = check_missing_glyphs(ff, "".join(sorted(chars)), include_system=include_system)
                if miss:
                    by_font[ff] = miss
        return by_font

    from .richtext_builder import visible_text_of_region

    vis = visible_text_of_region(region)
    if base_ff and vis:
        miss = check_missing_glyphs(base_ff, vis, include_system=include_system)
        if miss:
            return {base_ff: miss}
    return {}


def check_region_missing_glyphs(
    region: dict[str, Any],
    default_font_family: str = "",
    *,
    include_system: Optional[bool] = None,
) -> list[str]:
    """Check missing glyphs in ``region``, honoring per-inline ``style.fontFamily`` overrides in ``translation_rich``."""
    by_font = check_region_missing_glyphs_by_font(
        region,
        default_font_family,
        include_system=include_system,
    )
    missing: set[str] = set()
    for chars in by_font.values():
        missing.update(chars)
    return sorted(missing)
