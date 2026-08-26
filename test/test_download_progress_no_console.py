import _bootstrap  # noqa: F401, I001

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from manga_translator.utils import generic


class _Response:
    ok = True
    status_code = 200

    def __init__(self, payload: bytes):
        self._payload = payload
        self.headers = {"content-length": str(len(payload))}

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self._payload), chunk_size):
            yield self._payload[offset : offset + chunk_size]

    def close(self):
        pass


def _exercise_unicode_path_download(root: Path):
    payload = b"model-data" * 128
    destination = root / "中文目录" / "模型.bin"
    destination.parent.mkdir()

    with (
        patch.object(generic.requests, "get", return_value=_Response(payload)),
        patch.object(generic, "sys", SimpleNamespace(stdout=None, stderr=None)),
    ):
        generic.download_url_with_progressbar(
            "https://example.invalid/model.bin",
            str(destination),
        )

    assert destination.read_bytes() == payload


def test_download_writes_unicode_path_without_console_streams(tmp_path):
    _exercise_unicode_path_download(tmp_path)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="漫画下载测试-") as temp_dir:
        _exercise_unicode_path_download(Path(temp_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
