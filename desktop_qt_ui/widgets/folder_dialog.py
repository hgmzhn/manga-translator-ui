# -*- coding: utf-8 -*-
"""
现代化文件夹选择器对话框
支持多选、快捷栏、路径导航等功能
"""

import json
import os
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QDir, QModelIndex, pyqtSignal, QSize, QSortFilterProxyModel, QRect, QPoint
from PyQt6.QtGui import QIcon, QFileSystemModel, QStandardItemModel, QStandardItem, QFont, QPainter, QColor, QPen
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTreeView,
    QListView, QSplitter, QLineEdit, QLabel, QWidget, QFileIconProvider,
    QMessageBox, QAbstractItemView, QScrollArea, QToolButton, QStyle, QComboBox, QStyledItemDelegate
)


class CaseInsensitiveSortProxyModel(QSortFilterProxyModel):
    """不区分大小写的排序代理模型"""
    
    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """自定义排序比较"""
        left_data = self.sourceModel().data(left, Qt.ItemDataRole.DisplayRole)
        right_data = self.sourceModel().data(right, Qt.ItemDataRole.DisplayRole)
        
        if left_data is None or right_data is None:
            return False
        
        # 转换为小写进行比较
        left_str = str(left_data).lower()
        right_str = str(right_data).lower()
        
        return left_str < right_str


class FavoriteDelegate(QStyledItemDelegate):
    """带收藏星星的自定义委托"""
    
    def __init__(self, parent=None, favorite_folders=None, fs_model=None, proxy_model=None):
        super().__init__(parent)
        self.favorite_folders = favorite_folders if favorite_folders is not None else []
        self.fs_model = fs_model
        self.proxy_model = proxy_model
        self.star_size = 16  # 和图标一样大
        self.star_margin = 4  # 星星和图标之间的间距
        self.icon_size = 16  # 文件夹图标大小
        
    def paint(self, painter: QPainter, option, index: QModelIndex):
        """绘制项目"""
        # 先绘制默认内容
        super().paint(painter, option, index)
        
        # 获取文件夹路径
        if self.proxy_model and self.fs_model:
            source_index = self.proxy_model.mapToSource(index)
            folder_path = self.fs_model.filePath(source_index)
        else:
            return
        
        if not folder_path or not os.path.isdir(folder_path):
            return
        
        # 检查是否收藏
        is_favorited = folder_path in self.favorite_folders
        
        # 计算星星位置（在文本左侧）
        star_rect = self.get_star_rect(option.rect)
        
        # 绘制星星
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        if is_favorited:
            # 实心星星（已收藏）
            painter.setPen(QPen(QColor("#ffc107"), 1))
            painter.setBrush(QColor("#ffc107"))
        else:
            # 空心星星（未收藏）
            painter.setPen(QPen(QColor("#cccccc"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        
        # 绘制五角星
        self.draw_star(painter, star_rect)
        
        painter.restore()
    
    def draw_star(self, painter: QPainter, rect: QRect):
        """绘制五角星"""
        from math import cos, sin, pi
        
        center_x = rect.center().x()
        center_y = rect.center().y()
        radius = min(rect.width(), rect.height()) / 2 - 1
        
        points = []
        for i in range(10):
            angle = pi / 2 + (2 * pi * i / 10)
            r = radius if i % 2 == 0 else radius * 0.4
            x = center_x + r * cos(angle)
            y = center_y - r * sin(angle)
            points.append(QPoint(int(x), int(y)))
        
        from PyQt6.QtGui import QPolygon
        polygon = QPolygon(points)
        painter.drawPolygon(polygon)
    
    def get_star_rect(self, item_rect: QRect) -> QRect:
        """获取星星的绘制区域 - 在最左侧"""
        # 星星在最左侧
        x = item_rect.left() + self.star_margin
        y = item_rect.top() + (item_rect.height() - self.star_size) // 2
        return QRect(x, y, self.star_size, self.star_size)
    
    def initStyleOption(self, option, index):
        """调整样式选项，为星星留出空间"""
        super().initStyleOption(option, index)
        # 向右偏移内容，为星星留出空间
        option.rect.setLeft(option.rect.left() + self.star_size + self.star_margin * 2)
    
    def editorEvent(self, event, model, option, index):
        """处理鼠标点击事件"""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QMouseEvent
        
        if event.type() == QEvent.Type.MouseButtonRelease:
            if isinstance(event, QMouseEvent):
                star_rect = self.get_star_rect(option.rect)
                if star_rect.contains(event.pos()):
                    # 点击了星星区域
                    if self.proxy_model and self.fs_model:
                        source_index = self.proxy_model.mapToSource(index)
                        folder_path = self.fs_model.filePath(source_index)
                        
                        if folder_path and os.path.isdir(folder_path):
                            # 切换收藏状态
                            dialog = self.parent()
                            if isinstance(dialog, FolderDialog):
                                if folder_path in dialog.favorite_folders:
                                    dialog._remove_favorite_by_path(folder_path)
                                else:
                                    dialog._add_favorite(folder_path)
                            return True
        
        return super().editorEvent(event, model, option, index)


class ShortcutFavoriteDelegate(QStyledItemDelegate):
    """左侧快捷栏的收藏委托"""
    
    def __init__(self, parent=None, favorite_folders=None, shortcuts_model=None):
        super().__init__(parent)
        self.favorite_folders = favorite_folders if favorite_folders is not None else []
        self.shortcuts_model = shortcuts_model
        self.star_size = 16  # 和图标一样大
        self.star_margin = 4  # 星星和图标之间的间距
        self.icon_size = 16  # 图标大小
        
    def paint(self, painter: QPainter, option, index: QModelIndex):
        """绘制项目"""
        super().paint(painter, option, index)
        
        if not self.shortcuts_model:
            return
        
        item = self.shortcuts_model.itemFromIndex(index)
        if not item:
            return
        
        folder_path = item.data(Qt.ItemDataRole.UserRole)
        if not folder_path or not os.path.isdir(folder_path):
            return
        
        is_favorited = folder_path in self.favorite_folders
        
        star_rect = self.get_star_rect(option.rect)
        
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        if is_favorited:
            painter.setPen(QPen(QColor("#ffc107"), 1))
            painter.setBrush(QColor("#ffc107"))
        else:
            painter.setPen(QPen(QColor("#cccccc"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        
        self.draw_star(painter, star_rect)
        painter.restore()
    
    def draw_star(self, painter: QPainter, rect: QRect):
        """绘制五角星"""
        from math import cos, sin, pi
        
        center_x = rect.center().x()
        center_y = rect.center().y()
        radius = min(rect.width(), rect.height()) / 2 - 1
        
        points = []
        for i in range(10):
            angle = pi / 2 + (2 * pi * i / 10)
            r = radius if i % 2 == 0 else radius * 0.4
            x = center_x + r * cos(angle)
            y = center_y - r * sin(angle)
            points.append(QPoint(int(x), int(y)))
        
        from PyQt6.QtGui import QPolygon
        polygon = QPolygon(points)
        painter.drawPolygon(polygon)
    
    def get_star_rect(self, item_rect: QRect) -> QRect:
        """获取星星的绘制区域 - 在最左侧"""
        # 星星在最左侧
        x = item_rect.left() + self.star_margin
        y = item_rect.top() + (item_rect.height() - self.star_size) // 2
        return QRect(x, y, self.star_size, self.star_size)
    
    def initStyleOption(self, option, index):
        """调整样式选项，为星星留出空间"""
        super().initStyleOption(option, index)
        # 向右偏移内容，为星星留出空间
        option.rect.setLeft(option.rect.left() + self.star_size + self.star_margin * 2)
    
    def editorEvent(self, event, model, option, index):
        """处理鼠标点击事件"""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QMouseEvent
        
        if event.type() == QEvent.Type.MouseButtonRelease:
            if isinstance(event, QMouseEvent):
                star_rect = self.get_star_rect(option.rect)
                if star_rect.contains(event.pos()):
                    if not self.shortcuts_model:
                        return False
                    
                    item = self.shortcuts_model.itemFromIndex(index)
                    if not item:
                        return False
                    
                    folder_path = item.data(Qt.ItemDataRole.UserRole)
                    if folder_path and os.path.isdir(folder_path):
                        dialog = self.parent()
                        if isinstance(dialog, FolderDialog):
                            if folder_path in dialog.favorite_folders:
                                dialog._remove_favorite_by_path(folder_path)
                            else:
                                dialog._add_favorite(folder_path)
                        return True
        
        return super().editorEvent(event, model, option, index)


class FolderDialog(QDialog):
    """现代化文件夹选择对话框"""

    def __init__(self, parent=None, start_dir: str = "", multi_select: bool = True, config_service=None):
        super().__init__(parent)
        self.multi_select = multi_select
        self.selected_folders: List[str] = []
        self.history: List[str] = []  # 导航历史
        self.history_index = -1  # 当前历史位置
        self.favorite_folders: List[str] = []  # 收藏的文件夹
        self.config_service = config_service

        self.setWindowTitle("选择文件夹" + (" (可多选)" if multi_select else ""))
        self.setMinimumSize(1000, 650)
        self.resize(1000, 650)
        
        # 设置对话框使用系统调色板背景
        from PyQt6.QtGui import QPalette
        palette = self.palette()
        self.setAutoFillBackground(True)
        self.setPalette(palette)

        # 初始化文件系统模型
        self.fs_model = QFileSystemModel()
        self.fs_model.setRootPath(QDir.rootPath())
        # 显示所有文件夹，包括隐藏文件夹
        self.fs_model.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden)
        
        # 使用代理模型实现不区分大小写的排序
        self.proxy_model = CaseInsensitiveSortProxyModel()
        self.proxy_model.setSourceModel(self.fs_model)
        self.proxy_model.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        # 加载收藏文件夹
        self._load_favorite_folders()

        self._init_ui()
        self._connect_signals()

        # 设置初始目录
        if start_dir and os.path.isdir(start_dir):
            self.navigate_to(start_dir, add_to_history=True)
        else:
            self.navigate_to(str(Path.home()), add_to_history=True)

    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        # 获取系统调色板
        from PyQt6.QtGui import QPalette
        palette = self.palette()
        
        # 创建工具栏区域（后退/前进/上级目录）
        toolbar_widget = QWidget()
        toolbar_widget.setStyleSheet(f"""
            QWidget {{
                background-color: {palette.color(QPalette.ColorRole.Window).name()};
                border-bottom: 1px solid {palette.color(QPalette.ColorRole.Mid).name()};
            }}
            QToolButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 3px;
                padding: 4px;
                margin: 2px;
                color: {palette.color(QPalette.ColorRole.WindowText).name()};
            }}
            QToolButton:hover {{
                background-color: {palette.color(QPalette.ColorRole.Light).name()};
                border: 1px solid {palette.color(QPalette.ColorRole.Mid).name()};
            }}
            QToolButton:pressed {{
                background-color: {palette.color(QPalette.ColorRole.Midlight).name()};
            }}
            QToolButton:disabled {{
                color: {palette.color(QPalette.ColorRole.PlaceholderText).name()};
            }}
        """)
        toolbar_layout = QHBoxLayout(toolbar_widget)
        toolbar_layout.setContentsMargins(8, 4, 8, 4)
        toolbar_layout.setSpacing(2)

        # 后退按钮
        self.back_button = QToolButton()
        self.back_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self.back_button.setToolTip("后退")
        self.back_button.setIconSize(QSize(20, 20))
        self.back_button.setEnabled(False)
        toolbar_layout.addWidget(self.back_button)

        # 前进按钮
        self.forward_button = QToolButton()
        self.forward_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        self.forward_button.setToolTip("前进")
        self.forward_button.setIconSize(QSize(20, 20))
        self.forward_button.setEnabled(False)
        toolbar_layout.addWidget(self.forward_button)

        # 上级目录按钮
        self.parent_button = QToolButton()
        self.parent_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
        self.parent_button.setToolTip("上级目录")
        self.parent_button.setIconSize(QSize(20, 20))
        toolbar_layout.addWidget(self.parent_button)

        # 分隔符
        separator = QWidget()
        separator.setFixedWidth(1)
        separator.setStyleSheet("background-color: #c0c0c0;")
        toolbar_layout.addWidget(separator)

        # 刷新按钮
        self.refresh_button = QToolButton()
        self.refresh_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.refresh_button.setToolTip("刷新")
        self.refresh_button.setIconSize(QSize(20, 20))
        toolbar_layout.addWidget(self.refresh_button)

        toolbar_layout.addStretch()

        # 排序选项
        sort_label = QLabel("排序:")
        sort_label.setStyleSheet(f"color: {palette.color(QPalette.ColorRole.WindowText).name()}; font-size: 12px; margin-right: 4px;")
        toolbar_layout.addWidget(sort_label)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["名称 ↑", "名称 ↓", "修改时间 ↑", "修改时间 ↓", "大小 ↑", "大小 ↓"])
        self.sort_combo.setCurrentIndex(0)
        self.sort_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {palette.color(QPalette.ColorRole.Base).name()};
                border: 1px solid {palette.color(QPalette.ColorRole.Mid).name()};
                border-radius: 3px;
                padding: 4px 8px;
                min-width: 100px;
                font-size: 12px;
                color: {palette.color(QPalette.ColorRole.Text).name()};
            }}
            QComboBox:hover {{
                border: 1px solid #0078d4;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {palette.color(QPalette.ColorRole.Text).name()};
                margin-right: 5px;
            }}
        """)
        toolbar_layout.addWidget(self.sort_combo)

        layout.addWidget(toolbar_widget)

        # 创建地址栏区域（面包屑导航）
        address_widget = QWidget()
        address_widget.setStyleSheet(f"""
            QWidget {{
                background-color: {palette.color(QPalette.ColorRole.Base).name()};
                border: 1px solid {palette.color(QPalette.ColorRole.Mid).name()};
                border-radius: 2px;
            }}
        """)
        address_layout = QHBoxLayout(address_widget)
        address_layout.setContentsMargins(8, 8, 8, 8)
        address_layout.setSpacing(5)

        # 地址栏图标
        address_icon = QLabel()
        address_icon.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon).pixmap(16, 16))
        address_layout.addWidget(address_icon)

        # 面包屑导航滚动区域
        self.breadcrumb_scroll = QScrollArea()
        self.breadcrumb_scroll.setWidgetResizable(True)
        self.breadcrumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.breadcrumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.breadcrumb_scroll.setMaximumHeight(35)
        self.breadcrumb_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: transparent;
            }
        """)

        # 面包屑容器
        self.breadcrumb_widget = QWidget()
        self.breadcrumb_layout = QHBoxLayout(self.breadcrumb_widget)
        self.breadcrumb_layout.setContentsMargins(0, 0, 0, 0)
        self.breadcrumb_layout.setSpacing(0)
        self.breadcrumb_layout.addStretch()

        self.breadcrumb_scroll.setWidget(self.breadcrumb_widget)
        address_layout.addWidget(self.breadcrumb_scroll, 1)

        # 地址栏编辑按钮
        self.edit_path_button = QToolButton()
        self.edit_path_button.setText("✏️")
        self.edit_path_button.setToolTip("编辑路径")
        self.edit_path_button.setStyleSheet(f"""
            QToolButton {{
                background-color: transparent;
                border: none;
                padding: 2px;
            }}
            QToolButton:hover {{
                background-color: {palette.color(QPalette.ColorRole.Light).name()};
                border-radius: 2px;
            }}
        """)
        address_layout.addWidget(self.edit_path_button)

        # 路径输入框（初始隐藏，点击编辑按钮时显示）
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("输入路径后按回车跳转，或按 Esc 取消")
        self.path_edit.setStyleSheet(f"""
            QLineEdit {{
                padding: 8px;
                border: 2px solid #0078d4;
                border-radius: 3px;
                font-size: 13px;
                background-color: {palette.color(QPalette.ColorRole.Base).name()};
                color: {palette.color(QPalette.ColorRole.Text).name()};
            }}
        """)

        # 创建一个容器来包含面包屑和输入框，它们互斥显示
        self.address_container = QWidget()
        address_container_layout = QVBoxLayout(self.address_container)
        address_container_layout.setContentsMargins(8, 4, 8, 8)
        address_container_layout.setSpacing(0)
        
        # 面包屑容器
        self.breadcrumb_container = QWidget()
        breadcrumb_container_layout = QVBoxLayout(self.breadcrumb_container)
        breadcrumb_container_layout.setContentsMargins(0, 0, 0, 0)
        breadcrumb_container_layout.addWidget(address_widget)
        
        # 输入框容器
        self.path_edit_container = QWidget()
        path_edit_layout = QVBoxLayout(self.path_edit_container)
        path_edit_layout.setContentsMargins(0, 0, 0, 0)
        path_edit_layout.addWidget(self.path_edit)
        self.path_edit_container.hide()
        
        # 将两个容器添加到主地址栏容器
        address_container_layout.addWidget(self.breadcrumb_container)
        address_container_layout.addWidget(self.path_edit_container)

        layout.addWidget(self.address_container)

        # 主内容区域：左侧快捷栏 + 右侧文件夹树
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background-color: {palette.color(QPalette.ColorRole.Mid).name()};
                width: 1px;
            }}
        """)

        # 左侧快捷栏
        shortcuts_widget = self._create_shortcuts_panel()
        splitter.addWidget(shortcuts_widget)

        # 右侧文件夹树形视图
        self.folder_tree = QTreeView()
        self.folder_tree.setModel(self.proxy_model)
        self.folder_tree.setStyleSheet(f"""
            QTreeView {{
                border: none;
                background-color: {palette.color(QPalette.ColorRole.Base).name()};
                selection-background-color: #0078d4;
                selection-color: white;
                font-size: 13px;
                color: {palette.color(QPalette.ColorRole.Text).name()};
            }}
            QTreeView::item {{
                padding: 4px;
                border: none;
            }}
            QTreeView::item:hover {{
                background-color: {palette.color(QPalette.ColorRole.AlternateBase).name()};
            }}
            QTreeView::item:selected {{
                background-color: #0078d4;
                color: white;
            }}
        """)

        # 只显示名称列
        for i in range(1, self.fs_model.columnCount()):
            self.folder_tree.hideColumn(i)

        # 设置多选模式
        if self.multi_select:
            self.folder_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        else:
            self.folder_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        self.folder_tree.setHeaderHidden(False)
        self.folder_tree.setSortingEnabled(True)
        self.folder_tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.folder_tree.setAlternatingRowColors(False)
        
        # 设置自定义委托以显示收藏星星
        self.folder_delegate = FavoriteDelegate(self, self.favorite_folders, self.fs_model, self.proxy_model)
        self.folder_tree.setItemDelegate(self.folder_delegate)

        splitter.addWidget(self.folder_tree)

        # 设置分割比例：快捷栏占20%，文件夹树占80%
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 8)

        layout.addWidget(splitter, 1)

        # 底部提示和选中信息
        info_layout = QHBoxLayout()
        info_layout.setContentsMargins(8, 4, 8, 4)

        if self.multi_select:
            tip_label = QLabel("💡 提示：按住 Ctrl 或 Shift 可以多选文件夹")
            tip_label.setStyleSheet(f"color: {palette.color(QPalette.ColorRole.PlaceholderText).name()}; font-size: 12px;")
            info_layout.addWidget(tip_label)

        info_layout.addStretch()

        self.selection_label = QLabel("未选择")
        self.selection_label.setStyleSheet(f"color: #0078d4; font-weight: bold; font-size: 12px;")
        info_layout.addWidget(self.selection_label)

        layout.addLayout(info_layout)

        # 底部按钮
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(8, 8, 8, 8)
        button_layout.addStretch()

        self.ok_button = QPushButton("确定")
        self.ok_button.setMinimumWidth(100)
        self.ok_button.setMinimumHeight(32)
        self.ok_button.setEnabled(False)
        self.ok_button.setStyleSheet("""
            QPushButton {
                background-color: #0078d4;
                color: white;
                border: none;
                border-radius: 3px;
                padding: 6px 20px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #106ebe;
            }
            QPushButton:pressed {
                background-color: #005a9e;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #888888;
            }
        """)
        button_layout.addWidget(self.ok_button)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setMinimumWidth(100)
        self.cancel_button.setMinimumHeight(32)
        self.cancel_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {palette.color(QPalette.ColorRole.Button).name()};
                color: {palette.color(QPalette.ColorRole.ButtonText).name()};
                border: 1px solid {palette.color(QPalette.ColorRole.Mid).name()};
                border-radius: 3px;
                padding: 6px 20px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {palette.color(QPalette.ColorRole.Light).name()};
                border-color: {palette.color(QPalette.ColorRole.Dark).name()};
            }}
            QPushButton:pressed {{
                background-color: {palette.color(QPalette.ColorRole.Midlight).name()};
            }}
        """)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

    def _create_shortcuts_panel(self) -> QWidget:
        """创建左侧快捷栏 - 树形结构"""
        from PyQt6.QtGui import QPalette
        palette = self.palette()
        
        widget = QWidget()
        widget.setMinimumWidth(180)
        widget.setMaximumWidth(280)
        # 使用 Window 颜色作为背景，确保与系统主题一致
        widget.setStyleSheet(f"""
            QWidget {{
                background-color: {palette.color(QPalette.ColorRole.Window).name()};
                border-right: 1px solid {palette.color(QPalette.ColorRole.Mid).name()};
            }}
        """)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 创建树形视图
        self.shortcuts_tree = QTreeView()
        self.shortcuts_tree.setHeaderHidden(True)
        self.shortcuts_tree.setIndentation(12)
        self.shortcuts_tree.setAnimated(True)
        self.shortcuts_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.shortcuts_tree.setStyleSheet(f"""
            QTreeView {{
                border: none;
                background-color: transparent;
                selection-background-color: {palette.color(QPalette.ColorRole.Highlight).name()};
                selection-color: {palette.color(QPalette.ColorRole.HighlightedText).name()};
                font-size: 13px;
                outline: none;
                color: {palette.color(QPalette.ColorRole.Text).name()};
            }}
            QTreeView::item {{
                padding: 6px 8px;
                border: none;
            }}
            QTreeView::item:hover {{
                background-color: {palette.color(QPalette.ColorRole.Light).name()};
            }}
            QTreeView::item:selected {{
                background-color: {palette.color(QPalette.ColorRole.Highlight).name()};
                color: {palette.color(QPalette.ColorRole.HighlightedText).name()};
            }}
            QTreeView::branch {{
                background-color: transparent;
            }}
            QTreeView::branch:has-children:!has-siblings:closed,
            QTreeView::branch:closed:has-children:has-siblings {{
                image: url(none);
                border: none;
            }}
            QTreeView::branch:open:has-children:!has-siblings,
            QTreeView::branch:open:has-children:has-siblings {{
                image: url(none);
                border: none;
            }}
        """)

        self.shortcuts_tree_model = QStandardItemModel()
        self.shortcuts_tree.setModel(self.shortcuts_tree_model)

        # 构建快捷访问树
        self._build_shortcuts_tree()

        # 默认展开所有项
        self.shortcuts_tree.expandAll()

        layout.addWidget(self.shortcuts_tree)

        # 设置自定义委托以显示收藏星星
        self.shortcut_delegate = ShortcutFavoriteDelegate(self, self.favorite_folders, self.shortcuts_tree_model)
        self.shortcuts_tree.setItemDelegate(self.shortcut_delegate)

        # 连接点击信号
        self.shortcuts_tree.clicked.connect(self._on_tree_shortcut_clicked)

        return widget

    def _build_shortcuts_tree(self):
        """构建快捷访问树形结构"""
        home = Path.home()

        # 收藏文件夹分组 - 放在快速访问之后
        # 获取真实的快速访问文件夹（从注册表/系统）
        quick_access_folders = self._get_quick_access_folders()

        if quick_access_folders:
            # 快速访问分组
            quick_access_root = QStandardItem("📌 快速访问")
            quick_access_root.setSelectable(False)
            font = quick_access_root.font()
            font.setBold(True)
            quick_access_root.setFont(font)
            self.shortcuts_tree_model.appendRow(quick_access_root)

            for name, path in quick_access_folders:
                item = QStandardItem(name)
                item.setData(path, Qt.ItemDataRole.UserRole)
                item.setToolTip(path)
                quick_access_root.appendRow(item)

        # 收藏文件夹分组 - 放在快速访问和此电脑之间
        if self.favorite_folders:
            favorite_root = QStandardItem("⭐ 收藏夹")
            favorite_root.setSelectable(False)
            font = favorite_root.font()
            font.setBold(True)
            favorite_root.setFont(font)
            self.shortcuts_tree_model.appendRow(favorite_root)

            for path in self.favorite_folders:
                if os.path.exists(path):
                    folder_name = os.path.basename(path) or path
                    item = QStandardItem(f"📁 {folder_name}")
                    item.setData(path, Qt.ItemDataRole.UserRole)
                    item.setData("favorite", Qt.ItemDataRole.UserRole + 1)  # 标记为收藏项
                    item.setToolTip(path)
                    favorite_root.appendRow(item)

        # 此电脑分组
        this_pc_root = QStandardItem("💻 此电脑")
        this_pc_root.setSelectable(False)
        font = this_pc_root.font()
        font.setBold(True)
        this_pc_root.setFont(font)
        self.shortcuts_tree_model.appendRow(this_pc_root)

        # 用户文件夹
        user_folders = [
            ("📁 桌面", home / "Desktop"),
            ("📄 文档", home / "Documents"),
            ("📥 下载", home / "Downloads"),
            ("🖼️ 图片", home / "Pictures"),
            ("🎵 音乐", home / "Music"),
            ("🎬 视频", home / "Videos"),
        ]

        for name, path in user_folders:
            if path.exists():
                item = QStandardItem(name)
                item.setData(str(path), Qt.ItemDataRole.UserRole)
                item.setToolTip(str(path))
                this_pc_root.appendRow(item)

        # 驱动器
        drives = QDir.drives()
        drives_list = []
        for drive in drives:
            drive_path = Path(drive.absolutePath())
            if drive_path.exists():
                # 尝试获取驱动器卷标
                try:
                    import win32api
                    volume_name = win32api.GetVolumeInformation(str(drive_path))[0]
                    if volume_name:
                        display_name = f"💾 {volume_name} ({drive_path})"
                    else:
                        display_name = f"💾 本地磁盘 ({drive_path})"
                except:
                    display_name = f"💾 本地磁盘 ({drive_path})"

                drives_list.append((display_name, str(drive_path)))

        # 按盘符排序
        drives_list.sort(key=lambda x: x[1])
        for name, path in drives_list:
            item = QStandardItem(name)
            item.setData(path, Qt.ItemDataRole.UserRole)
            item.setToolTip(path)
            this_pc_root.appendRow(item)

    def _get_quick_access_folders(self):
        """从 Windows 注册表获取真实的快速访问文件夹"""
        quick_access = []

        try:
            import winreg

            # 尝试读取快速访问的固定文件夹（从注册表）
            # HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)

            # 常见的快速访问项
            shell_folders = {
                "Desktop": "📁 桌面",
                "My Pictures": "🖼️ 图片",
                "{374DE290-123F-4565-9164-39C4925E467B}": "📥 下载",
                "Personal": "📄 文档",
                "My Music": "🎵 音乐",
                "My Video": "🎬 视频",
            }

            for value_name, display_name in shell_folders.items():
                try:
                    path_value, _ = winreg.QueryValueEx(key, value_name)
                    # 展开环境变量
                    expanded_path = os.path.expandvars(path_value)
                    if os.path.exists(expanded_path):
                        quick_access.append((display_name, expanded_path))
                except:
                    pass

            winreg.CloseKey(key)

        except Exception as e:
            # 如果读取注册表失败，使用默认路径
            home = Path.home()
            default_folders = [
                ("📁 桌面", home / "Desktop"),
                ("📄 文档", home / "Documents"),
                ("📥 下载", home / "Downloads"),
                ("🖼️ 图片", home / "Pictures"),
            ]
            for name, path in default_folders:
                if path.exists():
                    quick_access.append((name, str(path)))

        # 添加用户目录下的其他常见文件夹（排除系统文件夹）
        try:
            home = Path.home()
            exclude_names = {'Desktop', 'Documents', 'Downloads', 'Pictures', 'Music', 'Videos',
                           'AppData', 'Application Data', 'Cookies', 'Local Settings',
                           'NetHood', 'PrintHood', 'Recent', 'SendTo', 'Templates',
                           'Start Menu', 'ntuser.dat', 'NTUSER.DAT'}

            additional_folders = []
            if home.exists():
                for item in home.iterdir():
                    if item.is_dir() and not item.name.startswith('.') and not item.name.startswith('$'):
                        if item.name not in exclude_names:
                            # 跳过 OneDrive（稍后单独处理）
                            if not item.name.startswith('OneDrive'):
                                additional_folders.append((f"📂 {item.name}", str(item)))

            # 排序并添加前5个
            additional_folders.sort(key=lambda x: x[0].lower())
            quick_access.extend(additional_folders[:5])

            # OneDrive
            onedrive_paths = [
                home / "OneDrive",
                home / "OneDrive - Personal",
                home / "OneDrive - 个人",
            ]
            for onedrive_path in onedrive_paths:
                if onedrive_path.exists():
                    quick_access.append(("☁️ OneDrive", str(onedrive_path)))
                    break

        except Exception as e:
            pass

        return quick_access

    def _on_tree_shortcut_clicked(self, index: QModelIndex):
        """树形快捷方式点击"""
        item = self.shortcuts_tree_model.itemFromIndex(index)
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            if path and os.path.isdir(path):
                self.navigate_to(path, add_to_history=True)

    def _connect_signals(self):
        """连接信号"""
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)

        # 工具栏按钮
        self.back_button.clicked.connect(self._go_back)
        self.forward_button.clicked.connect(self._go_forward)
        self.parent_button.clicked.connect(self._go_parent)
        self.refresh_button.clicked.connect(self._refresh_current)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)

        # 地址栏
        self.edit_path_button.clicked.connect(self._toggle_path_edit)
        self.path_edit.returnPressed.connect(self._on_path_edit_confirmed)
        self.path_edit.installEventFilter(self)

        self.folder_tree.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.folder_tree.doubleClicked.connect(self._on_folder_double_clicked)

    def navigate_to(self, path: str, add_to_history: bool = True):
        """导航到指定路径"""
        if not os.path.isdir(path):
            return

        path = os.path.normpath(path)

        # 添加到历史记录
        if add_to_history:
            # 如果当前不在历史末尾，删除当前位置之后的历史
            if self.history_index < len(self.history) - 1:
                self.history = self.history[:self.history_index + 1]

            # 如果新路径与当前路径不同，添加到历史
            if not self.history or self.history[-1] != path:
                self.history.append(path)
                self.history_index = len(self.history) - 1

        # 设置当前目录为根索引，只显示当前目录的内容（嵌套式）
        source_index = self.fs_model.index(path)
        if source_index.isValid():
            proxy_index = self.proxy_model.mapFromSource(source_index)
            self.folder_tree.setRootIndex(proxy_index)  # 只显示当前目录内容
            # 不需要设置 currentIndex，因为我们已经进入了这个目录

            # 更新面包屑导航
            self._update_breadcrumb(path)

            # 更新按钮状态
            self._update_navigation_buttons()
            
            # 更新选择状态（如果没有选中任何文件夹，显示当前目录）
            self._on_selection_changed()

    def _update_breadcrumb(self, path: str):
        """更新面包屑导航"""
        # 清空现有面包屑
        while self.breadcrumb_layout.count() > 1:  # 保留最后的 stretch
            item = self.breadcrumb_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 分解路径
        parts = []
        current = Path(path)

        # 构建路径部分
        while True:
            parts.insert(0, (str(current), current.name if current.name else str(current)))
            parent = current.parent
            if parent == current:  # 到达根目录
                break
            current = parent

        # 创建面包屑按钮
        for i, (full_path, name) in enumerate(parts):
            # 路径按钮
            from PyQt6.QtGui import QPalette
            palette = self.palette()
            
            btn = QPushButton(name if name else full_path)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border: none;
                    color: #0078d4;
                    text-align: left;
                    padding: 4px 8px;
                    font-size: 13px;
                }}
                QPushButton:hover {{
                    background-color: {palette.color(QPalette.ColorRole.Light).name()};
                    border-radius: 3px;
                }}
                QPushButton:pressed {{
                    background-color: {palette.color(QPalette.ColorRole.Midlight).name()};
                }}
            """)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, p=full_path: self.navigate_to(p, add_to_history=True))
            self.breadcrumb_layout.insertWidget(self.breadcrumb_layout.count() - 1, btn)

            # 分隔符（最后一个不加）
            if i < len(parts) - 1:
                from PyQt6.QtGui import QPalette
                palette = self.palette()
                separator = QLabel(" > ")
                separator.setStyleSheet(f"color: {palette.color(QPalette.ColorRole.PlaceholderText).name()}; font-size: 12px;")
                self.breadcrumb_layout.insertWidget(self.breadcrumb_layout.count() - 1, separator)

    def _update_navigation_buttons(self):
        """更新导航按钮状态"""
        self.back_button.setEnabled(self.history_index > 0)
        self.forward_button.setEnabled(self.history_index < len(self.history) - 1)

    def _go_back(self):
        """后退"""
        if self.history_index > 0:
            self.history_index -= 1
            path = self.history[self.history_index]
            self.navigate_to(path, add_to_history=False)

    def _go_forward(self):
        """前进"""
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            path = self.history[self.history_index]
            self.navigate_to(path, add_to_history=False)

    def _go_parent(self):
        """返回上级目录"""
        if self.history:
            current_path = self.history[self.history_index]
            parent_path = str(Path(current_path).parent)
            if parent_path != current_path:  # 确保不是根目录
                self.navigate_to(parent_path, add_to_history=True)

    def _refresh_current(self):
        """刷新当前目录"""
        if self.history:
            current_path = self.history[self.history_index]
            # 刷新文件系统模型
            source_index = self.fs_model.index(current_path)
            if source_index.isValid():
                proxy_index = self.proxy_model.mapFromSource(source_index)
                self.folder_tree.setRootIndex(proxy_index)

    def _on_sort_changed(self, index: int):
        """排序方式改变"""
        # 0: 名称升序, 1: 名称降序
        # 2: 修改时间升序, 3: 修改时间降序
        # 4: 大小升序, 5: 大小降序
        
        if index == 0:  # 名称 ↑
            self.folder_tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        elif index == 1:  # 名称 ↓
            self.folder_tree.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        elif index == 2:  # 修改时间 ↑
            self.folder_tree.sortByColumn(3, Qt.SortOrder.AscendingOrder)
        elif index == 3:  # 修改时间 ↓
            self.folder_tree.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        elif index == 4:  # 大小 ↑
            self.folder_tree.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        elif index == 5:  # 大小 ↓
            self.folder_tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)

    def _toggle_path_edit(self):
        """切换路径编辑模式"""
        if self.path_edit_container.isVisible():
            # 隐藏输入框，显示面包屑
            self._cancel_path_edit()
        else:
            # 显示输入框，隐藏面包屑
            self.breadcrumb_container.hide()
            self.path_edit_container.show()
            if self.history:
                self.path_edit.setText(self.history[self.history_index])
            self.path_edit.setFocus()
            self.path_edit.selectAll()

    def _on_path_edit_confirmed(self):
        """确认路径输入"""
        path = self.path_edit.text().strip()
        if path and os.path.isdir(path):
            self.navigate_to(path, add_to_history=True)
            # 切换回面包屑显示
            self._cancel_path_edit()
        else:
            QMessageBox.warning(self, "路径错误", f"路径不存在或不是有效目录：\n{path}")
            # 保持输入框显示，让用户修改

    def eventFilter(self, obj, event):
        """事件过滤器：处理 Esc 键取消路径编辑和点击外部区域"""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent, QMouseEvent
        
        if obj == self.path_edit:
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    # 取消编辑，恢复面包屑
                    self._cancel_path_edit()
                    return True
            elif event.type() == QEvent.Type.FocusOut:
                # 失去焦点时恢复面包屑
                self._cancel_path_edit()
                return False
        
        return super().eventFilter(obj, event)
    
    def _cancel_path_edit(self):
        """取消路径编辑，恢复面包屑显示"""
        if self.path_edit_container.isVisible():
            self.path_edit_container.hide()
            self.breadcrumb_container.show()

    def _on_folder_double_clicked(self, index: QModelIndex):
        """文件夹双击：进入该文件夹"""
        source_index = self.proxy_model.mapToSource(index)
        path = self.fs_model.filePath(source_index)
        if os.path.isdir(path):
            self.navigate_to(path, add_to_history=True)

    def _on_selection_changed(self):
        """选择改变时更新状态"""
        # 只获取第一列（名称列）的选中行，避免重复计数
        selected_rows = self.folder_tree.selectionModel().selectedRows(0)
        self.selected_folders = [self.fs_model.filePath(self.proxy_model.mapToSource(idx)) for idx in selected_rows]

        count = len(self.selected_folders)
        if count == 0:
            # 没有选中任何文件夹时，显示当前目录
            if self.history and self.history_index >= 0:
                current_dir = self.history[self.history_index]
                dir_name = os.path.basename(current_dir) or current_dir
                self.selection_label.setText(f"将添加当前目录: {dir_name}")
                self.ok_button.setEnabled(True)
            else:
                self.selection_label.setText("未选择")
                self.ok_button.setEnabled(False)
        elif count == 1:
            folder_name = os.path.basename(self.selected_folders[0])
            self.selection_label.setText(f"已选择: {folder_name}")
            self.ok_button.setEnabled(True)
        else:
            self.selection_label.setText(f"已选择 {count} 个文件夹")
            self.ok_button.setEnabled(True)

    def get_selected_folders(self) -> List[str]:
        """获取选中的文件夹列表"""
        # 如果没有选中任何文件夹，返回当前目录
        if not self.selected_folders and self.history and self.history_index >= 0:
            return [self.history[self.history_index]]
        return self.selected_folders

    def _get_config_path(self) -> str:
        """获取配置文件路径，支持打包和开发环境"""
        import sys
        
        if getattr(sys, 'frozen', False):
            # 打包环境：配置文件在 _internal/examples/config.json
            if hasattr(sys, '_MEIPASS'):
                base_path = sys._MEIPASS
            else:
                base_path = os.path.dirname(sys.executable)
            config_path = os.path.join(base_path, "examples", "config.json")
        else:
            # 开发环境：配置文件在项目根目录的 examples/config.json
            # 从当前文件向上找到项目根目录
            current_file = Path(__file__).resolve()
            # folder_dialog.py -> widgets -> desktop_qt_ui -> 项目根目录
            project_root = current_file.parent.parent.parent
            config_path = os.path.join(project_root, "examples", "config.json")
        
        return config_path
    
    def _get_favorites_config_path(self) -> str:
        """获取收藏文件夹配置文件路径（用户目录）"""
        # 使用用户目录存储收藏，避免污染模板文件
        user_config_dir = Path.home() / ".manga-translator-ui"
        user_config_dir.mkdir(exist_ok=True)
        return str(user_config_dir / "favorites.json")

    def _load_favorite_folders(self):
        """从配置文件加载收藏文件夹"""
        try:
            if self.config_service:
                # 使用config_service加载
                config = self.config_service.get_config()
                self.favorite_folders = config.app.favorite_folders or []
            else:
                # 降级方案：直接读取文件
                config_path = self._get_config_path()
                if os.path.exists(config_path):
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_dict = json.load(f)
                        self.favorite_folders = config_dict.get('app', {}).get('favorite_folders', [])
                else:
                    self.favorite_folders = []
        except Exception as e:
            print(f"加载收藏文件夹失败: {e}")
            self.favorite_folders = []

    def _save_favorite_folders(self):
        """保存收藏文件夹到配置文件"""
        try:
            if self.config_service:
                # 使用config_service保存
                config = self.config_service.get_config()
                config.app.favorite_folders = self.favorite_folders
                self.config_service.set_config(config)
                self.config_service.save_config_file()
            else:
                # 降级方案：直接写入文件
                config_path = self._get_config_path()
                
                # 读取现有配置
                config_dict = {}
                if os.path.exists(config_path):
                    try:
                        with open(config_path, 'r', encoding='utf-8') as f:
                            config_dict = json.load(f)
                    except:
                        config_dict = {}
                
                # 确保 app 键存在
                if 'app' not in config_dict:
                    config_dict['app'] = {}
                
                # 确保 app 是字典类型
                if not isinstance(config_dict['app'], dict):
                    config_dict['app'] = {}
                
                # 更新收藏文件夹
                config_dict['app']['favorite_folders'] = self.favorite_folders
                
                # 保存配置
                os.makedirs(os.path.dirname(config_path), exist_ok=True)
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(config_dict, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            print(f"保存收藏文件夹失败: {e}")
            # 不弹窗，避免打扰用户

    def _toggle_favorite(self):
        """切换当前文件夹的收藏状态"""
        if not self.history or self.history_index < 0:
            return
        
        current_path = self.history[self.history_index]
        
        if current_path in self.favorite_folders:
            self._remove_favorite_by_path(current_path)
        else:
            self._add_favorite(current_path)
    
    def _add_favorite(self, folder_path: str):
        """添加文件夹到收藏"""
        if folder_path not in self.favorite_folders:
            self.favorite_folders.append(folder_path)
            self._save_favorite_folders()
            self._update_favorites_in_tree()
        
    def _remove_favorite(self, item):
        """从收藏中移除指定项（通过树项）"""
        path = item.data(Qt.ItemDataRole.UserRole)
        self._remove_favorite_by_path(path)
    
    def _remove_favorite_by_path(self, folder_path: str):
        """从收藏中移除指定路径"""
        if folder_path in self.favorite_folders:
            self.favorite_folders.remove(folder_path)
            self._save_favorite_folders()
            self._update_favorites_in_tree()
            
    def _refresh_shortcuts_tree(self):
        """刷新快捷栏树"""
        self.shortcuts_tree_model.clear()
        self._build_shortcuts_tree()
        self.shortcuts_tree.expandAll()
        # 刷新视图以更新星星显示
        self.shortcuts_tree.viewport().update()
        self.folder_tree.viewport().update()
    
    def _update_favorites_in_tree(self):
        """只更新收藏夹部分，不重建整个树"""
        # 查找收藏夹根节点
        favorite_root = None
        favorite_root_index = -1
        for i in range(self.shortcuts_tree_model.rowCount()):
            item = self.shortcuts_tree_model.item(i)
            if item and item.text() == "⭐ 收藏夹":
                favorite_root = item
                favorite_root_index = i
                break
        
        # 如果有收藏夹，更新它
        if self.favorite_folders:
            if favorite_root:
                # 清空现有的收藏项
                favorite_root.removeRows(0, favorite_root.rowCount())
            else:
                # 创建收藏夹根节点（插入到第一个位置，快速访问之后）
                favorite_root = QStandardItem("⭐ 收藏夹")
                favorite_root.setSelectable(False)
                font = favorite_root.font()
                font.setBold(True)
                favorite_root.setFont(font)
                # 插入到快速访问之后（如果有的话）
                insert_index = 1 if self.shortcuts_tree_model.rowCount() > 0 else 0
                self.shortcuts_tree_model.insertRow(insert_index, favorite_root)
            
            # 添加收藏项
            for path in self.favorite_folders:
                if os.path.exists(path):
                    folder_name = os.path.basename(path) or path
                    item = QStandardItem(f"📁 {folder_name}")
                    item.setData(path, Qt.ItemDataRole.UserRole)
                    item.setData("favorite", Qt.ItemDataRole.UserRole + 1)
                    item.setToolTip(path)
                    favorite_root.appendRow(item)
            
            # 展开收藏夹
            if favorite_root:
                self.shortcuts_tree.expand(self.shortcuts_tree_model.indexFromItem(favorite_root))
        else:
            # 如果没有收藏了，删除收藏夹节点
            if favorite_root and favorite_root_index >= 0:
                self.shortcuts_tree_model.removeRow(favorite_root_index)
        
        # 刷新视图
        self.shortcuts_tree.viewport().update()
        self.folder_tree.viewport().update()


def select_folders(parent=None, start_dir: str = "", multi_select: bool = True, config_service=None) -> Optional[List[str]]:
    """
    显示文件夹选择对话框

    Args:
        parent: 父窗口
        start_dir: 起始目录
        multi_select: 是否支持多选
        config_service: 配置服务实例

    Returns:
        选中的文件夹路径列表，如果取消则返回 None
    """
    dialog = FolderDialog(parent, start_dir, multi_select, config_service)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.get_selected_folders()
    return None
