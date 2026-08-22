import _bootstrap  # noqa: F401, I001

import asyncio
from types import SimpleNamespace

import pytest
from PIL import Image

from manga_translator.upscaling import esrgan, waifu2x


@pytest.mark.parametrize(
    ("module", "upscaler_class", "runner_name"),
    [
        (esrgan, esrgan.ESRGANUpscaler, "_run_esrgan_executable"),
        (waifu2x, waifu2x.Waifu2xUpscaler, "_run_waifu2x_executable"),
    ],
)
def test_upscaler_removes_temp_directories_when_process_fails(
    tmp_path, monkeypatch, module, upscaler_class, runner_name
):
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    created_dirs = []

    def fake_mkdtemp():
        directory = temp_root / f"tmp{len(created_dirs)}"
        directory.mkdir()
        created_dirs.append(directory)
        return str(directory)

    def fail_runner(*_args):
        raise RuntimeError("upscaler failed")

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    upscaler = object.__new__(upscaler_class)
    upscaler.logger = SimpleNamespace(warning=lambda _message: None)
    monkeypatch.setattr(upscaler, runner_name, fail_runner)

    image = Image.new("RGB", (2, 2), "white")
    result = asyncio.run(upscaler._infer([image], 2))

    assert result == [image]
    assert created_dirs
    assert list(temp_root.iterdir()) == []
