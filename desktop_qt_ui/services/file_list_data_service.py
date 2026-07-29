"""后台构建桌面文件列表所需的完整、不可变快照。"""

from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable, Optional

from manga_translator.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from manga_translator.utils.path_manager import resolve_original_image_path
from PyQt6.QtCore import QObject, pyqtSignal


SUPPORTED_ARCHIVE_EXTENSIONS = frozenset({".pdf", ".epub", ".cbz", ".cbr", ".zip"})
SUPPORTED_LIST_EXTENSIONS = frozenset(SUPPORTED_IMAGE_EXTENSIONS) | SUPPORTED_ARCHIVE_EXTENSIONS
KIND_FOLDER = "folder"
KIND_IMAGE = "image"
KIND_ARCHIVE = "archive"


def canonical_path_key(path: str) -> str:
    """Windows 友好的路径身份键；显示路径仍保留规范化后的原大小写。"""
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def natural_sort_key(path: str) -> tuple:
    name = os.path.basename(os.path.normpath(path))
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name))


@dataclass(frozen=True, slots=True)
class FileCatalogNode:
    path: str
    kind: str
    children: tuple["FileCatalogNode", ...] = ()
    file_count: int = 0
    source_path: Optional[str] = None
    json_path: Optional[str] = None
    source_root: Optional[str] = None
    mtime_ns: int = 0
    size: int = 0

    @property
    def name(self) -> str:
        return os.path.basename(self.path) or self.path

    @property
    def thumbnail_key(self) -> tuple[str, int, int]:
        return canonical_path_key(self.path), self.mtime_ns, self.size


@dataclass(frozen=True, slots=True)
class FileCatalogSnapshot:
    generation: int
    sources: tuple[str, ...]
    roots: tuple[FileCatalogNode, ...]
    files: tuple[str, ...]
    image_files: tuple[str, ...]
    editor_files: tuple[str, ...]
    file_to_folder: dict[str, Optional[str]]
    source_by_file: dict[str, str]
    json_by_file: dict[str, str]
    excluded_folders: tuple[str, ...] = ()
    excluded_files: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @classmethod
    def empty(cls, generation: int = 0) -> "FileCatalogSnapshot":
        return cls(generation, (), (), (), (), (), {}, {}, {})

    def images_only(self) -> "FileCatalogSnapshot":
        """生成编辑器投影；复用同一份扫描数据，不再次访问磁盘。"""
        if self.files == self.image_files:
            return self

        def keep_images(node: FileCatalogNode) -> Optional[FileCatalogNode]:
            if node.kind == KIND_ARCHIVE:
                return None
            if node.kind == KIND_IMAGE:
                return node
            children = tuple(child for child in (keep_images(item) for item in node.children) if child is not None)
            return FileCatalogNode(
                path=node.path,
                kind=node.kind,
                children=children,
                file_count=sum(child.file_count for child in children),
                source_root=node.source_root,
                mtime_ns=node.mtime_ns,
                size=node.size,
            )

        roots = tuple(node for node in (keep_images(root) for root in self.roots) if node is not None)
        allowed = set(self.image_files)
        json_allowed = allowed | set(self.editor_files)
        return FileCatalogSnapshot(
            generation=self.generation,
            sources=self.sources,
            roots=roots,
            files=self.image_files,
            image_files=self.image_files,
            editor_files=self.editor_files,
            file_to_folder={path: folder for path, folder in self.file_to_folder.items() if path in allowed},
            source_by_file={path: source for path, source in self.source_by_file.items() if path in allowed},
            json_by_file={
                path: json_path
                for path, json_path in self.json_by_file.items()
                if path in json_allowed
            },
            excluded_folders=self.excluded_folders,
            excluded_files=self.excluded_files,
            warnings=self.warnings,
        )


class FileCatalogCancelled(RuntimeError):
    pass


class _CatalogBuilder:
    def __init__(
        self,
        generation: int,
        sources: Iterable[str],
        excluded_folders: Iterable[str],
        excluded_files: Iterable[str],
        cancel_event: Optional[threading.Event],
    ):
        self.generation = generation
        self.sources = self._normalize_unique(sources)
        self.excluded_folder_paths = self._normalize_unique(excluded_folders)
        self.excluded_file_paths = self._normalize_unique(excluded_files)
        self.excluded_folders = {canonical_path_key(path) for path in self.excluded_folder_paths}
        self.excluded_files = {canonical_path_key(path) for path in self.excluded_file_paths}
        self.cancel_event = cancel_event
        self.seen_files: set[str] = set()
        self.visited_folders: set[str] = set()
        self.files: list[str] = []
        self.image_files: list[str] = []
        self.editor_files: list[str] = []
        self._seen_editor_files: set[str] = set()
        self.file_to_folder: dict[str, Optional[str]] = {}
        self.source_by_file: dict[str, str] = {}
        self.json_by_file: dict[str, str] = {}
        self.warnings: list[str] = []
        self._translation_maps: dict[str, dict[str, str]] = {}
        self._json_indexes: dict[str, tuple[dict[str, str], dict[str, str]]] = {}

    @staticmethod
    def _normalize_unique(paths: Iterable[str]) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        for path in paths:
            if not path:
                continue
            normalized = os.path.abspath(os.path.normpath(path))
            key = canonical_path_key(normalized)
            if key in seen:
                continue
            seen.add(key)
            result.append(normalized)
        return tuple(result)

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise FileCatalogCancelled()

    @staticmethod
    def _has_ancestor(path_key: str, candidates: set[str]) -> bool:
        current = os.path.dirname(path_key)
        while current and current != os.path.dirname(current):
            if current in candidates:
                return True
            current = os.path.dirname(current)
        return current in candidates

    def _is_excluded_folder(self, path: str) -> bool:
        key = canonical_path_key(path)
        return key in self.excluded_folders or self._has_ancestor(key, self.excluded_folders)

    def _root_sources(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        folders: list[str] = []
        files: list[str] = []
        for path in self.sources:
            self._check_cancelled()
            if os.path.isdir(path):
                folders.append(path)
            elif os.path.isfile(path):
                files.append(path)
            else:
                self.warnings.append(f"路径不存在，已跳过: {path}")

        folder_keys = {canonical_path_key(path) for path in folders}
        folders = [
            path for path in folders
            if not self._has_ancestor(canonical_path_key(path), folder_keys)
            and not self._is_excluded_folder(path)
        ]
        files = [
            path for path in files
            if not self._has_ancestor(canonical_path_key(path), folder_keys)
            and canonical_path_key(path) not in self.excluded_files
            and not self._is_excluded_folder(os.path.dirname(path))
        ]
        return tuple(sorted(folders, key=natural_sort_key)), tuple(sorted(files, key=natural_sort_key))

    def build(self) -> FileCatalogSnapshot:
        roots: list[FileCatalogNode] = []
        folders, standalone_files = self._root_sources()
        for folder in folders:
            node = self._scan_folder(folder, folder)
            if node is not None:
                roots.append(node)
        for file_path in standalone_files:
            node = self._scan_file(file_path, None)
            if node is not None:
                roots.append(node)

        roots.sort(key=lambda node: natural_sort_key(node.path))
        return FileCatalogSnapshot(
            generation=self.generation,
            sources=self.sources,
            roots=tuple(roots),
            files=tuple(self.files),
            image_files=tuple(self.image_files),
            editor_files=tuple(self.editor_files),
            file_to_folder=self.file_to_folder,
            source_by_file=self.source_by_file,
            json_by_file=self.json_by_file,
            excluded_folders=self.excluded_folder_paths,
            excluded_files=self.excluded_file_paths,
            warnings=tuple(self.warnings),
        )

    def _scan_folder(self, folder_path: str, source_root: str) -> Optional[FileCatalogNode]:
        self._check_cancelled()
        normalized = os.path.abspath(os.path.normpath(folder_path))
        folder_key = canonical_path_key(normalized)
        if folder_key in self.visited_folders or self._is_excluded_folder(normalized):
            return None
        self.visited_folders.add(folder_key)

        directories: list[str] = []
        files: list[str] = []
        try:
            with os.scandir(normalized) as entries:
                for entry in entries:
                    self._check_cancelled()
                    if entry.name == "manga_translator_work":
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            directories.append(os.path.abspath(os.path.normpath(entry.path)))
                        elif entry.is_file(follow_symlinks=False):
                            extension = os.path.splitext(entry.name)[1].lower()
                            if extension in SUPPORTED_LIST_EXTENSIONS:
                                files.append(os.path.abspath(os.path.normpath(entry.path)))
                    except OSError as exc:
                        self.warnings.append(f"无法读取目录项 {entry.path}: {exc}")
        except OSError as exc:
            self.warnings.append(f"无法扫描目录 {normalized}: {exc}")

        children: list[FileCatalogNode] = []
        for directory in sorted(directories, key=natural_sort_key):
            node = self._scan_folder(directory, source_root)
            if node is not None:
                children.append(node)
        for file_path in sorted(files, key=natural_sort_key):
            node = self._scan_file(file_path, source_root)
            if node is not None:
                children.append(node)

        mtime_ns = 0
        try:
            mtime_ns = os.stat(normalized, follow_symlinks=False).st_mtime_ns
        except OSError:
            pass
        return FileCatalogNode(
            path=normalized,
            kind=KIND_FOLDER,
            children=tuple(children),
            file_count=sum(child.file_count for child in children),
            source_root=source_root,
            mtime_ns=mtime_ns,
        )

    def _scan_file(self, file_path: str, source_root: Optional[str]) -> Optional[FileCatalogNode]:
        self._check_cancelled()
        normalized = os.path.abspath(os.path.normpath(file_path))
        file_key = canonical_path_key(normalized)
        extension = os.path.splitext(normalized)[1].lower()
        if (
            extension not in SUPPORTED_LIST_EXTENSIONS
            or file_key in self.seen_files
            or file_key in self.excluded_files
            or self._is_excluded_folder(os.path.dirname(normalized))
        ):
            return None
        self.seen_files.add(file_key)

        try:
            stat = os.stat(normalized, follow_symlinks=False)
            mtime_ns, size = stat.st_mtime_ns, stat.st_size
        except OSError as exc:
            self.warnings.append(f"无法读取文件 {normalized}: {exc}")
            return None

        kind = KIND_ARCHIVE if extension in SUPPORTED_ARCHIVE_EXTENSIONS else KIND_IMAGE
        source_path: Optional[str] = None
        json_path: Optional[str] = None
        if kind == KIND_IMAGE:
            source_path = self._resolve_source_path(normalized)
            json_path = self._find_json_path(source_path)
            self.image_files.append(normalized)
            editor_key = canonical_path_key(source_path)
            if editor_key not in self._seen_editor_files:
                self._seen_editor_files.add(editor_key)
                self.editor_files.append(source_path)
            self.source_by_file[normalized] = source_path
            if json_path:
                self.json_by_file[normalized] = json_path
                self.json_by_file.setdefault(source_path, json_path)

        self.files.append(normalized)
        self.file_to_folder[normalized] = source_root
        return FileCatalogNode(
            path=normalized,
            kind=kind,
            file_count=1,
            source_path=source_path,
            json_path=json_path,
            source_root=source_root,
            mtime_ns=mtime_ns,
            size=size,
        )

    def _load_translation_map(self, folder_path: str) -> dict[str, str]:
        folder_key = canonical_path_key(folder_path)
        cached = self._translation_maps.get(folder_key)
        if cached is not None:
            return cached

        result: dict[str, str] = {}
        map_path = os.path.join(folder_path, "translation_map.json")
        if os.path.isfile(map_path):
            try:
                with open(map_path, "r", encoding="utf-8") as handle:
                    raw_map = json.load(handle)
                if isinstance(raw_map, dict):
                    for translated, source in raw_map.items():
                        if isinstance(translated, str) and isinstance(source, str):
                            result[canonical_path_key(translated)] = os.path.abspath(os.path.normpath(source))
            except (OSError, ValueError) as exc:
                self.warnings.append(f"无法读取 translation_map.json {map_path}: {exc}")
        self._translation_maps[folder_key] = result
        return result

    def _resolve_source_path(self, image_path: str) -> str:
        mapped = self._load_translation_map(os.path.dirname(image_path)).get(canonical_path_key(image_path))
        if mapped and os.path.isfile(mapped):
            return mapped
        return os.path.abspath(os.path.normpath(resolve_original_image_path(image_path)))

    def _json_index(self, folder_path: str) -> tuple[dict[str, str], dict[str, str]]:
        folder_key = canonical_path_key(folder_path)
        cached = self._json_indexes.get(folder_key)
        if cached is not None:
            return cached

        old_index: dict[str, str] = {}
        new_index: dict[str, str] = {}

        def collect(directory: str, target: dict[str, str]) -> None:
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if not entry.name.endswith("_translations.json") or not entry.is_file(follow_symlinks=False):
                            continue
                        stem = entry.name[: -len("_translations.json")]
                        target.setdefault(stem.casefold(), os.path.abspath(os.path.normpath(entry.path)))
            except OSError:
                return

        collect(folder_path, old_index)
        collect(os.path.join(folder_path, "manga_translator_work", "json"), new_index)
        self._json_indexes[folder_key] = (new_index, old_index)
        return new_index, old_index

    def _find_json_path(self, source_path: str) -> Optional[str]:
        folder = os.path.dirname(source_path)
        stem = os.path.splitext(os.path.basename(source_path))[0].casefold()
        new_index, old_index = self._json_index(folder)
        return new_index.get(stem) or old_index.get(stem)


def build_file_catalog_snapshot(
    sources: Iterable[str],
    *,
    excluded_folders: Iterable[str] = (),
    excluded_files: Iterable[str] = (),
    generation: int = 0,
    cancel_event: Optional[threading.Event] = None,
) -> FileCatalogSnapshot:
    """同步构建函数，供后台线程和小型确定性测试共同使用。"""
    return _CatalogBuilder(
        generation,
        sources,
        excluded_folders,
        excluded_files,
        cancel_event,
    ).build()


class FileListDataService(QObject):
    """两个常驻线程构建快照；每个频道只接受最新 generation。"""

    loading = pyqtSignal(str, int)
    snapshot_ready = pyqtSignal(str, int, object)
    error = pyqtSignal(str, int, str)

    def __init__(self, parent: Optional[QObject] = None, max_workers: int = 2):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="file_catalog")
        self._lock = threading.Lock()
        self._generations: dict[str, int] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._futures: dict[str, Future] = {}
        self._shutdown = False

    def request_snapshot(
        self,
        channel: str,
        sources: Iterable[str],
        excluded_folders: Iterable[str] = (),
        excluded_files: Iterable[str] = (),
    ) -> int:
        with self._lock:
            if self._shutdown:
                raise RuntimeError("FileListDataService 已关闭")
            generation = self._generations.get(channel, 0) + 1
            self._generations[channel] = generation
            previous = self._cancel_events.get(channel)
            if previous is not None:
                previous.set()
            cancel_event = threading.Event()
            self._cancel_events[channel] = cancel_event

        self.loading.emit(channel, generation)
        future = self._executor.submit(
            build_file_catalog_snapshot,
            tuple(sources),
            excluded_folders=tuple(excluded_folders),
            excluded_files=tuple(excluded_files),
            generation=generation,
            cancel_event=cancel_event,
        )
        with self._lock:
            self._futures[channel] = future
        future.add_done_callback(lambda completed, name=channel, token=generation: self._on_done(name, token, completed))
        return generation

    def _on_done(self, channel: str, generation: int, future: Future) -> None:
        try:
            snapshot = future.result()
        except FileCatalogCancelled:
            return
        except Exception as exc:
            with self._lock:
                current = self._generations.get(channel)
                active = not self._shutdown and current == generation
            if active:
                try:
                    self.error.emit(channel, generation, str(exc))
                except RuntimeError:
                    pass
            return

        with self._lock:
            current = self._generations.get(channel)
            active = not self._shutdown and current == generation
            if self._futures.get(channel) is future:
                self._futures.pop(channel, None)
        if active:
            try:
                self.snapshot_ready.emit(channel, generation, snapshot)
            except RuntimeError:
                pass

    def cancel(self, channel: str) -> None:
        with self._lock:
            self._generations[channel] = self._generations.get(channel, 0) + 1
            cancel_event = self._cancel_events.pop(channel, None)
            future = self._futures.pop(channel, None)
        if cancel_event is not None:
            cancel_event.set()
        if future is not None:
            future.cancel()

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            cancel_events = tuple(self._cancel_events.values())
            self._cancel_events.clear()
            self._futures.clear()
        for cancel_event in cancel_events:
            cancel_event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
