import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WIKI_DIR = ROOT / "doc" / "wiki"
LAYOUT_PATH = ROOT / "desktop_qt_ui" / "ui" / "main_page" / "settings_tab_layout.json"
MODELS_PATH = ROOT / "desktop_qt_ui" / "core" / "config_models.py"
DYNAMIC_SETTINGS_PATH = ROOT / "desktop_qt_ui" / "ui" / "main_page" / "dynamic_settings.py"
APP_LOGIC_PATH = ROOT / "desktop_qt_ui" / "app_logic.py"
RELEASE_CONFIG_PATH = ROOT / "config" / "config-example.json"
CATALOG_PATH = WIKI_DIR / "phase0-ui-parameter-fields.json"

FIXED_ACTIONS = {
    "ocr.ai_ocr_prompt_path",
    "colorizer.ai_colorizer_prompt_path",
    "render.ai_renderer_prompt_path",
}
SPECIAL_CONTROLS = {
    "upscale.upscale_ratio": "dynamic-combo",
    "filter_text_enabled": "toggle + open-filter-list action",
    "render.font_family": "font-combo + open-font-directory action",
    "use_custom_api_params": "toggle + edit-custom-api-params action",
}
COMBO_KEYS = {
    "cli.format",
    "translator.translator",
    "translator.target_lang",
    "translator.keep_lang",
    "ocr.ocr",
    "ocr.secondary_ocr",
    "ocr.ocr_vl_language_hint",
    "detector.detector",
    "inpainter.inpainter",
    "inpainter.inpainting_precision",
    "render.renderer",
    "render.alignment",
    "render.direction",
    "render.layout_mode",
    "upscale.upscaler",
    "colorizer.colorizer",
}
CLASS_BY_PREFIX = {
    "app": "AppSection",
    "translator": "TranslatorSettings",
    "ocr": "OcrSettings",
    "detector": "DetectorSettings",
    "inpainter": "InpainterSettings",
    "render": "RenderSettings",
    "upscale": "UpscaleSettings",
    "colorizer": "ColorizerSettings",
    "cli": "CliSettings",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def get_dotted(mapping: dict, dotted_key: str):
    value = mapping
    for part in dotted_key.split("."):
        if not isinstance(value, dict) or part not in value:
            fail(f"release default missing {dotted_key}")
        value = value[part]
    return value


def layout_entries(layout: dict) -> list[tuple[str, str]]:
    entries = []
    for tab in layout["tabs"]:
        page = tab["title"]
        for item in tab["items"]:
            if isinstance(item, str):
                entries.append((item, page))
    return entries


def assigned_string_set(tree: ast.Module, name: str) -> set[str]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and value.args:
            value = value.args[0]
        if isinstance(value, (ast.Set, ast.Tuple, ast.List)):
            return {
                element.value
                for element in value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
    fail(f"could not read {name} from dynamic_settings.py")


def model_fields(tree: ast.Module) -> dict[str, set[str]]:
    result = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        fields = {
            child.target.id
            for child in node.body
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
        }
        if fields:
            result[node.name] = fields
    return result


def default_source(key: str) -> str:
    if "." not in key:
        symbol = f"AppSettings.{key}"
    else:
        prefix, field = key.split(".", 1)
        symbol = f"{CLASS_BY_PREFIX[prefix]}.{field}"
    return f"config/config-example.json (release) -> desktop_qt_ui/core/config_models.py#{symbol}"


def expected_control(key: str, value):
    if key in SPECIAL_CONTROLS:
        return SPECIAL_CONTROLS[key]
    if key in COMBO_KEYS:
        return "combo"
    if isinstance(value, bool):
        return "toggle"
    if isinstance(value, int):
        return "int-input"
    if isinstance(value, float):
        return "float-input"
    if value is None and key in {"render.font_size", "ocr.ocr_vl_custom_prompt"}:
        return "optional-input"
    if isinstance(value, str):
        return "text-input"
    fail(f"cannot classify rendered control {key}: {value!r}")


def main() -> int:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    release_defaults = json.loads(RELEASE_CONFIG_PATH.read_text(encoding="utf-8"))
    dynamic_tree = ast.parse(DYNAMIC_SETTINGS_PATH.read_text(encoding="utf-8"))
    models_tree = ast.parse(MODELS_PATH.read_text(encoding="utf-8"))
    app_logic = APP_LOGIC_PATH.read_text(encoding="utf-8")

    entries = layout_entries(layout)
    layout_keys = [key for key, _page in entries]
    if len(layout_keys) != 109 or len(set(layout_keys)) != 109:
        fail(f"expected 109 unique layout entries, got {len(layout_keys)} / {len(set(layout_keys))}")

    fixed_actions = assigned_string_set(dynamic_tree, "_FIXED_PROMPT_KEYS")
    if fixed_actions != FIXED_ACTIONS:
        fail(f"fixed prompt actions changed: {sorted(fixed_actions)}")
    optional_inputs = assigned_string_set(dynamic_tree, "_OPTIONAL_INPUT_KEYS")
    if "font_color" in optional_inputs:
        fail("font_color became an optional-input key; update the catalog")
    if get_dotted(release_defaults, "render.font_color") is not None:
        fail("release font_color is no longer null; update the catalog")

    classes = model_fields(models_tree)
    for key in layout_keys:
        if key in FIXED_ACTIONS:
            continue
        if "." in key:
            prefix, field = key.split(".", 1)
            class_name = CLASS_BY_PREFIX[prefix]
        else:
            class_name, field = "AppSettings", key
        if field not in classes.get(class_name, set()):
            fail(f"model field missing for {key}: {class_name}.{field}")
        get_dotted(release_defaults, key)

    for key in COMBO_KEYS:
        short_key = key.rsplit(".", 1)[-1]
        if f'"{short_key}"' not in app_logic:
            fail(f"no option/display mapping evidence for combo {key}")

    declared_pages = dict(entries)
    expected_visible = set(layout_keys) - {"render.font_color"}
    actual_fields = catalog["visible_fields"]
    actual_by_key = {field["key"]: field for field in actual_fields}
    if len(actual_by_key) != len(actual_fields):
        fail("catalog has duplicate visible field keys")
    if set(actual_by_key) != expected_visible:
        fail("catalog visible keys no longer match the rendered release-default keys")

    for key, record in actual_by_key.items():
        if record["page"] != declared_pages[key]:
            fail(f"page mismatch for {key}")
        if key in FIXED_ACTIONS:
            if record["control"] != "prompt-editor-button":
                fail(f"fixed prompt action control mismatch for {key}")
            continue
        expected_source = default_source(key)
        if record["default_source"] != expected_source:
            fail(f"default source mismatch for {key}")
        expected = expected_control(key, get_dotted(release_defaults, key))
        if record["control"] != expected:
            fail(f"control mismatch for {key}: {record['control']} != {expected}")

    excluded = catalog["layout_entries_not_visible_with_release_defaults"]
    if len(excluded) != 1 or excluded[0]["key"] != "render.font_color":
        fail("catalog must retain only render.font_color as the release-default excluded entry")
    if catalog["baseline_comparison"] != {
        "requested_baseline": 109,
        "layout_parameter_entries": 109,
        "visible_parameter_fields": 108,
        "difference_from_requested_baseline": -1,
        "difference_explanation": "The layout has 109 string entries, matching the updated baseline. Its render.font_color entry has a null release default and no None widget branch, leaving 108 visible settings rows (one below 109).",
    }:
        fail("baseline comparison changed; update the catalog and its validation")

    print("PASS: layout=109, fixed-actions=3, visible=108, baseline-delta=-1, excluded=render.font_color")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
