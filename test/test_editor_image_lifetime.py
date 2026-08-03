"""底图生命周期回归。

背景：底图 PIL 对象同时被 ResourceManager 缓存、EditorSession、导出快照持有。
历史上有三套各自为政的 close 策略，其中 LRU 淘汰分支没有排除当前页，导致
来回翻页时当前页的图被后台预读线程关闭，之后自动导出崩在
`ValueError: Operation on closed image`，用户切不了图只能重启。

现在的约定：图以 eager 方式打开（不持有文件句柄），任何一方都只丢引用、
不 close，由引用计数回收。下面的用例锁定这个约定。

直接运行：uv run python test/test_editor_image_lifetime.py
"""

import os
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "desktop_qt_ui"))

from editor.core.resource_manager import ResourceManager  # noqa: E402
from editor.session import EditorSession  # noqa: E402


def _write_image(directory: Path, name: str, shade: int) -> str:
    path = directory / name
    Image.new("RGB", (32, 24), (shade, 64, 128)).save(path)
    return str(path)


def _assert_usable(image, label: str) -> None:
    """能 copy 就说明没被 close——closed image 会抛 ValueError。"""
    assert image is not None, f"{label}: image is None"
    duplicate = image.copy()
    assert duplicate.size == image.size, label


def test_evicted_images_stay_usable_for_other_holders(tmp_path):
    """被 LRU 淘汰的图，其它持有者（如 model）手里的引用必须仍然可用。"""
    manager = ResourceManager()
    paths = [_write_image(tmp_path, f"page{i}.png", i * 20) for i in range(manager._cache_limit + 3)]

    held = []
    for path in paths:
        resource = manager.load_image(path)
        held.append(resource.image)  # 模拟 session/model 直接持有同一个 PIL 对象

    assert len(manager._image_cache) <= manager._cache_limit
    for index, image in enumerate(held):
        _assert_usable(image, f"held[{index}] after eviction")


def test_current_image_survives_revisit_then_prefetch(tmp_path):
    """复现用户日志的翻页序列：回头翻页后当前页不得被自己触发的预读挤掉。"""
    manager = ResourceManager()
    paths = [_write_image(tmp_path, f"page{i}.png", i * 15) for i in range(manager._cache_limit + 2)]

    for path in paths[: manager._cache_limit]:
        manager.load_image(path)

    # 回头翻到第一页：命中缓存，它是最早加载的那张
    revisited = manager.load_image(paths[0])
    assert manager.get_current_image() is revisited

    # 切图后会预读相邻页，这里引入两张新图逼出淘汰
    manager.prefetch_image(paths[-2])
    manager.prefetch_image(paths[-1])

    assert manager.get_current_image() is revisited, "当前页资源被换掉了"
    assert paths[0] in manager._image_cache, "当前页被淘汰出缓存"
    _assert_usable(revisited.image, "current image after revisit + prefetch")


def test_clear_cache_does_not_kill_base_image(tmp_path):
    """底图可能被塞进 temp_cache（如充当修复图），清缓存不得波及它。"""
    manager = ResourceManager()
    base = manager.load_image(_write_image(tmp_path, "base.png", 90)).image

    manager.set_cache("inpainted_image", base)
    manager.clear_cache()

    _assert_usable(base, "base image after clear_cache")


def test_session_set_image_does_not_close_previous(tmp_path):
    """换图时 session 只丢引用，旧图对其它持有者仍然可用。"""
    manager = ResourceManager()
    session = EditorSession(manager)

    first = manager.load_image(_write_image(tmp_path, "first.png", 30)).image
    session.set_image(first)
    second = manager.load_image(_write_image(tmp_path, "second.png", 60)).image
    session.set_image(second)

    _assert_usable(first, "previous image after set_image")
    _assert_usable(second, "current image after set_image")


def test_unload_image_keeps_external_reference_usable(tmp_path):
    """卸载文档后，导出任务等外部持有者手里的快照来源仍然可用。"""
    manager = ResourceManager()
    base = manager.load_image(_write_image(tmp_path, "page.png", 45)).image

    manager.unload_image(release_from_cache=True)

    _assert_usable(base, "image after unload_image")


def test_loaded_image_holds_no_file_handle(tmp_path):
    """eager 打开后不占文件句柄：Windows 上能直接替换源文件。"""
    manager = ResourceManager()
    path = _write_image(tmp_path, "page.png", 120)
    resource = manager.load_image(path)

    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (32, 24), (0, 200, 0)).save(replacement)
    os.replace(replacement, path)  # 仍持有句柄的话这里会抛 PermissionError

    _assert_usable(resource.image, "image after source file replaced")


def main() -> int:
    import tempfile

    failures = 0
    tests = [
        test_evicted_images_stay_usable_for_other_holders,
        test_current_image_survives_revisit_then_prefetch,
        test_clear_cache_does_not_kill_base_image,
        test_session_set_image_does_not_close_previous,
        test_unload_image_keeps_external_reference_usable,
        test_loaded_image_holds_no_file_handle,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory() as directory:
            try:
                test(Path(directory))
            except Exception as error:  # noqa: BLE001
                failures += 1
                print(f"FAIL {test.__name__}: {error}")
            else:
                print(f"ok   {test.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
