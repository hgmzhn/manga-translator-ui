#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
高级文件夹管理面板 - 使用现代化UI
支持三层目录结构（Downloads/Source/Title/Chapters）
智能聚合、名称映射、章节详情、多选等功能
"""

import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Optional
from PyQt6.QtCore import Qt, QSettings, QSize
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton,
    QLineEdit, QTextEdit, QLabel, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox, QComboBox, QTreeWidget, QTreeWidgetItem,
    QDialog, QSplitter, QScrollArea, QToolButton, QStyle, QMenu
)
from PyQt6.QtGui import QColor, QPalette

# 导入名称映射
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'utils'))

try:
    from utils.name_replacer import NameReplacer
except ImportError:
    NameReplacer = None


class AdvancedFolderDialog(QDialog):
    """高级文件夹选择对话框 - 现代化UI风格"""
    
    def __init__(self, parent=None, start_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("高级文件夹 - 批量选择章节")
        self.setMinimumSize(1200, 750)
        self.resize(1200, 750)
        
        self.name_replacer = NameReplacer() if NameReplacer else None
        self.folder_data = {}  # {title: {'sources': [source1, source2], 'chapters': {source: [chapters]}}}
        self.selected_chapters = []
        
        # 智能选择根目录：如果start_dir看起来是章节路径，向上找到可能的根目录
        self.root_path = self._determine_root_path(start_dir)
        
        self._init_ui()
        self._apply_modern_style()
        
        # 尝试加载上次扫描结果
        self._load_scan_cache()
    
    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 工具栏区域
        toolbar = self._create_toolbar()
        layout.addWidget(toolbar)
        
        # 地址栏区域
        address_bar = self._create_address_bar()
        layout.addWidget(address_bar)
        
        # 主内容区域（使用分割器）
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # 左侧：快捷访问和搜索
        left_panel = self._create_left_panel()
        splitter.addWidget(left_panel)
        
        # 右侧：作品和章节列表
        right_panel = self._create_right_panel()
        splitter.addWidget(right_panel)
        
        # 设置分割比例：20% : 80%
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 8)
        
        layout.addWidget(splitter, 1)
        
        # 底部信息栏
        info_bar = self._create_info_bar()
        layout.addWidget(info_bar)
        
        # 底部按钮
        button_bar = self._create_button_bar()
        layout.addWidget(button_bar)
    
    def _create_toolbar(self) -> QWidget:
        """创建工具栏"""
        toolbar = QWidget()
        toolbar.setFixedHeight(45)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 4, 8, 4)
        toolbar_layout.setSpacing(4)
        
        toolbar_layout.addStretch()
        
        # 统计标签
        self.stats_label = QLabel("作品: 0 | 章节: 0 | 已选: 0")
        toolbar_layout.addWidget(self.stats_label)
        
        return toolbar
    
    def _create_address_bar(self) -> QWidget:
        """创建地址栏"""
        address_widget = QWidget()
        address_layout = QHBoxLayout(address_widget)
        address_layout.setContentsMargins(8, 8, 8, 8)
        address_layout.setSpacing(5)
        
        address_layout.addWidget(QLabel("根目录:"))
        
        self.root_path_edit = QLineEdit()
        self.root_path_edit.setText(self.root_path)
        self.root_path_edit.setPlaceholderText("选择包含 Source/Title/Chapters 结构的根目录")
        address_layout.addWidget(self.root_path_edit, 1)
        
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self.browse_folder)
        address_layout.addWidget(browse_btn)
        
        self.scan_btn = QPushButton("扫描作品")
        self.scan_btn.clicked.connect(self.scan_folders)
        address_layout.addWidget(self.scan_btn)
        
        return address_widget
    
    def _create_left_panel(self) -> QWidget:
        """创建左侧面板"""
        panel = QWidget()
        panel.setMinimumWidth(200)
        panel.setMaximumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # 搜索框
        layout.addWidget(QLabel("搜索作品:"))
        self.search_combo = QComboBox()
        self.search_combo.setEditable(True)
        self.search_combo.setPlaceholderText("输入或选择...")
        self.search_combo.currentTextChanged.connect(self.apply_filter)
        self.search_combo.lineEdit().textChanged.connect(self.apply_filter)
        layout.addWidget(self.search_combo)
        
        # 刷新映射名称按钮
        refresh_mapping_btn = QPushButton()
        refresh_mapping_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        refresh_mapping_btn.setText("  刷新映射名称")
        refresh_mapping_btn.setToolTip("从名称映射管理同步最新配置")
        refresh_mapping_btn.clicked.connect(self.refresh_mapping_names)
        layout.addWidget(refresh_mapping_btn)
        
        layout.addStretch()
        
        # 快速操作
        layout.addWidget(QLabel("快速操作:"))
        
        select_all_btn = QPushButton("全选章节")
        select_all_btn.clicked.connect(self.select_all_chapters)
        layout.addWidget(select_all_btn)
        
        deselect_all_btn = QPushButton("取消全选")
        deselect_all_btn.clicked.connect(self.deselect_all_chapters)
        layout.addWidget(deselect_all_btn)
        
        return panel
    
    def _create_right_panel(self) -> QWidget:
        """创建右侧面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 8, 8)
        layout.setSpacing(8)
        
        # 作品列表标题栏（带排序下拉框）
        title_bar = QWidget()
        title_bar_layout = QHBoxLayout(title_bar)
        title_bar_layout.setContentsMargins(0, 0, 0, 0)
        title_bar_layout.setSpacing(8)
        
        title_label = QLabel("作品与章节列表")
        title_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        title_bar_layout.addWidget(title_label)
        
        title_bar_layout.addStretch()
        
        # 排序方式下拉框
        sort_label = QLabel("排序:")
        title_bar_layout.addWidget(sort_label)
        
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("智能排序", "natural")
        self.sort_combo.addItem("名称", "name")
        self.sort_combo.addItem("修改日期", "date")
        self.sort_combo.setMinimumWidth(100)
        self.sort_combo.currentIndexChanged.connect(self.on_sort_changed)
        title_bar_layout.addWidget(self.sort_combo)
        
        # 升序/降序切换按钮
        self.sort_order_btn = QPushButton("↑ 升序")
        self.sort_order_btn.setMaximumWidth(80)
        self.sort_order_btn.clicked.connect(self.toggle_sort_order)
        title_bar_layout.addWidget(self.sort_order_btn)
        
        layout.addWidget(title_bar)
        
        # 树形视图
        self.title_tree = QTreeWidget()
        self.title_tree.setHeaderLabels(["作品/章节", "来源", "最新话/数量", "状态"])
        self.title_tree.setRootIsDecorated(True)
        self.title_tree.setAlternatingRowColors(True)
        # 启用扩展多选模式（支持Shift/Ctrl拉取多选）
        self.title_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.title_tree.itemExpanded.connect(self.on_item_expanded)
        self.title_tree.itemChanged.connect(self.on_item_changed)
        # 连接选择改变信号，同步到复选框
        self.title_tree.itemSelectionChanged.connect(self.on_selection_changed)
        
        # 当前排序方式 - 从设置中加载，默认为智能排序+升序
        settings = QSettings("MangaTranslator", "AdvancedFolder")
        self.sort_by = settings.value("sort_by", "natural")  # natural, name, date
        self.sort_ascending = settings.value("sort_ascending", True, type=bool)  # True=升序, False=降序
        
        # 应用加载的排序设置到UI
        index = self.sort_combo.findData(self.sort_by)
        if index >= 0:
            self.sort_combo.setCurrentIndex(index)
        
        if self.sort_ascending:
            self.sort_order_btn.setText("↑ 升序")
        else:
            self.sort_order_btn.setText("↓ 降序")
        
        # 设置列宽
        header = self.title_tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 350)
        header.resizeSection(1, 200)
        header.resizeSection(2, 150)
        header.resizeSection(3, 100)
        
        layout.addWidget(self.title_tree)
        
        # 日志区域
        log_label = QLabel("操作日志:")
        log_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(log_label)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        layout.addWidget(self.log_text)
        
        return panel
    
    def _create_info_bar(self) -> QWidget:
        """创建信息栏"""
        info_bar = QWidget()
        info_layout = QHBoxLayout(info_bar)
        info_layout.setContentsMargins(8, 4, 8, 4)
        
        tip_label = QLabel("💡 提示：展开作品后勾选需要的章节，支持 Ctrl/Shift 批量选择")
        info_layout.addWidget(tip_label)
        info_layout.addStretch()
        
        return info_bar
    
    def _create_button_bar(self) -> QWidget:
        """创建按钮栏"""
        button_bar = QWidget()
        button_layout = QHBoxLayout(button_bar)
        button_layout.setContentsMargins(8, 8, 8, 8)
        button_layout.addStretch()
        
        self.ok_button = QPushButton("确定")
        self.ok_button.setMinimumWidth(100)
        self.ok_button.setMinimumHeight(32)
        self.ok_button.setEnabled(False)
        self.ok_button.clicked.connect(self.accept)
        button_layout.addWidget(self.ok_button)
        
        cancel_button = QPushButton("取消")
        cancel_button.setMinimumWidth(100)
        cancel_button.setMinimumHeight(32)
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        
        return button_bar
    
    def _determine_root_path(self, start_dir: str) -> str:
        """智能判断根目录路径"""
        # 如果没有提供路径，使用Downloads
        if not start_dir:
            return str(Path.home() / "Downloads")
        
        # 如果路径不存在，使用Downloads
        if not os.path.isdir(start_dir):
            return str(Path.home() / "Downloads")
        
        # 检查是否是三层结构中的章节路径（最底层）
        # 路径模式：Downloads/Source/Title/Chapter
        path = Path(start_dir)
        
        # 尝试向上查找可能的根目录
        # 如果当前路径看起来像章节文件夹（包含图片文件）
        if self._looks_like_chapter_folder(path):
            # 向上3层到根目录
            if path.parent and path.parent.parent and path.parent.parent.parent:
                root_candidate = path.parent.parent.parent
                if os.path.isdir(root_candidate):
                    return str(root_candidate)
        
        # 如果当前路径看起来像作品文件夹（包含多个子文件夹）
        elif self._looks_like_title_folder(path):
            # 向上2层到根目录
            if path.parent and path.parent.parent:
                root_candidate = path.parent.parent
                if os.path.isdir(root_candidate):
                    return str(root_candidate)
        
        # 如果当前路径看起来像来源文件夹
        elif self._looks_like_source_folder(path):
            # 向上1层到根目录
            if path.parent:
                root_candidate = path.parent
                if os.path.isdir(root_candidate):
                    return str(root_candidate)
        
        # 默认使用提供的路径
        return start_dir
    
    def _looks_like_chapter_folder(self, path: Path) -> bool:
        """检查是否看起来像章节文件夹"""
        try:
            # 检查是否包含图片文件
            image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}
            for item in path.iterdir():
                if item.is_file() and item.suffix.lower() in image_extensions:
                    return True
        except:
            pass
        return False
    
    def _looks_like_title_folder(self, path: Path) -> bool:
        """检查是否看起来像作品文件夹"""
        try:
            # 检查是否包含多个子文件夹（可能是章节）
            subfolders = [item for item in path.iterdir() if item.is_dir()]
            return len(subfolders) >= 2
        except:
            pass
        return False
    
    def _looks_like_source_folder(self, path: Path) -> bool:
        """检查是否看起来像来源文件夹"""
        try:
            # 检查是否包含多个子文件夹（可能是作品）
            subfolders = [item for item in path.iterdir() if item.is_dir()]
            # 来源文件夹通常包含多个作品文件夹
            return len(subfolders) >= 1
        except:
            pass
        return False
    
    def _apply_modern_style(self):
        """应用现代化样式"""
        palette = self.palette()
        
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {palette.color(QPalette.ColorRole.Window).name()};
            }}
            QWidget {{
                font-size: 13px;
            }}
            QLineEdit, QComboBox {{
                padding: 6px;
                border: 1px solid {palette.color(QPalette.ColorRole.Mid).name()};
                border-radius: 3px;
            }}
            QLineEdit:focus, QComboBox:focus {{
                border: 2px solid #0078d4;
            }}
            QPushButton {{
                padding: 6px 16px;
                border: 1px solid {palette.color(QPalette.ColorRole.Mid).name()};
                border-radius: 3px;
                background-color: {palette.color(QPalette.ColorRole.Button).name()};
            }}
            QPushButton:hover {{
                background-color: {palette.color(QPalette.ColorRole.Light).name()};
            }}
            QPushButton:pressed {{
                background-color: {palette.color(QPalette.ColorRole.Midlight).name()};
            }}
            QPushButton#ok_button {{
                background-color: #0078d4;
                color: white;
                border: none;
            }}
            QPushButton#ok_button:hover {{
                background-color: #106ebe;
            }}
            QPushButton#ok_button:disabled {{
                background-color: #cccccc;
                color: #888888;
            }}
            QTreeWidget {{
                border: 1px solid {palette.color(QPalette.ColorRole.Mid).name()};
                selection-background-color: #0078d4;
            }}
            QTextEdit {{
                border: 1px solid {palette.color(QPalette.ColorRole.Mid).name()};
                border-radius: 3px;
            }}
        """)
        
        self.ok_button.setObjectName("ok_button")
    
    def browse_folder(self):
        """浏览选择文件夹"""
        folder = QFileDialog.getExistingDirectory(
            self, "选择根目录",
            self.root_path_edit.text() or self.root_path
        )
        
        if folder:
            self.root_path_edit.setText(folder)
            self._log(f"已选择文件夹: {folder}")
    
    def scan_folders(self):
        """扫描文件夹结构"""
        root_path = self.root_path_edit.text().strip()
        
        if not root_path or not os.path.isdir(root_path):
            QMessageBox.warning(self, "路径错误", "请选择有效的根目录")
            return
        
        self._log("开始扫描文件夹...")
        self.folder_data.clear()
        
        try:
            root = Path(root_path)
            scanned_sources = 0
            scanned_titles = 0
            
            for source_dir in root.iterdir():
                if not source_dir.is_dir():
                    continue
                
                source_name = source_dir.name
                scanned_sources += 1
                
                for title_dir in source_dir.iterdir():
                    if not title_dir.is_dir():
                        continue
                    
                    title_name = title_dir.name
                    scanned_titles += 1
                    
                    # 尝试映射名称
                    mapped_name = self._get_mapped_name(title_name)
                    
                    if mapped_name not in self.folder_data:
                        self.folder_data[mapped_name] = {
                            'original_names': set(),
                            'sources': set(),
                            'chapters': {}
                        }
                    
                    self.folder_data[mapped_name]['original_names'].add(title_name)
                    self.folder_data[mapped_name]['sources'].add(source_name)
                    
                    # 扫描章节
                    chapters = []
                    for chapter in title_dir.iterdir():
                        if chapter.is_dir():
                            chapters.append({
                                'name': chapter.name,
                                'path': str(chapter),
                                'source': source_name
                            })
                    
                    self.folder_data[mapped_name]['chapters'][source_name] = chapters
            
            self._log(f"✓ 扫描完成！发现 {scanned_sources} 个来源，{len(self.folder_data)} 个作品")
            self.refresh_title_list()
            self.refresh_mapping_names()
            
            # 保存扫描结果到缓存
            self._save_scan_cache()
            
        except Exception as e:
            self._log(f"❌ 扫描失败: {e}")
            QMessageBox.critical(self, "扫描错误", f"扫描文件夹时出错:\n{e}")
    
    def _get_mapped_name(self, original_name: str) -> str:
        """获取映射后的名称"""
        if not self.name_replacer:
            return original_name
        
        for raw_name, translated_name in self.name_replacer.mapping.items():
            raw_names = [n.strip() for n in raw_name.split('|')]
            if original_name in raw_names:
                return translated_name
        
        return original_name
    
    def refresh_title_list(self):
        """刷新作品列表"""
        self.title_tree.clear()
        
        total_chapters = 0
        
        for title, data in sorted(self.folder_data.items()):
            sources_str = ", ".join(sorted(data['sources']))
            chapter_count = sum(len(chapters) for chapters in data['chapters'].values())
            total_chapters += chapter_count
            
            parent_item = QTreeWidgetItem(self.title_tree)
            parent_item.setText(0, title)
            parent_item.setText(1, sources_str)
            parent_item.setText(2, f"{chapter_count} 章节")
            parent_item.setText(3, "未展开")
            parent_item.setData(0, Qt.ItemDataRole.UserRole, title)
            
            # 多来源高亮
            if len(data['sources']) > 1:
                parent_item.setBackground(1, QColor(255, 243, 205))
        
        # 暂时断开itemChanged信号，避免在批量加载时触发
        self.title_tree.itemChanged.disconnect(self.on_item_changed)
        
        try:
            # 不使用 expandAll，改为手动展开每个作品并加载章节
            for i in range(self.title_tree.topLevelItemCount()):
                parent = self.title_tree.topLevelItem(i)
                # 手动加载章节（不等待用户点击）
                self._load_chapters_for_item(parent)
                # 展开作品
                parent.setExpanded(True)
        finally:
            # 重新连接信号
            self.title_tree.itemChanged.connect(self.on_item_changed)
        
        self._update_stats(len(self.folder_data), total_chapters, 0)
        
        # 自动填充搜索框（优先使用映射名称）
        self._populate_search_combo_with_mapping()
    
    def _load_chapters_for_item(self, item: QTreeWidgetItem):
        """为指定作品项加载章节（三层结构：作品 → 来源 → 章节）"""
        # 如果已经加载过章节，不重复加载
        if item.childCount() > 0:
            return
        
        title = item.data(0, Qt.ItemDataRole.UserRole)
        if not title or title not in self.folder_data:
            return
        
        data = self.folder_data[title]
        sources = sorted(data['sources'])
        
        # 如果有多个来源，创建三层结构：作品 → 来源 → 章节
        if len(sources) > 1:
            # 获取每个来源的最新章节信息（用于排序）
            source_info = []
            for source_name in sources:
                chapters = data['chapters'].get(source_name, [])
                if chapters:
                    # 排序章节获取最新的
                    sorted_chapters = self._sort_chapters(chapters)
                    latest_chapter = sorted_chapters[-1] if not self.sort_ascending else sorted_chapters[0]
                    # 获取最新话的修改时间
                    try:
                        latest_time = os.path.getmtime(latest_chapter['path'])
                    except:
                        latest_time = 0
                    
                    source_info.append({
                        'name': source_name,
                        'chapters': chapters,
                        'latest_chapter': latest_chapter,
                        'latest_time': latest_time,
                        'count': len(chapters)
                    })
            
            # 按最新话时间排序来源（最新的在前）
            source_info.sort(key=lambda x: x['latest_time'], reverse=True)
            
            # 创建来源节点
            for idx, info in enumerate(source_info):
                source_item = QTreeWidgetItem(item)
                source_item.setText(0, info['name'])
                source_item.setText(1, "来源")
                # 显示：最新话 / 总数
                source_item.setText(2, f"{info['latest_chapter']['name']} / {info['count']}章")
                
                # 排序章节
                sorted_chapters = self._sort_chapters(info['chapters'])
                
                # 在来源下添加章节
                for chapter in sorted_chapters:
                    chapter_item = QTreeWidgetItem(source_item)
                    chapter_item.setText(0, chapter['name'])
                    chapter_item.setText(1, info['name'])
                    chapter_item.setText(2, chapter['path'])
                    chapter_item.setData(0, Qt.ItemDataRole.UserRole, chapter['path'])
                    # 设置复选框
                    chapter_item.setCheckState(0, Qt.CheckState.Unchecked)
                    chapter_item.setFlags(chapter_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                
                # 自动展开第一个来源（最新的）
                if idx == 0:
                    source_item.setExpanded(True)
        else:
            # 只有一个来源，直接显示章节
            source_name = sources[0] if sources else ""
            chapters = data['chapters'].get(source_name, [])
            
            # 排序章节
            sorted_chapters = self._sort_chapters(chapters)
            
            for chapter in sorted_chapters:
                child_item = QTreeWidgetItem(item)
                child_item.setText(0, chapter['name'])
                child_item.setText(1, source_name)
                child_item.setText(2, chapter['path'])
                child_item.setData(0, Qt.ItemDataRole.UserRole, chapter['path'])
                # 设置复选框
                child_item.setCheckState(0, Qt.CheckState.Unchecked)
                child_item.setFlags(child_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        
        item.setText(3, "已展开")
    
    def on_item_expanded(self, item: QTreeWidgetItem):
        """当项目展开时，加载章节（用户手动展开时）"""
        self._load_chapters_for_item(item)
    
    def on_item_changed(self, item: QTreeWidgetItem, column: int):
        """项目状态改变时更新统计"""
        if column == 0:
            self._update_selection_count()
    
    def on_selection_changed(self):
        """选择改变时同步到复选框（支持鼠标拉取多选）"""
        # 暂时断开itemChanged信号，避免递归
        self.title_tree.itemChanged.disconnect(self.on_item_changed)
        
        try:
            # 获取当前选中的项目
            selected_items = self.title_tree.selectedItems()
            
            # 只处理章节项（有UserRole数据存储路径的）
            for item in selected_items:
                path = item.data(0, Qt.ItemDataRole.UserRole)
                if path and isinstance(path, str) and os.path.isdir(path):  # 是章节项
                    # 勾选复选框
                    if item.checkState(0) != Qt.CheckState.Checked:
                        item.setCheckState(0, Qt.CheckState.Checked)
        finally:
            # 重新连接信号
            self.title_tree.itemChanged.connect(self.on_item_changed)
            # 更新统计
            self._update_selection_count()
    
    def on_sort_changed(self):
        """排序方式改变"""
        self.sort_by = self.sort_combo.currentData()
        
        # 保存排序设置
        settings = QSettings("MangaTranslator", "AdvancedFolder")
        settings.setValue("sort_by", self.sort_by)
        
        sort_names = {'name': '名称', 'natural': '智能排序', 'date': '修改日期'}
        self._log(f"✓ 排序方式: {sort_names.get(self.sort_by, self.sort_by)}")
        self._reload_all_chapters()
    
    def toggle_sort_order(self):
        """切换升序/降序"""
        self.sort_ascending = not self.sort_ascending
        
        # 保存排序设置
        settings = QSettings("MangaTranslator", "AdvancedFolder")
        settings.setValue("sort_ascending", self.sort_ascending)
        
        if self.sort_ascending:
            self.sort_order_btn.setText("↑ 升序")
            self._log("✓ 排序顺序: 升序")
        else:
            self.sort_order_btn.setText("↓ 降序")
            self._log("✓ 排序顺序: 降序")
        
        self._reload_all_chapters()
    
    def _reload_all_chapters(self):
        """重新加载所有展开的章节"""
        for i in range(self.title_tree.topLevelItemCount()):
            item = self.title_tree.topLevelItem(i)
            if item.childCount() > 0:
                # 清除现有子项
                item.takeChildren()
                # 重新加载
                self._load_chapters_for_item(item)
                item.setExpanded(True)
    
    def _sort_chapters(self, chapters: List[Dict]) -> List[Dict]:
        """根据当前排序方式排序章节"""
        import re
        
        if self.sort_by == "name":
            # 按名称排序
            sorted_list = sorted(chapters, key=lambda x: x['name'])
        elif self.sort_by == "natural":
            # 智能排序（自然排序，处理数字）
            def natural_key(text):
                return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', text)]
            sorted_list = sorted(chapters, key=lambda x: natural_key(x['name']))
        elif self.sort_by == "date":
            # 按修改日期排序
            def get_mtime(chapter):
                try:
                    return os.path.getmtime(chapter['path'])
                except:
                    return 0
            sorted_list = sorted(chapters, key=get_mtime)
        else:
            sorted_list = chapters
        
        # 应用升序/降序
        if not self.sort_ascending:
            sorted_list = list(reversed(sorted_list))
        
        return sorted_list
    
    def apply_filter(self):
        """应用搜索过滤"""
        keyword = self.search_combo.currentText().strip().lower()
        
        for i in range(self.title_tree.topLevelItemCount()):
            item = self.title_tree.topLevelItem(i)
            title = item.text(0).lower()
            sources = item.text(1).lower()
            
            if not keyword or keyword in title or keyword in sources:
                item.setHidden(False)
            else:
                item.setHidden(True)
    
    def _populate_search_combo(self):
        """填充搜索框（使用当前扫描到的作品名称）"""
        self.search_combo.clear()
        
        # 添加所有作品名称
        if self.folder_data:
            titles = sorted(self.folder_data.keys())
            self.search_combo.addItems(titles)
    
    def _populate_search_combo_with_mapping(self):
        """填充搜索框（优先使用名称映射管理中的熟肉名称）"""
        self.search_combo.clear()
        
        # 优先尝试从名称映射管理获取
        if self.name_replacer and self.name_replacer.mapping:
            translated_names = set(self.name_replacer.mapping.values())
            if translated_names:
                self.search_combo.addItems(sorted(translated_names))
                self._log(f"✓ 已从名称映射加载 {len(translated_names)} 个作品名称")
                return
        
        # 如果没有映射，使用扫描到的作品名称
        if self.folder_data:
            titles = sorted(self.folder_data.keys())
            self.search_combo.addItems(titles)
            self._log(f"✓ 已加载 {len(titles)} 个作品名称（未使用映射）")
    
    def refresh_mapping_names(self):
        """刷新映射名称到搜索框（从名称映射管理同步）"""
        if not self.name_replacer:
            self._log("⚠️ 名称映射功能未加载")
            return
        
        # 重新加载映射
        try:
            self.name_replacer.reload()
            self._log("✓ 已重新加载名称映射配置")
        except:
            pass
        
        # 使用统一的填充方法
        self._populate_search_combo_with_mapping()
    
    def select_all_chapters(self):
        """全选所有章节"""
        count = 0
        for i in range(self.title_tree.topLevelItemCount()):
            parent = self.title_tree.topLevelItem(i)
            if not parent.isHidden():
                count += self._check_all_chapters_recursive(parent, True)
        
        self._log(f"✓ 已全选 {count} 个章节")
    
    def deselect_all_chapters(self):
        """取消全选"""
        count = 0
        for i in range(self.title_tree.topLevelItemCount()):
            parent = self.title_tree.topLevelItem(i)
            count += self._check_all_chapters_recursive(parent, False)
        
        self._log(f"✓ 已取消全选")
    
    def get_selected_chapters(self) -> List[str]:
        """获取选中的章节路径"""
        selected = []
        
        for i in range(self.title_tree.topLevelItemCount()):
            parent = self.title_tree.topLevelItem(i)
            self._collect_checked_chapters_recursive(parent, selected)
        
        return selected
    
    def _check_all_chapters_recursive(self, item: QTreeWidgetItem, checked: bool) -> int:
        """递归勾选/取消勾选所有章节"""
        count = 0
        for i in range(item.childCount()):
            child = item.child(i)
            path = child.data(0, Qt.ItemDataRole.UserRole)
            
            if path and isinstance(path, str) and os.path.isdir(path):  # 是章节项
                child.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                count += 1
            else:  # 是来源项或其他，递归处理
                count += self._check_all_chapters_recursive(child, checked)
        
        return count
    
    def _collect_checked_chapters_recursive(self, item: QTreeWidgetItem, selected: List[str]):
        """递归收集所有勾选的章节"""
        for i in range(item.childCount()):
            child = item.child(i)
            path = child.data(0, Qt.ItemDataRole.UserRole)
            
            if path and isinstance(path, str) and os.path.isdir(path):  # 是章节项
                if child.checkState(0) == Qt.CheckState.Checked:
                    selected.append(path)
            else:  # 是来源项或其他，递归处理
                self._collect_checked_chapters_recursive(child, selected)
    
    def _update_selection_count(self):
        """更新选中数量"""
        count = len(self.get_selected_chapters())
        
        # 更新统计
        total_works = len(self.folder_data)
        total_chapters = sum(
            sum(len(chapters) for chapters in data['chapters'].values())
            for data in self.folder_data.values()
        )
        self._update_stats(total_works, total_chapters, count)
        
        # 更新确定按钮状态
        self.ok_button.setEnabled(count > 0)
    
    def _update_stats(self, title_count: int, chapter_count: int, selected_count: int):
        """更新统计信息"""
        self.stats_label.setText(
            f"作品: {title_count} | 章节: {chapter_count} | 已选: {selected_count}"
        )
    
    def _log(self, message: str):
        """输出日志"""
        self.log_text.append(message)
    
    def _save_scan_cache(self):
        """保存扫描结果到缓存"""
        try:
            import json
            settings = QSettings("MangaTranslator", "AdvancedFolder")
            
            # 转换set为list以便JSON序列化
            cache_data = {
                'root_path': self.root_path_edit.text(),
                'folder_data': {}
            }
            
            for title, data in self.folder_data.items():
                cache_data['folder_data'][title] = {
                    'original_names': list(data['original_names']),
                    'sources': list(data['sources']),
                    'chapters': data['chapters']  # chapters已经是可序列化的
                }
            
            settings.setValue("scan_cache", json.dumps(cache_data, ensure_ascii=False))
            self._log("✓ 已保存扫描结果")
            
        except Exception as e:
            self._log(f"⚠️ 保存扫描结果失败: {e}")
    
    def _load_scan_cache(self):
        """加载上次扫描结果"""
        try:
            import json
            settings = QSettings("MangaTranslator", "AdvancedFolder")
            cached_data = settings.value("scan_cache", "")
            
            if not cached_data:
                self._log("💡 首次使用，请点击【扫描作品】按钮开始")
                return
            
            cache = json.loads(cached_data)
            cached_root = cache.get('root_path', '')
            
            # 如果缓存的根目录与当前根目录不同，不加载
            if cached_root != self.root_path_edit.text():
                self._log(f"💡 根目录已更改，请重新扫描")
                return
            
            # 转换list回set
            self.folder_data = {}
            for title, data in cache.get('folder_data', {}).items():
                self.folder_data[title] = {
                    'original_names': set(data.get('original_names', [])),
                    'sources': set(data.get('sources', [])),
                    'chapters': data.get('chapters', {})
                }
            
            if self.folder_data:
                self.refresh_title_list()
                self._populate_search_combo_with_mapping()
                self._log(f"✓ 已加载上次扫描结果（{len(self.folder_data)} 个作品）")
            else:
                self._log("💡 请点击【扫描作品】按钮开始")
                
        except Exception as e:
            self._log(f"⚠️ 加载扫描结果失败: {e}")
            self._log("💡 请点击【扫描作品】按钮开始")


def show_advanced_folder_dialog(parent=None, start_dir: str = "") -> Optional[List[str]]:
    """显示高级文件夹对话框并返回选中的章节路径"""
    # 读取上次路径
    settings = QSettings("MangaTranslator", "AdvancedFolder")
    if not start_dir:
        start_dir = settings.value("last_dir", str(Path.home() / "Downloads"))
    
    dialog = AdvancedFolderDialog(parent, start_dir)
    
    if dialog.exec() == QDialog.DialogCode.Accepted:
        selected = dialog.get_selected_chapters()
        
        if selected:
            # 保存路径
            settings.setValue("last_dir", dialog.root_path_edit.text())
            return selected
    
    return None
