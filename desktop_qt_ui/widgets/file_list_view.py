import os
from typing import Dict, List, Optional

from PIL import Image
from PyQt6.QtCore import QSize, Qt, pyqtSignal, QRunnable, QThreadPool, QObject
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)


class ThumbnailSignals(QObject):
    """缩略图加载信号"""
    finished = pyqtSignal(str, QPixmap)  # file_path, pixmap
    error = pyqtSignal(str)  # file_path


class ThumbnailLoader(QRunnable):
    """后台线程加载缩略图"""
    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self.signals = ThumbnailSignals()
    
    def run(self):
        """在后台线程中加载和处理图片"""
        try:
            img = Image.open(self.file_path)
            img.thumbnail((40, 40))
            
            # Convert PIL image to QPixmap
            if img.mode == 'RGB':
                q_img = QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
            elif img.mode == 'RGBA':
                q_img = QImage(img.tobytes(), img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
            else:
                img = img.convert('RGBA')
                q_img = QImage(img.tobytes(), img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
            
            pixmap = QPixmap.fromImage(q_img)
            self.signals.finished.emit(self.file_path, pixmap)
        except Exception as e:
            self.signals.error.emit(self.file_path)


class FileItemWidget(QWidget):
    """自定义列表项，用于显示缩略图、文件名和移除按钮"""
    remove_requested = pyqtSignal(str)

    def __init__(self, file_path, is_folder=False, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.is_folder = is_folder
        self.thread_pool = QThreadPool.globalInstance()

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(5, 5, 5, 5)
        self.layout.setSpacing(10)

        # Thumbnail
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(40, 40)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(self.thumbnail_label)

        if is_folder or os.path.isdir(self.file_path):
            style = QApplication.style()
            icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
            self.thumbnail_label.setPixmap(icon.pixmap(QSize(40,40)))
        else:
            # 先显示占位图标
            style = QApplication.style()
            icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
            self.thumbnail_label.setPixmap(icon.pixmap(QSize(40,40)))
            # 在后台线程加载真正的缩略图
            self._load_thumbnail_async()

        # File Name
        display_name = os.path.basename(file_path)
        if is_folder:
            # 统计文件夹下的文件数量
            file_count = self._count_files(file_path)
            display_name = f"{display_name} ({file_count}个文件)"
        
        self.name_label = QLabel(display_name)
        self.name_label.setWordWrap(True)
        self.layout.addWidget(self.name_label, 1) # Stretch factor

        # Remove Button
        self.remove_button = QPushButton("✕")
        self.remove_button.setFixedSize(20, 20)
        self.remove_button.clicked.connect(self._emit_remove_request)
        self.layout.addWidget(self.remove_button)

    def _count_files(self, folder_path: str) -> int:
        """统计文件夹中的图片文件数量"""
        if not os.path.isdir(folder_path):
            return 0
        try:
            image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}
            files = [f for f in os.listdir(folder_path) 
                    if os.path.splitext(f)[1].lower() in image_extensions]
            return len(files)
        except:
            return 0

    def _load_thumbnail_async(self):
        """在后台线程加载缩略图"""
        loader = ThumbnailLoader(self.file_path)
        loader.signals.finished.connect(self._on_thumbnail_loaded)
        loader.signals.error.connect(self._on_thumbnail_error)
        self.thread_pool.start(loader)
    
    def _on_thumbnail_loaded(self, file_path: str, pixmap: QPixmap):
        """缩略图加载完成"""
        if file_path == self.file_path:
            self.thumbnail_label.setPixmap(pixmap)
    
    def _on_thumbnail_error(self, file_path: str):
        """缩略图加载失败"""
        if file_path == self.file_path:
            self.thumbnail_label.setText("ERR")

    def _emit_remove_request(self):
        self.remove_requested.emit(self.file_path)

    def get_path(self):
        return self.file_path


class FileListView(QTreeWidget):
    """显示文件列表的自定义控件（支持文件夹分组）"""
    file_remove_requested = pyqtSignal(str)
    file_selected = pyqtSignal(str)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        
        # 设置树形控件属性
        self.setHeaderHidden(True)  # 隐藏标题栏
        self.setIndentation(20)  # 设置缩进
        self.setAnimated(True)  # 启用展开/折叠动画
        
        # 存储文件夹到树节点的映射
        self.folder_nodes: Dict[str, QTreeWidgetItem] = {}
        
        # 连接选择信号
        self.itemSelectionChanged.connect(self._on_selection_changed)

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

    def add_files(self, file_paths: List[str]):
        """添加多个文件/文件夹到列表"""
        for path in file_paths:
            norm_path = os.path.normpath(path)
            
            if os.path.isdir(norm_path):
                # 添加文件夹
                self._add_folder(norm_path)
            else:
                # 添加单个文件
                self._add_single_file(norm_path)

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
            image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}
            files = [
                os.path.join(folder_path, f)
                for f in os.listdir(folder_path)
                if os.path.splitext(f)[1].lower() in image_extensions
            ]
            
            for file_path in sorted(files):
                self._add_file_to_folder(file_path, folder_item)
        except Exception as e:
            print(f"Error loading files from folder {folder_path}: {e}")

    def _add_file_to_folder(self, file_path: str, parent_item: QTreeWidgetItem):
        """将文件添加到文件夹节点下"""
        file_item = QTreeWidgetItem(parent_item)
        file_item.setData(0, Qt.ItemDataRole.UserRole, file_path)
        
        file_widget = FileItemWidget(file_path, is_folder=False)
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
        
        file_widget = FileItemWidget(file_path, is_folder=False)
        file_widget.remove_requested.connect(self.file_remove_requested.emit)
        
        self.addTopLevelItem(file_item)
        self.setItemWidget(file_item, 0, file_widget)

    def remove_file(self, file_path: str):
        """移除指定文件或文件夹"""
        norm_path = os.path.normpath(file_path)
        
        # 如果是文件夹
        if norm_path in self.folder_nodes:
            # 移除文件夹节点
            folder_item = self.folder_nodes[norm_path]
            index = self.indexOfTopLevelItem(folder_item)
            if index >= 0:
                self.takeTopLevelItem(index)
            del self.folder_nodes[norm_path]
            return
        
        # 如果是文件，查找并移除
        def find_and_remove(parent_item: Optional[QTreeWidgetItem] = None):
            if parent_item is None:
                # 搜索顶层项
                for i in range(self.topLevelItemCount()):
                    item = self.topLevelItem(i)
                    if item.data(0, Qt.ItemDataRole.UserRole) == norm_path:
                        self.takeTopLevelItem(i)
                        return True
                    # 递归搜索子项
                    if find_and_remove(item):
                        return True
            else:
                # 搜索子项
                for i in range(parent_item.childCount()):
                    child = parent_item.child(i)
                    if child.data(0, Qt.ItemDataRole.UserRole) == norm_path:
                        parent_item.removeChild(child)
                        return True
            return False
        
        find_and_remove()

    def clear(self):
        """清空所有项"""
        super().clear()
        self.folder_nodes.clear()
