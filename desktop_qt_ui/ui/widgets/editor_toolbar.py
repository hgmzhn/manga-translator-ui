
from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    PushButton,
    SingleDirectionScrollArea,
    Slider,
    ToolButton,
    VerticalSeparator,
)
from services import get_i18n_manager
from ui.fluent_icon import themed_fluent_svg_icon
from ui.widgets.hover_hint import set_hover_hint


class EditorToolbar(CardWidget):
    """
    编辑器顶部工具栏，包含返回、导出、撤销/重做、缩放、视图模式等全局操作。
    """
    # --- Define signals for all actions ---
    back_requested = pyqtSignal()
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.i18n = get_i18n_manager()
        self._themed_icon_buttons: list[tuple[ToolButton, str]] = []
        self.content_widget: QWidget | None = None
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
        self.scroll_area.setAutoFillBackground(False)
        self.scroll_area.viewport().setAutoFillBackground(False)
        outer_layout.addWidget(self.scroll_area)

        self.content_widget = QWidget()
        self.content_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.content_widget.setAutoFillBackground(False)
        layout = QHBoxLayout(self.content_widget)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(10)

        # --- File Actions ---
        self.back_button = ToolButton()
        self.back_button.setIcon(FIF.RETURN)
        set_hover_hint(self.back_button, self._t("Back to Main"))
        layout.addWidget(self.back_button)

        self.export_button = PushButton()
        self.export_button.setIcon(FIF.IMAGE_EXPORT)
        self.export_button.setText(self._t("Export Image"))
        set_hover_hint(self.export_button, self._t("Export current rendered image") + " (Ctrl+Q)")
        layout.addWidget(self.export_button)

        layout.addWidget(self._create_separator())

        # --- Edit Actions ---
        self.undo_button = ToolButton()
        self.undo_button.setIcon(FIF.LEFT_ARROW)
        self.undo_button.setEnabled(False)
        set_hover_hint(self.undo_button, self._t("Undo last operation") + " (Ctrl+Z)")
        layout.addWidget(self.undo_button)

        self.redo_button = ToolButton()
        self.redo_button.setIcon(FIF.RIGHT_ARROW)
        self.redo_button.setEnabled(False)
        set_hover_hint(self.redo_button, self._t("Redo last undone operation") + " (Ctrl+Y)")
        layout.addWidget(self.redo_button)

        layout.addWidget(self._create_separator())

        # --- View Actions ---
        self.zoom_out_button = ToolButton()
        self.zoom_out_button.setIcon(FIF.ZOOM_OUT)
        set_hover_hint(self.zoom_out_button, self._t("Zoom Out (-)"))
        layout.addWidget(self.zoom_out_button)

        self.zoom_label = BodyLabel("100%")
        self.zoom_label.setMinimumWidth(40)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.zoom_label)

        self.zoom_in_button = ToolButton()
        self.zoom_in_button.setIcon(FIF.ZOOM_IN)
        set_hover_hint(self.zoom_in_button, self._t("Zoom In (+)"))
        layout.addWidget(self.zoom_in_button)

        self.fit_window_button = ToolButton()
        self.fit_window_button.setIcon(FIF.FIT_PAGE)
        set_hover_hint(self.fit_window_button, self._t("Fit to Window"))
        layout.addWidget(self.fit_window_button)

        layout.addWidget(self._create_separator())

        self.display_mode_label = BodyLabel(self._t("Display Mode:"))
        layout.addWidget(self.display_mode_label)
        
        self.display_mode_combo = ComboBox()
        self._populate_display_mode_items()
        # 需要容纳新增的“原图对比”模式
        self.display_mode_combo.setFixedWidth(180)
        layout.addWidget(self.display_mode_combo)
        layout.addWidget(self._create_separator())

        self.opacity_label = BodyLabel(self._t("Original Image Opacity:"))
        layout.addWidget(self.opacity_label)
        self.original_image_alpha_slider = Slider(Qt.Orientation.Horizontal)
        self.original_image_alpha_slider.setRange(0, 100)
        self.original_image_alpha_slider.setValue(0) # Default to 0 (fully transparent, show inpainted)
        # 设置滑块自适应，较小的最小宽度
        self.original_image_alpha_slider.setMinimumWidth(80)
        layout.addWidget(self.original_image_alpha_slider)

        layout.addWidget(self._create_separator())

        # --- Align / Distribute ---
        self._build_align_distribute_ui(layout)

        layout.addStretch() # Pushes everything to the left
        self.content_widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.scroll_area.setWidget(self.content_widget)
        self.scroll_area.enableTransparentBackground()
        self._sync_content_width()

    # ------------------------------------------------------------------
    # 对齐/分布 UI — 单行 PS 风格布局
    # ------------------------------------------------------------------

    def _build_align_distribute_ui(self, layout: QHBoxLayout):
        """构建对齐/分布按钮组：单行 PS 风格。"""
        BTN_W = 28

        def _make_icon_btn(icon_file: str, tip: str):
            btn = ToolButton()
            btn.setIcon(themed_fluent_svg_icon(icon_file))
            btn.setToolTip(tip)
            btn.setFixedSize(QSize(BTN_W + 2, BTN_W + 2))
            btn.setIconSize(QSize(BTN_W, BTN_W))
            btn.setEnabled(False)
            self._themed_icon_buttons.append((btn, icon_file))
            return btn

        # 参照模式切换（独立放在外面）
        self.align_ref_button = PushButton()
        self.align_ref_button.setText(self._t("Selection"))
        self.align_ref_button.setIcon(FIF.LAYOUT)
        self.align_ref_button.setToolTip(self._t("Align reference: selection (bounding box) / canvas (whole image)"))
        self._align_ref = "selection"
        self._last_selection_count = 0
        self.align_ref_button.clicked.connect(self._toggle_align_ref)
        layout.addWidget(self.align_ref_button)
        layout.addWidget(self._create_separator())

        # ── 第 1 组: 左对齐 / 水平居中 / 右对齐 / 垂直间距分布 ──
        self.align_buttons: dict[str, ToolButton] = {}
        group1 = [
            ("left", "align_left.svg", self._t("Align Left")),
            ("horizontal_center", "align_horizontal_center.svg", self._t("Align Horizontal Center")),
            ("right", "align_right.svg", self._t("Align Right")),
        ]
        for mode, icon_file, tip in group1:
            btn = _make_icon_btn(icon_file, tip)
            btn.clicked.connect(lambda checked, m=mode: self.align_requested.emit(m))
            self.align_buttons[mode] = btn
            layout.addWidget(btn)

        btn = _make_icon_btn("distribute_spacing_v.svg", self._t("Distribute Vertical Spacing"))
        btn.clicked.connect(lambda: self._on_dist_spacing("vertical"))
        self._dist_v_btn = btn
        layout.addWidget(btn)

        # ── 第 2 组: 顶对齐 / 垂直居中 / 底对齐 / 水平间距分布 ──
        group2 = [
            ("top", "align_top.svg", self._t("Align Top")),
            ("vertical_center", "align_vertical_center.svg", self._t("Align Vertical Center")),
            ("bottom", "align_bottom.svg", self._t("Align Bottom")),
        ]
        for mode, icon_file, tip in group2:
            btn = _make_icon_btn(icon_file, tip)
            btn.clicked.connect(lambda checked, m=mode: self.align_requested.emit(m))
            self.align_buttons[mode] = btn
            layout.addWidget(btn)

        btn = _make_icon_btn("distribute_spacing_h.svg", self._t("Distribute Horizontal Spacing"))
        btn.clicked.connect(lambda: self._on_dist_spacing("horizontal"))
        self._dist_h_btn = btn
        layout.addWidget(btn)

    def _on_dist_spacing(self, orientation: str):
        """处理间距分布按钮点击（垂直/水平空白间隙均分）。"""
        if orientation == "vertical":
            self.distribute_requested.emit("spacing_v")
        else:
            self.distribute_requested.emit("spacing_h")

    def _toggle_align_ref(self):
        """切换对齐参照模式：选区 ↔ 画布。同时更新按钮启用状态。"""
        if self._align_ref == "selection":
            self._align_ref = "canvas"
            self.align_ref_button.setText(self._t("Canvas"))
        else:
            self._align_ref = "selection"
            self.align_ref_button.setText(self._t("Selection"))
        self.update_align_distribute_buttons(self._last_selection_count)

    def get_align_reference(self) -> str:
        return self._align_ref

    def update_align_distribute_buttons(self, selection_count: int):
        """根据选中数量和参照模式更新按钮启用状态。更多按钮始终可用。"""
        self._last_selection_count = selection_count
        align_enabled = (selection_count >= 1 and self._align_ref == "canvas") or (selection_count >= 2)
        dist_enabled = selection_count >= 3
        for btn in self.align_buttons.values():
            btn.setEnabled(align_enabled)
        self._dist_v_btn.setEnabled(dist_enabled)
        self._dist_h_btn.setEnabled(dist_enabled)

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
        self.back_button.clicked.connect(self.back_requested)
        self.export_button.clicked.connect(self.export_requested)
        self.undo_button.clicked.connect(self.undo_requested)
        self.redo_button.clicked.connect(self.redo_requested)
        self.zoom_in_button.clicked.connect(self.zoom_in_requested)
        self.zoom_out_button.clicked.connect(self.zoom_out_requested)
        self.fit_window_button.clicked.connect(self.fit_window_requested)
        self.display_mode_combo.currentIndexChanged.connect(self._emit_display_mode_changed)
        self.original_image_alpha_slider.valueChanged.connect(self.original_image_alpha_changed)

    def _display_mode_definitions(self):
        return [
            ("full", "Show Text and Boxes"),
            ("text_only", "Show Text Only"),
            ("box_only", "Show Boxes Only"),
            ("none", "Show Nothing"),
            ("compare_original_split", "Compare with Original (Two Panels)"),
        ]

    def _populate_display_mode_items(self, selected_mode: str | None = None):
        self.display_mode_combo.clear()
        for mode, text_key in self._display_mode_definitions():
            self.display_mode_combo.addItem(self._t(text_key), userData=mode)

        target_mode = selected_mode or "full"
        mode_index = self.display_mode_combo.findData(target_mode)
        if mode_index < 0:
            mode_index = 0
        self.display_mode_combo.setCurrentIndex(mode_index)

    def _emit_display_mode_changed(self, index: int):
        mode = self.display_mode_combo.itemData(index)
        if mode:
            self.display_mode_changed.emit(str(mode))

    # --- Public Slots ---
    def update_undo_redo_state(self, can_undo: bool, can_redo: bool):
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)

    def set_original_image_alpha_slider(self, alpha: float):
        """同步滑块值（alpha: 0.0-1.0）"""
        # 转换：alpha 0.0 = slider 0（完全透明），alpha 1.0 = slider 100（完全不透明）
        slider_value = int(alpha * 100)
        self.original_image_alpha_slider.blockSignals(True)
        self.original_image_alpha_slider.setValue(slider_value)
        self.original_image_alpha_slider.blockSignals(False)

    def update_zoom_level(self, zoom_level: float):
        self.zoom_label.setText(f"{zoom_level:.0%}")
    
    def set_export_enabled(self, enabled: bool):
        """设置导出按钮的启用状态"""
        self.export_button.setEnabled(enabled)
    
    def refresh_ui_texts(self):
        """刷新所有UI文本（用于语言切换）"""
        # 刷新按钮文本
        set_hover_hint(self.back_button, self._t("Back to Main"))
        self.export_button.setText(self._t("Export Image"))
        set_hover_hint(self.export_button, self._t("Export current rendered image") + " (Ctrl+Q)")
        set_hover_hint(self.undo_button, self._t("Undo last operation") + " (Ctrl+Z)")
        set_hover_hint(self.redo_button, self._t("Redo last undone operation") + " (Ctrl+Y)")
        set_hover_hint(self.zoom_out_button, self._t("Zoom Out (-)"))
        set_hover_hint(self.zoom_in_button, self._t("Zoom In (+)"))
        set_hover_hint(self.fit_window_button, self._t("Fit to Window"))
        
        # 刷新下拉菜单
        current_mode = self.display_mode_combo.currentData()
        self.display_mode_combo.blockSignals(True)
        self._populate_display_mode_items(current_mode)
        self.display_mode_combo.blockSignals(False)
        
        # 刷新标签
        if hasattr(self, 'display_mode_label'):
            self.display_mode_label.setText(self._t("Display Mode:"))
        if hasattr(self, 'opacity_label'):
            self.opacity_label.setText(self._t("Original Image Opacity:"))
        self._sync_content_width()

    def refresh_theme(self):
        for button, icon_file in self._themed_icon_buttons:
            button.setIcon(themed_fluent_svg_icon(icon_file))
        self.scroll_area.enableTransparentBackground()
