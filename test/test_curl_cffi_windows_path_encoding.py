import _bootstrap  # noqa: F401

import ctypes
import os

import pytest

from manga_translator.utils.curl_cffi_transport import _encode_curl_file_path


@pytest.mark.skipif(os.name != "nt", reason="Windows ACP behavior only")
def test_curl_file_path_uses_windows_acp_under_utf8_mode():
    acp = ctypes.windll.kernel32.GetACP()
    encoding = f"cp{acp}"
    path = r"C:\测试目录\cacert.pem"

    try:
        expected = path.encode(encoding)
    except UnicodeEncodeError:
        pytest.skip(f"test path is not representable in the active code page: {encoding}")

    encoded = _encode_curl_file_path(path)

    assert isinstance(encoded, bytes)
    assert encoded == expected
