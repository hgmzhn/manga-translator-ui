import _bootstrap  # noqa: F401

import subprocess
import sys


ROOT = _bootstrap.ROOT


def test_main_import_suppresses_qfluentwidgets_promotion():
    result = subprocess.run(
        [sys.executable, "-c", "import desktop_qt_ui.main"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "QFluentWidgets Pro" not in result.stdout
