"""Launch with: uv run python desktop_qt_ui/agent_debug.py [--image PATH]"""

from __future__ import annotations

import logging
import os
import sys
import threading
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger("agent_debug")


class _ConsoleFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s [%(name)s] %(message)s", "%H:%M:%S")
        self._secrets = ()

    def add_secret(self, value):
        value = value.strip()
        if value and value not in self._secrets:
            self._secrets = (*self._secrets, value)

    def format(self, record):
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, "[redacted]")
        return text


def _configure_console_logging():
    formatter = _ConsoleFormatter()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)

    def report_uncaught(kind, error, tb):
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, error, tb)
            return
        logger.error("Unhandled exception", exc_info=(kind, error, tb))

    def report_thread(args):
        if args.exc_type is not SystemExit:
            logger.error("Unhandled thread exception: %s",
                         args.thread.name if args.thread is not None else "unknown",
                         exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = report_uncaught
    threading.excepthook = report_thread
    # The asyncio loop's default exception handler already uses logging.
    return formatter


def _report_task_failure(future):
    # run_coroutine_threadsafe stores errors on a Future, so neither global
    # exception hooks nor the loop's unhandled-task handler can see them.
    if future.cancelled():
        return
    error = future.exception()
    if error is not None:
        logger.error("Background task failed", exc_info=(type(error), error, error.__traceback__))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Standalone Agent debugger")
    parser.add_argument("--image", help="Load a page into the Agent editing workspace and follow backend renders")
    args, qt_args = parser.parse_known_args()
    formatter = _configure_console_logging()
    logger.info("Agent debugger started; logging to terminal")
    sys.path[:0] = [str(ROOT / "desktop_qt_ui"), str(ROOT)]
    os.environ.setdefault("MANGA_TRANSLATOR_ENV_PATH", str(ROOT / ".env"))
    # Windows requires torch before Qt so Qt's DLL paths cannot shadow c10.
    try:
        import torch  # noqa: F401
    except ImportError:
        pass
    with redirect_stdout(StringIO()):
        import qfluentwidgets  # noqa: F401
    from PyQt6.QtWidgets import QApplication

    from manga_translator.utils.system_proxy import set_system_proxy_enabled
    from services import ServiceContainer, ServiceManager
    from services.async_service import AsyncService
    from services.config_service import ConfigService
    from services.i18n_service import I18nManager
    from ui.agent.debug_window import AgentDebugWindow
    from ui.theme import apply_application_theme

    class DebugAsyncService(AsyncService):
        def submit_task(self, coro):
            future = super().submit_task(coro)
            if future is not None:
                future.add_done_callback(_report_task_failure)
            return future

    app = QApplication([sys.argv[0], *qt_args])
    # The reused ChatPage resolves its async runner through ServiceManager.
    # Install only debug dependencies; do not initialize translation/editor services.
    container = ServiceContainer(str(ROOT))
    ServiceManager._container = container
    config = None
    window = None
    try:
        config = ConfigService(str(ROOT))
        container.register_service("config", config)
        container.register_service("async", DebugAsyncService())
        settings = config.get_config().app
        i18n = I18nManager(
            locale_dir=str(ROOT / "desktop_qt_ui" / "locales"),
            fallback_locale="zh_CN",
            config_language=settings.ui_language,
        )
        container.register_service("i18n", i18n)
        set_system_proxy_enabled(bool(settings.use_system_proxy))
        apply_application_theme(settings.theme, app)
        window = AgentDebugWindow(config, i18n)
        formatter.add_secret(window.chat_page.key_input.text())
        window.chat_page.key_input.editingFinished.connect(
            lambda: formatter.add_secret(window.chat_page.key_input.text())
        )
        app.aboutToQuit.connect(window.shutdown)
        window.show()
        if args.image:
            window.load_file(args.image)
        return app.exec()
    finally:
        if window is not None:
            # Keep the asyncio service alive until its native render worker exits.
            cleanup = window.shutdown()
            if cleanup is not None:
                cleanup.result()
        if config is not None:
            config.shutdown()
        ServiceManager.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
