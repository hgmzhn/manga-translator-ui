"""fonts/ 目录字体枚举的共享 helper（审查 F15）。

此前 property_panel、main_page/layout、main_page/dynamic_settings、
rich_text_floating_editor 各自复制了一份 os.listdir + 扩展名过滤，
行为已开始分叉（排序键不一致）；统一收口到这里：

- 过滤 .ttf/.otf/.ttc
- 显示名 = 去扩展名文件名
- 按显示名（忽略大小写）排序
- 下拉框条目 userData = 文件名
"""
import logging
import os

from .resource_helper import resource_path

logger = logging.getLogger('manga_translator')

FONT_FILE_EXTENSIONS = ('.ttf', '.otf', '.ttc')


def fonts_directory() -> str:
    """字体目录绝对路径（开发环境 = 项目根/fonts，打包后 = _MEIPASS/fonts）。"""
    return resource_path('fonts')


def list_font_files() -> list[tuple[str, str]]:
    """枚举 fonts/ 目录下的字体文件，返回 (显示名, 文件名) 列表。

    显示名为去扩展名的文件名；按显示名（忽略大小写）排序；
    目录不存在或不可读时返回空列表。
    """
    font_files: list[tuple[str, str]] = []
    try:
        fonts_dir = fonts_directory()
        if os.path.isdir(fonts_dir):
            for filename in os.listdir(fonts_dir):
                if filename.lower().endswith(FONT_FILE_EXTENSIONS):
                    font_files.append((os.path.splitext(filename)[0], filename))
    except OSError as exc:
        logger.warning(f"扫描字体目录失败: {exc}")
    font_files.sort(key=lambda item: (item[0].lower(), item[1].lower()))
    return font_files


def populate_font_combo(combo, current: str | None = None) -> None:
    """清空并填充字体下拉框：条目显示名 = 去扩展名，userData = 文件名。

    ``current`` 传当前字体（文件名或含路径均可）时选中对应条目；
    条目不在列表里则追加一条（userData 保留原值）再选中。
    """
    combo.clear()
    for display_name, filename in list_font_files():
        combo.addItem(display_name, userData=filename)
    if not current:
        return
    basename = os.path.basename(current)
    for index in range(combo.count()):
        item_data = combo.itemData(index)
        if item_data == current or item_data == basename:
            combo.setCurrentIndex(index)
            return
    combo.addItem(os.path.splitext(basename)[0] or basename, userData=current)
    combo.setCurrentIndex(combo.count() - 1)
