"""高容量文件树：完整后台快照 + 原生 Model/View + 可见缩略图。"""

from __future__ import annotations

import os
import weakref
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

from PyQt6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QColor,
    QFontMetrics,
    QImage,
    QImageReader,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)
from qfluentwidgets import FluentIcon as FIF, StrongBodyLabel, TreeView, isDarkTheme

from services.file_list_data_service import (
    KIND_ARCHIVE,
    KIND_FOLDER,
    KIND_IMAGE,
    FileCatalogNode,
    FileCatalogSnapshot,
    FileListDataService,
    canonical_path_key,
    natural_sort_key,
)


PATH_ROLE = int(Qt.ItemDataRole.UserRole)
KIND_ROLE = PATH_ROLE + 1
COUNT_ROLE = PATH_ROLE + 2
NODE_ROLE = PATH_ROLE + 3
THUMBNAIL_ROLE = PATH_ROLE + 4

_thumbnail_executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="thumbnail_loader",
)
_default_catalog_service: Optional[FileListDataService] = None


def _catalog_service() -> FileListDataService:
    global _default_catalog_service
    if _default_catalog_service is None:
        _default_catalog_service = FileListDataService(max_workers=2)
    return _default_catalog_service


def shutdown_thumbnail_executor() -> None:
    """兼容现有退出钩子，同时关闭文件索引与缩略图常驻池。"""
    global _thumbnail_executor, _default_catalog_service
    if _thumbnail_executor is not None:
        _thumbnail_executor.shutdown(wait=False, cancel_futures=True)
        _thumbnail_executor = None
    if _default_catalog_service is not None:
        _default_catalog_service.shutdown()
        _default_catalog_service = None


def _single_shot(msec: int, owner: QObject, slot) -> None:
    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.timeout.connect(slot)
    timer.timeout.connect(timer.deleteLater)
    timer.start(msec)


def _load_thumbnail_worker(file_path: str) -> tuple[str, QImage]:
    """工作线程只返回 QImage；QPixmap 必须留在 GUI 线程创建。"""
    reader = QImageReader(file_path)
    reader.setAutoTransform(True)
    source_size = reader.size()
    if source_size.isValid():
        source_size.scale(QSize(40, 40), Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(source_size)
    image = reader.read()
    if not image.isNull():
        return file_path, image

    try:
        from PIL import Image, ImageOps

        with Image.open(file_path) as pil_image:
            pil_image = ImageOps.exif_transpose(pil_image)
            pil_image.thumbnail((40, 40), Image.Resampling.LANCZOS)
            rgba = pil_image.convert("RGBA")
            raw = rgba.tobytes("raw", "RGBA")
            image = QImage(
                raw,
                rgba.width,
                rgba.height,
                rgba.width * 4,
                QImage.Format.Format_RGBA8888,
            ).copy()
    except Exception:
        image = QImage()
    return file_path, image


class _ThumbnailBridge(QObject):
    loaded = pyqtSignal(int, object, str, object)


class _CatalogItem:
    __slots__ = (
        "path",
        "kind",
        "file_count",
        "source_path",
        "json_path",
        "source_root",
        "mtime_ns",
        "size",
        "parent",
        "children",
        "row",
    )

    def __init__(self, node: FileCatalogNode, parent: Optional["_CatalogItem"] = None, row: int = 0):
        self.path = node.path
        self.kind = node.kind
        self.file_count = node.file_count
        self.source_path = node.source_path
        self.json_path = node.json_path
        self.source_root = node.source_root
        self.mtime_ns = node.mtime_ns
        self.size = node.size
        self.parent = parent
        self.row = row
        self.children = [_CatalogItem(child, self, index) for index, child in enumerate(node.children)]

    @property
    def thumbnail_key(self) -> tuple[str, int, int]:
        return canonical_path_key(self.path), self.mtime_ns, self.size


class FileCatalogModel(QAbstractItemModel):
    """纯内存树模型；reset、导航和删除均不访问磁盘。"""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._roots: list[_CatalogItem] = []
        self._path_items: dict[str, _CatalogItem] = {}
        self._image_items: list[_CatalogItem] = []
        self._thumbnails: dict[str, QPixmap] = {}
        self.snapshot = FileCatalogSnapshot.empty()

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if column != 0 or row < 0:
            return QModelIndex()
        children = parent.internalPointer().children if parent.isValid() else self._roots
        if row >= len(children):
            return QModelIndex()
        return self.createIndex(row, column, children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        item = index.internalPointer()
        parent = item.parent
        if parent is None:
            return QModelIndex()
        return self.createIndex(parent.row, 0, parent)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.column() > 0:
            return 0
        return len(parent.internalPointer().children) if parent.isValid() else len(self._roots)

    def columnCount(self, _parent: QModelIndex = QModelIndex()) -> int:
        return 1

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid():
            return None
        item: _CatalogItem = index.internalPointer()
        if role == int(Qt.ItemDataRole.DisplayRole):
            name = os.path.basename(item.path) or item.path
            return f"{name} ({item.file_count}个文件)" if item.kind == KIND_FOLDER else name
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return item.path
        if role == PATH_ROLE:
            return item.path
        if role == KIND_ROLE:
            return item.kind
        if role == COUNT_ROLE:
            return item.file_count
        if role == NODE_ROLE:
            return item
        if role == THUMBNAIL_ROLE:
            return self._thumbnails.get(canonical_path_key(item.path))
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def set_snapshot(self, snapshot: FileCatalogSnapshot) -> None:
        self.beginResetModel()
        self.snapshot = snapshot
        self._roots = [_CatalogItem(node, row=index) for index, node in enumerate(snapshot.roots)]
        self._thumbnails.clear()
        self._reindex()
        self.endResetModel()

    def clear(self) -> None:
        self.set_snapshot(FileCatalogSnapshot.empty(self.snapshot.generation + 1))

    def _reindex(self) -> None:
        self._path_items.clear()
        self._image_items.clear()

        def visit(item: _CatalogItem) -> None:
            self._path_items[canonical_path_key(item.path)] = item
            if item.kind == KIND_IMAGE:
                self._image_items.append(item)
            for row, child in enumerate(item.children):
                child.parent = item
                child.row = row
                visit(child)

        for row, root in enumerate(self._roots):
            root.parent = None
            root.row = row
            visit(root)

    def item_for_path(self, path: str) -> Optional[_CatalogItem]:
        return self._path_items.get(canonical_path_key(path))

    def index_for_path(self, path: str) -> QModelIndex:
        item = self.item_for_path(path)
        return self.createIndex(item.row, 0, item) if item is not None else QModelIndex()

    def image_paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self._image_items)

    def set_thumbnail(self, path: str, pixmap: QPixmap) -> None:
        item = self.item_for_path(path)
        if item is None:
            return
        key = canonical_path_key(path)
        self._thumbnails[key] = pixmap
        index = self.createIndex(item.row, 0, item)
        self.dataChanged.emit(index, index, [THUMBNAIL_ROLE])

    def remove_path(self, path: str) -> tuple[str, ...]:
        target = self.item_for_path(path)
        if target is None:
            return ()

        removed: list[str] = []

        def collect(item: _CatalogItem) -> None:
            removed.append(item.path)
            for child in item.children:
                collect(child)

        collect(target)
        self.beginResetModel()
        parent = target.parent
        siblings = parent.children if parent is not None else self._roots
        siblings.remove(target)
        while parent is not None:
            parent.file_count = sum(child.file_count for child in parent.children)
            grandparent = parent.parent
            if parent.file_count == 0:
                parent_siblings = grandparent.children if grandparent is not None else self._roots
                parent_siblings.remove(parent)
            parent = grandparent
        for removed_path in removed:
            self._thumbnails.pop(canonical_path_key(removed_path), None)
        self._reindex()
        self.endResetModel()
        return tuple(removed)


class FileCatalogDelegate(QStyledItemDelegate):
    remove_requested = pyqtSignal(str)
    ROW_HEIGHT = 66
    ICON_SIZE = 40
    CLOSE_SIZE = 28

    def __init__(self, parent: Optional[QObject] = None, translate=None):
        super().__init__(parent)
        self._translate = translate or (lambda key: key)

    def sizeHint(self, _option: QStyleOptionViewItem, _index: QModelIndex) -> QSize:
        return QSize(120, self.ROW_HEIGHT)

    @classmethod
    def _close_rect(cls, rect: QRect) -> QRect:
        return QRect(
            rect.right() - cls.CLOSE_SIZE - 8,
            rect.center().y() - cls.CLOSE_SIZE // 2,
            cls.CLOSE_SIZE,
            cls.CLOSE_SIZE,
        )

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        rect = option.rect.adjusted(4, 3, -4, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            color = option.palette.highlight().color() if selected else option.palette.midlight().color()
            color.setAlpha(72 if selected else 55)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 8, 8)

        kind = index.data(KIND_ROLE)
        pixmap = index.data(THUMBNAIL_ROLE)
        icon_rect = QRect(rect.left() + 8, rect.center().y() - 20, self.ICON_SIZE, self.ICON_SIZE)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            target = QRect(
                icon_rect.center().x() - pixmap.width() // 2,
                icon_rect.center().y() - pixmap.height() // 2,
                pixmap.width(),
                pixmap.height(),
            )
            painter.drawPixmap(target, pixmap)
        else:
            if kind == KIND_FOLDER:
                icon = FIF.FOLDER
            elif kind == KIND_ARCHIVE:
                icon = FIF.ZIP_FOLDER
            else:
                icon = FIF.DOCUMENT
            icon.render(
                painter,
                QRect(icon_rect.center().x() - 16, icon_rect.center().y() - 16, 32, 32),
            )

        close_rect = self._close_rect(rect)
        text_rect = QRect(
            icon_rect.right() + 10,
            rect.top(),
            max(1, close_rect.left() - icon_rect.right() - 18),
            rect.height(),
        )
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        metrics = QFontMetrics(option.font)
        painter.setFont(option.font)
        dark = isDarkTheme()
        painter.setPen(QColor(255, 255, 255) if dark else QColor(31, 31, 31))
        node = index.data(NODE_ROLE)
        if kind == KIND_IMAGE and node is not None:
            title_rect = QRect(text_rect.left(), rect.top() + 7, text_rect.width(), 24)
            painter.drawText(
                title_rect,
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, title_rect.width()),
            )
            translated = bool(getattr(node, "json_path", None))
            if translated:
                status_color = QColor("#6BCB77" if dark else "#0F9D58")
            else:
                status_color = QColor(255, 255, 255, 150) if dark else QColor(0, 0, 0, 130)
            status_rect = QRect(text_rect.left(), rect.top() + 33, text_rect.width(), 20)
            dot_rect = QRect(status_rect.left(), status_rect.center().y() - 3, 6, 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(status_color)
            painter.drawEllipse(dot_rect)
            painter.setPen(status_color)
            painter.drawText(
                status_rect.adjusted(12, 0, 0, 0),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                self._translate("Translated" if translated else "Untranslated"),
            )
        else:
            painter.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, text_rect.width()),
            )

        FIF.CLOSE.render(
            painter,
            QRect(close_rect.center().x() - 8, close_rect.center().y() - 8, 16, 16),
        )
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if event.type() not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            return False
        if not isinstance(event, QMouseEvent) or event.button() != Qt.MouseButton.LeftButton:
            return False
        rect = option.rect.adjusted(4, 3, -4, -3)
        if not self._close_rect(rect).contains(event.position().toPoint()):
            return False
        if event.type() == QEvent.Type.MouseButtonRelease:
            path = index.data(PATH_ROLE)
            if isinstance(path, str):
                self.remove_requested.emit(path)
        return True


class FileListView(TreeView):
    """主页和编辑器共用的虚拟化文件树。"""

    file_remove_requested = pyqtSignal(str)
    file_selected = pyqtSignal(str)
    files_dropped = pyqtSignal(list)
    _THUMBNAIL_CACHE_SIZE = 200
    _UI_COALESCE_MS = 16
    _thumbnail_cache: OrderedDict[tuple[str, int, int], QPixmap] = OrderedDict()

    def __init__(
        self,
        model=None,
        parent=None,
        *,
        data_service: Optional[FileListDataService] = None,
    ):
        super().__init__(parent)
        self.legacy_model = model
        self.catalog_model = model if isinstance(model, FileCatalogModel) else FileCatalogModel(self)
        self.setModel(self.catalog_model)
        self._delegate = FileCatalogDelegate(self, self._t)
        self.setItemDelegate(self._delegate)

        self.setHeaderHidden(True)
        self.setIndentation(12)
        self.setRootIsDecorated(True)
        self.setAnimated(False)
        self.setUniformRowHeights(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(TreeView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setDragEnabled(False)

        self._data_service = data_service or _catalog_service()
        self._channel = f"file-list-view-{id(self)}"
        self._expected_catalog_generation = 0
        self._view_generation = 0
        self._sources: list[str] = []
        self._source_keys: set[str] = set()
        self._excluded_folders: set[str] = set()
        self._excluded_files: set[str] = set()
        self._expanded_keys: set[str] = set()
        self._restore_selected_path: Optional[str] = None
        self._thumbnail_update_scheduled = False
        self._thumbnail_pending: set[tuple[int, tuple[str, int, int]]] = set()
        self._thumbnail_bridge = _ThumbnailBridge(self)

        self.empty_hint_label = StrongBodyLabel(self._empty_state_text(), self.viewport())
        self.empty_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint_label.setWordWrap(True)
        self.empty_hint_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._set_empty_hint_color()
        self._state = "empty"

        self._delegate.remove_requested.connect(self.file_remove_requested)
        self.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.verticalScrollBar().valueChanged.connect(self._schedule_visible_thumbnail_loads)
        self.expanded.connect(self._schedule_visible_thumbnail_loads)
        self.collapsed.connect(self._schedule_visible_thumbnail_loads)
        self._thumbnail_bridge.loaded.connect(
            self._on_thumbnail_loaded,
            type=Qt.ConnectionType.QueuedConnection,
        )
        self._data_service.loading.connect(
            self._on_catalog_loading,
            type=Qt.ConnectionType.QueuedConnection,
        )
        self._data_service.snapshot_ready.connect(
            self._on_catalog_ready,
            type=Qt.ConnectionType.QueuedConnection,
        )
        self._data_service.error.connect(
            self._on_catalog_error,
            type=Qt.ConnectionType.QueuedConnection,
        )
        service = self._data_service
        channel = self._channel
        self.destroyed.connect(lambda _obj=None: service.cancel(channel))
        self._sync_empty_state_overlay()

    def _t(self, key: str, **kwargs) -> str:
        try:
            from services import get_i18n_manager

            manager = get_i18n_manager()
            if manager is not None:
                return manager.translate(key, **kwargs)
        except Exception:
            pass
        return key

    def _empty_state_text(self) -> str:
        return self._t("Drag and drop files or folders here\nor click the buttons above to add")

    def _set_empty_hint_color(self, *, error: bool = False) -> None:
        if error:
            self.empty_hint_label.setTextColor(QColor("#D13438"), QColor("#FF7B7B"))
            return
        self.empty_hint_label.setTextColor(
            QColor(0, 0, 0, 130),
            QColor(255, 255, 255, 150),
        )

    def refresh_empty_state_text(self) -> None:
        if self._state == "empty":
            self.empty_hint_label.setText(self._empty_state_text())
        self._sync_empty_state_overlay()

    def _capture_view_state(self) -> None:
        self._expanded_keys = set()

        def visit(parent: QModelIndex = QModelIndex()) -> None:
            for row in range(self.catalog_model.rowCount(parent)):
                index = self.catalog_model.index(row, 0, parent)
                if self.isExpanded(index):
                    path = index.data(PATH_ROLE)
                    if isinstance(path, str):
                        self._expanded_keys.add(canonical_path_key(path))
                visit(index)

        visit()
        current_path = self.currentIndex().data(PATH_ROLE)
        if isinstance(current_path, str):
            self._restore_selected_path = current_path

    def _restore_view_state(self) -> None:
        for key in self._expanded_keys:
            item = self.catalog_model._path_items.get(key)
            if item is not None:
                self.expand(self.catalog_model.createIndex(item.row, 0, item))
        if self._restore_selected_path:
            self._select_path(self._restore_selected_path, emit_if_missing=False)

    def set_loading(self, text: Optional[str] = None) -> None:
        self._capture_view_state()
        self._view_generation += 1
        self._thumbnail_pending.clear()
        self.catalog_model.clear()
        self._state = "loading"
        self._set_empty_hint_color()
        self.empty_hint_label.setText(text or self._t("正在加载文件列表..."))
        self._sync_empty_state_overlay()

    def set_snapshot(self, snapshot: FileCatalogSnapshot) -> None:
        self._view_generation += 1
        self._thumbnail_pending.clear()
        self.catalog_model.set_snapshot(snapshot)
        self._sources = list(snapshot.sources)
        self._source_keys = {canonical_path_key(path) for path in self._sources}
        self._excluded_folders = {canonical_path_key(path) for path in snapshot.excluded_folders}
        self._excluded_files = {canonical_path_key(path) for path in snapshot.excluded_files}
        self.setRootIsDecorated(any(node.kind == KIND_FOLDER for node in snapshot.roots))
        self._state = "empty" if not snapshot.roots else "ready"
        self._set_empty_hint_color()
        self.empty_hint_label.setText(self._empty_state_text())
        self._restore_view_state()
        self._sync_empty_state_overlay()
        self._schedule_visible_thumbnail_loads()

    def set_error(self, message: str) -> None:
        self._view_generation += 1
        self._thumbnail_pending.clear()
        self.catalog_model.clear()
        self._state = "error"
        self._set_empty_hint_color(error=True)
        self.empty_hint_label.setText(message)
        self._sync_empty_state_overlay()

    @pyqtSlot(str, int)
    def _on_catalog_loading(self, channel: str, generation: int) -> None:
        if channel != self._channel:
            return
        self._expected_catalog_generation = generation
        self.set_loading()

    @pyqtSlot(str, int, object)
    def _on_catalog_ready(self, channel: str, generation: int, snapshot: object) -> None:
        if channel == self._channel and generation == self._expected_catalog_generation:
            self.set_snapshot(snapshot)

    @pyqtSlot(str, int, str)
    def _on_catalog_error(self, channel: str, generation: int, message: str) -> None:
        if channel == self._channel and generation == self._expected_catalog_generation:
            self.set_error(message)

    def _request_snapshot(self) -> None:
        self._expected_catalog_generation = self._data_service.request_snapshot(
            self._channel,
            tuple(self._sources),
            tuple(self._excluded_folders),
            tuple(self._excluded_files),
        )

    def add_files(self, file_paths: list[str]) -> None:
        changed = False
        for path in file_paths:
            if not path:
                continue
            normalized = os.path.abspath(os.path.normpath(path))
            key = canonical_path_key(normalized)
            if key in self._source_keys:
                continue
            self._source_keys.add(key)
            self._sources.append(normalized)
            changed = True
        if changed:
            self._request_snapshot()

    def add_files_from_tree(self, folder_tree: dict) -> None:
        """旧接口适配：只取树根并交给后台全量扫描；新调用应直接 set_snapshot。"""
        if not folder_tree:
            self.set_snapshot(FileCatalogSnapshot.empty())
            return
        folders = {canonical_path_key(path): os.path.abspath(os.path.normpath(path)) for path in folder_tree}
        roots = []
        for key, path in folders.items():
            parent = os.path.dirname(key)
            is_root = True
            while parent and parent != os.path.dirname(parent):
                if parent in folders:
                    is_root = False
                    break
                parent = os.path.dirname(parent)
            if is_root:
                roots.append(path)
        self._sources = sorted(roots, key=natural_sort_key)
        self._source_keys = {canonical_path_key(path) for path in self._sources}
        self._request_snapshot()

    def add_files_with_tree(self, file_paths: list[str], folder_map: Optional[dict] = None) -> None:
        del folder_map
        self.add_files(file_paths)

    def remove_file(self, file_path: str) -> None:
        item = self.catalog_model.item_for_path(file_path)
        kind = item.kind if item is not None else None
        key = canonical_path_key(file_path)
        if key in self._source_keys:
            self._source_keys.remove(key)
            self._sources = [path for path in self._sources if canonical_path_key(path) != key]
            if kind == KIND_FOLDER:
                self._excluded_folders = {
                    excluded for excluded in self._excluded_folders
                    if not (excluded == key or excluded.startswith(key + os.sep))
                }
                self._excluded_files = {
                    excluded for excluded in self._excluded_files
                    if not excluded.startswith(key + os.sep)
                }
        elif kind == KIND_FOLDER:
            self._excluded_folders.add(key)
        else:
            self._excluded_files.add(key)

        removed = self.catalog_model.remove_path(file_path)
        removed_keys = {canonical_path_key(path) for path in removed}
        for cache_key in tuple(self._thumbnail_cache):
            if cache_key[0] in removed_keys:
                self._thumbnail_cache.pop(cache_key, None)
        self._sync_empty_state_overlay()

    def clear(self, clear_cache: bool = False) -> None:
        self._data_service.cancel(self._channel)
        self._expected_catalog_generation += 1
        self._view_generation += 1
        self._sources.clear()
        self._source_keys.clear()
        self._excluded_folders.clear()
        self._excluded_files.clear()
        self._thumbnail_pending.clear()
        self._expanded_keys.clear()
        self._restore_selected_path = None
        self.catalog_model.clear()
        if clear_cache:
            self._thumbnail_cache.clear()
        self._state = "empty"
        self._set_empty_hint_color()
        self.empty_hint_label.setText(self._empty_state_text())
        self._sync_empty_state_overlay()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_empty_state_overlay()
        self._schedule_visible_thumbnail_loads()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._schedule_visible_thumbnail_loads()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.catalog_model.rowCount() != 0:
            return
        painter = QPainter(self.viewport())
        pen = QPen(QColor(127, 127, 127, 105), 1.5, Qt.PenStyle.DashLine)
        pen.setDashPattern([6, 4])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.viewport().rect().adjusted(16, 16, -16, -16), 12, 12)

    def _sync_empty_state_overlay(self) -> None:
        empty = self.catalog_model.rowCount() == 0
        self.empty_hint_label.setVisible(empty)
        if empty:
            self.empty_hint_label.setGeometry(self.viewport().rect().adjusted(20, 20, -20, -20))
            self.empty_hint_label.raise_()
        self.viewport().update()

    def _on_selection_changed(self, *_args) -> None:
        index = self.currentIndex()
        if index.isValid() and index.data(KIND_ROLE) == KIND_IMAGE:
            path = index.data(PATH_ROLE)
            if isinstance(path, str):
                self.file_selected.emit(path)

    def _schedule_visible_thumbnail_loads(self, *_args) -> None:
        if self._thumbnail_update_scheduled:
            return
        self._thumbnail_update_scheduled = True
        _single_shot(self._UI_COALESCE_MS, self, self._load_visible_thumbnails)

    def _load_visible_thumbnails(self) -> None:
        self._thumbnail_update_scheduled = False
        if not self.isVisible() or self.catalog_model.rowCount() == 0:
            return
        index = self.indexAt(QPoint(4, 1))
        if not index.isValid():
            index = self.catalog_model.index(0, 0)
        while index.isValid():
            rect = self.visualRect(index)
            if rect.top() > self.viewport().height():
                break
            if rect.bottom() >= 0 and index.data(KIND_ROLE) == KIND_IMAGE:
                self._request_thumbnail(index)
            index = self.indexBelow(index)

    def _request_thumbnail(self, index: QModelIndex) -> None:
        global _thumbnail_executor
        item = index.data(NODE_ROLE)
        if not isinstance(item, _CatalogItem) or _thumbnail_executor is None:
            return
        cache_key = item.thumbnail_key
        cached = self._thumbnail_cache.get(cache_key)
        if cached is not None:
            self._thumbnail_cache.move_to_end(cache_key)
            self.catalog_model.set_thumbnail(item.path, cached)
            return
        pending_key = (self._view_generation, cache_key)
        if pending_key in self._thumbnail_pending:
            return
        self._thumbnail_pending.add(pending_key)

        bridge_ref = weakref.ref(self._thumbnail_bridge)
        generation = self._view_generation
        future = _thumbnail_executor.submit(_load_thumbnail_worker, item.path)

        def done(completed: Future, token=generation, key=cache_key, path=item.path) -> None:
            try:
                _loaded_path, image = completed.result()
            except Exception:
                image = QImage()
            bridge = bridge_ref()
            if bridge is not None:
                try:
                    bridge.loaded.emit(token, key, path, image)
                except RuntimeError:
                    pass

        future.add_done_callback(done)

    @pyqtSlot(int, object, str, object)
    def _on_thumbnail_loaded(self, generation: int, cache_key: object, path: str, image: object) -> None:
        pending_key = (generation, cache_key)
        self._thumbnail_pending.discard(pending_key)
        if generation != self._view_generation or not isinstance(image, QImage) or image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        self._thumbnail_cache[cache_key] = pixmap
        self._thumbnail_cache.move_to_end(cache_key)
        while len(self._thumbnail_cache) > self._THUMBNAIL_CACHE_SIZE:
            self._thumbnail_cache.popitem(last=False)
        self.catalog_model.set_thumbnail(path, pixmap)

    def _select_path(self, path: str, *, emit_if_missing: bool = True) -> bool:
        index = self.catalog_model.index_for_path(path)
        if not index.isValid():
            return False if emit_if_missing else False
        parent = index.parent()
        ancestors = []
        while parent.isValid():
            ancestors.append(parent)
            parent = parent.parent()
        for ancestor in reversed(ancestors):
            self.expand(ancestor)
        self.setCurrentIndex(index)
        self.scrollTo(index, TreeView.ScrollHint.EnsureVisible)
        return True

    def select_next_image(self) -> None:
        paths = self.catalog_model.image_paths()
        if not paths:
            return
        current = self.currentIndex().data(PATH_ROLE)
        keys = [canonical_path_key(path) for path in paths]
        if not isinstance(current, str) or canonical_path_key(current) not in keys:
            self._select_path(paths[0])
            return
        next_index = keys.index(canonical_path_key(current)) + 1
        if next_index < len(paths):
            self._select_path(paths[next_index])

    def select_prev_image(self) -> None:
        paths = self.catalog_model.image_paths()
        current = self.currentIndex().data(PATH_ROLE)
        if not paths or not isinstance(current, str):
            return
        keys = [canonical_path_key(path) for path in paths]
        current_key = canonical_path_key(current)
        if current_key not in keys:
            return
        previous_index = keys.index(current_key) - 1
        if previous_index >= 0:
            self._select_path(paths[previous_index])

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()
