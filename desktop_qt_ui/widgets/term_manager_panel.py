#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
专有名词管理面板
按作品分类管理专有名词（人名、地名、特殊术语）
布局参考名称映射管理，采用树形视图展示
"""

import os
import sys
from pathlib import Path
from typing import Optional
from PyQt6.QtCore import Qt, QSettings, QSortFilterProxyModel
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton,
    QLineEdit, QTextEdit, QLabel, QTreeView, QHeaderView,
    QAbstractItemView, QFileDialog, QMessageBox, QInputDialog,
    QComboBox, QSplitter, QDialog
)
from PyQt6.QtGui import QStandardItemModel, QStandardItem

# 导入工具模块
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'utils'))

try:
    from utils.term_manager import TermManager, get_term_manager
except ImportError:
    TermManager = None
    get_term_manager = None


class MultiTermDialog(QDialog):
    """多对一映射输入对话框（与名称映射管理一致：每个原文独立输入框）"""
    
    def __init__(self, parent=None, title="添加专有名词", categories=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.setMinimumHeight(450)
        self._original_rows = []
        self._categories = categories or {}
        self._init_ui()
    
    def _init_ui(self):
        """初始化界面"""
        from PyQt6.QtWidgets import QScrollArea, QDialogButtonBox
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)
        
        # 类别选择（新增）
        cat_label = QLabel("术语类别：")
        main_layout.addWidget(cat_label)
        
        self.category_combo = QComboBox()
        if self._categories:
            for key, val in self._categories.items():
                self.category_combo.addItem(val, key)
        main_layout.addWidget(self.category_combo)
        
        # 译文输入（单行）
        trans_label = QLabel("译文（中文）：")
        main_layout.addWidget(trans_label)
        
        self.translation_edit = QLineEdit()
        self.translation_edit.setPlaceholderText("例如：教主")
        main_layout.addWidget(self.translation_edit)
        
        # 原文列表标题栏
        orig_label_row = QHBoxLayout()
        orig_label_row.addWidget(QLabel("原文（可添加多个）："))
        orig_label_row.addStretch()
        
        add_btn = QPushButton("新增原文")
        add_btn.clicked.connect(lambda: self.add_original_field(""))
        orig_label_row.addWidget(add_btn)
        
        clear_btn = QPushButton("清空全部")
        clear_btn.clicked.connect(self.clear_all_originals)
        orig_label_row.addWidget(clear_btn)
        
        main_layout.addLayout(orig_label_row)
        
        # 可滚动的原文输入区域
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        main_layout.addWidget(scroll, 1)
        
        self.original_container = QWidget()
        self.original_layout = QVBoxLayout(self.original_container)
        self.original_layout.setContentsMargins(0, 0, 0, 0)
        self.original_layout.setSpacing(8)
        scroll.setWidget(self.original_container)
        
        # 初始化一个空行
        self.add_original_field("")
        
        # 提示说明
        hint_label = QLabel("💡 提示：多个原文会映射到同一个译文，翻译时任意匹配")
        hint_label.setStyleSheet("color: #666; font-size: 11px;")
        hint_label.setWordWrap(True)
        main_layout.addWidget(hint_label)
        
        # 确认/取消按钮
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        main_layout.addWidget(btn_box)
    
    def add_original_field(self, text: str = ""):
        """新增一个原文输入行"""
        row_widget = QWidget(self.original_container)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        
        line_edit = QLineEdit(row_widget)
        line_edit.setPlaceholderText("例如：교주 / 教主 / Cult Leader")
        line_edit.setText(text)
        row_layout.addWidget(line_edit, 1)
        
        remove_btn = QPushButton("删除", row_widget)
        remove_btn.clicked.connect(lambda: self._remove_row(row_widget))
        row_layout.addWidget(remove_btn)
        
        self.original_layout.addWidget(row_widget)
        self._original_rows.append(row_widget)
    
    def _remove_row(self, row_widget: QWidget):
        """删除一行原文输入"""
        if row_widget in self._original_rows:
            self._original_rows.remove(row_widget)
        row_widget.setParent(None)
        row_widget.deleteLater()
        
        # 保证至少有一行
        if not self._original_rows:
            self.add_original_field("")
    
    def clear_all_originals(self):
        """清空所有原文输入，保留一空行"""
        for w in list(self._original_rows):
            self._remove_row(w)
        if not self._original_rows:
            self.add_original_field("")
    
    def _on_accept(self):
        """校验并关闭对话框"""
        translation = self.translation_edit.text().strip()
        originals = self.get_originals()
        
        if not translation:
            QMessageBox.warning(self, "输入错误", "请填写译文")
            return
        if not originals:
            QMessageBox.warning(self, "输入错误", "请至少添加一个原文")
            return
        
        self.accept()
    
    def get_originals(self):
        """获取所有原文（返回列表）"""
        originals = []
        for w in self._original_rows:
            le = w.findChild(QLineEdit)
            if le:
                text = le.text().strip()
                if text:
                    originals.append(text)
        # 去重
        seen = set()
        unique_originals = []
        for orig in originals:
            if orig not in seen:
                seen.add(orig)
                unique_originals.append(orig)
        return unique_originals
    
    def get_values(self):
        """获取输入的值（返回：原文列表, 译文, 类别key）"""
        translation = self.translation_edit.text().strip()
        originals = self.get_originals()
        category = self.category_combo.currentData()
        return originals, translation, category
    
    def set_values(self, originals_list, translation: str, category: str = None):
        """设置初始值（编辑模式）"""
        self.translation_edit.setText(translation)
        
        # 设置类别
        if category:
            index = self.category_combo.findData(category)
            if index >= 0:
                self.category_combo.setCurrentIndex(index)
        
        # 清空现有行
        for w in list(self._original_rows):
            self._remove_row(w)
        
        # 添加原文行
        if originals_list:
            for orig in originals_list:
                self.add_original_field(orig)
        else:
            self.add_original_field("")


class TermFilterProxy(QSortFilterProxyModel):
    """专有名词过滤代理：多列、递归过滤"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._kw = ""
    
    def setFilterKeyword(self, text: str):
        self._kw = (text or "").strip().lower()
        self.invalidateFilter()
    
    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        if not self._kw:
            return True
        
        model = self.sourceModel()
        idx0 = model.index(source_row, 0, source_parent)
        idx1 = model.index(source_row, 1, source_parent)
        idx2 = model.index(source_row, 2, source_parent)
        
        def _t(idx):
            v = model.data(idx, Qt.ItemDataRole.DisplayRole)
            return (v or "").lower()
        
        # 当前行匹配
        if self._kw in _t(idx0) or self._kw in _t(idx1) or self._kw in _t(idx2):
            return True
        
        # 子项匹配则保留父项
        child_count = model.rowCount(idx0)
        for i in range(child_count):
            if self.filterAcceptsRow(i, idx0):
                return True
        
        return False


class TermManagerPanel(QWidget):
    """专有名词管理面板"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.term_manager = get_term_manager() if get_term_manager else None
        if not self.term_manager:
            QMessageBox.critical(self, "错误", "无法加载专有名词管理器")
            return
        
        self._init_ui()
        self._load_settings()
        self.load_terms()
    
    def _init_ui(self):
        """初始化界面"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)
        
        # 顶部说明
        info_label = QLabel(
            "专有名词管理系统：按作品管理角色名、地名和特殊术语，自动应用到高质量翻译中。"
            "刷新时自动同步【名称映射管理】中的熟肉名称。"
        )
        info_label.setStyleSheet("color: #666; padding: 4px;")
        info_label.setWordWrap(True)
        main_layout.addWidget(info_label)
        
        # 创建垂直分割器
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # 上半部分：专有名词管理
        term_group = self._create_term_management_section()
        splitter.addWidget(term_group)
        
        # 下半部分：结果输出
        result_group = self._create_result_section()
        splitter.addWidget(result_group)
        
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        
        main_layout.addWidget(splitter)
        
        self.splitter = splitter
    
    def _create_term_management_section(self) -> QWidget:
        """创建专有名词管理区域"""
        group = QGroupBox("专有名词管理")
        layout = QVBoxLayout(group)
        
        # 顶部操作栏
        top_layout = QHBoxLayout()
        
        # 作品选择
        top_layout.addWidget(QLabel("作品:"))
        self.work_combo = QComboBox()
        self.work_combo.setPlaceholderText("选择作品")
        self.work_combo.currentTextChanged.connect(self.on_work_changed)
        top_layout.addWidget(self.work_combo, 1)
        
        # 添加作品按钮
        add_work_btn = QPushButton("新建作品")
        add_work_btn.clicked.connect(self.add_work)
        top_layout.addWidget(add_work_btn)
        
        # 删除作品按钮
        del_work_btn = QPushButton("删除作品")
        del_work_btn.clicked.connect(self.delete_work)
        top_layout.addWidget(del_work_btn)
        
        # 【新增】同步名称映射按钮
        sync_btn = QPushButton("同步名称映射")
        sync_btn.setToolTip("从【名称映射管理】同步所有熟肉名称到作品列表")
        sync_btn.clicked.connect(self.sync_name_mappings)
        top_layout.addWidget(sync_btn)
        
        layout.addLayout(top_layout)
        
        # 搜索与计数
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("搜索:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入关键字过滤...")
        self.search_edit.textChanged.connect(self.apply_filter)
        search_layout.addWidget(self.search_edit, 1)
        
        self.count_label = QLabel("显示 0 / 总 0")
        search_layout.addWidget(self.count_label)
        layout.addLayout(search_layout)
        
        # 树形视图
        self.tree_view = QTreeView()
        self.tree_view.setRootIsDecorated(True)
        self.tree_view.setAlternatingRowColors(True)
        self.tree_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree_view.doubleClicked.connect(self.edit_term)
        layout.addWidget(self.tree_view)
        
        # 操作按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        add_btn = QPushButton("添加术语")
        add_btn.clicked.connect(self.add_term)
        
        edit_btn = QPushButton("编辑")
        edit_btn.clicked.connect(lambda: self.edit_term())
        
        delete_btn = QPushButton("删除")
        delete_btn.clicked.connect(self.delete_term)
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.load_terms)
        
        export_btn = QPushButton("导出")
        export_btn.clicked.connect(self.export_terms)
        
        import_btn = QPushButton("导入")
        import_btn.clicked.connect(self.import_terms)
        
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(edit_btn)
        btn_layout.addWidget(delete_btn)
        btn_layout.addWidget(refresh_btn)
        btn_layout.addWidget(export_btn)
        btn_layout.addWidget(import_btn)
        layout.addLayout(btn_layout)
        
        return group
    
    def _create_result_section(self) -> QWidget:
        """创建结果输出区域"""
        group = QGroupBox("操作日志")
        layout = QVBoxLayout(group)
        
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        layout.addWidget(self.result_text)
        
        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        clear_btn = QPushButton("清空日志")
        clear_btn.clicked.connect(self.result_text.clear)
        btn_layout.addWidget(clear_btn)
        
        stats_btn = QPushButton("查看统计")
        stats_btn.clicked.connect(self.show_statistics)
        btn_layout.addWidget(stats_btn)
        
        layout.addLayout(btn_layout)
        
        return group
    
    def _log_message(self, message: str):
        """输出日志消息"""
        if hasattr(self, 'result_text'):
            self.result_text.append(message)
    
    def load_terms(self):
        """加载所有专有名词到树形视图，自动同步名称映射"""
        if not self.term_manager:
            return
        
        # 保存当前选择的作品
        current_work = self.work_combo.currentText()
        
        # 重新加载数据
        self.term_manager.reload()
        
        # 【新增】从名称映射管理获取所有熟肉名称
        all_works = set(self.term_manager.get_all_works())  # 现有作品
        
        # 获取名称映射中的所有熟肉名
        sync_count = 0
        try:
            from utils.name_replacer import NameReplacer
            name_replacer = NameReplacer()
            
            # 获取所有映射的熟肉名（译文）
            for raw_name, translated_name in name_replacer.mapping.items():
                # 处理多语言名称（用|分隔）
                if '|' in raw_name:
                    # 对于多语言名称，使用第一个名称作为代表
                    pass
                # 添加熟肉名到作品列表
                all_works.add(translated_name)
            
            sync_count = len(name_replacer.mapping)
        except Exception as e:
            # 初始化时result_text可能还未创建，使用print作为后备
            if hasattr(self, 'result_text'):
                self._log_message(f"⚠ 同步名称映射失败: {e}")
            else:
                print(f"⚠ 同步名称映射失败: {e}")
        
        # 更新作品列表（排序）
        self.work_combo.clear()
        sorted_works = sorted(all_works)
        self.work_combo.addItems(sorted_works)
        
        # 恢复选择
        if current_work and current_work in sorted_works:
            self.work_combo.setCurrentText(current_work)
        elif sorted_works:
            self.work_combo.setCurrentIndex(0)
        
        # 记录同步日志（仅在UI初始化后）
        if sync_count > 0 and hasattr(self, 'result_text'):
            self._log_message(f"✓ 已同步 {sync_count} 个名称映射，共 {len(sorted_works)} 个作品")
        
        # 加载术语树
        self.load_work_terms()
    
    def load_work_terms(self):
        """加载当前选中作品的专有名词"""
        if not self.term_manager:
            return
        
        work_name = self.work_combo.currentText()
        if not work_name:
            # 显示空模型
            model = QStandardItemModel()
            model.setHorizontalHeaderLabels(["原文", "译文", "类别"])
            self.tree_view.setModel(model)
            self._update_count(0, 0)
            return
        
        # 创建模型
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(["原文", "译文", "类别"])
        
        total_count = 0
        
        # 按类别分组
        for category in self.term_manager.ALL_CATEGORIES:
            terms = self.term_manager.get_terms(work_name, category)
            if not terms:
                continue
            
            # 创建父项
            category_name = self.term_manager.CATEGORY_DISPLAY_NAMES.get(category, category)
            parent_item = QStandardItem(f"{category_name} ({len(terms)})")
            parent_item.setEditable(False)
            parent_item.setData(category, Qt.ItemDataRole.UserRole)  # 存储类别标识
            
            parent_col1 = QStandardItem("")
            parent_col1.setEditable(False)
            
            parent_col2 = QStandardItem("")
            parent_col2.setEditable(False)
            
            model.appendRow([parent_item, parent_col1, parent_col2])
            
            # 添加子项
            for original, translation in sorted(terms.items()):
                orig_item = QStandardItem(original)
                orig_item.setEditable(False)
                
                trans_item = QStandardItem(translation)
                trans_item.setEditable(False)
                
                cat_item = QStandardItem(category_name)
                cat_item.setEditable(False)
                
                parent_item.appendRow([orig_item, trans_item, cat_item])
                total_count += 1
        
        # 设置代理
        if not hasattr(self, 'proxy') or self.proxy is None:
            self.proxy = TermFilterProxy(self)
        self.proxy.setSourceModel(model)
        self.tree_view.setModel(self.proxy)
        
        # 设置列宽
        header = self.tree_view.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 200)
        header.resizeSection(1, 200)
        header.resizeSection(2, 100)
        
        # 更新计数
        self._update_count(total_count, total_count)
    
    def on_work_changed(self, work_name: str):
        """作品切换时重新加载术语"""
        self.load_work_terms()
    
    def sync_name_mappings(self):
        """手动同步名称映射"""
        try:
            from utils.name_replacer import NameReplacer
            name_replacer = NameReplacer()
            
            # 保存当前选择
            current_work = self.work_combo.currentText()
            
            # 获取现有作品
            existing_works = set(self.term_manager.get_all_works())
            
            # 获取名称映射中的所有熟肉名
            new_works = set()
            for raw_name, translated_name in name_replacer.mapping.items():
                if translated_name not in existing_works:
                    new_works.add(translated_name)
            
            if new_works:
                # 更新作品列表
                all_works = existing_works | new_works
                self.work_combo.clear()
                self.work_combo.addItems(sorted(all_works))
                
                # 恢复选择
                if current_work:
                    self.work_combo.setCurrentText(current_work)
                
                self._log_message(f"✓ 同步完成！从名称映射中新增 {len(new_works)} 个作品名:")
                for work in sorted(new_works)[:10]:  # 只显示前10个
                    self._log_message(f"  · {work}")
                if len(new_works) > 10:
                    self._log_message(f"  ... 还有 {len(new_works) - 10} 个")
            else:
                self._log_message("✓ 同步完成！未发现新的作品名")
                
        except Exception as e:
            QMessageBox.warning(self, "同步失败", f"同步名称映射失败:\n{e}")
            self._log_message(f"❌ 同步失败: {e}")
    
    def add_work(self):
        """添加新作品"""
        work_name, ok = QInputDialog.getText(
            self, "新建作品", "请输入作品名称（熟肉名）:"
        )
        
        if ok and work_name.strip():
            work_name = work_name.strip()
            
            # 检查是否已存在
            if work_name in self.term_manager.get_all_works():
                QMessageBox.warning(self, "警告", f"作品「{work_name}」已存在")
                return
            
            # 初始化作品结构
            for category in self.term_manager.ALL_CATEGORIES:
                self.term_manager.add_term(work_name, category, "__init__", "")
                self.term_manager.remove_term(work_name, category, "__init__")
            
            self.result_text.append(f"✓ 已创建作品: {work_name}")
            self.load_terms()
            self.work_combo.setCurrentText(work_name)
    
    def delete_work(self):
        """删除作品"""
        work_name = self.work_combo.currentText()
        if not work_name:
            QMessageBox.warning(self, "未选择", "请先选择要删除的作品")
            return
        
        term_count = self.term_manager.get_work_term_count(work_name)
        
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除作品「{work_name}」及其所有专有名词（{term_count}个）吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.term_manager.remove_work(work_name)
            self.result_text.append(f"✓ 已删除作品: {work_name}")
            self.load_terms()
    
    def add_term(self):
        """添加专有名词（支持多对一）"""
        work_name = self.work_combo.currentText()
        if not work_name:
            QMessageBox.warning(self, "未选择作品", "请先选择或创建作品")
            return
        
        # 使用自定义对话框输入所有信息（包括类别）
        dialog = MultiTermDialog(
            self, 
            title="添加专有名词",
            categories=self.term_manager.CATEGORY_DISPLAY_NAMES
        )
        
        if dialog.exec() == dialog.DialogCode.Accepted:
            originals, translation, category = dialog.get_values()
            
            if not originals or not translation or not category:
                return
            
            category_name = self.term_manager.CATEGORY_DISPLAY_NAMES.get(category, category)
            
            # 添加所有原文到同一个译文
            try:
                for original in originals:
                    self.term_manager.add_term(work_name, category, original, translation)
                
                # 显示结果
                if len(originals) == 1:
                    self.result_text.append(f"✓ 已添加: {originals[0]} → {translation} ({category_name})")
                else:
                    self.result_text.append(f"✓ 已添加: {' | '.join(originals)} → {translation} ({category_name})")
                
                self.load_work_terms()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"添加失败: {e}")
    
    def edit_term(self, index=None):
        """编辑专有名词（支持多对一）"""
        if isinstance(index, bool):
            index = None
        
        if index is None:
            index = self.tree_view.currentIndex()
        
        if not index.isValid():
            QMessageBox.warning(self, "未选择", "请先选择要编辑的术语")
            return
        
        # 获取源索引
        source_index = self.proxy.mapToSource(index)
        model = source_index.model()
        
        # 判断是父项还是子项
        parent_index = source_index.parent()
        if not parent_index.isValid():
            # 点击的是父项（类别），不允许编辑
            return
        
        # 获取数据
        original = model.data(source_index.siblingAtColumn(0), Qt.ItemDataRole.DisplayRole)
        old_translation = model.data(source_index.siblingAtColumn(1), Qt.ItemDataRole.DisplayRole)
        
        # 获取类别
        parent_item = model.itemFromIndex(parent_index)
        category = parent_item.data(Qt.ItemDataRole.UserRole)
        
        work_name = self.work_combo.currentText()
        category_name = self.term_manager.CATEGORY_DISPLAY_NAMES.get(category, category)
        
        # 查找所有相同译文的原文
        all_terms = self.term_manager.get_terms(work_name, category)
        same_translation_originals = [orig for orig, trans in all_terms.items() if trans == old_translation]
        
        # 使用自定义对话框编辑（预填充所有相同译文的原文）
        dialog = MultiTermDialog(
            self, 
            title="编辑专有名词",
            categories=self.term_manager.CATEGORY_DISPLAY_NAMES
        )
        dialog.set_values(same_translation_originals, old_translation, category)
        
        if dialog.exec() == dialog.DialogCode.Accepted:
            new_originals, new_translation, new_category = dialog.get_values()
            
            if not new_originals or not new_translation:
                return
            
            try:
                # 如果类别改变了，需要先删除旧类别下的术语
                category_changed = (new_category != category)
                
                # 删除旧的映射
                for old_orig in same_translation_originals:
                    self.term_manager.remove_term(work_name, category, old_orig)
                
                # 添加新的映射（使用新类别）
                for new_orig in new_originals:
                    self.term_manager.add_term(work_name, new_category, new_orig, new_translation)
                
                # 获取新类别显示名称
                new_category_name = self.term_manager.CATEGORY_DISPLAY_NAMES.get(new_category, new_category)
                
                # 显示结果
                if len(new_originals) == 1:
                    self.result_text.append(f"✓ 已更新: {new_originals[0]} → {new_translation} ({new_category_name})")
                else:
                    self.result_text.append(f"✓ 已更新: {' | '.join(new_originals)} → {new_translation} ({new_category_name})")
                
                if category_changed:
                    self.result_text.append(f"  └─ 类别变更: {category_name} → {new_category_name}")
                
                self.load_work_terms()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"更新失败: {e}")
    
    def delete_term(self):
        """删除专有名词"""
        index = self.tree_view.currentIndex()
        if not index.isValid():
            QMessageBox.warning(self, "未选择", "请先选择要删除的术语")
            return
        
        # 获取源索引
        source_index = self.proxy.mapToSource(index)
        model = source_index.model()
        
        # 判断是父项还是子项
        parent_index = source_index.parent()
        if not parent_index.isValid():
            # 点击的是父项（类别），删除整个类别
            parent_item = model.itemFromIndex(source_index)
            category = parent_item.data(Qt.ItemDataRole.UserRole)
            category_name = self.term_manager.CATEGORY_DISPLAY_NAMES.get(category, category)
            
            work_name = self.work_combo.currentText()
            terms = self.term_manager.get_terms(work_name, category)
            
            reply = QMessageBox.question(
                self, "确认删除",
                f"确定要删除类别「{category_name}」的所有术语（{len(terms)}个）吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                for original in list(terms.keys()):
                    self.term_manager.remove_term(work_name, category, original)
                self.result_text.append(f"✓ 已删除类别: {category_name} ({len(terms)}个)")
                self.load_work_terms()
        else:
            # 删除单个术语
            original = model.data(source_index.siblingAtColumn(0), Qt.ItemDataRole.DisplayRole)
            translation = model.data(source_index.siblingAtColumn(1), Qt.ItemDataRole.DisplayRole)
            
            parent_item = model.itemFromIndex(parent_index)
            category = parent_item.data(Qt.ItemDataRole.UserRole)
            
            work_name = self.work_combo.currentText()
            
            reply = QMessageBox.question(
                self, "确认删除",
                f"确定删除该术语？\n{original} → {translation}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self.term_manager.remove_term(work_name, category, original)
                self.result_text.append(f"✓ 已删除: {original} → {translation}")
                self.load_work_terms()
    
    def export_terms(self):
        """导出专有名词"""
        work_name = self.work_combo.currentText()
        if not work_name:
            QMessageBox.warning(self, "未选择作品", "请先选择要导出的作品")
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出专有名词", f"{work_name}_terms.json",
            "JSON Files (*.json)"
        )
        
        if file_path:
            try:
                self.term_manager.export_work_terms(work_name, file_path)
                self.result_text.append(f"✓ 已导出到: {file_path}")
                QMessageBox.information(self, "成功", "导出成功")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导出失败: {e}")
    
    def import_terms(self):
        """导入专有名词"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入专有名词", "",
            "JSON Files (*.json)"
        )
        
        if file_path:
            # 询问是否合并
            reply = QMessageBox.question(
                self, "导入模式",
                "是否与现有数据合并？\n\n"
                "是：合并数据（保留现有，添加新的）\n"
                "否：覆盖数据（替换同名作品）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            merge = (reply == QMessageBox.StandardButton.Yes)
            
            try:
                self.term_manager.import_work_terms(file_path, merge)
                self.result_text.append(f"✓ 已导入: {file_path}")
                self.load_terms()
                QMessageBox.information(self, "成功", "导入成功")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导入失败: {e}")
    
    def apply_filter(self, text: str = None):
        """应用过滤器"""
        if text is None:
            text = self.search_edit.text()
        
        if hasattr(self, 'proxy') and self.proxy is not None:
            self.proxy.setFilterKeyword(text)
        
        # 更新计数
        self._update_visible_count()
    
    def _update_count(self, visible: int, total: int):
        """更新计数标签"""
        self.count_label.setText(f"显示 {visible} / 总 {total}")
    
    def _update_visible_count(self):
        """更新可见项计数"""
        visible = 0
        total = 0
        
        if hasattr(self, 'proxy') and self.proxy is not None:
            source_model = self.proxy.sourceModel()
            
            # 统计源模型总数
            for r in range(source_model.rowCount()):
                parent_idx = source_model.index(r, 0)
                child_count = source_model.rowCount(parent_idx)
                total += child_count
            
            # 统计代理模型可见数
            for r in range(self.proxy.rowCount()):
                parent_idx = self.proxy.index(r, 0)
                child_count = self.proxy.rowCount(parent_idx)
                visible += child_count
        
        self._update_count(visible, total)
    
    def show_statistics(self):
        """显示统计信息"""
        if not self.term_manager:
            return
        
        stats = self.term_manager.get_statistics()
        
        msg = f"""统计信息：
        
作品总数: {stats['total_works']}
术语总数: {stats['total_terms']}

分类统计:
- {self.term_manager.CATEGORY_DISPLAY_NAMES[self.term_manager.CATEGORY_CHARACTER]}: {stats['category_counts'][self.term_manager.CATEGORY_CHARACTER]}
- {self.term_manager.CATEGORY_DISPLAY_NAMES[self.term_manager.CATEGORY_PLACE]}: {stats['category_counts'][self.term_manager.CATEGORY_PLACE]}
- {self.term_manager.CATEGORY_DISPLAY_NAMES[self.term_manager.CATEGORY_TERM]}: {stats['category_counts'][self.term_manager.CATEGORY_TERM]}
"""
        
        QMessageBox.information(self, "统计信息", msg)
    
    def _settings(self) -> QSettings:
        """获取设置对象"""
        return QSettings("MangaTranslator", "TermManagerPanel")
    
    def _load_settings(self):
        """加载设置"""
        try:
            s = self._settings()
            # 恢复分割器位置
            if hasattr(self, 'splitter'):
                sizes_str = s.value("splitter_sizes")
                if sizes_str:
                    sizes = [int(x) for x in sizes_str.split(',')]
                    self.splitter.setSizes(sizes)
        except Exception:
            pass
    
    def _save_settings(self):
        """保存设置"""
        try:
            s = self._settings()
            # 保存分割器位置
            if hasattr(self, 'splitter'):
                sizes = self.splitter.sizes()
                s.setValue("splitter_sizes", ','.join(map(str, sizes)))
        except Exception:
            pass
    
    def closeEvent(self, event):
        """关闭事件"""
        self._save_settings()
        super().closeEvent(event)
