# -*- coding: utf-8 -*-
"""
现代化文件夹选择器对话框
支持多选、快捷栏、路径导航等功能
"""

import os
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QDir, QModelIndex, pyqtSignal, QSize
from PyQt6.QtGui import QIcon, QFileSystemModel, QStandardItemModel, QStandardItem, QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTreeView,
    QListView, QSplitter, QLineEdit, QLabel, QWidget, QFileIconProvider,
    QMessageBox, QAbstractItemView, QScrollArea, QToolButton, QStyle
)


class FolderDialog(QDialog):
    """现代化文件夹选择对话框"""

    def __init__(self, parent=None, start_dir: str = "", multi_select: bool = True):
        super().__init__(parent)
        self.multi_select = multi_select
        self.selected_folders: List[str] = []
        self.history: List[str] = []  # 导航历史
        self.history_index = -1  # 当前历史位置

        self.setWindowTitle("选择文件夹" + (" (可多选)" if multi_select else ""))
        self.setMinimumSize(1000, 650)
        self.resize(1000, 650)

        # 初始化文件系统模型
        self.fs_model = QFileSystemModel()
        self.fs_model.setRootPath(QDir.rootPath())
        self.fs_model.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot)

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

        # 创建工具栏区域（后退/前进/上级目录）
        toolbar_widget = QWidget()
        toolbar_widget.setStyleSheet("""
            QWidget {
                background-color: #f0f0f0;
                border-bottom: 1px solid #c0c0c0;
            }
            QToolButton {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 3px;
                padding: 4px;
                margin: 2px;
            }
            QToolButton:hover {
                background-color: #e0e0e0;
                border: 1px solid #b0b0b0;
            }
            QToolButton:pressed {
                background-color: #d0d0d0;
            }
            QToolButton:disabled {
                color: #a0a0a0;
            }
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

        layout.addWidget(toolbar_widget)

        # 创建地址栏区域（面包屑导航）
        address_widget = QWidget()
        address_widget.setStyleSheet("""
            QWidget {
                background-color: white;
                border: 1px solid #c0c0c0;
                border-radius: 2px;
            }
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
        self.edit_path_button.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                border: none;
                padding: 2px;
            }
            QToolButton:hover {
                background-color: #e0e0e0;
                border-radius: 2px;
            }
        """)
        address_layout.addWidget(self.edit_path_button)

        # 路径输入框（初始隐藏，点击编辑按钮时显示）
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("输入路径后按回车跳转，或按 Esc 取消")
        self.path_edit.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 2px solid #0078d4;
                border-radius: 3px;
                font-size: 13px;
                background-color: white;
            }
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
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #e0e0e0;
                width: 1px;
            }
        """)

        # 左侧快捷栏
        shortcuts_widget = self._create_shortcuts_panel()
        splitter.addWidget(shortcuts_widget)

        # 右侧文件夹树形视图
        self.folder_tree = QTreeView()
        self.folder_tree.setModel(self.fs_model)
        self.folder_tree.setStyleSheet("""
            QTreeView {
                border: none;
                background-color: white;
                selection-background-color: #0078d4;
                selection-color: white;
                font-size: 13px;
            }
            QTreeView::item {
                padding: 4px;
                border: none;
            }
            QTreeView::item:hover {
                background-color: #f0f0f0;
            }
            QTreeView::item:selected {
                background-color: #0078d4;
                color: white;
            }
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
            tip_label.setStyleSheet("color: #666; font-size: 12px;")
            info_layout.addWidget(tip_label)

        info_layout.addStretch()

        self.selection_label = QLabel("未选择")
        self.selection_label.setStyleSheet("color: #0078d4; font-weight: bold; font-size: 12px;")
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
        self.cancel_button.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                color: #333333;
                border: 1px solid #c0c0c0;
                border-radius: 3px;
                padding: 6px 20px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
                border-color: #b0b0b0;
            }
            QPushButton:pressed {
                background-color: #d0d0d0;
            }
        """)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

    def _create_shortcuts_panel(self) -> QWidget:
        """创建左侧快捷栏 - 树形结构"""
        widget = QWidget()
        widget.setMinimumWidth(180)
        widget.setMaximumWidth(280)
        widget.setStyleSheet("""
            QWidget {
                background-color: #fafafa;
                border-right: 1px solid #e0e0e0;
            }
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
        self.shortcuts_tree.setStyleSheet("""
            QTreeView {
                border: none;
                background-color: transparent;
                selection-background-color: #e5f3ff;
                selection-color: #000000;
                font-size: 13px;
                outline: none;
            }
            QTreeView::item {
                padding: 6px 8px;
                border: none;
            }
            QTreeView::item:hover {
                background-color: #f0f0f0;
            }
            QTreeView::item:selected {
                background-color: #e5f3ff;
                color: #000000;
            }
            QTreeView::branch {
                background-color: transparent;
            }
            QTreeView::branch:has-children:!has-siblings:closed,
            QTreeView::branch:closed:has-children:has-siblings {
                image: url(none);
                border: none;
            }
            QTreeView::branch:open:has-children:!has-siblings,
            QTreeView::branch:open:has-children:has-siblings {
                image: url(none);
                border: none;
            }
        """)

        self.shortcuts_tree_model = QStandardItemModel()
        self.shortcuts_tree.setModel(self.shortcuts_tree_model)

        # 构建快捷访问树
        self._build_shortcuts_tree()

        # 默认展开所有项
        self.shortcuts_tree.expandAll()

        layout.addWidget(self.shortcuts_tree)

        # 连接点击信号
        self.shortcuts_tree.clicked.connect(self._on_tree_shortcut_clicked)

        return widget

    def _build_shortcuts_tree(self):
        """构建快捷访问树形结构"""
        home = Path.home()

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
        index = self.fs_model.index(path)
        if index.isValid():
            self.folder_tree.setRootIndex(index)  # 只显示当前目录内容
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
            btn = QPushButton(name if name else full_path)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    border: none;
                    color: #0078d4;
                    text-align: left;
                    padding: 4px 8px;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background-color: #e5f3ff;
                    border-radius: 3px;
                }
                QPushButton:pressed {
                    background-color: #cce8ff;
                }
            """)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, p=full_path: self.navigate_to(p, add_to_history=True))
            self.breadcrumb_layout.insertWidget(self.breadcrumb_layout.count() - 1, btn)

            # 分隔符（最后一个不加）
            if i < len(parts) - 1:
                separator = QLabel(" > ")
                separator.setStyleSheet("color: #666; font-size: 12px;")
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
            index = self.fs_model.index(current_path)
            if index.isValid():
                self.folder_tree.setRootIndex(index)

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
        path = self.fs_model.filePath(index)
        if os.path.isdir(path):
            self.navigate_to(path, add_to_history=True)

    def _on_selection_changed(self):
        """选择改变时更新状态"""
        # 只获取第一列（名称列）的选中行，避免重复计数
        selected_rows = self.folder_tree.selectionModel().selectedRows(0)
        self.selected_folders = [self.fs_model.filePath(idx) for idx in selected_rows]

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


def select_folders(parent=None, start_dir: str = "", multi_select: bool = True) -> Optional[List[str]]:
    """
    显示文件夹选择对话框

    Args:
        parent: 父窗口
        start_dir: 起始目录
        multi_select: 是否支持多选

    Returns:
        选中的文件夹路径列表，如果取消则返回 None
    """
    dialog = FolderDialog(parent, start_dir, multi_select)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.get_selected_folders()
    return None
