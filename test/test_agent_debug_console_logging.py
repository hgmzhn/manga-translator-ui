import _bootstrap  # noqa: F401

import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_console_reports_background_thread_and_uncaught_errors_without_file_handlers():
    # Global hooks and logging handlers must stay isolated from the test runner.
    code = '''
import _bootstrap
import asyncio
import logging
import sys
import threading
from concurrent.futures import Future
from desktop_qt_ui.agent_debug import _configure_console_logging, _report_task_failure

formatter = _configure_console_logging()
formatter.add_secret("debug-test-secret")
assert all(type(handler) is logging.StreamHandler for handler in logging.getLogger().handlers)

def fail():
    raise TypeError("None is not iterable: debug-test-secret")

future = Future()
future.add_done_callback(_report_task_failure)
try:
    fail()
except TypeError as error:
    future.set_exception(error)
try:
    future.result()
except TypeError as error:
    assert "debug-test-secret" in str(error)  # Logging must not alter the exception.
else:
    raise AssertionError("The original error was lost")

cancelled = Future()
cancelled.add_done_callback(_report_task_failure)
cancelled.cancel()
successful = Future()
successful.add_done_callback(_report_task_failure)
successful.set_result("ok")
assert successful.result() == "ok"

try:
    fail()
except TypeError:
    sys.excepthook(*sys.exc_info())

thread = threading.Thread(target=fail, name="diagnostic-test")
thread.start()
thread.join()

async def detached():
    raise RuntimeError("detached-async-error")

async def run():
    task = asyncio.create_task(detached())
    await asyncio.sleep(0)
    del task

asyncio.run(run())
print("console diagnostics verified")
'''
    result = subprocess.run(
        ["uv", "run", "python", "-"], input=code, text=True, encoding="utf-8",
        capture_output=True, cwd=_bootstrap.ROOT, timeout=60,
        env={**os.environ, "PYTHONPATH": str(_bootstrap.ROOT / "test"), "PYTHONUTF8": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert "console diagnostics verified" in result.stdout
    assert result.stderr.count("Background task failed") == 1
    assert "Unhandled exception" in result.stderr
    assert "Unhandled thread exception: diagnostic-test" in result.stderr
    assert "Task exception was never retrieved" in result.stderr
    assert "detached-async-error" in result.stderr
    assert "Traceback (most recent call last)" in result.stderr
    assert "in fail" in result.stderr
    assert "debug-test-secret" not in result.stderr
    assert "[redacted]" in result.stderr


def main():
    return pytest.main([str(Path(__file__).resolve()), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
