from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CardWidget, TitleLabel

from ui.secondary_pages.replacements_editor import ReplacementsEditorPanel


def create_replacements_page(self) -> QWidget:
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(12)

    header_card = CardWidget()
    header_layout = QVBoxLayout(header_card)
    header_layout.setContentsMargins(16, 12, 16, 12)
    header_layout.setSpacing(4)
    self.replacements_page_title_label = TitleLabel(self._t("Replacement Rules"))
    self.replacements_page_subtitle_label = BodyLabel(
        self._t("Manage text replacement rules applied to translations before rendering")
    )
    self.replacements_page_subtitle_label.setWordWrap(True)
    header_layout.addWidget(self.replacements_page_title_label)
    header_layout.addWidget(self.replacements_page_subtitle_label)
    page_layout.addWidget(header_card)

    # MainView 是纯逻辑 QObject，不能当控件父级；面板随 addWidget 进布局后自动认领父级
    self.replacements_editor_panel = ReplacementsEditorPanel(t_func=self._t)
    page_layout.addWidget(self.replacements_editor_panel, 1)

    return page
