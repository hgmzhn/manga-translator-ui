"""Readable chat responses and expandable native tool activity."""

from dataclasses import dataclass
from html import escape
import json
import re

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QTextDocument, QTextOption
from qfluentwidgets import TextBrowser, qconfig, themeColor


@dataclass
class ToolRecord:
    tool_call_id: str
    tool_name: str
    arguments: object = None
    result: object = None
    status: str = "running"
    expanded: bool = False

    def finish(self, result, *, validation_error=False):
        self.result = result
        data = result if isinstance(result, dict) else {}
        if validation_error or data.get("status") == "validation_error":
            self.status = "invalid"
        elif data.get("status") == "error" or data.get("error"):
            self.status = "failed"
        elif data.get("render_status") == "failed":
            self.status = "render_failed"
        elif data.get("status") == "accepted":
            self.status = "rendered" if data.get("render_status") == "rendered" else "accepted"
        else:
            self.status = "complete"

    def summary(self, translate):
        args = self.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        args = args if isinstance(args, dict) else {}
        if self.tool_name == "read_skill":
            return str(args.get("name", ""))
        edits = args.get("edits")
        if isinstance(edits, list):
            regions = list(dict.fromkeys(
                str(edit["region_id"]) for edit in edits if isinstance(edit, dict) and "region_id" in edit
            ))
            fields = list(dict.fromkeys(
                key for edit in edits if isinstance(edit, dict) for key in edit if key not in {"region_id", "op"}
            ))
            return translate("Chat tool edit summary", count=len(regions), fields=", ".join(fields))
        return ""

    def html(self, index, translate):
        label = self.tool_name
        detail = self.summary(translate)
        if detail:
            label += " · " + detail
        label += " — " + translate("Chat tool " + self.status)
        arrow = "▾" if self.expanded else "▸"
        result = f'<p><a href="tool:{index}">{arrow} {escape(label)}</a></p>'
        if self.expanded:
            for title, value in (("Chat tool arguments", self.arguments), ("Chat tool result", self.result)):
                if value is None:
                    continue
                if title == "Chat tool arguments" and isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        pass
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str)
                result += f'<p><b>{escape(translate(title))}</b></p><pre>{escape(text)}</pre>'
        return result


class ConversationView(TextBrowser):
    """Render Markdown without loading embedded resources or navigating links."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        option = self.document().defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDefaultTextOption(option)
        qconfig.themeChangedFinished.connect(self._refresh_theme)
        qconfig.themeColorChanged.connect(self._refresh_theme)
        self._refresh_theme()

    def _refresh_theme(self):
        self.document().setDefaultStyleSheet(
            f'a {{ color: {themeColor().name()}; text-decoration: none; }}'
        )
        self.show_entries(self._entries)

    def loadResource(self, resource_type: int, name: QUrl):
        # A model message may contain arbitrary Markdown image/HTML URLs.
        return None

    def clear(self):
        self._entries = []
        super().clear()

    def markdown(self, text):
        document = QTextDocument()
        document.setDefaultFont(self.font())
        document.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownNoHTML)
        match = re.search(r"<body[^>]*>(.*)</body>", document.toHtml(), re.DOTALL)
        return match.group(1) if match else escape(text)

    def show_entries(self, entries):
        self._entries = entries
        bar = self.verticalScrollBar()
        position = bar.value()
        follow = position >= bar.maximum() - 24
        cards = ['<table width="100%" cellspacing="0" cellpadding="10">'
                 f'<tr><td>{entry}</td></tr></table>' for entry in entries]
        self.setHtml("<br>".join(cards))
        bar.setValue(bar.maximum() if follow else position)
