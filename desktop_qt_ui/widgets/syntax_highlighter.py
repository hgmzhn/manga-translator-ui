
import re

from PyQt6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat


class HorizontalTagHighlighter(QSyntaxHighlighter):
    """
    用于高亮显示 <H>...</H> 标签的语法高亮器，
    模仿旧UI中的 `_highlight_horizontal_tags` 功能。
    """
    def __init__(self, parent):
        super().__init__(parent)

        # 格式1: 用于 <H> 和 </H> 标签本身 (使其不可见)
        self.tag_format = QTextCharFormat()
        # 将标签设置为完全透明，字体大小为0
        self.tag_format.setForeground(QColor(0, 0, 0, 0))  # 完全透明
        self.tag_format.setFontPointSize(0.1)  # 设置极小的字体大小
        # 隐藏标签的另一种方法：使用与背景相同的颜色
        # 获取父控件的背景色
        if parent:
            bg_color = parent.palette().color(parent.backgroundRole())
            self.tag_format.setForeground(bg_color)

        # 格式2: 用于标签内的内容 (高亮背景)
        self.content_format = QTextCharFormat()
        self.content_format.setBackground(QColor("#4B4B4B"))
        self.content_format.setForeground(QColor("white"))

        # 正则表达式，与旧代码一致
        self.pattern = re.compile(r'(<H>)(.*?)(</H>)', re.IGNORECASE | re.DOTALL)

    def highlightBlock(self, text):
        """在文本块中应用高亮"""
        for match in self.pattern.finditer(text):
            # 高亮开始标签 <H> - 使其不可见
            self.setFormat(match.start(1), match.end(1) - match.start(1), self.tag_format)
            
            # 高亮内容
            self.setFormat(match.start(2), match.end(2) - match.start(2), self.content_format)
            
            # 高亮结束标签 </H> - 使其不可见
            self.setFormat(match.start(3), match.end(3) - match.start(3), self.tag_format)
