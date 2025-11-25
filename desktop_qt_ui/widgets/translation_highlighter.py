"""
译文标记高亮器
为译文内容和标记提供视觉化的语法高亮
"""

from PyQt6.QtCore import QRegularExpression
from PyQt6.QtGui import QColor, QTextCharFormat, QFont, QSyntaxHighlighter


class TranslationMarkupHighlighter(QSyntaxHighlighter):
    """
    译文标记高亮器
    根据标记框中的信息，在内容框中高亮显示对应的文本
    """
    
    def __init__(self, parent_document, markup_getter=None):
        """
        Args:
            parent_document: 要应用高亮的文档（QTextEdit.document()）
            markup_getter: 获取标记信息的回调函数，返回标记字符串
        """
        super().__init__(parent_document)
        self.markup_getter = markup_getter
        self._setup_formats()
    
    def _setup_formats(self):
        """设置不同类型标记的格式"""
        # 横排文字格式 - 浅蓝色背景
        self.horizontal_format = QTextCharFormat()
        self.horizontal_format.setBackground(QColor("#E3F2FD"))  # 浅蓝色
        self.horizontal_format.setForeground(QColor("#1976D2"))  # 深蓝色文字
        self.horizontal_format.setFontWeight(QFont.Weight.Bold)
        
        # 换行位置格式 - 显示特殊符号
        self.newline_format = QTextCharFormat()
        self.newline_format.setBackground(QColor("#FFF3E0"))  # 浅橙色
        self.newline_format.setForeground(QColor("#F57C00"))  # 橙色
    
    def set_markup_getter(self, getter):
        """设置标记获取函数"""
        self.markup_getter = getter
    
    def highlightBlock(self, text):
        """高亮当前文本块"""
        if not self.markup_getter:
            return
        
        markup_text = self.markup_getter()
        if not markup_text:
            return
        
        # 解析标记
        h_ranges, newline_positions = self._parse_markup(markup_text)
        
        # 计算当前块在整个文档中的偏移
        block_start = self.currentBlock().position()
        block_length = len(text)
        
        # 应用横排高亮
        for start, end in h_ranges:
            # 检查是否与当前块重叠
            if start < block_start + block_length and end > block_start:
                # 计算在当前块中的相对位置
                rel_start = max(0, start - block_start)
                rel_end = min(block_length, end - block_start)
                
                # 应用格式
                self.setFormat(rel_start, rel_end - rel_start, self.horizontal_format)
        
        # 在换行位置后添加视觉标记
        for pos in newline_positions:
            if block_start <= pos < block_start + block_length:
                rel_pos = pos - block_start
                # 高亮换行位置后的一个字符
                if rel_pos < block_length:
                    self.setFormat(rel_pos, 1, self.newline_format)
    
    def _parse_markup(self, markup_text):
        """
        解析标记文本
        
        Returns:
            (h_ranges, newline_positions)
            h_ranges: [(start, end), ...] 横排文字的范围
            newline_positions: [pos, ...] 换行位置
        """
        h_ranges = []
        newline_positions = []
        
        for mark in markup_text.split():
            if mark.startswith('⇄'):
                # 横排标记
                range_str = mark[1:]
                if '-' in range_str:
                    try:
                        start, end = map(int, range_str.split('-'))
                        h_ranges.append((start, end))
                    except ValueError:
                        pass
            elif mark.startswith('↵'):
                # 换行标记
                try:
                    pos = int(mark[1:])
                    newline_positions.append(pos)
                except ValueError:
                    pass
        
        return h_ranges, newline_positions


class MarkupBoxHighlighter(QSyntaxHighlighter):
    """
    标记框的语法高亮器
    为标记框中的标记符号添加颜色
    """
    
    def __init__(self, parent_document):
        super().__init__(parent_document)
        self._setup_formats()
    
    def _setup_formats(self):
        """设置格式"""
        # 横排标记格式
        self.h_mark_format = QTextCharFormat()
        self.h_mark_format.setForeground(QColor("#1976D2"))  # 蓝色
        self.h_mark_format.setFontWeight(QFont.Weight.Bold)
        
        # 换行标记格式
        self.newline_mark_format = QTextCharFormat()
        self.newline_mark_format.setForeground(QColor("#F57C00"))  # 橙色
        self.newline_mark_format.setFontWeight(QFont.Weight.Bold)
        
        # 数字格式
        self.number_format = QTextCharFormat()
        self.number_format.setForeground(QColor("#00897B"))  # 青色
    
    def highlightBlock(self, text):
        """高亮当前文本块"""
        # 高亮横排标记 ⇄
        h_pattern = QRegularExpression(r'⇄\d+-\d+')
        match_iterator = h_pattern.globalMatch(text)
        while match_iterator.hasNext():
            match = match_iterator.next()
            self.setFormat(match.capturedStart(), 1, self.h_mark_format)  # ⇄ 符号
            # 数字部分
            num_start = match.capturedStart() + 1
            num_length = match.capturedLength() - 1
            self.setFormat(num_start, num_length, self.number_format)
        
        # 高亮换行标记 ↵
        newline_pattern = QRegularExpression(r'↵\d+')
        match_iterator = newline_pattern.globalMatch(text)
        while match_iterator.hasNext():
            match = match_iterator.next()
            self.setFormat(match.capturedStart(), 1, self.newline_mark_format)  # ↵ 符号
            # 数字部分
            num_start = match.capturedStart() + 1
            num_length = match.capturedLength() - 1
            self.setFormat(num_start, num_length, self.number_format)
