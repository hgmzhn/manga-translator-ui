from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ui.secondary_pages.replacements_editor import ReplacementsEditorPanel

def create_replacements_page(self) -> QWidget:
    page = QWidget()
    page.setObjectName("content_page_replacements")
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(18, 16, 18, 14)
    page_layout.setSpacing(12)

    # --- Header Card ---
    header_card = QWidget()
    header_card.setObjectName("header_card")
    header_layout = QVBoxLayout(header_card)
    header_layout.setContentsMargins(12, 4, 12, 4)
    header_layout.setSpacing(4)
    self.replacements_page_title_label = QLabel(self._t("Replacement Rules"))
    self.replacements_page_title_label.setObjectName("page_title")
    self.replacements_page_subtitle_label = QLabel(
        self._t("Manage text replacement rules applied to translations before rendering")
    )
    self.replacements_page_subtitle_label.setObjectName("page_subtitle")
    self.replacements_page_subtitle_label.setWordWrap(True)
    header_layout.addWidget(self.replacements_page_title_label)
    header_layout.addWidget(self.replacements_page_subtitle_label)
    page_layout.addWidget(header_card)

    # --- Editor Panel ---
    self.replacements_editor_panel = ReplacementsEditorPanel(t_func=self._t, parent=self)
    page_layout.addWidget(self.replacements_editor_panel, 1)

    return page


