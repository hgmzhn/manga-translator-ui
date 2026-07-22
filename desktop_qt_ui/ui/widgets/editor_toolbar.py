
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QActionGroup
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QProxyStyle,
    QSizePolicy,
    QStyle,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CardWidget,
    CheckableMenu,
    DropDownPushButton,
    FluentIcon as FIF,
    MenuIndicatorType,
    RoundMenu,
    SingleDirectionScrollArea,
    Slider,
    ToolButton,
    VerticalSeparator,
)
from services import get_i18n_manager
from ui.fluent_icon import themed_fluent_svg_icon
from ui.widgets.hover_hint import set_hover_hint


class _LeadingIndicatorMenuStyle(QProxyStyle):
    """给左侧选中标记腾出独立列，排列为：标记 → 图标 → 文字。"""

    CONTENT_OFFSET = 24

    def subElementRect(self, element, option, widget=None):
        rect = super().subElementRect(element, option, widget)
        if element == QStyle.SubElement.SE_ItemViewItemDecoration:
            rect.translate(self.CONTENT_OFFSET, 0)
        elif element == QStyle.SubElement.SE_ItemViewItemText:
            rect.setLeft(rect.left() + self.CONTENT_OFFSET)
        return rect


class _IconCheckableMenu(CheckableMenu):
    """带独立左侧选中标记列和语义图标列的 CheckableMenu。"""

    def __init__(self, title="", parent=None, indicatorType=MenuIndicatorType.CHECK):
        super().__init__(title, parent, indicatorType)
        self._leading_indicator_style = _LeadingIndicatorMenuStyle(self.view.style())
        self._leading_indicator_style.setParent(self.view)
        self.view.setStyle(self._leading_indicator_style)


class _StayOpenCheckableMenu(_IconCheckableMenu):
    """点击选项后不关闭的单选菜单。

    排列菜单需要一次打开后连续操作（切参照、连续对齐/分布），
    父类 _onItemClicked 会先 _hideMenu 再触发动作，这里跳过关闭。
    菜单仍可通过点击外部/Esc 正常关闭。
    """

    def _onItemClicked(self, item):
        action = item.data(Qt.ItemDataRole.UserRole)
        if action not in self._actions or not action.isEnabled():
            return
        action.trigger()


class EditorToolbar(CardWidget):
    """
    编辑器顶部工具栏。常驻控件只保留适应窗口、原图不透明度滑条，
    其余操作分装进三个单级下拉菜单（不分级）：
    「菜单」= 导出/撤销重做/缩放 + 通用开关；「显示模式」= 画布显示单选；
    「排列」= 参照单选 + 对齐/分布文字选项（点击不关闭，可连续操作）。
    返回主页不设入口：主窗口侧边栏随时可切换页面。
    """
    # --- Define signals for all actions ---
    export_requested = pyqtSignal()
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    zoom_in_requested = pyqtSignal()
    zoom_out_requested = pyqtSignal()
    fit_window_requested = pyqtSignal()
    display_mode_changed = pyqtSignal(str)
    original_image_alpha_changed = pyqtSignal(int)
    align_requested = pyqtSignal(str)
    distribute_requested = pyqtSignal(str)
    snap_enabled_changed = pyqtSignal(bool)
    rich_text_popup_enabled_changed = pyqtSignal(bool)

    def __init__(
        self,
        parent=None,
        snap_enabled: bool = False,
        rich_text_popup_enabled: bool = True,
    ):
        super().__init__(parent)
        self.i18n = get_i18n_manager()
        self._themed_icon_buttons: list[tuple[ToolButton, str]] = []
        self.content_widget: QWidget | None = None
        # 菜单在语言切换时整体重建，所有需要恢复的状态都存在字段里
        self._display_mode = "full"
        self._align_ref = "selection"
        self._can_undo = False
        self._can_redo = False
        self._export_enabled = True
        self._last_selection_count = 0
        self._snap_enabled = bool(snap_enabled)
        self._rich_text_popup_enabled = bool(rich_text_popup_enabled)
        self.main_menu: RoundMenu | None = None
        self.display_menu: RoundMenu | None = None
        self.arrange_menu: RoundMenu | None = None
        self._init_ui()
        self._connect_signals()

    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def _init_ui(self):
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(54)

        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(6, 4, 6, 4)
        outer_layout.setSpacing(0)

        self.scroll_area = SingleDirectionScrollArea(self, Qt.Orientation.Horizontal)
        self.scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.scroll_area.setMinimumHeight(44)
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setFrameShape(SingleDirectionScrollArea.Shape.NoFrame)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.enableTransparentBackground()
        outer_layout.addWidget(self.scroll_area)

        self.content_widget = QWidget(self.scroll_area)
        layout = QHBoxLayout(self.content_widget)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        # --- 下拉菜单组：通用 / 显示模式 / 排列（每个都是单级菜单，功能不分级） ---
        self.menu_button = DropDownPushButton()
        self.menu_button.setIcon(FIF.MENU)
        self.menu_button.setText(self._t("Menu"))
        layout.addWidget(self.menu_button)

        self.display_mode_button = DropDownPushButton()
        self.display_mode_button.setIcon(FIF.VIEW)
        self.display_mode_button.setText(self._t("Display Mode"))
        layout.addWidget(self.display_mode_button)

        self.arrange_button = DropDownPushButton()
        self.arrange_button.setIcon(FIF.LAYOUT)
        self.arrange_button.setText(self._t("Arrange"))
        layout.addWidget(self.arrange_button)

        self._build_menus()

        layout.addWidget(self._create_separator())

        # --- 常驻: 适应窗口 ---
        self.fit_window_button = ToolButton()
        self.fit_window_button.setIcon(FIF.FIT_PAGE)
        set_hover_hint(self.fit_window_button, self._t("Fit to Window"))
        layout.addWidget(self.fit_window_button)

        layout.addWidget(self._create_separator())

        # --- 常驻: 原图不透明度 ---
        self.opacity_label = BodyLabel(self._t("Original Image Opacity:"))
        layout.addWidget(self.opacity_label)
        self.original_image_alpha_slider = Slider(Qt.Orientation.Horizontal)
        self.original_image_alpha_slider.setRange(0, 100)
        self.original_image_alpha_slider.setValue(0)  # Default to 0 (fully transparent, show inpainted)
        self.original_image_alpha_slider.setMinimumWidth(140)
        layout.addWidget(self.original_image_alpha_slider)

        layout.addStretch()  # Pushes everything to the left
        self.content_widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.scroll_area.setWidget(self.content_widget)
        self.scroll_area.enableTransparentBackground()
        self._sync_content_width()

    # ------------------------------------------------------------------
    # 主菜单
    # ------------------------------------------------------------------

    def _build_menus(self):
        """构建三个独立的单级下拉菜单。语言切换时整体重建，状态从字段恢复。"""
        old_menus = [self.main_menu, self.display_menu, self.arrange_menu]
        # 旧菜单里的主题图标按钮即将销毁，先清空登记表防止悬空引用
        self._themed_icon_buttons.clear()

        # --- 通用菜单：导出 / 撤销重做 / 缩放 / 持久化开关 ---
        menu = _IconCheckableMenu(parent=self, indicatorType=MenuIndicatorType.CHECK)

        # 导出的真实快捷键 Ctrl+Q 由 EditorShortcutManager 全局注册，这里只做文本提示
        self.export_action = Action(FIF.IMAGE_EXPORT, self._t("Export Image") + " (Ctrl+Q)")
        self.export_action.setEnabled(self._export_enabled)
        self.export_action.triggered.connect(self.export_requested)
        menu.addAction(self.export_action)
        menu.addSeparator()

        # 撤销/重做的真实快捷键由 EditorShortcutManager 全局注册（带焦点感知），
        # 这里只在文本上做提示，不设 QAction shortcut，避免双重触发。
        self.undo_action = Action(FIF.LEFT_ARROW, self._t("Undo") + " (Ctrl+Z)")
        self.undo_action.setEnabled(self._can_undo)
        self.undo_action.triggered.connect(self.undo_requested)
        menu.addAction(self.undo_action)

        self.redo_action = Action(FIF.RIGHT_ARROW, self._t("Redo") + " (Ctrl+Y)")
        self.redo_action.setEnabled(self._can_redo)
        self.redo_action.triggered.connect(self.redo_requested)
        menu.addAction(self.redo_action)
        menu.addSeparator()

        self.zoom_in_action = Action(FIF.ZOOM_IN, self._t("Zoom In (+)"))
        self.zoom_in_action.triggered.connect(self.zoom_in_requested)
        menu.addAction(self.zoom_in_action)

        self.zoom_out_action = Action(FIF.ZOOM_OUT, self._t("Zoom Out (-)"))
        self.zoom_out_action.triggered.connect(self.zoom_out_requested)
        menu.addAction(self.zoom_out_action)

        menu.addSeparator()
        self.snap_action = Action(
            themed_fluent_svg_icon("ic_fluent_target_arrow_24_regular.svg"),
            self._t("Enable Editor Snapping"),
        )
        self.snap_action.setCheckable(True)
        self.snap_action.setChecked(self._snap_enabled)
        self.snap_action.triggered.connect(self._on_snap_action_triggered)
        menu.addAction(self.snap_action)

        self.rich_text_popup_action = Action(
            themed_fluent_svg_icon("ic_fluent_text_edit_style_24_regular.svg"),
            self._t("Show Rich Text Editor Popup"),
        )
        self.rich_text_popup_action.setCheckable(True)
        self.rich_text_popup_action.setChecked(self._rich_text_popup_enabled)
        self.rich_text_popup_action.triggered.connect(self._on_rich_text_popup_action_triggered)
        menu.addAction(self.rich_text_popup_action)

        self.main_menu = menu
        self.menu_button.setMenu(menu)

        # --- 显示模式菜单：五种画布显示状态单选 ---
        display_menu = CheckableMenu(parent=self, indicatorType=MenuIndicatorType.RADIO)
        display_group = QActionGroup(display_menu)
        display_group.setExclusive(True)
        self.display_mode_actions: dict[str, Action] = {}
        for mode, text_key in self._display_mode_definitions():
            action = Action(self._t(text_key))
            action.setCheckable(True)
            action.setChecked(mode == self._display_mode)
            action.triggered.connect(lambda checked, m=mode: self._on_display_mode_selected(m))
            display_group.addAction(action)
            display_menu.addAction(action)
            self.display_mode_actions[mode] = action

        self.display_menu = display_menu
        self.display_mode_button.setMenu(display_menu)

        # --- 排列菜单：参照单选 + 对齐/分布选项（文字+图标，点击不关闭） ---
        arrange_menu = _StayOpenCheckableMenu(parent=self, indicatorType=MenuIndicatorType.RADIO)
        ref_group = QActionGroup(arrange_menu)
        ref_group.setExclusive(True)
        self.align_ref_actions: dict[str, Action] = {}
        for reference, icon_file, text_key in (
            ("selection", "ic_fluent_select_object_24_regular.svg", "Reference: Selection"),
            ("canvas", "ic_fluent_image_24_regular.svg", "Reference: Canvas"),
        ):
            action = Action(themed_fluent_svg_icon(icon_file), self._t(text_key))
            action.setCheckable(True)
            action.setChecked(reference == self._align_ref)
            action.triggered.connect(lambda checked, r=reference: self._on_align_ref_selected(r))
            ref_group.addAction(action)
            arrange_menu.addAction(action)
            self.align_ref_actions[reference] = action

        arrange_menu.addSeparator()
        self.align_actions: dict[str, Action] = {}
        for mode, icon_file, text_key in (
            ("left", "align_left.svg", "Align Left"),
            ("horizontal_center", "align_horizontal_center.svg", "Align Horizontal Center"),
            ("right", "align_right.svg", "Align Right"),
            ("top", "align_top.svg", "Align Top"),
            ("vertical_center", "align_vertical_center.svg", "Align Vertical Center"),
            ("bottom", "align_bottom.svg", "Align Bottom"),
        ):
            action = Action(themed_fluent_svg_icon(icon_file), self._t(text_key))
            action.setEnabled(False)
            action.triggered.connect(lambda checked, m=mode: self.align_requested.emit(m))
            arrange_menu.addAction(action)
            self.align_actions[mode] = action

        arrange_menu.addSeparator()
        self._dist_v_action = Action(
            themed_fluent_svg_icon("distribute_spacing_v.svg"), self._t("Distribute Vertical Spacing")
        )
        self._dist_v_action.setEnabled(False)
        self._dist_v_action.triggered.connect(lambda: self.distribute_requested.emit("spacing_v"))
        arrange_menu.addAction(self._dist_v_action)

        self._dist_h_action = Action(
            themed_fluent_svg_icon("distribute_spacing_h.svg"), self._t("Distribute Horizontal Spacing")
        )
        self._dist_h_action.setEnabled(False)
        self._dist_h_action.triggered.connect(lambda: self.distribute_requested.emit("spacing_h"))
        arrange_menu.addAction(self._dist_h_action)

        # 重建（语言切换）后按当前选区数恢复启停状态
        self._apply_align_button_states()

        self.arrange_menu = arrange_menu
        self.arrange_button.setMenu(arrange_menu)

        for old in old_menus:
            if old is not None:
                old.deleteLater()

    def _display_mode_definitions(self):
        return [
            ("full", "Show Text and Boxes"),
            ("text_only", "Show Text Only"),
            ("box_only", "Show Boxes Only"),
            ("none", "Show Nothing"),
            ("compare_original_split", "Compare with Original (Two Panels)"),
        ]

    def _on_display_mode_selected(self, mode: str):
        if mode == self._display_mode:
            return
        self._display_mode = mode
        self.display_mode_changed.emit(mode)

    def _on_snap_action_triggered(self, checked: bool = False):
        self.set_snap_enabled(checked, emit=True)

    def set_snap_enabled(self, enabled: bool, emit: bool = False):
        """同步编辑器吸附开关；外部同步配置时默认不回发信号。"""
        enabled = bool(enabled)
        changed = enabled != self._snap_enabled
        self._snap_enabled = enabled

        action = getattr(self, "snap_action", None)
        if action is not None and action.isChecked() != enabled:
            action.blockSignals(True)
            action.setChecked(enabled)
            action.blockSignals(False)

        if emit and changed:
            self.snap_enabled_changed.emit(enabled)

    def is_snap_enabled(self) -> bool:
        return self._snap_enabled

    def _on_rich_text_popup_action_triggered(self, checked: bool = False):
        self.set_rich_text_popup_enabled(checked, emit=True)

    def set_rich_text_popup_enabled(self, enabled: bool, emit: bool = False):
        """同步富文本浮动编辑器开关；外部配置同步时默认不回发信号。"""
        enabled = bool(enabled)
        changed = enabled != self._rich_text_popup_enabled
        self._rich_text_popup_enabled = enabled

        action = getattr(self, "rich_text_popup_action", None)
        if action is not None and action.isChecked() != enabled:
            action.blockSignals(True)
            action.setChecked(enabled)
            action.blockSignals(False)

        if emit and changed:
            self.rich_text_popup_enabled_changed.emit(enabled)

    def is_rich_text_popup_enabled(self) -> bool:
        return self._rich_text_popup_enabled

    def _on_align_ref_selected(self, reference: str):
        if reference == self._align_ref:
            return
        self._align_ref = reference
        self._apply_align_button_states()

    def get_align_reference(self) -> str:
        return self._align_ref

    def update_align_distribute_buttons(self, selection_count: int):
        """根据选中数量和参照模式更新对齐/分布选项的启用状态。"""
        self._last_selection_count = selection_count
        self._apply_align_button_states()

    def _apply_align_button_states(self):
        count = self._last_selection_count
        align_enabled = (count >= 1 and self._align_ref == "canvas") or (count >= 2)
        dist_enabled = count >= 3
        for action in self.align_actions.values():
            action.setEnabled(align_enabled)
        self._dist_v_action.setEnabled(dist_enabled)
        self._dist_h_action.setEnabled(dist_enabled)

    # ------------------------------------------------------------------
    # 布局辅助
    # ------------------------------------------------------------------

    def _create_separator(self):
        separator = VerticalSeparator()
        separator.setFixedHeight(24)
        return separator

    def _sync_content_width(self):
        """Keep the scroll area's inner widget as wide as its controls need."""
        if self.content_widget is None:
            return

        content_layout = self.content_widget.layout()
        if content_layout is not None:
            content_layout.activate()
            content_width = content_layout.sizeHint().width()
        else:
            content_width = self.content_widget.sizeHint().width()

        self.content_widget.setMinimumWidth(content_width)
        viewport_width = self.scroll_area.viewport().width() if hasattr(self, "scroll_area") else 0
        self.content_widget.resize(
            max(content_width, viewport_width),
            max(self.content_widget.sizeHint().height(), self.scroll_area.viewport().height()),
        )

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_content_width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_content_width()

    def _connect_signals(self):
        self.fit_window_button.clicked.connect(self.fit_window_requested)
        self.original_image_alpha_slider.valueChanged.connect(self.original_image_alpha_changed)

    # --- Public Slots ---
    def update_undo_redo_state(self, can_undo: bool, can_redo: bool):
        self._can_undo = bool(can_undo)
        self._can_redo = bool(can_redo)
        self.undo_action.setEnabled(self._can_undo)
        self.redo_action.setEnabled(self._can_redo)

    def set_original_image_alpha_slider(self, alpha: float):
        """同步滑块值（alpha: 0.0-1.0）"""
        # 转换：alpha 0.0 = slider 0（完全透明），alpha 1.0 = slider 100（完全不透明）
        slider_value = int(alpha * 100)
        self.original_image_alpha_slider.blockSignals(True)
        self.original_image_alpha_slider.setValue(slider_value)
        self.original_image_alpha_slider.blockSignals(False)

    def set_export_enabled(self, enabled: bool):
        """设置导出选项的启用状态"""
        self._export_enabled = bool(enabled)
        self.export_action.setEnabled(self._export_enabled)

    def refresh_ui_texts(self):
        """刷新所有UI文本（用于语言切换）。菜单整体重建，状态从字段恢复。"""
        self.menu_button.setText(self._t("Menu"))
        self.display_mode_button.setText(self._t("Display Mode"))
        self.arrange_button.setText(self._t("Arrange"))
        set_hover_hint(self.fit_window_button, self._t("Fit to Window"))
        self.opacity_label.setText(self._t("Original Image Opacity:"))
        self._build_menus()
        self._sync_content_width()

    def refresh_theme(self):
        for button, icon_file in self._themed_icon_buttons:
            button.setIcon(themed_fluent_svg_icon(icon_file))
