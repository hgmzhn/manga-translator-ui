# -*- coding: utf-8 -*-
"""
现代化文件夹选择器对话框
支持多选、快捷栏、路径导航等功能
"""

import json
import os
from pathlib import Path
from typing import List, Optional

from manga_translator.runtime_paths import get_config_path
from PyQt6.QtCore import (
    QDir,
    QModelIndex,
    QPoint,
    QRect,
    QSortFilterProxyModel,
    Qt,
)
from PyQt6.QtGui import (
    QColor,
    QFileSystemModel,
    QIcon,
    QPainter,
    QPen,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileIconProvider,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    BreadcrumbBar,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    FluentStyleSheet,
    PrimaryPushButton,
    RoundMenu,
    TreeItemDelegate,
    TreeView,
    isDarkTheme,
    themeColor,
)
from qfluentwidgets import LineEdit as QLineEdit
from qfluentwidgets import PushButton as QPushButton
from qfluentwidgets import ToolButton as QToolButton

from services import get_i18n_manager
from ui.secondary_pages.fluent_dialog import (
    DialogCode,
    FluentSecondaryDialog,
    normalize_dialog_parent,
)
from ui.widgets.hover_hint import set_hover_hint

_DEFAULT_FOLDER_SORT = "name_ascending"
_FOLDER_SORT_STATE_TO_SPEC = {
    "name_ascending": (0, Qt.SortOrder.AscendingOrder),
    "name_descending": (0, Qt.SortOrder.DescendingOrder),
    "size_ascending": (1, Qt.SortOrder.AscendingOrder),
    "size_descending": (1, Qt.SortOrder.DescendingOrder),
    "type_ascending": (2, Qt.SortOrder.AscendingOrder),
    "type_descending": (2, Qt.SortOrder.DescendingOrder),
    "modified_ascending": (3, Qt.SortOrder.AscendingOrder),
    "modified_descending": (3, Qt.SortOrder.DescendingOrder),
}
_FOLDER_SORT_SPEC_TO_STATE = {
    spec: state for state, spec in _FOLDER_SORT_STATE_TO_SPEC.items()
}


def _normalize_folder_sort_state(value: object) -> str:
    if isinstance(value, str) and value in _FOLDER_SORT_STATE_TO_SPEC:
        return value
    return _DEFAULT_FOLDER_SORT


def _folder_sort_spec(state: object) -> tuple[int, Qt.SortOrder]:
    return _FOLDER_SORT_STATE_TO_SPEC[_normalize_folder_sort_state(state)]


def _folder_sort_state(column: int, order: Qt.SortOrder) -> Optional[str]:
    return _FOLDER_SORT_SPEC_TO_STATE.get((column, order))


class CaseInsensitiveSortProxyModel(QSortFilterProxyModel):
    """按文件系统属性排序，名称比较不区分大小写。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.header_overrides = {}

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """使用原始文件属性比较，避免格式化文本造成日期和大小错序。"""
        source_model = self.sourceModel()
        if isinstance(source_model, QFileSystemModel):
            column = left.column()
            if column == 3:
                left_modified = (
                    source_model.fileInfo(left).lastModified().toMSecsSinceEpoch()
                )
                right_modified = (
                    source_model.fileInfo(right).lastModified().toMSecsSinceEpoch()
                )
                return left_modified < right_modified

        left_data = source_model.data(left, Qt.ItemDataRole.DisplayRole)
        right_data = source_model.data(right, Qt.ItemDataRole.DisplayRole)
        if left_data is None or right_data is None:
            return False

        return str(left_data).casefold() < str(right_data).casefold()

    def set_header_override(self, section: int, text: str):
        """设置表头显示文本覆盖"""
        self.header_overrides[section] = text

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """优先返回自定义表头，避免 QFileSystemModel 使用系统默认列名"""
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and section in self.header_overrides
        ):
            return self.header_overrides[section]
        return super().headerData(section, orientation, role)


class _FavoriteStarDelegate(TreeItemDelegate):
    """在 Fluent 树样式之上叠加收藏星标的委托基类。

    继承 qfluentwidgets 的 TreeItemDelegate（parent 必须是树视图），
    保留原生悬停/选中样式；星标在悬停/选中/已收藏时显示，点击切换收藏。
    收藏状态与配色实时读取对话框，避免持有过期引用。
    """

    STAR_SIZE = 16  # 和图标一样大
    STAR_MARGIN = 4  # 星星和图标之间的间距

    def __init__(self, tree: TreeView, dialog: "FolderDialog"):
        super().__init__(tree)
        self._dialog = dialog

    def _folder_path(self, index: QModelIndex) -> str:
        """由子类实现：从 index 解析出文件夹路径。"""
        raise NotImplementedError

    def paint(self, painter: QPainter, option, index: QModelIndex):
        # 先绘制 Fluent 默认样式
        super().paint(painter, option, index)

        folder_path = self._folder_path(index)
        if not folder_path or not os.path.isdir(folder_path):
            return

        is_favorited = folder_path in self._dialog.favorite_folders
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        # 仅在悬停/选中/已收藏时显示星标
        if not (is_favorited or is_selected or is_hovered):
            return

        # 星星画在行右侧，避免与图标和文本重叠
        star_rect = self.get_star_rect(option.rect)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if is_favorited:
            # 实心星星（已收藏）
            favorite_color = QColor(self._dialog._favorite_star_color)
            painter.setPen(QPen(favorite_color, 1))
            painter.setBrush(favorite_color)
        else:
            # 空心星星（未收藏）
            outline_color = QColor(
                self._dialog._border_hover_color
                if is_selected
                else self._dialog._border_color
            )
            painter.setPen(QPen(outline_color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)

        self.draw_star(painter, star_rect)
        painter.restore()

    def draw_star(self, painter: QPainter, rect: QRect):
        """绘制五角星"""
        from math import cos, pi, sin

        from PyQt6.QtGui import QPolygon

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

        painter.drawPolygon(QPolygon(points))

    def get_star_rect(self, item_rect: QRect) -> QRect:
        """获取星星的绘制区域 - 在行右侧"""
        x = item_rect.right() - self.STAR_SIZE - self.STAR_MARGIN - 6
        y = item_rect.top() + (item_rect.height() - self.STAR_SIZE) // 2
        return QRect(x, y, self.STAR_SIZE, self.STAR_SIZE)

    def editorEvent(self, event, model, option, index):
        """点击星标区域切换收藏状态"""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QMouseEvent

        if event.type() == QEvent.Type.MouseButtonRelease and isinstance(
            event, QMouseEvent
        ):
            star_rect = self.get_star_rect(option.rect)
            if star_rect.contains(event.position().toPoint()):
                folder_path = self._folder_path(index)
                if folder_path and os.path.isdir(folder_path):
                    if folder_path in self._dialog.favorite_folders:
                        self._dialog._remove_favorite_by_path(folder_path)
                    else:
                        self._dialog._add_favorite(folder_path)
                    return True

        return super().editorEvent(event, model, option, index)


class FavoriteDelegate(_FavoriteStarDelegate):
    """目录树（QFileSystemModel + 排序代理）的收藏星标委托"""

    def __init__(self, tree: TreeView, dialog: "FolderDialog", fs_model, proxy_model):
        super().__init__(tree, dialog)
        self.fs_model = fs_model
        self.proxy_model = proxy_model

    def _folder_path(self, index: QModelIndex) -> str:
        if self.fs_model is None or self.proxy_model is None:
            return ""
        source_index = self.proxy_model.mapToSource(index)
        return self.fs_model.filePath(source_index) or ""


class ShortcutFavoriteDelegate(_FavoriteStarDelegate):
    """左侧快捷栏的收藏星标委托"""

    def __init__(self, tree: TreeView, dialog: "FolderDialog", shortcuts_model):
        super().__init__(tree, dialog)
        self.shortcuts_model = shortcuts_model

    def _folder_path(self, index: QModelIndex) -> str:
        if self.shortcuts_model is None:
            return ""
        item = self.shortcuts_model.itemFromIndex(index)
        if item is None:
            return ""
        return item.data(Qt.ItemDataRole.UserRole) or ""


class FolderDialog(FluentSecondaryDialog):
    """现代化文件夹选择对话框"""

    def __init__(
        self,
        parent=None,
        start_dir: str = "",
        multi_select: bool = True,
        config_service=None,
    ):
        super().__init__(parent)
        self.multi_select = multi_select
        self.selected_folders: List[str] = []
        self.history: List[str] = []  # 导航历史
        self.history_index = -1  # 当前历史位置
        self.favorite_folders: List[str] = []  # 收藏的文件夹
        self._path_error_dialog_active = False  # 路径校验弹窗期间不因 FocusOut 取消编辑
        self.config_service = config_service
        self.i18n = get_i18n_manager()
        self._setup_fluent_colors()

        self.setWindowTitle(
            self._t("Select Folder")
            + (self._t(" (Multi-select)") if multi_select else "")
        )
        self.setWindowIcon(FluentIcon.FOLDER.qicon())
        self.setMinimumSize(760, 520)
        self.resize(1000, 650)

        # 初始化文件系统模型
        self.fs_model = QFileSystemModel()
        self.fs_model.setRootPath(QDir.rootPath())
        # 显示所有文件夹，包括隐藏文件夹
        self.fs_model.setFilter(
            QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden
        )

        # 使用代理模型实现不区分大小写的排序
        self.proxy_model = CaseInsensitiveSortProxyModel()
        self.proxy_model.setSourceModel(self.fs_model)
        self.proxy_model.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        # 加载收藏文件夹
        self._load_favorite_folders()
        self.folder_sort_state = self._load_folder_sort_state()

        self._init_ui()
        self._connect_signals()
        self._refresh_header_i18n()

        # 设置初始目录
        if start_dir and os.path.isdir(start_dir):
            self.navigate_to(start_dir, add_to_history=True)
        else:
            self.navigate_to(str(Path.home()), add_to_history=True)

    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def _setup_fluent_colors(self):
        """Initialize colors from qfluentwidgets' native theme state."""
        dark = isDarkTheme()
        accent = QColor(themeColor())

        self._border_color = QColor(255, 255, 255, 54) if dark else QColor(0, 0, 0, 46)
        self._border_hover_color = QColor(accent)
        self._border_hover_color.setAlpha(190)
        self._favorite_star_color = QColor(accent)

    def _theme_plain_container(self, widget: QWidget):
        del widget

    def _theme_tree_view(self, tree: TreeView):
        """Apply qfluentwidgets' native tree style."""
        FluentStyleSheet.TREE_VIEW.apply(tree)
        tree.setFrameShape(TreeView.Shape.NoFrame)
        tree.setAlternatingRowColors(False)

    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # 创建工具栏区域（后退/前进/上级目录）
        toolbar_widget = QWidget()
        self._theme_plain_container(toolbar_widget)
        toolbar_layout = QHBoxLayout(toolbar_widget)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(4)

        # 后退按钮
        self.back_button = QToolButton()
        self.back_button.setIcon(FluentIcon.LEFT_ARROW)
        set_hover_hint(self.back_button, self._t("Back"))
        self.back_button.setFixedSize(34, 34)
        self.back_button.setEnabled(False)
        toolbar_layout.addWidget(self.back_button)

        # 前进按钮
        self.forward_button = QToolButton()
        self.forward_button.setIcon(FluentIcon.RIGHT_ARROW)
        set_hover_hint(self.forward_button, self._t("Forward"))
        self.forward_button.setFixedSize(34, 34)
        self.forward_button.setEnabled(False)
        toolbar_layout.addWidget(self.forward_button)

        # 上级目录按钮
        self.parent_button = QToolButton()
        self.parent_button.setIcon(FluentIcon.UP)
        set_hover_hint(self.parent_button, self._t("Parent Directory"))
        self.parent_button.setFixedSize(34, 34)
        toolbar_layout.addWidget(self.parent_button)

        # 刷新按钮
        self.refresh_button = QToolButton()
        self.refresh_button.setIcon(FluentIcon.SYNC)
        set_hover_hint(self.refresh_button, self._t("Refresh"))
        self.refresh_button.setFixedSize(34, 34)
        toolbar_layout.addWidget(self.refresh_button)

        # 顶部单行：导航按钮 + 地址栏
        top_bar_widget = CardWidget()
        top_bar_layout = QHBoxLayout(top_bar_widget)
        top_bar_layout.setContentsMargins(10, 6, 10, 6)
        top_bar_layout.setSpacing(8)

        # 创建地址栏区域（面包屑导航）
        address_widget = CardWidget()
        address_layout = QHBoxLayout(address_widget)
        address_layout.setContentsMargins(8, 4, 8, 4)
        address_layout.setSpacing(5)

        # 地址栏左侧不显示标签，保持和现代资源管理器一致

        self.breadcrumb_bar = BreadcrumbBar()
        self.breadcrumb_bar.setMaximumHeight(35)
        address_layout.addWidget(self.breadcrumb_bar, 1)

        # 地址栏编辑按钮
        self.edit_path_button = QToolButton()
        self.edit_path_button.setIcon(FluentIcon.EDIT)
        set_hover_hint(self.edit_path_button, self._t("Edit Path"))
        address_layout.addWidget(self.edit_path_button)

        # 路径输入框（初始隐藏，点击编辑按钮时显示）
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(self._t("Path input hint"))

        # 创建一个容器来包含面包屑和输入框，它们互斥显示
        self.address_container = QWidget()
        self._theme_plain_container(self.address_container)
        address_container_layout = QVBoxLayout(self.address_container)
        address_container_layout.setContentsMargins(0, 0, 0, 0)
        address_container_layout.setSpacing(0)

        # 面包屑容器
        self.breadcrumb_container = QWidget()
        self._theme_plain_container(self.breadcrumb_container)
        breadcrumb_container_layout = QVBoxLayout(self.breadcrumb_container)
        breadcrumb_container_layout.setContentsMargins(0, 0, 0, 0)
        breadcrumb_container_layout.addWidget(address_widget)

        # 输入框容器
        self.path_edit_container = QWidget()
        self._theme_plain_container(self.path_edit_container)
        path_edit_layout = QVBoxLayout(self.path_edit_container)
        path_edit_layout.setContentsMargins(0, 0, 0, 0)
        path_edit_layout.addWidget(self.path_edit)
        self.path_edit_container.hide()

        # 将两个容器添加到主地址栏容器
        address_container_layout.addWidget(self.breadcrumb_container)
        address_container_layout.addWidget(self.path_edit_container)
        top_bar_layout.addWidget(toolbar_widget, 0)
        top_bar_layout.addWidget(self.address_container, 1)

        layout.addWidget(top_bar_widget)

        # 主内容区域：左侧快捷栏 + 右侧文件夹树
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._theme_plain_container(splitter)

        # 左侧快捷栏
        shortcuts_widget = self._create_shortcuts_panel()
        splitter.addWidget(shortcuts_widget)

        # 右侧文件夹树形视图
        self.folder_tree = TreeView()
        self.folder_tree.setMouseTracking(True)
        self.folder_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_tree.setModel(self.proxy_model)
        self._theme_tree_view(self.folder_tree)

        # 名称列启用收藏星标委托（悬停/选中显示，点击切换收藏）
        self.favorite_delegate = FavoriteDelegate(
            self.folder_tree, self, self.fs_model, self.proxy_model
        )
        self.folder_tree.setItemDelegateForColumn(0, self.favorite_delegate)

        # 仅显示两列：名称、修改日期
        self.folder_tree.showColumn(0)  # Name
        self.folder_tree.showColumn(3)  # Date Modified
        self.folder_tree.hideColumn(1)  # Size
        self.folder_tree.hideColumn(2)  # Type

        # 设置多选模式
        if self.multi_select:
            self.folder_tree.setSelectionMode(
                QAbstractItemView.SelectionMode.ExtendedSelection
            )
        else:
            self.folder_tree.setSelectionMode(
                QAbstractItemView.SelectionMode.SingleSelection
            )
        self.folder_tree.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )

        self.folder_tree.setHeaderHidden(False)
        self.folder_tree.setSortingEnabled(True)
        sort_column, sort_order = _folder_sort_spec(self.folder_sort_state)
        self.folder_tree.sortByColumn(sort_column, sort_order)
        self.folder_tree.setAlternatingRowColors(False)
        header = self.folder_tree.header()
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        header.setMinimumHeight(34)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(False)
        header.moveSection(3, 1)  # Date Modified 到第2列
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(3, 180)

        splitter.addWidget(self.folder_tree)

        # 设置分割比例：快捷栏占20%，文件夹树占80%
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 8)

        layout.addWidget(splitter, 1)

        # 底部提示和选中信息
        info_widget = CardWidget()
        info_layout = QHBoxLayout(info_widget)
        info_layout.setContentsMargins(10, 6, 10, 6)

        if self.multi_select:
            tip_label = CaptionLabel(
                self._t(
                    "Tip: Hold Ctrl or Shift to select multiple folders, right-click to favorite"
                )
            )
            info_layout.addWidget(tip_label)

        info_layout.addStretch()

        self.selection_label = BodyLabel(self._t("Not Selected"))
        info_layout.addWidget(self.selection_label)

        layout.addWidget(info_widget)

        # 底部按钮
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(8, 8, 8, 8)
        button_layout.addStretch()

        self.ok_button = PrimaryPushButton(self._t("OK"))
        self.ok_button.setIcon(FluentIcon.ACCEPT)
        self.ok_button.setMinimumWidth(100)
        self.ok_button.setMinimumHeight(32)
        self.ok_button.setEnabled(False)
        button_layout.addWidget(self.ok_button)

        self.cancel_button = QPushButton(self._t("Cancel"))
        self.cancel_button.setIcon(FluentIcon.CANCEL)
        self.cancel_button.setMinimumWidth(100)
        self.cancel_button.setMinimumHeight(32)
        # Use standard button style from theme
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

    def _create_shortcuts_panel(self) -> QWidget:
        """创建左侧快捷栏 - 树形结构"""
        widget = CardWidget()
        widget.setMinimumWidth(180)
        widget.setMaximumWidth(280)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 创建树形视图
        self.shortcuts_tree = TreeView()
        self.shortcuts_tree.setMouseTracking(True)
        self.shortcuts_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.shortcuts_tree.setHeaderHidden(True)
        self.shortcuts_tree.setIndentation(12)
        self.shortcuts_tree.setAnimated(True)
        self.shortcuts_tree.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._theme_tree_view(self.shortcuts_tree)

        self.shortcuts_tree_model = QStandardItemModel()
        self.shortcuts_tree.setModel(self.shortcuts_tree_model)

        # 快捷栏同样启用收藏星标委托
        self.shortcut_favorite_delegate = ShortcutFavoriteDelegate(
            self.shortcuts_tree, self, self.shortcuts_tree_model
        )
        self.shortcuts_tree.setItemDelegateForColumn(0, self.shortcut_favorite_delegate)

        # 构建快捷访问树
        self._build_shortcuts_tree()

        # 默认展开所有项
        self.shortcuts_tree.expandAll()

        layout.addWidget(self.shortcuts_tree)

        # 连接点击信号
        self.shortcuts_tree.clicked.connect(self._on_tree_shortcut_clicked)

        return widget

    def _make_shortcut_item(
        self,
        text: str,
        path: str = "",
        icon: Optional[QIcon] = None,
        selectable: bool = True,
    ) -> QStandardItem:
        """创建快捷栏项（统一图标和数据）"""
        item = QStandardItem(text)
        if icon and not icon.isNull():
            item.setIcon(icon)
        item.setSelectable(selectable)
        if path:
            item.setData(path, Qt.ItemDataRole.UserRole)
            item.setToolTip(path)
        return item

    def _normalize_shortcut_name(self, name: str) -> str:
        """移除名称前缀里的 emoji/符号，保留可读文本"""
        parts = name.split(" ", 1)
        if len(parts) == 2:
            prefix = parts[0]
            if not prefix.isalnum():
                return parts[1]
        return name

    def _build_shortcuts_tree(self):
        """构建快捷访问树形结构"""
        home = Path.home()
        style = self.style()
        icon_provider = QFileIconProvider()
        dir_icon = icon_provider.icon(QFileIconProvider.IconType.Folder)
        desktop_icon = style.standardIcon(QStyle.StandardPixmap.SP_DesktopIcon)
        drive_icon = style.standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon)
        file_icon = FluentIcon.DOCUMENT.qicon()
        quick_icon = FluentIcon.HOME.qicon()
        favorite_icon = FluentIcon.HEART.qicon()

        # 收藏文件夹分组 - 放在快速访问之后
        # 获取真实的快速访问文件夹（从注册表/系统）
        quick_access_folders = self._get_quick_access_folders()

        if quick_access_folders:
            # 快速访问分组
            quick_access_root = self._make_shortcut_item(
                self._t("Quick Access"), icon=quick_icon, selectable=False
            )
            font = quick_access_root.font()
            font.setBold(True)
            quick_access_root.setFont(font)
            self.shortcuts_tree_model.appendRow(quick_access_root)

            for name, path in quick_access_folders:
                clean_name = self._normalize_shortcut_name(name)
                item = self._make_shortcut_item(clean_name, path=path, icon=dir_icon)
                quick_access_root.appendRow(item)

        # 收藏文件夹分组 - 放在快速访问和此电脑之间
        if self.favorite_folders:
            favorite_root = self._make_shortcut_item(
                self._t("Favorites"), icon=favorite_icon, selectable=False
            )
            font = favorite_root.font()
            font.setBold(True)
            favorite_root.setFont(font)
            self.shortcuts_tree_model.appendRow(favorite_root)

            for path in self.favorite_folders:
                if os.path.exists(path):
                    folder_name = os.path.basename(path) or path
                    item = self._make_shortcut_item(
                        folder_name, path=path, icon=dir_icon
                    )
                    item.setData(
                        "favorite", Qt.ItemDataRole.UserRole + 1
                    )  # 标记为收藏项
                    favorite_root.appendRow(item)

        # 此电脑分组
        this_pc_root = self._make_shortcut_item(
            self._t("This PC"), icon=drive_icon, selectable=False
        )
        font = this_pc_root.font()
        font.setBold(True)
        this_pc_root.setFont(font)
        self.shortcuts_tree_model.appendRow(this_pc_root)

        # 用户文件夹
        user_folders = [
            (self._t("Desktop"), home / "Desktop", desktop_icon),
            (self._t("Documents"), home / "Documents", file_icon),
            (self._t("Downloads"), home / "Downloads", dir_icon),
            (self._t("Pictures"), home / "Pictures", dir_icon),
            (self._t("Music"), home / "Music", dir_icon),
            (self._t("Videos"), home / "Videos", dir_icon),
        ]

        for name, path, icon in user_folders:
            if path.exists():
                item = self._make_shortcut_item(name, path=str(path), icon=icon)
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
                        display_name = f"{volume_name} ({drive_path})"
                    else:
                        display_name = f"{self._t('Local Disk')} ({drive_path})"
                except Exception:
                    display_name = f"{self._t('Local Disk')} ({drive_path})"

                drives_list.append((display_name, str(drive_path)))

        # 按盘符排序
        drives_list.sort(key=lambda x: x[1])
        for name, path in drives_list:
            item = self._make_shortcut_item(name, path=path, icon=drive_icon)
            this_pc_root.appendRow(item)

    def _get_quick_access_folders(self):
        """从 Windows 注册表获取真实的快速访问文件夹"""
        quick_access = []

        try:
            import winreg

            # 尝试读取快速访问的固定文件夹（从注册表）
            # HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders
            key_path = (
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            )
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)

            # 常见的快速访问项
            shell_folders = {
                "Desktop": self._t("Desktop"),
                "My Pictures": self._t("Pictures"),
                "{374DE290-123F-4565-9164-39C4925E467B}": self._t("Downloads"),
                "Personal": self._t("Documents"),
                "My Music": self._t("Music"),
                "My Video": self._t("Videos"),
            }

            for value_name, display_name in shell_folders.items():
                try:
                    path_value, _ = winreg.QueryValueEx(key, value_name)
                    # 展开环境变量
                    expanded_path = os.path.expandvars(path_value)
                    if os.path.exists(expanded_path):
                        quick_access.append((display_name, expanded_path))
                except Exception:
                    pass

            winreg.CloseKey(key)

        except Exception:
            # 如果读取注册表失败，使用默认路径
            home = Path.home()
            default_folders = [
                (self._t("Desktop"), home / "Desktop"),
                (self._t("Documents"), home / "Documents"),
                (self._t("Downloads"), home / "Downloads"),
                (self._t("Pictures"), home / "Pictures"),
            ]
            for name, path in default_folders:
                if path.exists():
                    quick_access.append((name, str(path)))

        # 添加用户目录下的其他常见文件夹（排除系统文件夹）
        try:
            home = Path.home()
            exclude_names = {
                "Desktop",
                "Documents",
                "Downloads",
                "Pictures",
                "Music",
                "Videos",
                "AppData",
                "Application Data",
                "Cookies",
                "Local Settings",
                "NetHood",
                "PrintHood",
                "Recent",
                "SendTo",
                "Templates",
                "Start Menu",
                "ntuser.dat",
                "NTUSER.DAT",
            }

            additional_folders = []
            if home.exists():
                for item in home.iterdir():
                    if (
                        item.is_dir()
                        and not item.name.startswith(".")
                        and not item.name.startswith("$")
                    ):
                        if item.name not in exclude_names:
                            # 跳过 OneDrive（稍后单独处理）
                            if not item.name.startswith("OneDrive"):
                                additional_folders.append(
                                    (f"📂 {item.name}", str(item))
                                )

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

        except Exception:
            pass

        return quick_access

    def _on_tree_shortcut_clicked(self, index: QModelIndex):
        """树形快捷方式点击"""
        item = self.shortcuts_tree_model.itemFromIndex(index)
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            if path and os.path.isdir(path):
                self.navigate_to(path, add_to_history=True)

    def _show_folder_tree_context_menu(self, pos):
        """右键菜单：目录树收藏操作"""
        index = self.folder_tree.indexAt(pos)
        if not index.isValid():
            return

        source_index = self.proxy_model.mapToSource(index)
        folder_path = self.fs_model.filePath(source_index)
        if not folder_path or not os.path.isdir(folder_path):
            return

        menu = RoundMenu(parent=self)
        if folder_path in self.favorite_folders:
            action = Action(self._t("Remove from Favorites"), self)
            action.triggered.connect(lambda: self._remove_favorite_by_path(folder_path))
        else:
            action = Action(self._t("Add to Favorites"), self)
            action.triggered.connect(lambda: self._add_favorite(folder_path))
        menu.addAction(action)
        menu.exec(self.folder_tree.viewport().mapToGlobal(pos))

    def _show_shortcuts_context_menu(self, pos):
        """右键菜单：快捷栏收藏操作"""
        index = self.shortcuts_tree.indexAt(pos)
        if not index.isValid():
            return

        item = self.shortcuts_tree_model.itemFromIndex(index)
        if not item:
            return

        folder_path = item.data(Qt.ItemDataRole.UserRole)
        if not folder_path or not os.path.isdir(folder_path):
            return

        menu = RoundMenu(parent=self)
        if folder_path in self.favorite_folders:
            action = Action(self._t("Remove from Favorites"), self)
            action.triggered.connect(lambda: self._remove_favorite_by_path(folder_path))
        else:
            action = Action(self._t("Add to Favorites"), self)
            action.triggered.connect(lambda: self._add_favorite(folder_path))
        menu.addAction(action)
        menu.exec(self.shortcuts_tree.viewport().mapToGlobal(pos))

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
        self.breadcrumb_bar.currentItemChanged.connect(self._on_breadcrumb_item_changed)
        self.edit_path_button.clicked.connect(self._toggle_path_edit)
        self.path_edit.returnPressed.connect(self._on_path_edit_confirmed)
        self.path_edit.installEventFilter(self)

        self.folder_tree.selectionModel().selectionChanged.connect(
            self._on_selection_changed
        )
        self.folder_tree.doubleClicked.connect(self._on_folder_double_clicked)
        self.folder_tree.customContextMenuRequested.connect(
            self._show_folder_tree_context_menu
        )
        self.folder_tree.header().sortIndicatorChanged.connect(
            self._on_folder_sort_indicator_changed
        )
        self.shortcuts_tree.customContextMenuRequested.connect(
            self._show_shortcuts_context_menu
        )

    def _refresh_header_i18n(self):
        """刷新目录表头文案（覆盖 QFileSystemModel 默认系统列名）"""
        self.proxy_model.set_header_override(0, self._t("Name"))
        self.proxy_model.set_header_override(3, self._t("Date Modified"))
        if hasattr(self, "folder_tree"):
            self.folder_tree.header().viewport().update()

    def _on_breadcrumb_item_changed(self, route_key: str):
        """导航到 BreadcrumbBar item 对应的路径。"""
        self.navigate_to(route_key, add_to_history=True)

    def navigate_to(self, path: str, add_to_history: bool = True):
        """导航到指定路径"""
        if not os.path.isdir(path):
            return

        path = os.path.normpath(path)

        # 添加到历史记录
        if add_to_history:
            # 如果当前不在历史末尾，删除当前位置之后的历史
            if self.history_index < len(self.history) - 1:
                self.history = self.history[: self.history_index + 1]

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
        parts = []
        current = Path(path)

        while True:
            parts.insert(
                0, (str(current), current.name if current.name else str(current))
            )
            parent = current.parent
            if parent == current:  # 到达根目录
                break
            current = parent

        self.breadcrumb_bar.blockSignals(True)
        try:
            self.breadcrumb_bar.clear()
            for full_path, name in parts:
                self.breadcrumb_bar.addItem(full_path, name if name else full_path)
            self.breadcrumb_bar.setCurrentItem(path)
        finally:
            self.breadcrumb_bar.blockSignals(False)

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

    def _on_folder_sort_indicator_changed(self, column: int, order: Qt.SortOrder):
        state = _folder_sort_state(column, order)
        if state is None or state == self.folder_sort_state:
            return
        self.folder_sort_state = state
        self._save_folder_sort_state()

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
            # 模态警告会抢走输入框焦点；置位标志，让 FocusOut 不取消编辑，
            # 警告关闭后恢复焦点，保留用户已输入的内容供修改。
            self._path_error_dialog_active = True
            try:
                QMessageBox.warning(
                    self,
                    self._t("Path Error"),
                    self._t(
                        "Path does not exist or is not a valid directory:\n{path}",
                        path=path,
                    ),
                )
            finally:
                self._path_error_dialog_active = False
            self.path_edit.setFocus()
            self.path_edit.selectAll()

    def eventFilter(self, obj, event):
        """事件过滤器：处理 Esc 键取消路径编辑和点击外部区域"""
        from PyQt6.QtCore import QEvent

        if obj == self.path_edit:
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    # 取消编辑，恢复面包屑
                    self._cancel_path_edit()
                    return True
            elif event.type() == QEvent.Type.FocusOut:
                # 校验警告弹窗抢焦点导致的 FocusOut 不算用户离开编辑
                if not self._path_error_dialog_active:
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
        self.selected_folders = [
            self.fs_model.filePath(self.proxy_model.mapToSource(idx))
            for idx in selected_rows
        ]

        count = len(self.selected_folders)
        if count == 0:
            # 没有选中任何文件夹时，显示当前目录
            if self.history and self.history_index >= 0:
                current_dir = self.history[self.history_index]
                dir_name = os.path.basename(current_dir) or current_dir
                self.selection_label.setText(
                    self._t("Will add current directory: {name}", name=dir_name)
                )
                self.ok_button.setEnabled(True)
            else:
                self.selection_label.setText(self._t("Not Selected"))
                self.ok_button.setEnabled(False)
        elif count == 1:
            folder_name = os.path.basename(self.selected_folders[0])
            self.selection_label.setText(self._t("Selected: {name}", name=folder_name))
            self.ok_button.setEnabled(True)
        else:
            self.selection_label.setText(
                self._t("Selected {count} folders", count=count)
            )
            self.ok_button.setEnabled(True)

    def get_selected_folders(self) -> List[str]:
        """获取选中的文件夹列表"""
        # 如果没有选中任何文件夹，返回当前目录
        if not self.selected_folders and self.history and self.history_index >= 0:
            return [self.history[self.history_index]]
        return self.selected_folders

    def _get_config_path(self) -> str:
        """获取配置文件路径，支持打包和开发环境"""
        return get_config_path("config.json")

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
                    with open(config_path, "r", encoding="utf-8") as f:
                        config_dict = json.load(f)
                        self.favorite_folders = config_dict.get("app", {}).get(
                            "favorite_folders", []
                        )
                else:
                    self.favorite_folders = []
        except Exception as e:
            print(f"加载收藏文件夹失败: {e}")
            self.favorite_folders = []

    def _load_folder_sort_state(self) -> str:
        """Load the last folder-tree sort selection."""
        try:
            if self.config_service:
                value = self.config_service.get_config().app.folder_dialog_sort
            else:
                value = _DEFAULT_FOLDER_SORT
                config_path = self._get_config_path()
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        config_dict = json.load(f)
                    value = config_dict.get("app", {}).get(
                        "folder_dialog_sort",
                        _DEFAULT_FOLDER_SORT,
                    )
            return _normalize_folder_sort_state(value)
        except Exception as e:
            print(f"加载文件夹排序方式失败: {e}")
            return _DEFAULT_FOLDER_SORT

    def _save_folder_sort_state(self):
        """Persist the folder-tree sort selection."""
        try:
            if self.config_service:
                config = self.config_service.get_config()
                config.app.folder_dialog_sort = self.folder_sort_state
                self.config_service.set_config(config)
                self.config_service.save_config_file()
                return

            config_path = self._get_config_path()
            config_dict = {}
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        config_dict = json.load(f)
                except Exception:
                    config_dict = {}

            if not isinstance(config_dict.get("app"), dict):
                config_dict["app"] = {}
            config_dict["app"]["folder_dialog_sort"] = self.folder_sort_state
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存文件夹排序方式失败: {e}")

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
                        with open(config_path, "r", encoding="utf-8") as f:
                            config_dict = json.load(f)
                    except Exception:
                        config_dict = {}

                # 确保 app 键存在
                if "app" not in config_dict:
                    config_dict["app"] = {}

                # 确保 app 是字典类型
                if not isinstance(config_dict["app"], dict):
                    config_dict["app"] = {}

                # 更新收藏文件夹
                config_dict["app"]["favorite_folders"] = self.favorite_folders

                # 保存配置
                os.makedirs(os.path.dirname(config_path), exist_ok=True)
                with open(config_path, "w", encoding="utf-8") as f:
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
            if item and item.text() == self._t("Favorites"):
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
                favorite_root = self._make_shortcut_item(
                    self._t("Favorites"),
                    icon=FluentIcon.HEART.qicon(),
                    selectable=False,
                )
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
                    item = self._make_shortcut_item(
                        folder_name,
                        path=path,
                        icon=QFileIconProvider().icon(
                            QFileIconProvider.IconType.Folder
                        ),
                    )
                    item.setData("favorite", Qt.ItemDataRole.UserRole + 1)
                    favorite_root.appendRow(item)

            # 展开收藏夹
            if favorite_root:
                self.shortcuts_tree.expand(
                    self.shortcuts_tree_model.indexFromItem(favorite_root)
                )
        else:
            # 如果没有收藏了，删除收藏夹节点
            if favorite_root and favorite_root_index >= 0:
                self.shortcuts_tree_model.removeRow(favorite_root_index)

        # 刷新视图
        self.shortcuts_tree.viewport().update()
        self.folder_tree.viewport().update()


def select_folders(
    parent=None, start_dir: str = "", multi_select: bool = True, config_service=None
) -> Optional[List[str]]:
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
    # parent 归一化到顶层窗口；parent=None 时回退当前活动窗口
    # （FluentSecondaryDialog 基类同样兜底，这里显式声明对话框侧契约）
    dialog = FolderDialog(
        normalize_dialog_parent(parent), start_dir, multi_select, config_service
    )
    if dialog.exec() == DialogCode.Accepted:
        return dialog.get_selected_folders()
    return None
