from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CardWidget, TitleLabel

from ui.secondary_pages.batch_edit_panel import BatchEditPanel


def create_batch_edit_page(self) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(18, 16, 18, 14)
    layout.setSpacing(12)

    header = CardWidget()
    header_layout = QVBoxLayout(header)
    header_layout.setContentsMargins(16, 12, 16, 12)
    header_layout.setSpacing(4)
    self.batch_edit_page_title_label = TitleLabel(self._t("Batch Management"))
    self.batch_edit_page_subtitle_label = BodyLabel(
        self._t("Match regions across the main file list and edit their text, styling, "
                "and properties in bulk")
    )
    self.batch_edit_page_subtitle_label.setWordWrap(True)
    header_layout.addWidget(self.batch_edit_page_title_label)
    header_layout.addWidget(self.batch_edit_page_subtitle_label)
    layout.addWidget(header)

    # MainView 是纯逻辑 QObject，不能当控件父级；面板随 addWidget 进布局后自动认领父级
    self.batch_edit_panel = BatchEditPanel(t_func=self._t)
    layout.addWidget(self.batch_edit_panel, 1)
    return page
