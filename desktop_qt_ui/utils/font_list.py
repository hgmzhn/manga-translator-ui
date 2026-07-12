"""Qt 字体数据库共享 helper。

与 BallonsTranslator 一致：系统字体和应用字体都按 family 展示；项目
``fonts/`` 中的文件仅负责注册进 Qt，不再把文件路径作为编辑器字体值。

注册统一走 ``text_render.register_font_file``：家族名以 ``[`` 开头的字体
（如 "[工具箱]xxx-简繁"）会被 Qt 的 "Family [Foundry]" 语法解析成空家族名，
QFont 匹配固定落到同一字体；注册层会自动改写为去掉方括号的内存副本。
"""
import logging
import os

from PyQt6.QtGui import QFont, QFontDatabase, QGuiApplication
from PyQt6.QtWidgets import QFontComboBox

from manga_translator.rendering.text_render import qt_family_is_ambiguous, register_font_file

from .resource_helper import resource_path

logger = logging.getLogger('manga_translator')

FONT_FILE_EXTENSIONS = ('.ttf', '.otf', '.ttc')
_REGISTERED_FONT_FAMILIES: dict[str, list[str]] = {}


def fonts_directory() -> str:
    """字体目录绝对路径（开发环境 = 项目根/fonts，打包后 = _MEIPASS/fonts）。"""
    return resource_path('fonts')


def list_font_files() -> list[tuple[str, str]]:
    """枚举项目字体文件，并在 Qt 已启动时将它们注册为应用字体。"""
    font_files: list[tuple[str, str]] = []
    try:
        fonts_dir = fonts_directory()
        if os.path.isdir(fonts_dir):
            for filename in os.listdir(fonts_dir):
                if filename.lower().endswith(FONT_FILE_EXTENSIONS):
                    font_files.append((os.path.splitext(filename)[0], filename))
                    path = os.path.normcase(os.path.abspath(os.path.join(fonts_dir, filename)))
                    if QGuiApplication.instance() is not None and path not in _REGISTERED_FONT_FAMILIES:
                        _REGISTERED_FONT_FAMILIES[path] = register_font_file(path)
    except OSError as exc:
        logger.warning(f"扫描字体目录失败: {exc}")
    font_files.sort(key=lambda item: (item[0].casefold(), item[1].casefold()))
    return font_files


def list_font_families() -> list[str]:
    """返回系统字体与已注册应用字体的 family 列表。

    过滤掉以 ``[`` 开头的家族名——它们经 Qt 的 foundry 语法解析后家族名为空，
    QFont 选中的永远是错误字体（系统级安装的工具箱字体会产生这类条目）。
    """
    if QGuiApplication.instance() is None:
        return []
    list_font_files()
    families = {
        name for name in QFontDatabase.families()
        if name and not qt_family_is_ambiguous(name)
    }
    return sorted(families, key=str.casefold)


def font_family_for_file(filename: str) -> str:
    """Return the first Qt family registered from a project font file."""
    if not filename or QGuiApplication.instance() is None:
        return ''
    list_font_files()
    path = os.path.normcase(os.path.abspath(os.path.join(fonts_directory(), filename)))
    families = _REGISTERED_FONT_FAMILIES.get(path) or []
    return families[0] if families else ''


def populate_font_combo(combo, current: str | None = None) -> None:
    """清空并填充字体下拉框：显示值和 userData 都是 Qt family。

    ``current`` 传当前 family 时选中对应条目；
    条目不在列表里则追加一条（userData 保留原值）再选中。
    """
    if isinstance(combo, QFontComboBox):
        list_font_families()
        combo.setFontFilters(QFontComboBox.FontFilter.ScalableFonts)
        if current:
            combo.setCurrentFont(QFont(current))
        return
    combo.clear()
    for family in list_font_families():
        combo.addItem(family, userData=family)
    if not current:
        return
    for index in range(combo.count()):
        item_data = combo.itemData(index)
        if item_data == current:
            combo.setCurrentIndex(index)
            return
    combo.addItem(current, userData=current)
    combo.setCurrentIndex(combo.count() - 1)
