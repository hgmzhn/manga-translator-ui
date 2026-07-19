from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CardWidget, TitleLabel

from ui.secondary_pages.rich_text_rules_editor import RichTextRulesEditorPanel


def create_rich_text_rules_page(self) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(18, 16, 18, 14)
    layout.setSpacing(12)

    header = CardWidget()
    header_layout = QVBoxLayout(header)
    header_layout.setContentsMargins(16, 12, 16, 12)
    header_layout.setSpacing(4)
    self.rich_text_rules_page_title_label = TitleLabel(self._t("Rich Text Rules"))
    self.rich_text_rules_page_subtitle_label = BodyLabel(
        self._t("Automatically style text matched after replacement rules are applied")
    )
    self.rich_text_rules_page_subtitle_label.setWordWrap(True)
    header_layout.addWidget(self.rich_text_rules_page_title_label)
    header_layout.addWidget(self.rich_text_rules_page_subtitle_label)
    layout.addWidget(header)

    self.rich_text_rules_editor_panel = RichTextRulesEditorPanel(t_func=self._t, parent=self)
    layout.addWidget(self.rich_text_rules_editor_panel, 1)
    return page
