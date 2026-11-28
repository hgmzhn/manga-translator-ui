"""Font catalog & CJK glyph-coverage validator for manga-translator-ui."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
import time
import unicodedata
from collections import OrderedDict
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


def _scan_font_dir_snapshot(
    base_dir: Path,
) -> tuple[tuple[tuple[str, int, int], ...], tuple[tuple[str, int, tuple[str, ...]], ...], bool]:
    """Single-pass directory walk producing ``(files, watched_dirs, consistent)`` from identical ``scandir`` entries."""
    files_out: list[tuple[str, int, int]] = []
    watched_out: list[tuple[str, int, tuple[str, ...]]] = []
    visited_dirs: set[str] = set()
    consistent = True

    def _walk(cur_dir: Path) -> None:
        nonlocal consistent
        try:
            real_key = os.path.normcase(os.path.realpath(cur_dir))
        except OSError:
            consistent = False
            return
        if real_key in visited_dirs:
            return
        visited_dirs.add(real_key)

        try:
            st_before = os.stat(cur_dir)
            with os.scandir(cur_dir) as it:
                entries = sorted(it, key=lambda e: (e.name.casefold(), e.name))
        except OSError:
            consistent = False
            return

        names = tuple(e.name for e in entries)
        subdirs: list[Path] = []
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=True):
                    subdirs.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=True) and entry.name.lower().endswith(FONT_EXTENSIONS):
                    st_f = entry.stat(follow_symlinks=True)
                    files_out.append((str(Path(entry.path)), int(st_f.st_size), int(st_f.st_mtime_ns)))
            except OSError:
                consistent = False
                continue

        watched_out.append((str(cur_dir), int(st_before.st_mtime_ns), names))
        for sub in subdirs:
            _walk(sub)

        try:
            st_after = os.stat(cur_dir)
            names_after = tuple(sorted(os.listdir(cur_dir), key=lambda n: (n.casefold(), n)))
        except OSError:
            consistent = False
            return
        if int(st_after.st_mtime_ns) != int(st_before.st_mtime_ns) or names_after != names:
            consistent = False

    if base_dir.is_dir():
        _walk(base_dir)
    else:
        consistent = False
    return tuple(files_out), tuple(watched_out), consistent


def _iter_font_files(base_dir: Path) -> list[tuple[Path, int, int]]:
    """Recursively enumerate font files and their ``(st_size, st_mtime_ns)`` under ``base_dir`` in deterministic order."""
    files, _, _ = _scan_font_dir_snapshot(base_dir)
    return [(Path(fp_str), fsize, mtime_ns) for fp_str, fsize, mtime_ns in files]


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


_IndexedFontFile = tuple[str, int, int]
_MAX_DIR_SNAPSHOT_TOKENS = 128
_MAX_TOKEN_DIR_PATHS = 2048


class _MissingDirSnapshotError(KeyError):
    """Raised internally to abort ``@lru_cache`` population when a directory snapshot token is missing."""


@dataclass
class _SharedFontDirIndexEntry:
    root_dir: str
    norm_dir_key: str
    token: str
    files: tuple[_IndexedFontFile, ...]
    watched_dirs: tuple[tuple[str, int, tuple[str, ...]], ...]
    verified_mono: float
    verified: bool = True


_FONT_DIR_INDEX_LOCK = threading.RLock()
_FONT_DIR_INDEX_CACHE: dict[str, _SharedFontDirIndexEntry] = {}
_FONT_DIR_SNAPSHOTS_BY_TOKEN: OrderedDict[str, tuple[_IndexedFontFile, ...]] = OrderedDict()
_FONT_DIR_PATHS_BY_TOKEN: OrderedDict[str, str] = OrderedDict()


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


def _collect_watched_dirs_snapshot(base_dir: Path) -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    """Record ``(dir_path_str, dir_mtime_ns, sorted_entry_names)`` across ``base_dir`` and its subdirectories."""
    _, watched_dirs, _ = _scan_font_dir_snapshot(base_dir)
    return watched_dirs


def _remember_snapshot_for_token(
    token: str,
    files: tuple[_IndexedFontFile, ...],
    root_dir: Optional[str] = None,
) -> None:
    _FONT_DIR_SNAPSHOTS_BY_TOKEN[token] = files
    _FONT_DIR_SNAPSHOTS_BY_TOKEN.move_to_end(token)
    while len(_FONT_DIR_SNAPSHOTS_BY_TOKEN) > _MAX_DIR_SNAPSHOT_TOKENS:
        _FONT_DIR_SNAPSHOTS_BY_TOKEN.popitem(last=False)
    if root_dir is not None:
        _FONT_DIR_PATHS_BY_TOKEN[token] = root_dir
        _FONT_DIR_PATHS_BY_TOKEN.move_to_end(token)
        while len(_FONT_DIR_PATHS_BY_TOKEN) > _MAX_TOKEN_DIR_PATHS:
            _FONT_DIR_PATHS_BY_TOKEN.popitem(last=False)


def _is_shared_dir_index_fresh(entry: _SharedFontDirIndexEntry) -> bool:
    if not entry.verified or not entry.watched_dirs or not os.path.isdir(entry.root_dir):
        return False
    now_ns = time.time_ns()
    now_mono = time.monotonic()

    # Fast path for large immutable system font directories (>128 files) whose directory mtime is safely
    # outside the OS filesystem clock quantum (>100ms old) and was fully verified within the last 2 seconds.
    if (
        len(entry.files) > 128
        and (now_mono - entry.verified_mono) < 2.0
        and entry.watched_dirs
        and all((now_ns - d_mtime_ns) > 100_000_000 for _, d_mtime_ns, _ in entry.watched_dirs)
    ):
        try:
            for d_str, d_mtime_ns, _ in entry.watched_dirs:
                if int(os.stat(d_str).st_mtime_ns) != d_mtime_ns:
                    return False
            return True
        except OSError:
            return False

    try:
        for d_str, d_mtime_ns, d_names in entry.watched_dirs:
            if int(os.stat(d_str).st_mtime_ns) != d_mtime_ns:
                return False
            cur_names = tuple(sorted(os.listdir(d_str), key=lambda n: (n.casefold(), n)))
            if cur_names != d_names:
                return False
        for fp_str, fsize, mtime_ns in entry.files:
            st_f = os.stat(fp_str)
            if int(st_f.st_size) != fsize or int(st_f.st_mtime_ns) != mtime_ns:
                return False
    except OSError:
        return False

    entry.verified_mono = now_mono
    return True


def _get_or_build_dir_index(base_dir: Path) -> Optional[_SharedFontDirIndexEntry]:
    if base_dir is None or not base_dir.is_dir():
        return None
    norm_dir_key = os.path.normcase(os.path.abspath(str(base_dir)))
    with _FONT_DIR_INDEX_LOCK:
        cached = _FONT_DIR_INDEX_CACHE.get(norm_dir_key)
        if cached is not None and _is_shared_dir_index_fresh(cached):
            _remember_snapshot_for_token(cached.token, cached.files, cached.root_dir)
            return cached

        files, watched_dirs, scan_consistent = _scan_font_dir_snapshot(base_dir)

        hasher = hashlib.blake2b(digest_size=16)
        hasher.update(norm_dir_key.encode("utf-8", errors="surrogatepass"))
        hasher.update(b"\x00")
        for fp_str, fsize, mtime_ns in files:
            norm_fp = os.path.normcase(os.path.abspath(fp_str))
            hasher.update(f"{norm_fp}|{fsize}|{mtime_ns}\n".encode("utf-8", errors="surrogatepass"))
        token = hasher.hexdigest()

        if not scan_consistent or not watched_dirs:
            # R4-F07: Never publish a directory scan that mutated mid-walk as a verified index or cached token snapshot.
            _FONT_DIR_INDEX_CACHE.pop(norm_dir_key, None)
            return _SharedFontDirIndexEntry(
                root_dir=str(base_dir),
                norm_dir_key=norm_dir_key,
                token=token,
                files=files,
                watched_dirs=watched_dirs,
                verified_mono=0.0,
                verified=False,
            )

        entry = _SharedFontDirIndexEntry(
            root_dir=str(base_dir),
            norm_dir_key=norm_dir_key,
            token=token,
            files=files,
            watched_dirs=watched_dirs,
            verified_mono=time.monotonic(),
            verified=True,
        )
        _FONT_DIR_INDEX_CACHE[norm_dir_key] = entry
        _remember_snapshot_for_token(token, files, str(base_dir))
        return entry


def _get_font_dir_entries(dirs: Iterable[Path]) -> list[_SharedFontDirIndexEntry]:
    entries: list[_SharedFontDirIndexEntry] = []
    for d in dirs:
        entry = _get_or_build_dir_index(d)
        if entry is not None:
            entries.append(entry)
    return entries


def _get_font_dirs_tokens(dirs: Iterable[Path]) -> tuple[str, ...]:
    return tuple(e.token for e in _get_font_dir_entries(dirs))


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
) -> Optional[tuple[int, int, int, int]]:
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

    exact_qt_rank = 0 if qt_cf == target_fam else 1
    return (name_tier, source_rank, style_rank, exact_qt_rank)


def _scan_target_font_candidates(
    snapshots: Iterable[tuple[_IndexedFontFile, ...]],
    src_kind: str,
    src_rank: int,
    target_fam: str,
    explicit_style: Optional[str],
) -> Optional[tuple[tuple[int, int, int, int], Optional[frozenset[int]]]]:
    if not target_fam:
        return None

    candidates: list[tuple[tuple[int, int, int, int], int, str, int, int, int]] = []
    order_seq = 0
    best_possible = (0, src_rank, 0, 0)

    for files in snapshots:
        stop_early = False
        for fp_str, fsize, mtime_ns in files:
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
                    candidates.append((rank, order_seq, fp_str, fsize, mtime_ns, face_idx))
                    order_seq += 1
                    if rank == best_possible:
                        stop_early = True
                        break
            if stop_early:
                break
        if stop_early:
            break

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    best_rank, _, best_fp_str, best_fsize, best_mtime_ns, best_face_idx = candidates[0]
    inspected_faces = _inspect_font_file(best_fp_str, best_fsize, best_mtime_ns, src_kind)
    for f_idx, _, face_cps in inspected_faces:
        if f_idx == best_face_idx:
            return (best_rank, face_cps)
    return None


@lru_cache(maxsize=2048)
def _cached_resolve_target_font_codepoints(
    dir_tokens: tuple[str, ...],
    src_kind: str,
    src_rank: int,
    target_fam: str,
    explicit_style: Optional[str],
) -> Optional[tuple[tuple[int, int, int, int], Optional[frozenset[int]]]]:
    if not target_fam or not dir_tokens:
        return None

    snapshots: list[tuple[_IndexedFontFile, ...]] = []
    with _FONT_DIR_INDEX_LOCK:
        for tok in dir_tokens:
            files = _FONT_DIR_SNAPSHOTS_BY_TOKEN.get(tok)
            if files is None:
                for live_entry in _FONT_DIR_INDEX_CACHE.values():
                    if live_entry.verified and live_entry.token == tok:
                        files = live_entry.files
                        if len(dir_tokens) <= _MAX_DIR_SNAPSHOT_TOKENS:
                            _remember_snapshot_for_token(tok, files, live_entry.root_dir)
                        break
            if files is None:
                # R4-F43: Never treat an evicted/missing snapshot token as `()` or cache a poisoned result!
                raise _MissingDirSnapshotError(tok)
            snapshots.append(files)

    return _scan_target_font_candidates(snapshots, src_kind, src_rank, target_fam, explicit_style)


def _resolve_target_font_codepoints(
    dir_tokens: tuple[str, ...],
    src_kind: str,
    src_rank: int,
    target_fam: str,
    explicit_style: Optional[str],
    *,
    _pinned_snapshots: Optional[tuple[tuple[_IndexedFontFile, ...], ...]] = None,
) -> Optional[tuple[tuple[int, int, int, int], Optional[frozenset[int]]]]:
    if not target_fam or not dir_tokens:
        return None

    if _pinned_snapshots is not None and len(_pinned_snapshots) == len(dir_tokens):
        if len(dir_tokens) <= _MAX_DIR_SNAPSHOT_TOKENS:
            with _FONT_DIR_INDEX_LOCK:
                for tok, snap in zip(dir_tokens, _pinned_snapshots):
                    if tok not in _FONT_DIR_SNAPSHOTS_BY_TOKEN:
                        _remember_snapshot_for_token(tok, snap)

    try:
        return _cached_resolve_target_font_codepoints(dir_tokens, src_kind, src_rank, target_fam, explicit_style)
    except _MissingDirSnapshotError:
        if _pinned_snapshots is not None and len(_pinned_snapshots) == len(dir_tokens):
            return _scan_target_font_candidates(_pinned_snapshots, src_kind, src_rank, target_fam, explicit_style)

        rebuilt_snapshots: list[tuple[_IndexedFontFile, ...]] = []
        all_tokens_exact_and_verified = True
        for tok in dir_tokens:
            with _FONT_DIR_INDEX_LOCK:
                snap = _FONT_DIR_SNAPSHOTS_BY_TOKEN.get(tok)
                known_dir = _FONT_DIR_PATHS_BY_TOKEN.get(tok)
                if snap is None:
                    for live_entry in _FONT_DIR_INDEX_CACHE.values():
                        if live_entry.verified and live_entry.token == tok:
                            snap = live_entry.files
                            break
            if snap is not None:
                rebuilt_snapshots.append(snap)
                continue
            if known_dir is not None:
                rebuilt = _get_or_build_dir_index(Path(known_dir))
                if rebuilt is not None:
                    rebuilt_snapshots.append(rebuilt.files)
                    if not rebuilt.verified or rebuilt.token != tok:
                        all_tokens_exact_and_verified = False
                    continue
            all_tokens_exact_and_verified = False

        if (
            all_tokens_exact_and_verified
            and len(rebuilt_snapshots) == len(dir_tokens)
            and len(dir_tokens) <= _MAX_DIR_SNAPSHOT_TOKENS
        ):
            with _FONT_DIR_INDEX_LOCK:
                for tok, snap in zip(dir_tokens, rebuilt_snapshots):
                    _remember_snapshot_for_token(tok, snap)
            try:
                return _cached_resolve_target_font_codepoints(
                    dir_tokens, src_kind, src_rank, target_fam, explicit_style
                )
            except _MissingDirSnapshotError:
                pass

        if not rebuilt_snapshots:
            return None
        return _scan_target_font_candidates(rebuilt_snapshots, src_kind, src_rank, target_fam, explicit_style)


_resolve_target_font_codepoints.cache_clear = _cached_resolve_target_font_codepoints.cache_clear  # type: ignore[attr-defined]
_resolve_target_font_codepoints.cache_info = _cached_resolve_target_font_codepoints.cache_info  # type: ignore[attr-defined]
_resolve_target_font_codepoints.cache_parameters = _cached_resolve_target_font_codepoints.cache_parameters  # type: ignore[attr-defined]
_resolve_target_font_codepoints.__wrapped__ = _cached_resolve_target_font_codepoints.__wrapped__  # type: ignore[attr-defined]


def _resolve_entries_codepoints(
    entries: list[_SharedFontDirIndexEntry],
    src_kind: str,
    src_rank: int,
    target_fam: str,
    explicit_style: Optional[str],
) -> Optional[tuple[tuple[int, int, int, int], Optional[frozenset[int]]]]:
    if not entries:
        return None
    snapshots = tuple(e.files for e in entries)
    if not all(e.verified for e in entries):
        return _scan_target_font_candidates(snapshots, src_kind, src_rank, target_fam, explicit_style)
    tokens = tuple(e.token for e in entries)
    return _resolve_target_font_codepoints(
        tokens,
        src_kind,
        src_rank,
        target_fam,
        explicit_style,
        _pinned_snapshots=snapshots,
    )


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
    project_entries = _get_font_dir_entries(project_dirs)
    proj_hit = _resolve_entries_codepoints(project_entries, "project", 0, target_fam, explicit_style)
    # R3-F61: Skip system font directory enumeration completely when project fonts already match the family!
    if proj_hit is not None and proj_hit[0][0] == 0:
        return proj_hit[1]

    if not include_system:
        return proj_hit[1] if proj_hit is not None else None

    system_entries = _get_font_dir_entries(_system_font_dirs())
    sys_hit = _resolve_entries_codepoints(system_entries, "system", 1, target_fam, explicit_style)
    if proj_hit is not None and sys_hit is not None:
        return proj_hit[1] if proj_hit[0] <= sys_hit[0] else sys_hit[1]
    if proj_hit is not None:
        return proj_hit[1]
    if sys_hit is not None:
        return sys_hit[1]
    return None


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
        dir_entry = _get_or_build_dir_index(base_dir)
        if dir_entry is None:
            return
        for fp_str, fsize, mtime_ns in dir_entry.files:
            if q_norm:
                fpath_name = Path(fp_str).name
                face_names = _match_font_names_only(fp_str, fsize, mtime_ns)
                if face_names and not any(
                    q_norm in unicodedata.normalize("NFKC", f"{qt_cf} {disp_cf} {fpath_name}").casefold()
                    for _, qt_cf, _, disp_cf, _, _ in face_names
                ):
                    continue
            for _, entry, _ in _inspect_font_file(fp_str, fsize, mtime_ns, source_kind):
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
