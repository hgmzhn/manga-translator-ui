import _bootstrap  # noqa: F401

import json

from PyQt6.QtWidgets import QApplication
from qfluentwidgets import IconWidget

from ui.secondary_pages.prompt_preview import (
    PromptEditorDialog,
    _GLOSSARY_CATEGORIES,
    _GLOSSARY_CATEGORY_ICON_FILES,
    _PROMPT_ICON_FILES,
    _glossary_category_icon,
    _prompt_icon,
)


_REMOVED_EMOJI = "🧭📝📖📄👤📍🏢🔮⚡🐾📚🎨📏📌🖌🖼"


def test_prompt_editor_uses_fluent_icons_instead_of_emoji(tmp_path):
    app = QApplication.instance() or QApplication([])
    prompt_path = tmp_path / "prompt.json"
    prompt_path.write_text(
        json.dumps(
            {
                "system_prompt": "Translate naturally.",
                "project_data": {
                    "title": "Demo",
                    "terminology": {"hero": "brave one"},
                },
                "style_guide": ["Keep names consistent."],
                "translation_rules": ["Preserve punctuation."],
                "glossary": {category: [] for category in _GLOSSARY_CATEGORIES},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    dialog = PromptEditorDialog(str(prompt_path))

    assert set(_PROMPT_ICON_FILES) >= {
        "system_prompt",
        "project_title",
        "terminology",
        "style_guide",
        "translation_rules",
        "glossary",
        "template_edit",
        "raw_edit",
    }
    assert set(_GLOSSARY_CATEGORY_ICON_FILES) == set(_GLOSSARY_CATEGORIES)
    assert all(not _prompt_icon(key).isNull() for key in _PROMPT_ICON_FILES)
    assert all(
        not _glossary_category_icon(category).isNull()
        for category in _GLOSSARY_CATEGORIES
    )

    for route_key in ("template_edit", "raw_edit"):
        item = dialog._tab_segmented.items[route_key]
        assert not item.icon().isNull()
        assert not any(emoji in item.text() for emoji in _REMOVED_EMOJI)

    assert len(dialog._section_containers) == 6
    for _, container in dialog._section_containers:
        icon_widgets = container.findChildren(IconWidget)
        assert icon_widgets
        assert all(not widget.icon.isNull() for widget in icon_widgets)

    for category in _GLOSSARY_CATEGORIES:
        item = dialog._glossary_tab_segmented.items[f"glossary_{category}"]
        assert not item.icon().isNull()
        assert not any(emoji in item.text() for emoji in _REMOVED_EMOJI)

    dialog.close()
    app.processEvents()
