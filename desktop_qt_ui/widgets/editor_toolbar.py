
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from services import get_i18n_manager
from widgets.hover_hint import set_hover_hint


class EditorToolbar(QWidget):
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
        self._init_ui()
        self._connect_signals()
    
    def _t(self, key: str, **kwargs) -> str:
        """翻译辅助方法"""
        if self.i18n:
            return self.i18n.translate(key, **kwargs)
        return key

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)

        # --- File Actions ---
        self.back_button = QToolButton()
        self.back_button.setText(self._t("Back"))
        set_hover_hint(self.back_button, self._t("Back to Main"))
        self.back_button.setObjectName("editor_back_button")
        layout.addWidget(self.back_button)

        self.export_button = QToolButton()
        self.export_button.setText(self._t("Export Image"))
        set_hover_hint(self.export_button, self._t("Export current rendered image") + " (Ctrl+Q)")
        self.export_button.setObjectName("editor_export_button")
        layout.addWidget(self.export_button)

        layout.addWidget(self._create_separator())

        # --- Edit Actions ---
        self.undo_button = QToolButton()
        self.undo_button.setText(self._t("Undo"))
        self.undo_button.setEnabled(False)
        set_hover_hint(self.undo_button, self._t("Undo last operation") + " (Ctrl+Z)")
        self.undo_button.setObjectName("editor_undo_button")
        layout.addWidget(self.undo_button)

        self.redo_button = QToolButton()
        self.redo_button.setText(self._t("Redo"))
        self.redo_button.setEnabled(False)
        set_hover_hint(self.redo_button, self._t("Redo last undone operation") + " (Ctrl+Y)")
        self.redo_button.setObjectName("editor_redo_button")
        layout.addWidget(self.redo_button)

        layout.addWidget(self._create_separator())

        # --- View Actions ---
        self.zoom_out_button = QToolButton()
        self.zoom_out_button.setText(self._t("Zoom Out (-)"))
        self.zoom_out_button.setObjectName("editor_zoom_out_button")
        layout.addWidget(self.zoom_out_button)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(40)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.zoom_label)

        self.zoom_in_button = QToolButton()
        self.zoom_in_button.setText(self._t("Zoom In (+)"))
        self.zoom_in_button.setObjectName("editor_zoom_in_button")
        layout.addWidget(self.zoom_in_button)

        self.fit_window_button = QToolButton()
        self.fit_window_button.setText(self._t("Fit to Window"))
        self.fit_window_button.setObjectName("editor_fit_window_button")
        layout.addWidget(self.fit_window_button)
        
        layout.addWidget(self._create_separator())

        # --- Display Mode ---
        # 创建一个容器来包装显示模式控件，确保它们作为一个整体
        display_mode_container = QWidget()
        display_mode_container.setObjectName("editor_display_mode_container")
        display_mode_layout = QHBoxLayout(display_mode_container)
        display_mode_layout.setContentsMargins(0, 0, 0, 0)
        display_mode_layout.setSpacing(5)
        
        self.display_mode_label = QLabel(self._t("Display Mode:"))
        display_mode_layout.addWidget(self.display_mode_label)
        
        self.display_mode_combo = QComboBox()
        self.display_mode_combo.setObjectName("editor_display_mode_combo")
        self._populate_display_mode_items()
        # 需要容纳新增的“原图对比”模式
        self.display_mode_combo.setFixedWidth(180)
        display_mode_layout.addWidget(self.display_mode_combo)
        
        # 添加分隔符到容器内
        display_mode_layout.addWidget(self._create_separator())
        
        # 设置容器的尺寸策略，防止被压缩
        from PyQt6.QtWidgets import QSizePolicy
        display_mode_container.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        
        # 将整个容器添加到主布局
        layout.addWidget(display_mode_container, 0)

        self.opacity_label = QLabel(self._t("Original Image Opacity:"))
        layout.addWidget(self.opacity_label)
        self.original_image_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.original_image_alpha_slider.setObjectName("editor_opacity_slider")
        self.original_image_alpha_slider.setRange(0, 100)
        self.original_image_alpha_slider.setValue(0) # Default to 0 (fully transparent, show inpainted)
        # 设置滑块自适应，较小的最小宽度
        self.original_image_alpha_slider.setMinimumWidth(80)
        layout.addWidget(self.original_image_alpha_slider)

        layout.addWidget(self._create_separator())

        # --- Align / Distribute ---
        self._build_align_distribute_ui(layout)

        layout.addStretch() # Pushes everything to the left

    def _build_align_distribute_ui(self, layout: QHBoxLayout):
        """构建对齐/分布按钮组。"""
        from PyQt6.QtWidgets import QSizePolicy

        # 参照模式切换按钮
        self.align_ref_button = QToolButton()
        self.align_ref_button.setText("选区")
        self.align_ref_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.align_ref_button.setObjectName("editor_align_ref_button")
        self.align_ref_button.setToolTip("对齐参照：选区（包围盒）/ 画布（整张图）")
        self._align_ref = "selection"
        self.align_ref_button.clicked.connect(self._toggle_align_ref)
        self._last_selection_count = 0
        layout.addWidget(self.align_ref_button)

        layout.addWidget(self._create_separator())

        # 对齐按钮的 grid: 2 rows × 3 cols
        align_modes = [
            ("top", "⊤"), ("vertical_center", "⇅"), ("bottom", "⊥"),
            ("left", "⊣"), ("horizontal_center", "⇔"), ("right", "⊢"),
        ]
        align_tips = {
            "top": "顶对齐", "vertical_center": "垂直居中", "bottom": "底对齐",
            "left": "左对齐", "horizontal_center": "水平居中", "right": "右对齐",
        }
        self.align_buttons: dict[str, QToolButton] = {}
        align_container = QWidget()
        align_container.setObjectName("editor_align_container")
        align_vbox = QVBoxLayout(align_container)
        align_vbox.setContentsMargins(0, 0, 0, 0)
        align_vbox.setSpacing(1)
        align_label = QLabel("对齐")
        align_label.setObjectName("editor_align_label")
        align_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        align_vbox.addWidget(align_label)
        align_grid = QGridLayout()
        align_grid.setSpacing(1)
        for idx, (mode, symbol) in enumerate(align_modes):
            btn = QToolButton()
            btn.setText(symbol)
            btn.setObjectName(f"editor_align_{mode}")
            btn.setToolTip(align_tips[mode])
            btn.setFixedSize(24, 24)
            btn.setEnabled(False)
            btn.clicked.connect(lambda checked, m=mode: self.align_requested.emit(m))
            align_grid.addWidget(btn, idx // 3, idx % 3)
            self.align_buttons[mode] = btn
        align_vbox.addLayout(align_grid)
        layout.addWidget(align_container)

        layout.addWidget(self._create_separator())

        # 分布按钮的 grid: 2 rows × 3 cols
        dist_modes = [
            ("top", "⊤═"), ("vertical_center", "⇅═"), ("bottom", "⊥═"),
            ("left", "⊣═"), ("horizontal_center", "⇔═"), ("right", "⊢═"),
        ]
        dist_tips = {
            "top": "按顶分布", "vertical_center": "垂直居中分布", "bottom": "按底分布",
            "left": "按左分布", "horizontal_center": "水平居中分布", "right": "按右分布",
        }
        self.distribute_buttons: dict[str, QToolButton] = {}
        dist_container = QWidget()
        dist_container.setObjectName("editor_dist_container")
        dist_vbox = QVBoxLayout(dist_container)
        dist_vbox.setContentsMargins(0, 0, 0, 0)
        dist_vbox.setSpacing(1)
        dist_label = QLabel("分布")
        dist_label.setObjectName("editor_dist_label")
        dist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dist_vbox.addWidget(dist_label)
        dist_grid = QGridLayout()
        dist_grid.setSpacing(1)
        for idx, (mode, symbol) in enumerate(dist_modes):
            btn = QToolButton()
            btn.setText(symbol)
            btn.setObjectName(f"editor_dist_{mode}")
            btn.setToolTip(dist_tips[mode])
            btn.setFixedSize(24, 24)
            btn.setEnabled(False)
            btn.clicked.connect(lambda checked, m=mode: self.distribute_requested.emit(m))
            dist_grid.addWidget(btn, idx // 3, idx % 3)
            self.distribute_buttons[mode] = btn
        dist_vbox.addLayout(dist_grid)
        layout.addWidget(dist_container)

    def _toggle_align_ref(self):
        """切换对齐参照模式：选区 ↔ 画布。同时更新按钮启用状态。"""
        if self._align_ref == "selection":
            self._align_ref = "canvas"
            self.align_ref_button.setText("画布")
        else:
            self._align_ref = "selection"
            self.align_ref_button.setText("选区")
        self.update_align_distribute_buttons(self._last_selection_count)

    def get_align_reference(self) -> str:
        return self._align_ref

    def update_align_distribute_buttons(self, selection_count: int):
        """根据选中数量和参照模式更新按钮启用状态。"""
        self._last_selection_count = selection_count
        align_enabled = (selection_count >= 1 and self._align_ref == "canvas") or (selection_count >= 2)
        dist_enabled = selection_count >= 3
        for btn in self.align_buttons.values():
            btn.setEnabled(align_enabled)
        for btn in self.distribute_buttons.values():
            btn.setEnabled(dist_enabled)

    def _create_separator(self):
        separator = QFrame()
        separator.setObjectName("editor_toolbar_separator")
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        separator.setLineWidth(1)
        separator.setMidLineWidth(0)
        separator.setFixedWidth(2)  # 分隔符可以固定宽度，因为它只是一条线
        # 设置分隔符的最小高度，确保它垂直显示
        separator.setMinimumHeight(20)
        return separator

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
            self.display_mode_combo.addItem(self._t(text_key), mode)

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
        self.back_button.setText(self._t("Back"))
        set_hover_hint(self.back_button, self._t("Back to Main"))
        self.export_button.setText(self._t("Export Image"))
        set_hover_hint(self.export_button, self._t("Export current rendered image") + " (Ctrl+Q)")
        self.undo_button.setText(self._t("Undo"))
        set_hover_hint(self.undo_button, self._t("Undo last operation") + " (Ctrl+Z)")
        self.redo_button.setText(self._t("Redo"))
        set_hover_hint(self.redo_button, self._t("Redo last undone operation") + " (Ctrl+Y)")
        self.zoom_out_button.setText(self._t("Zoom Out (-)"))
        self.zoom_in_button.setText(self._t("Zoom In (+)"))
        self.fit_window_button.setText(self._t("Fit to Window"))
        
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
