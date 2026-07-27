import os
import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from manga_translator.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from PyQt6.QtCore import QObject, QRectF, QSize, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTreeWidgetItem,
    QWidget,
)
from qfluentwidgets import BodyLabel, CardWidget, FluentIcon as FIF, StrongBodyLabel, ToolButton, TreeWidget, isDarkTheme

from manga_translator.utils import open_pil_image

# 全局线程池，用于异步加载缩略图
_thumbnail_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="thumbnail_loader")
_directory_scan_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="directory_scanner")


def shutdown_thumbnail_executor():
    """关闭后台线程池"""
    global _thumbnail_executor, _directory_scan_executor
    for executor in (_thumbnail_executor, _directory_scan_executor):
        try:
            executor.shutdown(wait=False)
        except Exception:
            pass


def _single_shot(msec: int, owner: QObject, slot) -> None:
    """带接收者上下文的 singleShot：owner 销毁后回调不再触发。

    PyQt6 的 QTimer.singleShot 没有 (msec, receiver, callable) 重载，
    这里用父对象为 owner 的一次性 QTimer 实现等价的存活保护，
    避免回调在控件销毁后触发导致 RuntimeError。
    """
    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.timeout.connect(slot)
    timer.timeout.connect(timer.deleteLater)
    timer.start(msec)


class ThumbnailSignals(QObject):
    """用于从工作线程发送信号到主线程"""
    thumbnail_loaded = pyqtSignal(str, object)  # file_path, pixmap or None


def natural_sort_key(path: str):
    """
    生成自然排序的键，支持数字排序
    例如: file1.jpg, file2.jpg, file10.jpg 会按 1, 2, 10 排序
    """
    filename = os.path.basename(path)
    parts = []
    for part in re.split(r'(\d+)', filename):
        if part.isdigit():
            parts.append(int(part))
        else:
            parts.append(part.lower())
    return parts


def _scan_directory_worker(folder_path: str, supported_extensions: set[str]) -> tuple[str, List[str], List[str]]:
    """扫描单层目录，不递归创建 UI。"""
    norm_folder = os.path.normpath(folder_path)
    subdirs = []
    files = []
    with os.scandir(norm_folder) as entries:
        for entry in entries:
            if entry.name == 'manga_translator_work':
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    subdirs.append(os.path.normpath(entry.path))
                elif entry.is_file(follow_symlinks=False) and os.path.splitext(entry.name)[1].lower() in supported_extensions:
                    files.append(os.path.normpath(entry.path))
            except OSError:
                continue

    subdirs.sort(key=natural_sort_key)
    files.sort(key=natural_sort_key)
    return norm_folder, subdirs, files


def _load_thumbnail_worker(file_path: str) -> tuple[str, Optional[QPixmap]]:
    """
    在工作线程中加载缩略图
    返回 (file_path, pixmap) 或 (file_path, None) 如果失败
    """
    try:
        img = open_pil_image(file_path, eager=False)
        img.thumbnail((40, 40))
        
        # Convert PIL image to QPixmap
        if img.mode == 'RGB':
            q_img = QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
        elif img.mode == 'RGBA':
            q_img = QImage(img.tobytes(), img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
        else:  # Fallback for other modes like L, P, etc.
            img = img.convert('RGBA')
            q_img = QImage(img.tobytes(), img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)

        pixmap = QPixmap.fromImage(q_img)
        return (file_path, pixmap)
    except Exception as e:
        print(f"Error loading thumbnail for {file_path}: {e}")
        return (file_path, None)


class FileItemWidget(CardWidget):
    """自定义列表项，用于显示缩略图、文件名和移除按钮"""
    remove_requested = pyqtSignal(str)
    ROW_HEIGHT = 58
    
    # MAX Cache Size
    MAX_CACHE_SIZE = 200
    # 类级别的缩略图缓存 (LRU)
    _thumbnail_cache: 'OrderedDict[str, QPixmap]' = OrderedDict()
    # 类级别的信号对象（所有实例共享）
    _signals = ThumbnailSignals()
    # 存储所有活动的实例，用于分发缩略图信号。
    # 条目在 destroyed 信号里移除（不能靠 __del__：注册表本身持强引用时它永不触发）。
    _active_instances: Dict[str, List['FileItemWidget']] = {}

    def __init__(self, file_path, is_folder=False, parent=None, defer_thumbnail: bool = False):
        super().__init__(parent)
        self.file_path = file_path
        self.is_folder = is_folder
        self._thumbnail_loading = False
        self._thumbnail_loaded = False
        self._thumbnail_failed = False
        self._display_name = os.path.basename(file_path)
        self._count_suffix = ""
        self.setBorderRadius(8)
        self.setFixedHeight(self.ROW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # 注册实例；C++ 对象销毁时从注册表移除，保证注册表只含存活实例
        if not is_folder and not os.path.isdir(file_path):
            FileItemWidget._active_instances.setdefault(file_path, []).append(self)
            self.destroyed.connect(
                lambda _obj=None, path=file_path, instance_id=id(self):
                FileItemWidget._unregister_instance(path, instance_id)
            )

        # 注意：不要占用 self.layout（会遮蔽 QWidget.layout()）
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(10, 8, 8, 8)
        self._layout.setSpacing(10)

        # Thumbnail
        self.thumbnail_label = QLabel(self)
        self.thumbnail_label.setFixedSize(40, 40)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self.thumbnail_label)

        if is_folder or os.path.isdir(self.file_path):
            self.thumbnail_label.setPixmap(FIF.FOLDER.icon().pixmap(QSize(32, 32)))
        elif self._is_archive_file(self.file_path):
            # 压缩包/文档文件显示特殊图标
            self.thumbnail_label.setPixmap(FIF.ZIP_FOLDER.icon().pixmap(QSize(32, 32)))
        else:
            # 连接全局信号（只连接一次）
            if not hasattr(FileItemWidget, '_signals_connected'):
                FileItemWidget._signals.thumbnail_loaded.connect(FileItemWidget._dispatch_thumbnail)
                FileItemWidget._signals_connected = True
            if defer_thumbnail:
                if self.file_path in FileItemWidget._thumbnail_cache:
                    self._apply_cached_thumbnail()
                else:
                    self.thumbnail_label.clear()
            else:
                self.ensure_thumbnail_loaded()

        # File Name
        self.base_display_name = self._display_name  # 保存基础名称
        
        self.name_label = BodyLabel(self._display_name, self)
        self.name_label.setWordWrap(False)
        self.name_label.setMinimumWidth(0)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.name_label.setToolTip(file_path)
        self._layout.addWidget(self.name_label, 1)  # Stretch factor

        # Remove Button
        self.remove_button = ToolButton(FIF.CLOSE, self)
        self.remove_button.setFixedSize(34, 34)
        self.remove_button.setIconSize(QSize(16, 16))
        self.remove_button.setToolTip("Remove")
        self.remove_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 防止获取焦点
        self.remove_button.clicked.connect(self._emit_remove_request)
        self._layout.addWidget(self.remove_button)
        _single_shot(0, self, self._update_elided_name)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elided_name()

    def _update_elided_name(self):
        available_width = max(24, self.name_label.width())
        metrics = QFontMetrics(self.name_label.font())
        next_text = ""
        if self._count_suffix:
            suffix_width = metrics.horizontalAdvance(self._count_suffix)
            if suffix_width < available_width:
                base_width = max(12, available_width - suffix_width)
                base_text = metrics.elidedText(self.base_display_name, Qt.TextElideMode.ElideMiddle, base_width)
                next_text = f"{base_text}{self._count_suffix}"
            else:
                next_text = self._count_suffix.strip()
        else:
            next_text = metrics.elidedText(self._display_name, Qt.TextElideMode.ElideMiddle, available_width)
        if self.name_label.text() != next_text:
            self.name_label.setText(next_text)
    
    @classmethod
    def _unregister_instance(cls, file_path: str, instance_id: int):
        """destroyed 回调：把已销毁的实例从注册表移除。

        此时 C++ 对象已析构，不能触碰实例本身，只按 id 比对移除。
        """
        instances = cls._active_instances.get(file_path)
        if not instances:
            return
        instances[:] = [instance for instance in instances if id(instance) != instance_id]
        if not instances:
            del cls._active_instances[file_path]

    @classmethod
    def _dispatch_thumbnail(cls, file_path: str, pixmap: Optional[QPixmap]):
        """分发缩略图到目标文件对应的存活实例"""
        for instance in list(cls._active_instances.get(file_path, ())):
            try:
                instance._on_thumbnail_loaded(file_path, pixmap)
            except RuntimeError:
                # destroyed 尚未派发完成的窗口期兜底
                cls._unregister_instance(file_path, id(instance))

    def update_file_count(self, count: int):
        """更新文件夹显示的文件数量"""
        if self.is_folder:
            self._count_suffix = f" ({count}个文件)"
            self._display_name = f"{self.base_display_name}{self._count_suffix}"
            self._update_elided_name()

    def clear_file_count(self):
        """清除文件夹数量后缀，用于数量未知的懒加载目录。"""
        if self.is_folder:
            self._count_suffix = ""
            self._display_name = self.base_display_name
            self._update_elided_name()

    def _apply_cached_thumbnail(self) -> bool:
        """应用已缓存的缩略图，不触发文件读取。"""
        pixmap = FileItemWidget._thumbnail_cache.get(self.file_path)
        if pixmap is None:
            return False
        self.thumbnail_label.setPixmap(pixmap)
        FileItemWidget._thumbnail_cache.move_to_end(self.file_path)
        self._thumbnail_loaded = True
        return True

    def ensure_thumbnail_loaded(self):
        """按需异步加载缩略图，只有可见项会调用。"""
        if self.is_folder or os.path.isdir(self.file_path) or self._is_archive_file(self.file_path):
            return
        if self._thumbnail_loaded or self._thumbnail_loading or self._thumbnail_failed:
            return

        # 检查缓存
        if self._apply_cached_thumbnail():
            return
        
        # 显示加载中提示
        self.thumbnail_label.setText("...")
        self._thumbnail_loading = True
        
        # 提交到线程池异步加载
        future = _thumbnail_executor.submit(_load_thumbnail_worker, self.file_path)
        future.add_done_callback(self._on_thumbnail_future_done)

    def _load_thumbnail(self):
        """兼容旧调用：改为按需加载入口。"""
        self.ensure_thumbnail_loaded()
    
    def _on_thumbnail_future_done(self, future):
        """线程池任务完成回调"""
        try:
            file_path, pixmap = future.result()
            # 通过信号发送到主线程
            FileItemWidget._signals.thumbnail_loaded.emit(file_path, pixmap)
        except Exception as e:
            print(f"Error in thumbnail future callback: {e}")
    
    def _on_thumbnail_loaded(self, file_path: str, pixmap: Optional[QPixmap]):
        """在主线程中接收缩略图加载完成的信号"""
        self._thumbnail_loading = False
        
        # 检查 widget 是否还存在
        try:
            if pixmap:
                self.thumbnail_label.setPixmap(pixmap)
                self._thumbnail_loaded = True
                
                # 更新缓存 (LRU逻辑)
                if file_path in FileItemWidget._thumbnail_cache:
                    # 如果已存在，移动到末尾（标记为最近使用）
                    FileItemWidget._thumbnail_cache.move_to_end(file_path)
                    # 更新内容（以防万一）
                    FileItemWidget._thumbnail_cache[file_path] = pixmap
                else:
                    # 如果不存在，添加到末尾
                    FileItemWidget._thumbnail_cache[file_path] = pixmap
                    # 检查容量
                    if len(FileItemWidget._thumbnail_cache) > FileItemWidget.MAX_CACHE_SIZE:
                        # 移除第一个元素（最久未使用）
                        FileItemWidget._thumbnail_cache.popitem(last=False)
            else:
                self._thumbnail_failed = True
                self.thumbnail_label.setText("ERR")
        except RuntimeError:
            # Widget 已被删除，忽略
            pass

    def _emit_remove_request(self):
        """发射删除请求信号"""
        self.remove_requested.emit(self.file_path)
    
    @staticmethod
    def _is_archive_file(file_path: str) -> bool:
        """检查文件是否是压缩包/文档格式"""
        archive_extensions = {'.pdf', '.epub', '.cbz', '.cbr', '.zip'}
        ext = os.path.splitext(file_path)[1].lower()
        return ext in archive_extensions

    def get_path(self):
        return self.file_path
    
    @classmethod
    def clear_thumbnail_cache(cls):
        """清空缩略图缓存"""
        cls._thumbnail_cache.clear()
    
    @classmethod
    def remove_from_cache(cls, file_path: str):
        """从缓存中移除指定文件的缩略图"""
        if file_path in cls._thumbnail_cache:
            del cls._thumbnail_cache[file_path]


class FileListView(TreeWidget):
    """显示文件列表的自定义控件（支持文件夹分组）"""
    file_remove_requested = pyqtSignal(str)
    file_selected = pyqtSignal(str)
    files_dropped = pyqtSignal(list)  # 新增：拖放文件信号
    _folders_scanned = pyqtSignal(list)  # 内部信号：文件夹扫描完成
    _filesystem_directory_scanned = pyqtSignal(str, object, object)
    _ROW_HEIGHT = FileItemWidget.ROW_HEIGHT + 8
    _EMPTY_STATE_MARGIN = 16
    _FS_PLACEHOLDER_ROLE = Qt.ItemDataRole.UserRole + 1
    _FS_LOADED_ROLE = Qt.ItemDataRole.UserRole + 2
    _FS_LOADING_ROLE = Qt.ItemDataRole.UserRole + 3
    _FS_DEFERRED_WIDGET_ROLE = Qt.ItemDataRole.UserRole + 4
    _FS_TREE_BACKED_ROLE = Qt.ItemDataRole.UserRole + 5
    _FS_POPULATE_CHUNK_SIZE = 120
    _UI_COALESCE_MS = 16
    _SUPPORTED_IMAGE_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS
    _SUPPORTED_ARCHIVE_EXTENSIONS = {'.pdf', '.epub', '.cbz', '.cbr', '.zip'}
    _SUPPORTED_LIST_EXTENSIONS = set(_SUPPORTED_IMAGE_EXTENSIONS) | _SUPPORTED_ARCHIVE_EXTENSIONS

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        
        # 导入i18n
        from services import get_i18n_manager
        self.i18n = get_i18n_manager()
        
        # 设置树形控件属性
        self.setHeaderHidden(True)  # 隐藏标题栏
        self.setColumnCount(1)
        self.setIndentation(12)  # 设置缩进
        self.setRootIsDecorated(False)
        self.setAnimated(True)  # 启用展开/折叠动画
        self.setBorderRadius(8)
        self.setUniformRowHeights(True)
        self.header().setStretchLastSection(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(self.ScrollMode.ScrollPerPixel)
        
        # 启用拖放
        self.setAcceptDrops(True)
        self.setDragEnabled(False)  # 禁用拖出，只允许拖入
        
        # 存储文件夹到树节点的映射
        self.folder_nodes: Dict[str, QTreeWidgetItem] = {}
        self._thumbnail_update_scheduled = False
        self._root_decoration_update_scheduled = False
        self._item_width_sync_scheduled = False
        self._fs_pending_items: Dict[str, QTreeWidgetItem] = {}
        self._fs_populate_jobs = {}
        self._folder_tree_data: Dict[str, dict] = {}
        
        # 连接选择信号
        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.verticalScrollBar().valueChanged.connect(self._schedule_visible_thumbnail_loads)
        self.verticalScrollBar().valueChanged.connect(self._schedule_item_widget_width_sync)
        self.itemExpanded.connect(self._schedule_visible_thumbnail_loads)
        self.itemExpanded.connect(self._schedule_item_widget_width_sync)
        self.itemCollapsed.connect(self._schedule_visible_thumbnail_loads)
        self.itemCollapsed.connect(self._schedule_item_widget_width_sync)
        self.itemExpanded.connect(self._on_item_expanded)
        
        # 连接内部信号（确保在主线程中处理）
        self._folders_scanned.connect(self._on_folders_scanned)
        self._filesystem_directory_scanned.connect(self._on_filesystem_directory_scanned)

        self.empty_hint_label = StrongBodyLabel(self._empty_state_text(), self.viewport())
        self.empty_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint_label.setWordWrap(True)
        self.empty_hint_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.empty_hint_label.setTextColor(
            QColor(0, 0, 0, 130),
            QColor(255, 255, 255, 150),
        )
        self._sync_empty_state_overlay()

    def _refresh_root_decoration(self):
        has_top_level_folder = False
        for index in range(self.topLevelItemCount()):
            path = self.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
            if isinstance(path, str) and os.path.isdir(path):
                has_top_level_folder = True
                break
        if self.rootIsDecorated() != has_top_level_folder:
            self.setRootIsDecorated(has_top_level_folder)
        self._schedule_item_widget_width_sync()

    def _schedule_root_decoration_refresh(self):
        if self._root_decoration_update_scheduled:
            return
        self._root_decoration_update_scheduled = True
        _single_shot(0, self, self._run_root_decoration_refresh)

    def _run_root_decoration_refresh(self):
        self._root_decoration_update_scheduled = False
        self._refresh_root_decoration()

    def _schedule_item_widget_width_sync(self):
        if self._item_width_sync_scheduled:
            return
        self._item_width_sync_scheduled = True
        _single_shot(self._UI_COALESCE_MS, self, self._run_item_widget_width_sync)

    def _run_item_widget_width_sync(self):
        self._item_width_sync_scheduled = False
        self._sync_item_widget_widths()

    def setItemWidget(self, item: QTreeWidgetItem, column: int, widget: QWidget):
        if isinstance(widget, FileItemWidget):
            item.setSizeHint(column, QSize(0, self._ROW_HEIGHT))
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            widget.setFixedHeight(FileItemWidget.ROW_HEIGHT)
        super().setItemWidget(item, column, widget)
        self._sync_empty_state_overlay()
        self._schedule_root_decoration_refresh()
        if isinstance(widget, FileItemWidget):
            self._schedule_visible_thumbnail_loads()

    def _finalize_folder_item(self, folder_item: QTreeWidgetItem):
        folder_item.setExpanded(False)
        self._schedule_root_decoration_refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_item_widget_widths()
        self._sync_empty_state_overlay()
        self._schedule_visible_thumbnail_loads()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_visible_thumbnail_loads()

    def _schedule_visible_thumbnail_loads(self, *_args):
        if self._thumbnail_update_scheduled:
            return
        self._thumbnail_update_scheduled = True
        _single_shot(self._UI_COALESCE_MS, self, self._load_visible_thumbnails)

    def _load_visible_thumbnails(self):
        self._thumbnail_update_scheduled = False
        for item in self._collect_visible_items():
            widget = self.itemWidget(item, 0)
            if widget is None and item.data(0, self._FS_DEFERRED_WIDGET_ROLE):
                widget = self._materialize_file_item_widget(item)
            if isinstance(widget, FileItemWidget):
                widget.ensure_thumbnail_loaded()

    def _collect_visible_items(self) -> List[QTreeWidgetItem]:
        viewport = self.viewport()
        if viewport is None or viewport.height() <= 0 or viewport.width() <= 0:
            return []

        visible_items = {}
        width = viewport.width()
        sample_x_positions = {
            max(0, min(4, width - 1)),
            max(0, width // 2),
            max(0, width - 4),
        }
        step = max(1, self._ROW_HEIGHT // 2)
        for y in range(0, viewport.height() + step, step):
            for x in sample_x_positions:
                item = self.itemAt(x, y)
                if item is not None:
                    visible_items[id(item)] = item
        return list(visible_items.values())

    def _materialize_file_item_widget(self, item: QTreeWidgetItem) -> Optional[FileItemWidget]:
        file_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(file_path, str) or os.path.isdir(file_path):
            return None

        existing_widget = self.itemWidget(item, 0)
        if isinstance(existing_widget, FileItemWidget):
            item.setData(0, self._FS_DEFERRED_WIDGET_ROLE, False)
            return existing_widget

        file_widget = FileItemWidget(file_path, is_folder=False, defer_thumbnail=True)
        file_widget.remove_requested.connect(self.file_remove_requested.emit)
        item.setText(0, "")
        item.setData(0, self._FS_DEFERRED_WIDGET_ROLE, False)
        self.setItemWidget(item, 0, file_widget)
        return file_widget

    def _sync_item_widget_widths(self):
        """只同步列宽；行内控件几何交给 QTreeView 自己管理。

        item widget 由视图按 visualRect 摆放（updateEditorGeometries），
        手动 setFixedWidth 会与视图的几何管理抢控制权，且只覆盖可见行。
        控件宽度变化后省略号文本由 FileItemWidget.resizeEvent 自行刷新。
        """
        viewport_width = max(120, self.viewport().width() - 6)
        self.setColumnWidth(0, viewport_width)
    
    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key
    
    def refresh_empty_state_text(self):
        """重绘空列表占位提示文本（用于语言切换）。"""
        self.empty_hint_label.setText(self._empty_state_text())
        self._sync_empty_state_overlay()
        self.viewport().update()
        self.update()

    def _empty_state_text(self) -> str:
        return self._t("Drag and drop files or folders here\nor click the buttons above to add")

    def _empty_state_color(self) -> QColor:
        return QColor(255, 255, 255, 110) if isDarkTheme() else QColor(0, 0, 0, 105)

    def _empty_state_rect(self):
        return self.viewport().rect().adjusted(
            self._EMPTY_STATE_MARGIN,
            self._EMPTY_STATE_MARGIN,
            -self._EMPTY_STATE_MARGIN,
            -self._EMPTY_STATE_MARGIN,
        )

    def _sync_empty_state_overlay(self):
        is_empty = self.topLevelItemCount() == 0
        self.empty_hint_label.setVisible(is_empty)
        if not is_empty:
            return
        self.empty_hint_label.setGeometry(self._empty_state_rect())
        self.empty_hint_label.raise_()

    def paintEvent(self, event):
        """重写绘制事件，在列表为空时显示提示"""
        super().paintEvent(event)
        if self.topLevelItemCount() != 0:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(self._empty_state_color(), 1.5, Qt.PenStyle.DashLine)
        pen.setDashPattern([6, 4])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(self._empty_state_rect()), 12, 12)
        painter.end()

    def _on_selection_changed(self):
        """处理选择变化"""
        selected_items = self.selectedItems()
        if not selected_items:
            return
        
        tree_item = selected_items[0]
        file_path = tree_item.data(0, Qt.ItemDataRole.UserRole)
        
        # 只有当选中的是文件（不是文件夹节点）时才发出信号
        if file_path and not os.path.isdir(file_path):
            self.file_selected.emit(file_path)

    def _is_supported_list_file(self, file_path: str) -> bool:
        return os.path.splitext(file_path)[1].lower() in self._SUPPORTED_LIST_EXTENSIONS

    def _filesystem_key(self, path: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.normpath(path)))

    def _is_filesystem_placeholder(self, item: QTreeWidgetItem) -> bool:
        return bool(item.data(0, self._FS_PLACEHOLDER_ROLE))

    def _has_filesystem_placeholder(self, folder_item: QTreeWidgetItem) -> bool:
        for index in range(folder_item.childCount()):
            if self._is_filesystem_placeholder(folder_item.child(index)):
                return True
        return False

    def _add_filesystem_placeholder(self, folder_item: QTreeWidgetItem):
        if self._has_filesystem_placeholder(folder_item):
            return
        placeholder = QTreeWidgetItem()
        placeholder.setText(0, "...")
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        placeholder.setData(0, self._FS_PLACEHOLDER_ROLE, True)
        folder_item.addChild(placeholder)

    def _remove_filesystem_placeholders(self, folder_item: QTreeWidgetItem):
        for index in range(folder_item.childCount() - 1, -1, -1):
            child = folder_item.child(index)
            if self._is_filesystem_placeholder(child):
                folder_item.removeChild(child)

    def _folder_has_child_folders(self, folder_item: QTreeWidgetItem) -> bool:
        for index in range(folder_item.childCount()):
            child_path = folder_item.child(index).data(0, Qt.ItemDataRole.UserRole)
            if isinstance(child_path, str) and os.path.isdir(child_path):
                return True
        return False

    def _create_filesystem_folder_item(
        self,
        folder_path: str,
        parent_item: QTreeWidgetItem = None,
        tree_backed: bool = False,
    ) -> QTreeWidgetItem:
        norm_folder = os.path.normpath(folder_path)
        folder_item = QTreeWidgetItem()
        folder_item.setData(0, Qt.ItemDataRole.UserRole, norm_folder)
        folder_item.setData(0, self._FS_LOADED_ROLE, False)
        folder_item.setData(0, self._FS_LOADING_ROLE, False)
        folder_item.setData(0, self._FS_TREE_BACKED_ROLE, tree_backed)

        folder_widget = FileItemWidget(norm_folder, is_folder=True)
        folder_widget.remove_requested.connect(self.file_remove_requested.emit)

        if parent_item is None:
            self.addTopLevelItem(folder_item)
        else:
            parent_item.addChild(folder_item)
        self.setItemWidget(folder_item, 0, folder_widget)
        self.folder_nodes[norm_folder] = folder_item
        self._add_filesystem_placeholder(folder_item)
        self._finalize_folder_item(folder_item)
        return folder_item

    def _add_filesystem_folder(self, folder_path: str, parent_item: QTreeWidgetItem = None) -> Optional[QTreeWidgetItem]:
        norm_folder = os.path.normpath(folder_path)
        existing_item = self.folder_nodes.get(norm_folder)
        if existing_item is not None:
            return existing_item
        return self._create_filesystem_folder_item(norm_folder, parent_item)

    def _add_tree_folder(self, folder_path: str, parent_item: QTreeWidgetItem = None) -> Optional[QTreeWidgetItem]:
        norm_folder = os.path.normpath(folder_path)
        existing_item = self.folder_nodes.get(norm_folder)
        if existing_item is not None:
            return existing_item
        return self._create_filesystem_folder_item(norm_folder, parent_item, tree_backed=True)

    def _request_filesystem_directory(self, folder_item: QTreeWidgetItem):
        folder_path = folder_item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(folder_path, str) or not os.path.isdir(folder_path):
            return
        if folder_item.data(0, self._FS_LOADED_ROLE) or folder_item.data(0, self._FS_LOADING_ROLE):
            return

        norm_folder = os.path.normpath(folder_path)
        folder_key = self._filesystem_key(norm_folder)
        folder_item.setData(0, self._FS_LOADING_ROLE, True)

        if folder_item.data(0, self._FS_TREE_BACKED_ROLE):
            folder_data = self._folder_tree_data.get(norm_folder, {})
            subdirs = list(folder_data.get('subfolders', []))
            files = list(folder_data.get('files', []))
            self._start_filesystem_populate_job(
                folder_key,
                folder_item,
                subdirs,
                files,
                tree_backed=True,
                recursive_file_count=self._count_files_in_folder_tree(norm_folder, self._folder_tree_data),
            )
            return

        self._fs_pending_items[folder_key] = folder_item
        future = _directory_scan_executor.submit(
            _scan_directory_worker,
            norm_folder,
            self._SUPPORTED_LIST_EXTENSIONS,
        )
        future.add_done_callback(
            lambda scan_future, key=folder_key: self._on_filesystem_scan_future_done(key, scan_future)
        )

    def _on_item_expanded(self, item: QTreeWidgetItem):
        self._request_filesystem_directory(item)

    def _on_filesystem_scan_future_done(self, folder_key: str, future):
        try:
            _folder_path, subdirs, files = future.result()
        except Exception as e:
            print(f"Error scanning folder: {e}")
            subdirs = []
            files = []
        self._filesystem_directory_scanned.emit(folder_key, subdirs, files)

    @pyqtSlot(str, object, object)
    def _on_filesystem_directory_scanned(self, folder_key: str, subdirs: List[str], files: List[str]):
        folder_item = self._fs_pending_items.pop(folder_key, None)
        if folder_item is None:
            return

        self._start_filesystem_populate_job(folder_key, folder_item, subdirs, files)

    def _start_filesystem_populate_job(
        self,
        folder_key: str,
        folder_item: QTreeWidgetItem,
        subdirs: List[str],
        files: List[str],
        tree_backed: bool = False,
        recursive_file_count: int = None,
    ):
        self._fs_populate_jobs[folder_key] = {
            'folder_item': folder_item,
            'subdirs': subdirs,
            'files': files,
            'direct_file_count': len(files),
            'recursive_file_count': recursive_file_count,
            'tree_backed': tree_backed,
            'subdir_index': 0,
            'file_index': 0,
            'placeholder_removed': False,
        }
        _single_shot(0, self, lambda key=folder_key: self._process_filesystem_populate_chunk(key))

    def _process_filesystem_populate_chunk(self, folder_key: str):
        job = self._fs_populate_jobs.get(folder_key)
        if job is None:
            return

        folder_item = job['folder_item']
        try:
            if folder_item.treeWidget() is not self:
                self._fs_populate_jobs.pop(folder_key, None)
                return
        except RuntimeError:
            self._fs_populate_jobs.pop(folder_key, None)
            return

        if not job['placeholder_removed']:
            self._remove_filesystem_placeholders(folder_item)
            job['placeholder_removed'] = True

        added_count = 0
        self.setUpdatesEnabled(False)
        try:
            while added_count < self._FS_POPULATE_CHUNK_SIZE and job['subdir_index'] < len(job['subdirs']):
                subdir = job['subdirs'][job['subdir_index']]
                job['subdir_index'] += 1
                if job.get('tree_backed'):
                    self._add_tree_folder(subdir, folder_item)
                else:
                    self._add_filesystem_folder(subdir, folder_item)
                added_count += 1

            while added_count < self._FS_POPULATE_CHUNK_SIZE and job['file_index'] < len(job['files']):
                file_path = job['files'][job['file_index']]
                job['file_index'] += 1
                self._add_file_to_folder(file_path, folder_item, defer_widget=True)
                added_count += 1
        finally:
            self.setUpdatesEnabled(True)

        has_more = job['subdir_index'] < len(job['subdirs']) or job['file_index'] < len(job['files'])
        if has_more:
            _single_shot(1, self, lambda key=folder_key: self._process_filesystem_populate_chunk(key))
            return

        folder_item.setData(0, self._FS_LOADED_ROLE, True)
        folder_item.setData(0, self._FS_LOADING_ROLE, False)
        self._fs_populate_jobs.pop(folder_key, None)
        folder_widget = self.itemWidget(folder_item, 0)
        if isinstance(folder_widget, FileItemWidget):
            if job.get('recursive_file_count') is not None:
                folder_widget.update_file_count(job['recursive_file_count'])
            elif job['direct_file_count'] > 0 or not job['subdirs']:
                folder_widget.update_file_count(job['direct_file_count'])
            else:
                folder_widget.clear_file_count()
        self._schedule_root_decoration_refresh()
        self._schedule_item_widget_width_sync()
        self._schedule_visible_thumbnail_loads()
        self.viewport().update()

    def add_files(self, file_paths: List[str]):
        """添加多个文件/文件夹到列表（异步处理大文件夹）"""
        folders_to_add = []
        files_to_add = []
        
        for path in file_paths:
            norm_path = os.path.normpath(path)
            if os.path.isdir(norm_path):
                folders_to_add.append(norm_path)
            else:
                files_to_add.append(norm_path)
        
        # 立即添加单个文件（不会阻塞）
        for file_path in files_to_add:
            self._add_single_file(file_path)
        
        for folder_path in folders_to_add:
            self._add_filesystem_folder(folder_path)
    
    def _scan_folder_structure(self, folder_path: str):
        """兼容旧调用：递归扫描文件夹结构（不创建UI元素）"""
        all_extensions = self._SUPPORTED_LIST_EXTENSIONS
        structure = {'subdirs': [], 'files': [], 'subdir_data': {}}
        try:
            with os.scandir(folder_path) as entries:
                for entry in entries:
                    if entry.name == 'manga_translator_work':
                        continue
                    try:
                        if entry.is_dir():
                            structure['subdirs'].append(os.path.normpath(entry.path))
                        elif entry.is_file() and os.path.splitext(entry.name)[1].lower() in all_extensions:
                            structure['files'].append(os.path.normpath(entry.path))
                    except OSError:
                        continue

            structure['subdirs'].sort(key=natural_sort_key)
            structure['files'].sort(key=natural_sort_key)
            for subdir in structure['subdirs']:
                structure['subdir_data'][subdir] = self._scan_folder_structure(subdir)
            return structure
        except Exception as e:
            print(f"Error scanning folder {folder_path}: {e}")
            return {'subdirs': [], 'files': [], 'subdir_data': {}}
    
    @pyqtSlot(list)
    def _on_folders_scanned(self, folder_data_list):
        """文件夹扫描完成后的回调（在主线程中创建UI元素）"""
        try:
            for folder_path, folder_data in folder_data_list:
                self._add_folder_tree_from_data(folder_path, folder_data)
        finally:
            # 恢复光标
            QApplication.restoreOverrideCursor()
    
    def _add_folder_tree_from_data(self, folder_path: str, folder_data: dict):
        """从扫描的数据创建文件夹树（在主线程中执行）"""
        if folder_path in self.folder_nodes:
            return
        
        # 创建顶层文件夹节点
        folder_item = QTreeWidgetItem(self)
        folder_item.setData(0, Qt.ItemDataRole.UserRole, folder_path)
        
        folder_widget = FileItemWidget(folder_path, is_folder=True)
        folder_widget.remove_requested.connect(self.file_remove_requested.emit)
        
        self.addTopLevelItem(folder_item)
        self.setItemWidget(folder_item, 0, folder_widget)
        self.folder_nodes[folder_path] = folder_item
        
        # 递归添加子文件夹和文件
        self._populate_folder_tree_from_data(folder_item, folder_path, folder_data)
        
        # 更新文件数量
        file_count = self._count_files_in_tree(folder_item)
        folder_widget.update_file_count(file_count)
        self._finalize_folder_item(folder_item)
    
    def _populate_folder_tree_from_data(self, parent_item: QTreeWidgetItem, folder_path: str, folder_data: dict):
        """从扫描的数据填充文件夹树（在主线程中执行）"""
        # 添加子文件夹
        for subdir in folder_data.get('subdirs', []):
            subdir_item = QTreeWidgetItem(parent_item)
            subdir_item.setData(0, Qt.ItemDataRole.UserRole, subdir)
            
            subdir_widget = FileItemWidget(subdir, is_folder=True)
            subdir_widget.remove_requested.connect(self.file_remove_requested.emit)
            
            parent_item.addChild(subdir_item)
            self.setItemWidget(subdir_item, 0, subdir_widget)
            self.folder_nodes[subdir] = subdir_item
            
            # 递归处理子文件夹
            subdir_data = folder_data.get('subdir_data', {}).get(subdir)
            if subdir_data:
                self._populate_folder_tree_from_data(subdir_item, subdir, subdir_data)
            
            # 更新子文件夹的文件数量
            file_count = self._count_files_in_tree(subdir_item)
            subdir_widget.update_file_count(file_count)
        
        # 添加文件
        for file_path in folder_data.get('files', []):
            file_item = QTreeWidgetItem(parent_item)
            file_item.setData(0, Qt.ItemDataRole.UserRole, file_path)
            
            file_widget = FileItemWidget(file_path, is_folder=False, defer_thumbnail=True)
            file_widget.remove_requested.connect(self.file_remove_requested.emit)
            
            parent_item.addChild(file_item)
            self.setItemWidget(file_item, 0, file_widget)
        
        # 触发重绘
        self.viewport().update()
    
    def add_files_from_tree(self, folder_tree: dict):
        """
        从完整的树结构添加文件
        folder_tree: {folder_path: {'files': [...], 'subfolders': [...]}}
        """
        if not folder_tree:
            return

        self._folder_tree_data = self._normalize_folder_tree(folder_tree)

        # 找到所有根文件夹（没有父文件夹在tree中的文件夹）
        all_folders = set(self._folder_tree_data.keys())
        root_folders = []

        for folder in all_folders:
            is_root = True
            for other_folder in all_folders:
                if folder != other_folder and self._is_child_path(folder, other_folder):
                    is_root = False
                    break
            if is_root:
                root_folders.append(folder)
        
        # 按自然排序
        root_folders.sort(key=natural_sort_key)

        # 编辑器切换时只添加根文件夹，子项仍按展开懒加载，避免重复创建大量 UI 控件。
        for root_folder in root_folders:
            folder_item = self._add_tree_folder(root_folder)
            folder_widget = self.itemWidget(folder_item, 0) if folder_item is not None else None
            if isinstance(folder_widget, FileItemWidget):
                folder_widget.update_file_count(self._count_files_in_folder_tree(root_folder, self._folder_tree_data))

    def _normalize_folder_tree(self, folder_tree: dict) -> dict:
        normalized_tree = {}
        for folder_path, folder_data in folder_tree.items():
            norm_folder = os.path.normpath(folder_path)
            normalized_tree[norm_folder] = {
                'files': [os.path.normpath(file_path) for file_path in folder_data.get('files', [])],
                'subfolders': [os.path.normpath(subfolder) for subfolder in folder_data.get('subfolders', [])],
            }
        return normalized_tree

    def _is_child_path(self, child_path: str, parent_path: str) -> bool:
        child_norm = os.path.normcase(os.path.abspath(os.path.normpath(child_path)))
        parent_norm = os.path.normcase(os.path.abspath(os.path.normpath(parent_path)))
        if child_norm == parent_norm:
            return False
        try:
            return os.path.commonpath([child_norm, parent_norm]) == parent_norm
        except ValueError:
            return False

    def _count_files_in_folder_tree(self, folder_path: str, folder_tree: dict) -> int:
        folder_data = folder_tree.get(folder_path, {})
        count = len(folder_data.get('files', []))
        for subfolder in folder_data.get('subfolders', []):
            count += self._count_files_in_folder_tree(os.path.normpath(subfolder), folder_tree)
        return count
    
    def _create_folder_node_from_tree(self, folder_path: str, folder_tree: dict, parent_item: QTreeWidgetItem = None):
        """从树结构递归创建文件夹节点"""
        if folder_path not in folder_tree:
            return
        
        # 创建文件夹节点
        if parent_item is None:
            folder_item = QTreeWidgetItem(self)
            self.addTopLevelItem(folder_item)
        else:
            folder_item = QTreeWidgetItem(parent_item)
            parent_item.addChild(folder_item)
        
        folder_item.setData(0, Qt.ItemDataRole.UserRole, folder_path)
        
        # 创建文件夹控件
        folder_widget = FileItemWidget(folder_path, is_folder=True)
        folder_widget.remove_requested.connect(self.file_remove_requested.emit)
        self.setItemWidget(folder_item, 0, folder_widget)
        
        # 保存文件夹节点
        self.folder_nodes[folder_path] = folder_item
        
        # 添加该文件夹直接包含的文件
        folder_data = folder_tree[folder_path]
        for file_path in folder_data.get('files', []):
            self._add_file_to_folder(file_path, folder_item)
        
        # 递归添加子文件夹
        subfolders = folder_data.get('subfolders', [])
        subfolders.sort(key=natural_sort_key)
        for subfolder in subfolders:
            self._create_folder_node_from_tree(subfolder, folder_tree, folder_item)
        
        # 更新文件数量
        file_count = self._count_files_in_tree(folder_item)
        folder_widget.update_file_count(file_count)
        self._finalize_folder_item(folder_item)
    
    def add_files_with_tree(self, file_paths: List[str], folder_map: dict = None):
        """
        添加文件列表，并根据folder_map创建树形结构
        只添加file_paths中的文件，不扫描整个文件夹
        
        Args:
            file_paths: 要添加的文件列表
            folder_map: 文件到文件夹的映射 {file_path: folder_path}
        """
        if not folder_map:
            # 如果没有folder_map，使用普通的add_files
            self.add_files(file_paths)
            return
        
        # 按文件夹分组
        folder_groups = {}  # 文件按其直接父文件夹分组
        single_files = []
        all_folders = set()
        
        # 首先收集所有文件的直接父文件夹
        for file_path in file_paths:
            norm_file_path = os.path.normpath(file_path)
            mapped_folder = folder_map.get(file_path)
            
            if mapped_folder:
                norm_mapped_folder = os.path.normpath(mapped_folder)
                
                # 文件应该被添加到其直接父文件夹
                file_dir = os.path.dirname(norm_file_path)
                if file_dir not in folder_groups:
                    folder_groups[file_dir] = []
                folder_groups[file_dir].append(norm_file_path)
                
                # 添加直接父文件夹
                all_folders.add(file_dir)
                
                # 添加 folder_map 中映射的文件夹
                all_folders.add(norm_mapped_folder)
            else:
                single_files.append(norm_file_path)
        
        # 为每个文件夹添加从其到顶层文件夹的所有中间文件夹
        if all_folders:
            expanded_folders = set(all_folders)  # 先包含所有已知文件夹
            
            # 为每个文件夹添加所有父文件夹，直到到达顶层文件夹或根目录
            for folder in list(all_folders):
                current = folder
                while True:
                    parent = os.path.dirname(current)
                    # 如果父文件夹为空或与当前相同（到达根目录），停止
                    if not parent or parent == current:
                        break
                    expanded_folders.add(parent)
                    current = parent
                    # 如果父文件夹已经在原始 all_folders 中，说明这是顶层文件夹，停止
                    if parent in all_folders:
                        break
            
            all_folders = expanded_folders
        
        # 构建文件夹层级关系
        folder_hierarchy = self._build_folder_hierarchy(all_folders)
        
        # 按层级创建文件夹树
        self._create_folder_tree_with_files(folder_hierarchy, folder_groups)
        
        # 添加单独的文件
        for file_path in single_files:
            self._add_single_file(file_path)
    
    def _build_folder_hierarchy(self, folders: set) -> dict:
        """
        构建文件夹的层级关系
        返回: {parent_folder: [child_folders]}
        """
        hierarchy = {}
        root_folders = []
        
        for folder in folders:
            # 查找父文件夹
            parent = None
            for other_folder in folders:
                if folder != other_folder and folder.startswith(other_folder + os.sep):
                    # folder 是 other_folder 的子文件夹
                    if parent is None or len(other_folder) > len(parent):
                        parent = other_folder
            
            if parent:
                if parent not in hierarchy:
                    hierarchy[parent] = []
                hierarchy[parent].append(folder)
            else:
                root_folders.append(folder)
        
        return {'__root__': root_folders, **hierarchy}
    
    def _create_folder_tree_with_files(self, hierarchy: dict, folder_groups: dict, parent_item=None, parent_folder=None):
        """
        递归创建文件夹树形结构
        使用自然排序（数字排序：1, 2, 10 而不是 1, 10, 2）
        """
        folders_to_process = hierarchy.get(parent_folder or '__root__', [])
        
        # 使用自然排序
        for folder_path in sorted(folders_to_process, key=natural_sort_key):
            # 创建文件夹节点
            if parent_item is None:
                # 顶层文件夹
                folder_item = QTreeWidgetItem(self)
                self.addTopLevelItem(folder_item)
            else:
                # 子文件夹
                folder_item = QTreeWidgetItem(parent_item)
                parent_item.addChild(folder_item)
            
            folder_item.setData(0, Qt.ItemDataRole.UserRole, folder_path)
            
            # 创建文件夹控件
            folder_widget = FileItemWidget(folder_path, is_folder=True)
            folder_widget.remove_requested.connect(self.file_remove_requested.emit)
            self.setItemWidget(folder_item, 0, folder_widget)
            
            # 保存文件夹节点
            self.folder_nodes[folder_path] = folder_item
            
            # 添加该文件夹中的文件
            if folder_path in folder_groups:
                for file_path in folder_groups[folder_path]:
                    self._add_file_to_folder(file_path, folder_item)
            
            # 递归处理子文件夹
            if folder_path in hierarchy:
                self._create_folder_tree_with_files(hierarchy, folder_groups, folder_item, folder_path)
            
            # 更新文件数量（包括子文件夹中的文件）
            file_count = len(folder_groups.get(folder_path, []))
            # 递归统计子文件夹中的文件数量
            for child_folder in hierarchy.get(folder_path, []):
                file_count += self._count_files_in_hierarchy(child_folder, hierarchy, folder_groups)
            folder_widget.update_file_count(file_count)
            self._finalize_folder_item(folder_item)
    
    def _count_files_in_hierarchy(self, folder_path: str, hierarchy: dict, folder_groups: dict) -> int:
        """递归统计文件夹及其子文件夹中的文件数量"""
        count = len(folder_groups.get(folder_path, []))
        for child_folder in hierarchy.get(folder_path, []):
            count += self._count_files_in_hierarchy(child_folder, hierarchy, folder_groups)
        return count
    
    def _add_folder_with_files(self, folder_path: str, file_list: List[str]):
        """
        添加文件夹及其指定的文件（不扫描整个文件夹）
        
        Args:
            folder_path: 文件夹路径
            file_list: 要添加的文件列表
        """
        if folder_path in self.folder_nodes:
            # 文件夹已存在，只添加新文件
            folder_item = self.folder_nodes[folder_path]
            for file_path in file_list:
                self._add_file_to_folder(file_path, folder_item)
            # 更新文件数量
            folder_widget = self.itemWidget(folder_item, 0)
            if isinstance(folder_widget, FileItemWidget):
                folder_widget.update_file_count(len(file_list))
            return
        
        # 创建顶层文件夹节点
        folder_item = QTreeWidgetItem(self)
        folder_item.setData(0, Qt.ItemDataRole.UserRole, folder_path)
        
        # 创建文件夹项的自定义控件
        folder_widget = FileItemWidget(folder_path, is_folder=True)
        folder_widget.remove_requested.connect(self.file_remove_requested.emit)
        
        self.addTopLevelItem(folder_item)
        self.setItemWidget(folder_item, 0, folder_widget)
        
        # 保存文件夹节点
        self.folder_nodes[folder_path] = folder_item
        
        # 添加文件到文件夹
        for file_path in file_list:
            self._add_file_to_folder(file_path, folder_item)
        
        # 更新文件数量显示
        folder_widget.update_file_count(len(file_list))
        self._finalize_folder_item(folder_item)
    
    def _add_folder_tree(self, folder_path: str):
        """添加文件夹及其完整的树形结构"""
        if folder_path in self.folder_nodes:
            return  # 文件夹已存在
        
        # 创建顶层文件夹节点
        folder_item = QTreeWidgetItem(self)
        folder_item.setData(0, Qt.ItemDataRole.UserRole, folder_path)
        
        # 创建文件夹项的自定义控件
        folder_widget = FileItemWidget(folder_path, is_folder=True)
        folder_widget.remove_requested.connect(self.file_remove_requested.emit)
        
        self.addTopLevelItem(folder_item)
        self.setItemWidget(folder_item, 0, folder_widget)
        
        # 保存文件夹节点
        self.folder_nodes[folder_path] = folder_item
        
        # 递归添加子文件夹和文件
        self._populate_folder_tree(folder_item, folder_path)
        
        # 更新文件数量显示
        file_count = self._count_files_recursive(folder_path)
        folder_widget.update_file_count(file_count)
        self._finalize_folder_item(folder_item)
    
    def _count_files_recursive(self, folder_path: str) -> int:
        """递归统计文件夹中的图片文件数量"""
        if not os.path.isdir(folder_path):
            return 0
        try:
            all_extensions = self._SUPPORTED_LIST_EXTENSIONS
            count = 0
            for root, dirs, files in os.walk(folder_path):
                # 忽略 manga_translator_work 目录
                if 'manga_translator_work' in dirs:
                    dirs.remove('manga_translator_work')
                    
                for filename in files:
                    if os.path.splitext(filename)[1].lower() in all_extensions:
                        count += 1
            return count
        except Exception:
            return 0
    
    def _populate_folder_tree(self, parent_item: QTreeWidgetItem, folder_path: str):
        """递归填充文件夹树形结构"""
        try:
            all_extensions = self._SUPPORTED_LIST_EXTENSIONS
            
            # 获取当前文件夹的直接子项
            items = os.listdir(folder_path)
            
            # 分离文件夹和文件
            subdirs = []
            files = []
            
            for item in items:
                # 忽略 manga_translator_work 目录
                if item == 'manga_translator_work':
                    continue
                    
                item_path = os.path.join(folder_path, item)
                if os.path.isdir(item_path):
                    subdirs.append(item_path)
                elif os.path.splitext(item)[1].lower() in all_extensions:
                    files.append(item_path)
            
            # 先添加子文件夹
            for subdir in sorted(subdirs, key=natural_sort_key):
                subdir_item = QTreeWidgetItem(parent_item)
                subdir_item.setData(0, Qt.ItemDataRole.UserRole, subdir)
                
                subdir_widget = FileItemWidget(subdir, is_folder=True)
                subdir_widget.remove_requested.connect(self.file_remove_requested.emit)
                
                parent_item.addChild(subdir_item)
                self.setItemWidget(subdir_item, 0, subdir_widget)
                
                # 保存子文件夹节点
                self.folder_nodes[subdir] = subdir_item
                
                # 递归处理子文件夹
                self._populate_folder_tree(subdir_item, subdir)
                
                # 更新子文件夹的文件数量显示
                file_count = self._count_files_recursive(subdir)
                subdir_widget.update_file_count(file_count)
            
            # 再添加文件
            for file_path in sorted(files, key=natural_sort_key):
                file_item = QTreeWidgetItem(parent_item)
                file_item.setData(0, Qt.ItemDataRole.UserRole, file_path)
                
                file_widget = FileItemWidget(file_path, is_folder=False, defer_thumbnail=True)
                file_widget.remove_requested.connect(self.file_remove_requested.emit)
                
                parent_item.addChild(file_item)
                self.setItemWidget(file_item, 0, file_widget)
                
        except Exception as e:
            print(f"Error populating folder tree for {folder_path}: {e}")
        
        # 触发重绘以隐藏占位提示
        self.viewport().update()

    def _add_folder(self, folder_path: str):
        """添加文件夹及其包含的所有图片文件"""
        if folder_path in self.folder_nodes:
            return  # 文件夹已存在
        
        # 创建文件夹节点
        folder_item = QTreeWidgetItem(self)
        folder_item.setData(0, Qt.ItemDataRole.UserRole, folder_path)
        
        # 创建文件夹项的自定义控件
        folder_widget = FileItemWidget(folder_path, is_folder=True)
        folder_widget.remove_requested.connect(self.file_remove_requested.emit)
        
        self.addTopLevelItem(folder_item)
        self.setItemWidget(folder_item, 0, folder_widget)
        
        # 保存文件夹节点
        self.folder_nodes[folder_path] = folder_item
        
        # 添加文件夹中的文件
        try:
            all_extensions = self._SUPPORTED_LIST_EXTENSIONS
            files = [
                os.path.join(folder_path, f)
                for f in os.listdir(folder_path)
                if os.path.splitext(f)[1].lower() in all_extensions
            ]
            
            for file_path in sorted(files, key=natural_sort_key):
                self._add_file_to_folder(file_path, folder_item)
            
            # 更新文件夹显示的文件数
            self._update_folder_count(folder_item)
            self._finalize_folder_item(folder_item)
        except Exception as e:
            print(f"Error loading files from folder {folder_path}: {e}")
    
    def _add_folder_group(self, folder_path: str, files: List[str]):
        """添加文件夹分组（使用提供的文件列表）"""
        if folder_path in self.folder_nodes:
            # 文件夹已存在，添加新文件
            folder_item = self.folder_nodes[folder_path]
            existing_files = set()
            for i in range(folder_item.childCount()):
                child = folder_item.child(i)
                existing_files.add(child.data(0, Qt.ItemDataRole.UserRole))
            
            for file_path in files:
                if file_path not in existing_files:
                    self._add_file_to_folder(file_path, folder_item)
            
            # 更新文件夹显示的文件数
            self._update_folder_count(folder_item)
            return
        
        # 创建文件夹节点
        folder_item = QTreeWidgetItem(self)
        folder_item.setData(0, Qt.ItemDataRole.UserRole, folder_path)
        
        # 创建文件夹项的自定义控件
        folder_widget = FileItemWidget(folder_path, is_folder=True)
        folder_widget.remove_requested.connect(self.file_remove_requested.emit)
        
        self.addTopLevelItem(folder_item)
        self.setItemWidget(folder_item, 0, folder_widget)
        
        # 保存文件夹节点
        self.folder_nodes[folder_path] = folder_item
        
        # 添加文件列表
        for file_path in sorted(files, key=natural_sort_key):
            self._add_file_to_folder(file_path, folder_item)
        
        # 更新文件夹显示的文件数
        self._update_folder_count(folder_item)
        self._finalize_folder_item(folder_item)

    def _add_file_to_folder(self, file_path: str, parent_item: QTreeWidgetItem, defer_widget: bool = False):
        """将文件添加到文件夹节点下"""
        file_item = QTreeWidgetItem()
        file_item.setData(0, Qt.ItemDataRole.UserRole, file_path)
        file_item.setSizeHint(0, QSize(0, self._ROW_HEIGHT))
        if defer_widget:
            file_item.setText(0, os.path.basename(file_path))
            file_item.setToolTip(0, file_path)
            file_item.setData(0, self._FS_DEFERRED_WIDGET_ROLE, True)
            parent_item.addChild(file_item)
            return
        
        file_widget = FileItemWidget(file_path, is_folder=False, defer_thumbnail=True)
        file_widget.remove_requested.connect(self.file_remove_requested.emit)
        
        parent_item.addChild(file_item)
        self.setItemWidget(file_item, 0, file_widget)

    def _add_single_file(self, file_path: str):
        """添加单个文件（不属于任何文件夹）"""
        # 检查文件是否已存在
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == file_path:
                return  # 文件已存在
        
        file_item = QTreeWidgetItem(self)
        file_item.setData(0, Qt.ItemDataRole.UserRole, file_path)
        
        file_widget = FileItemWidget(file_path, is_folder=False, defer_thumbnail=True)
        file_widget.remove_requested.connect(self.file_remove_requested.emit)
        
        self.addTopLevelItem(file_item)
        self.setItemWidget(file_item, 0, file_widget)
        self._refresh_root_decoration()

    def remove_file(self, file_path: str):
        """移除指定文件或文件夹"""
        norm_path = os.path.normpath(file_path)
        
        # 临时断开选择信号，避免删除时触发选择事件
        try:
            self.itemSelectionChanged.disconnect(self._on_selection_changed)
        except Exception:
            pass
        
        try:
            # 递归查找并移除项
            def find_and_remove_item(parent_item: Optional[QTreeWidgetItem] = None) -> tuple[bool, Optional[QTreeWidgetItem]]:
                if parent_item is None:
                    # 搜索顶层项
                    for i in range(self.topLevelItemCount()):
                        item = self.topLevelItem(i)
                        item_path = item.data(0, Qt.ItemDataRole.UserRole)
                        
                        if item_path == norm_path:
                            # 找到了，删除这个顶层项
                            self.takeTopLevelItem(i)
                            # 如果是文件夹，从folder_nodes中移除
                            if norm_path in self.folder_nodes:
                                del self.folder_nodes[norm_path]
                            # 递归删除所有子文件夹的引用
                            self._remove_folder_nodes_recursive(item)
                            return True, None
                        
                        # 递归搜索子项
                        result, parent = find_and_remove_item(item)
                        if result:
                            return True, parent
                    
                    return False, None
                else:
                    # 搜索子项
                    for i in range(parent_item.childCount()):
                        child = parent_item.child(i)
                        child_path = child.data(0, Qt.ItemDataRole.UserRole)
                        
                        if child_path == norm_path:
                            # 找到了，删除这个子项
                            parent_item.removeChild(child)
                            # 如果是文件夹，从folder_nodes中移除
                            if norm_path in self.folder_nodes:
                                del self.folder_nodes[norm_path]
                            # 递归删除所有子文件夹的引用
                            self._remove_folder_nodes_recursive(child)
                            # 递归向上更新所有父文件夹的文件数量
                            self._update_all_parent_counts(parent_item)
                            return True, parent_item
                        
                        # 递归搜索更深层的子项
                        result, parent = find_and_remove_item(child)
                        if result:
                            return True, parent
                    
                    return False, None
            
            find_and_remove_item()
            
            # 删除后清除选择状态，避免自动触发加载
            self.clearSelection()
            
        finally:
            # 重新连接选择信号
            try:
                self.itemSelectionChanged.connect(self._on_selection_changed)
            except Exception:
                pass
        self._refresh_root_decoration()
        self._sync_empty_state_overlay()
        self.viewport().update()
    
    def _remove_folder_nodes_recursive(self, item: QTreeWidgetItem):
        """递归移除文件夹节点的所有子文件夹引用"""
        for i in range(item.childCount()):
            child = item.child(i)
            child_path = child.data(0, Qt.ItemDataRole.UserRole)
            if child_path in self.folder_nodes:
                del self.folder_nodes[child_path]
            # 递归处理子项
            self._remove_folder_nodes_recursive(child)

    def _update_folder_count(self, folder_item: QTreeWidgetItem) -> bool:
        """
        更新文件夹显示的文件数量（递归统计）
        返回: True 如果文件夹被删除，False 否则
        """
        if folder_item:
            widget = self.itemWidget(folder_item, 0)
            if isinstance(widget, FileItemWidget) and widget.is_folder:
                # 递归统计所有文件数量
                count = self._count_files_in_tree(folder_item)
                folder_path = folder_item.data(0, Qt.ItemDataRole.UserRole)
                is_filesystem_node = folder_item.data(0, self._FS_LOADED_ROLE) is not None
                has_unloaded_children = (
                    is_filesystem_node
                    and (
                        self._has_filesystem_placeholder(folder_item)
                        or not folder_item.data(0, self._FS_LOADED_ROLE)
                    )
                )
                has_child_folders = self._folder_has_child_folders(folder_item)
                
                # 如果文件夹为空（计数为0），删除该文件夹节点
                if count == 0:
                    if has_unloaded_children or has_child_folders:
                        return False
                    # 从 folder_nodes 中移除
                    if folder_path in self.folder_nodes:
                        del self.folder_nodes[folder_path]
                    
                    # 从树中移除
                    parent = folder_item.parent()
                    if parent:
                        parent.removeChild(folder_item)
                    else:
                        # 顶层项
                        index = self.indexOfTopLevelItem(folder_item)
                        if index >= 0:
                            self.takeTopLevelItem(index)
                    return True
                else:
                    widget.update_file_count(count)
        return False
    
    def _update_all_parent_counts(self, item: QTreeWidgetItem):
        """递归向上更新所有父文件夹的文件数量，如果文件夹为空则删除"""
        current = item
        while current:
            parent = current.parent()  # 先保存父节点，因为 current 可能被删除
            was_deleted = self._update_folder_count(current)
            if was_deleted:
                # 如果当前文件夹被删除了，继续检查父文件夹
                current = parent
            else:
                # 如果没被删除，继续向上更新
                current = parent
    
    def _count_files_in_tree(self, tree_item: QTreeWidgetItem) -> int:
        """递归统计树节点中的文件数量"""
        count = 0
        for i in range(tree_item.childCount()):
            child = tree_item.child(i)
            child_path = child.data(0, Qt.ItemDataRole.UserRole)
            if child_path and os.path.isfile(child_path):
                count += 1
            elif child_path and os.path.isdir(child_path):
                # 递归统计子文件夹
                count += self._count_files_in_tree(child)
        return count

    def clear(self, clear_cache: bool = False):
        """
        清空所有项
        
        Args:
            clear_cache: 是否同时清空缩略图缓存（默认 False，保留缓存以便重用）
        """
        super().clear()
        self.folder_nodes.clear()
        self._fs_pending_items.clear()
        self._fs_populate_jobs.clear()
        self._folder_tree_data.clear()
        
        if clear_cache:
            FileItemWidget.clear_thumbnail_cache()
        
        # 触发重绘以显示占位提示
        self._sync_empty_state_overlay()
        self.viewport().update()

    # 拖放事件处理
    def dragEnterEvent(self, event):
        """拖入事件：检查是否包含文件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """拖动移动事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """放下事件：处理拖入的文件和文件夹"""
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path:
                    paths.append(path)
            
            if paths:
                # 发射信号，让业务逻辑层处理
                self.files_dropped.emit(paths)
            
            event.acceptProposedAction()
        else:
            event.ignore()

    def select_next_image(self):
        """Select the next image in the list, skipping folders."""
        current_items = self.selectedItems()
        next_item = None
        
        if not current_items:
            # If nothing selected, find first file
            from PyQt6.QtWidgets import QTreeWidgetItemIterator
            iterator = QTreeWidgetItemIterator(self)
            while iterator.value():
                item = iterator.value()
                file_path = item.data(0, Qt.ItemDataRole.UserRole)
                if file_path and os.path.isfile(file_path):
                    self.setCurrentItem(item)
                    return
                iterator += 1
            return
            
        current = current_items[0]
        next_item = self.itemBelow(current)
        while next_item:
            file_path = next_item.data(0, Qt.ItemDataRole.UserRole)
            if file_path and os.path.isfile(file_path):
                self.setCurrentItem(next_item)
                return
            next_item = self.itemBelow(next_item)

    def select_prev_image(self):
        """Select the previous image in the list, skipping folders."""
        current_items = self.selectedItems()
        if not current_items:
            return
            
        current = current_items[0]
        prev_item = self.itemAbove(current)
        while prev_item:
            file_path = prev_item.data(0, Qt.ItemDataRole.UserRole)
            if file_path and os.path.isfile(file_path):
                self.setCurrentItem(prev_item)
                return
            prev_item = self.itemAbove(prev_item)
