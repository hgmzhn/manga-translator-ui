"""Launch with: uv run python desktop_qt_ui/agent_debug.py [--image PATH]"""

from __future__ import annotations

import os
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Standalone Agent debugger")
    parser.add_argument("--image", help="Load an image and its project sidecars into render preview")
    args, qt_args = parser.parse_known_args()
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
        container.register_service("async", AsyncService())
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
