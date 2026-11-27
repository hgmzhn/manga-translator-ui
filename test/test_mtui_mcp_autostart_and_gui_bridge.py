"""End-to-end tests for embedded MCP HTTP/SSE auto-start, Qt live GUI bridge, settings/locales, and CLI modes."""

import _bootstrap  # noqa: F401, I001

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import types
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

from core.config_models import AppSection
from desktop_qt_ui.main import parse_desktop_mcp_args
from editor.document_state import DocumentSnapshot
from editor.editor_model import EditorModel
from mtui_mcp.embedded_server import EmbeddedMcpHttpServer
from mtui_mcp.gui_bridge import call_live_gui, gui_session_paths, is_live_gui_session_available
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import save_page_json_atomic
from mtui_mcp.server import mtui_live_gui_session
from services.config_service import ConfigService
from services.i18n_service import I18nManager
from services.mcp_service import McpGuiBridgeServer, McpService

_QAPP = QApplication.instance() or QApplication([])
ROOT = _bootstrap.ROOT
LOCALE_DIR = ROOT / "desktop_qt_ui" / "locales"
LOCALES = ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES")


def _import_streamable_http_client():
    try:
        from mcp.client.streamable_http import streamable_http_client

        return streamable_http_client
    except ImportError:
        from mcp.client.streamable_http import streamablehttp_client

        return streamablehttp_client


@pytest.mark.asyncio
async def test_embedded_mcp_http_server_health_streamable_http_sse_and_port_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify EmbeddedMcpHttpServer(port=0) `/health`, Streamable HTTP `/mcp`, SSE `/sse`, port-in-use False, and stop()."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    for proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(proxy_key, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    streamable_client = _import_streamable_http_client()
    srv = EmbeddedMcpHttpServer(host="127.0.0.1", port=0, mtui_root=ROOT, warm_up=False)
    try:
        assert srv.start(timeout=5.0) is True
        assert srv.is_running is True
        assert srv.bound_port is not None and srv.bound_port > 0

        # 1. GET /health check
        with urllib.request.urlopen(srv.health_url, timeout=5.0) as resp:
            assert resp.status == 200
            health = json.loads(resp.read().decode("utf-8"))
        assert health["status"] == "ok"
        assert health["server"] == "mtui-mcp"
        assert health["port"] == srv.bound_port
        assert health["endpoints"]["streamable_http"] == "/mcp"
        assert health["endpoints"]["sse"] == "/sse"

        # 2. Real MCP Streamable HTTP handshake + 24 tools + 2 prompts + mtui_status
        async with streamable_client(srv.streamable_http_url) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                init_res = await session.initialize()
                srv_info = getattr(init_res, "server_info", None) or getattr(init_res, "serverInfo")
                assert srv_info.name == "mtui-mcp"

                tools_res = await session.list_tools()
                tool_names = {t.name for t in tools_res.tools}
                assert len(tool_names) == 24
                assert "mtui_status" in tool_names
                assert "mtui_live_gui_session" in tool_names

                prompts_res = await session.list_prompts()
                assert {p.name for p in prompts_res.prompts} == {
                    "proofread_and_polish_chapter",
                    "inspect_and_fix_page",
                }

                status_call = await session.call_tool("mtui_status", {"warm_up_worker": False})
                assert not getattr(status_call, "is_error", getattr(status_call, "isError", False))
                status_payload = json.loads(status_call.content[0].text)
                assert "engine_available" in status_payload

        # 3. Real MCP SSE handshake + 24 tools + mtui_status
        async with sse_client(srv.sse_url) as (sse_read, sse_write):
            async with ClientSession(sse_read, sse_write) as session:
                init_sse = await session.initialize()
                srv_info_sse = getattr(init_sse, "server_info", None) or getattr(init_sse, "serverInfo")
                assert srv_info_sse.name == "mtui-mcp"

                sse_tools = await session.list_tools()
                assert len(sse_tools.tools) == 24

                sse_status = await session.call_tool("mtui_status", {"warm_up_worker": False})
                assert not getattr(sse_status, "is_error", getattr(sse_status, "isError", False))
                assert "engine_available" in json.loads(sse_status.content[0].text)

        # 4. Port-in-use conflict returns False gracefully without crashing or disturbing active server
        conflict_srv = EmbeddedMcpHttpServer(host="127.0.0.1", port=srv.bound_port, mtui_root=ROOT, warm_up=False)
        assert conflict_srv.start(timeout=2.0) is False
        assert conflict_srv.is_running is False
        assert srv.is_running is True
    finally:
        srv.stop()

    # 5. Clean idempotent stop()
    assert srv.is_running is False
    assert srv.bound_port is None
    srv.stop()


class _GuiHistoryStub:
    def __init__(self, model: EditorModel) -> None:
        self._model = model
        self._undo_stack: list[list[dict[str, Any]]] = []
        self._redo_stack: list[list[dict[str, Any]]] = []
        self._rev = 0
        self._clean_rev = 0

    def record_edit(self, before_regions: list[dict[str, Any]]) -> None:
        self._undo_stack.append([dict(r) for r in before_regions])
        self._redo_stack.clear()
        self._rev += 1

    def current_revision(self) -> int:
        return self._rev

    def is_clean(self) -> bool:
        return self._rev == self._clean_rev

    def mark_clean(self) -> None:
        self._clean_rev = self._rev

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def step_undo(self) -> None:
        cur = [dict(r) for r in (self._model.get_regions() or [])]
        prev = self._undo_stack.pop()
        self._redo_stack.append(cur)
        self._model.replace_regions(prev)
        self._rev += 1

    def step_redo(self) -> None:
        cur = [dict(r) for r in (self._model.get_regions() or [])]
        nxt = self._redo_stack.pop()
        self._undo_stack.append(cur)
        self._model.replace_regions(nxt)
        self._rev += 1


def _run_in_worker_pumping_qt(fn):
    """Run blocking IPC client callable on a background thread while pumping Qt events on the main thread."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn)
        deadline = time.monotonic() + 15.0
        while not fut.done() and time.monotonic() < deadline:
            _QAPP.processEvents()
            time.sleep(0.005)
        _QAPP.processEvents()
        return fut.result(timeout=2.0)


def test_mcp_gui_bridge_server_qt_main_thread_roundtrip_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify McpGuiBridgeServer + gui_bridge.py / mtui_live_gui_session roundtrip on Qt main thread and clean pipe/key teardown."""
    session_name = f"pytest-gui-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MTUI_MCP_SESSION", session_name)

    chapter_dir = tmp_path / "live_gui_ch"
    chapter_dir.mkdir(parents=True)
    p1 = chapter_dir / "001.png"
    p2 = chapter_dir / "002.png"
    img1 = np.full((200, 160, 3), 240, dtype=np.uint8)
    img2 = np.full((220, 180, 3), 230, dtype=np.uint8)
    Image.fromarray(img1).save(p1)
    Image.fromarray(img2).save(p2)

    for page_p, src_txt, tr_txt in ((p1, "一頁目", "第一页台词"), (p2, "二頁目", "第二页台词")):
        pp = resolve_page_paths(page_p)
        save_page_json_atomic(
            pp,
            str(page_p.resolve()),
            {
                "regions": [
                    {
                        "lines": [[[20.0, 20.0], [100.0, 20.0], [100.0, 120.0], [20.0, 120.0]]],
                        "center": [60.0, 70.0],
                        "text": src_txt,
                        "translation": tr_txt,
                        "font_family": "FZHei-B01",
                        "font_size": 24,
                        "direction": "v",
                        "alignment": "left",
                    }
                ]
            },
            auto_initial_snapshot=False,
        )

    observed_threads: list[QThread] = []

    class _TrackedEditorModel(EditorModel):
        def get_regions(self):
            observed_threads.append(QThread.currentThread())
            return super().get_regions()

    editor_model = _TrackedEditorModel()
    editor_model.apply_document_snapshot(
        DocumentSnapshot(
            source_path=str(p1.resolve()),
            image=img1,
            display_image_path=str(p1.resolve()),
            regions=[
                {
                    "lines": [[[20.0, 20.0], [100.0, 20.0], [100.0, 120.0], [20.0, 120.0]]],
                    "center": [60.0, 70.0],
                    "text": "一頁目",
                    "translation": "第一页台词",
                    "font_family": "FZHei-B01",
                    "font_size": 24,
                    "direction": "v",
                    "alignment": "left",
                }
            ],
        )
    )
    history = _GuiHistoryStub(editor_model)
    saved_paths: list[str] = []
    exported_flags: list[bool] = []
    aux_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    class _DocServiceStub:
        def __init__(self, controller: Any) -> None:
            self.controller = controller
            self._aux_load_executor = aux_executor

        @staticmethod
        def resolve_editor_image_paths(candidate_path: str) -> tuple[str, str]:
            norm = str(Path(candidate_path).resolve())
            return norm, norm

        @staticmethod
        def _auto_save_on_switch_enabled() -> bool:
            return True

        @staticmethod
        def _auto_export_on_switch_enabled() -> bool:
            return False

        def clear_editor_state(self) -> None:
            editor_model.clear_document()

        def apply_loaded_data_to_model(self, snapshot: DocumentSnapshot) -> None:
            editor_model.apply_document_snapshot(snapshot)
            history.mark_clean()

    class _ControllerStub:
        def __init__(self) -> None:
            self.history_service = history
            self.export_service = types.SimpleNamespace(
                unfinished_count=lambda: 0,
                has_unsaved_changes=lambda: not history.is_clean(),
            )
            self.document_service = _DocServiceStub(self)

        def commit_pending_edits(self) -> None:
            pass

        def undo(self) -> None:
            history.step_undo()

        def redo(self) -> None:
            history.step_redo()

        def save_editor_state(self) -> bool:
            src = editor_model.get_source_image_path()
            assert src is not None
            saved_paths.append(src)
            history.mark_clean()
            return True

        def export_image(self, automatic: bool = False) -> object:
            exported_flags.append(automatic)
            return object()

    def _fake_doc_worker_load(worker_self):
        src = Path(worker_self.image_path)
        arr = np.asarray(Image.open(src).convert("RGB"), dtype=np.uint8)
        pp = resolve_page_paths(src)
        raw = json.loads(pp.json_path.read_text(encoding="utf-8"))
        img_data = next(iter(raw.values()))
        return DocumentSnapshot(
            source_path=str(src.resolve()),
            image=arr,
            display_image_path=str(src.resolve()),
            regions=img_data.get("regions", []),
        )

    monkeypatch.setattr("editor.document_load_worker.DocumentLoadWorker.load", _fake_doc_worker_load)

    controller = _ControllerStub()
    translating_flag = {"running": True}
    app_logic = types.SimpleNamespace(
        source_files=[str(p1.resolve()), str(p2.resolve())],
        state_manager=types.SimpleNamespace(is_translating=lambda: translating_flag["running"]),
        current_worker=None,
    )

    bridge = McpGuiBridgeServer(
        app_logic=app_logic,
        editor_controller=controller,
        editor_model=editor_model,
        session_name=session_name,
    )
    expected_addr, expected_key = gui_session_paths(session_name)

    try:
        assert bridge.start() is True
        assert bridge.is_running is True
        assert bridge.address == expected_addr
        assert bridge.key_file == expected_key
        assert expected_key.is_file()
        assert len(expected_key.read_bytes()) == 32

        # Second bridge on the same live session must detect active peer and return False gracefully
        dup_bridge = McpGuiBridgeServer(
            app_logic=app_logic,
            editor_controller=controller,
            editor_model=editor_model,
            session_name=session_name,
        )
        assert _run_in_worker_pumping_qt(dup_bridge.start) is False

        # 1. `mtui_live_gui_session(action="status")` + `list_pages` from worker thread -> marshaled onto Qt main thread!
        observed_threads.clear()
        status_json = _run_in_worker_pumping_qt(lambda: json.loads(mtui_live_gui_session(action="status")))
        assert status_json["live_gui_connected"] is True
        gui_state = status_json["gui_state"]
        assert gui_state["status"] == "ok"
        assert gui_state["total_pages"] == 2
        assert gui_state["translation_running"] is True
        assert os.path.normcase(gui_state["active_page"]) == os.path.normcase(str(p1.resolve()))
        assert observed_threads and all(t == _QAPP.thread() for t in observed_threads)

        # 2. `wait_for_translation` polls until `translation_running` flips False
        def _finish_translation_soon() -> None:
            time.sleep(0.15)
            translating_flag["running"] = False

        threading.Thread(target=_finish_translation_soon, daemon=True).start()
        waited = _run_in_worker_pumping_qt(
            lambda: json.loads(mtui_live_gui_session(action="wait_for_translation", timeout_seconds=5))
        )
        assert waited["translation_running"] is False

        # 3. `read_page` + live edit + `undo` + `redo` + `save_page` (including revision guard)
        read1 = _run_in_worker_pumping_qt(lambda: call_live_gui("read_page", session_name=session_name))
        assert read1["page_open"] is True
        assert read1["regions"][0]["translation"] == "第一页台词"
        rev_initial = read1["revision"]

        # Simulate user editing region 0 on the Qt main thread
        before_regs = editor_model.get_regions()
        history.record_edit(before_regs)
        editor_model.update_region(0, {**before_regs[0], "translation": "GUI新改台词"})

        # Stale revision must be rejected on undo
        with pytest.raises(RuntimeError, match="revision mismatch"):
            _run_in_worker_pumping_qt(
                lambda: json.loads(mtui_live_gui_session(action="undo", revision=rev_initial))
            )

        undo_res = _run_in_worker_pumping_qt(lambda: json.loads(mtui_live_gui_session(action="undo")))
        assert undo_res["applied"] is True
        assert editor_model.get_regions()[0]["translation"] == "第一页台词"

        redo_res = _run_in_worker_pumping_qt(lambda: json.loads(mtui_live_gui_session(action="redo")))
        assert redo_res["applied"] is True
        assert editor_model.get_regions()[0]["translation"] == "GUI新改台词"
        assert redo_res["dirty"] is True

        save_res = _run_in_worker_pumping_qt(lambda: json.loads(mtui_live_gui_session(action="save_page")))
        assert save_res["saved"] is True
        assert save_res["dirty"] is False
        assert len(saved_paths) == 1

        # 4. `open_page` switching live canvas to `002.png`
        opened = _run_in_worker_pumping_qt(
            lambda: json.loads(mtui_live_gui_session(action="open_page", path=str(p2)))
        )
        assert opened["page_open"] is True
        assert opened["page_name"] == "002.png"
        assert opened["regions"][0]["translation"] == "第二页台词"
    finally:
        bridge.stop()
        aux_executor.shutdown(wait=True)

    # 5. Clean shutdown removes key file and reports disconnected cleanly
    assert bridge.is_running is False
    assert not expected_key.exists()
    assert is_live_gui_session_available(session_name) is False
    disconnected = json.loads(mtui_live_gui_session(action="status"))
    assert disconnected == {"live_gui_connected": False, "gui_state": None}


def test_mcp_service_config_defaults_locales_cli_args_and_live_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify AppSection/config-example/6-locale sync, ConfigService persistence, CLI overrides, and McpService live toggle."""
    # 1. AppSection defaults & config-example.json & settings_tab_layout.json
    app_defaults = AppSection()
    assert app_defaults.mcp_server_enabled is True
    assert app_defaults.mcp_server_host == "127.0.0.1"
    assert app_defaults.mcp_server_port == 8765

    example_cfg = json.loads((ROOT / "config" / "config-example.json").read_text(encoding="utf-8"))
    assert example_cfg["app"]["mcp_server_enabled"] is True
    assert example_cfg["app"]["mcp_server_host"] == "127.0.0.1"
    assert example_cfg["app"]["mcp_server_port"] == 8765

    layout = json.loads(
        (ROOT / "desktop_qt_ui" / "ui" / "main_page" / "settings_tab_layout.json").read_text(encoding="utf-8")
    )
    assert sum(tab["items"].count("app.mcp_server_enabled") for tab in layout["tabs"]) == 1
    assert sum(tab["items"].count("app.mcp_server_port") for tab in layout["tabs"]) == 1

    # 2. All 6 locale files translate MCP labels & descriptions
    for locale in LOCALES:
        catalog = json.loads((LOCALE_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        mgr = I18nManager(locale_dir=str(LOCALE_DIR), fallback_locale="zh_CN", config_language=locale)
        for key in (
            "label_mcp_server_enabled",
            "desc_app_mcp_server_enabled",
            "label_mcp_server_port",
            "desc_app_mcp_server_port",
        ):
            assert catalog.get(key, "").strip(), (locale, key)
            assert mgr.translate(key) == catalog[key], (locale, key)

    # 3. ConfigService roundtrip & clean example-payload reset in temp root
    temp_root = tmp_path / "cfg_root"
    (temp_root / "config").mkdir(parents=True)
    shutil.copy2(ROOT / "config" / "config-example.json", temp_root / "config" / "config-example.json")
    cfg_svc = ConfigService(str(temp_root))
    try:
        loaded = cfg_svc.get_config()
        assert loaded.app.mcp_server_enabled is True
        assert loaded.app.mcp_server_port == 8765

        loaded.app.mcp_server_enabled = False
        loaded.app.mcp_server_port = 9123
        cfg_svc.set_config(loaded)
        assert cfg_svc.save_config_file() is True
        assert cfg_svc.flush_pending_writes() is True

        user_saved = json.loads((temp_root / "config" / "config.json").read_text(encoding="utf-8"))
        example_saved = json.loads((temp_root / "config" / "config-example.json").read_text(encoding="utf-8"))
        assert user_saved["app"]["mcp_server_enabled"] is False
        assert user_saved["app"]["mcp_server_port"] == 9123
        # Example template must keep clean release defaults!
        assert example_saved["app"]["mcp_server_enabled"] is True
        assert example_saved["app"]["mcp_server_port"] == 8765
    finally:
        cfg_svc.shutdown()

    # 4. Desktop CLI arg parser preserves unknown Qt args
    parsed_on, qt_rest = parse_desktop_mcp_args(["--mcp", "--mcp-host", "127.0.0.1", "--mcp-port", "8899", "-style", "fusion"])
    assert parsed_on.mcp_enabled is True
    assert parsed_on.mcp_host == "127.0.0.1"
    assert parsed_on.mcp_port == 8899
    assert qt_rest == ["-style", "fusion"]

    parsed_off, _ = parse_desktop_mcp_args(["--no-mcp"])
    assert parsed_off.mcp_enabled is False

    # 5. McpService pytest suppression guard + live start / toggle off / toggle on / shutdown
    session_name = f"pytest-svc-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    monkeypatch.delenv("MTUI_MCP_FORCE_AUTOSTART", raising=False)
    monkeypatch.delenv("MTUI_MCP_ENABLED", raising=False)

    svc = McpService(str(ROOT), session_name=session_name)
    try:
        assert svc.is_autostart_suppressed_by_pytest() is True
        assert svc.autostart() is False

        monkeypatch.setenv("MTUI_MCP_FORCE_AUTOSTART", "1")
        assert svc.is_autostart_suppressed_by_pytest() is False
        monkeypatch.delenv("MTUI_MCP_FORCE_AUTOSTART", raising=False)

        assert svc.start(host="127.0.0.1", port=0) is True
        assert svc.is_running is True
        assert svc.http_running is True
        assert svc.gui_bridge_running is True
        assert svc.bound_port is not None and svc.bound_port > 0
        key_path = svc.key_file
        assert key_path is not None and key_path.is_file()

        with urllib.request.urlopen(svc.health_url, timeout=5.0) as resp:
            assert resp.status == 200

        # Live toggle OFF via `apply_config(enabled=False)`
        assert svc.apply_config(enabled=False) is False
        assert svc.is_running is False
        assert svc.http_running is False
        assert svc.gui_bridge_running is False
        assert not key_path.exists()

        # Live toggle ON via `apply_config(enabled=True, port=0)`
        assert svc.apply_config(enabled=True, host="127.0.0.1", port=0) is True
        assert svc.http_running is True
        assert svc.gui_bridge_running is True
    finally:
        svc.shutdown()

    assert svc.is_running is False
    _, final_key = gui_session_paths(session_name)
    assert not final_key.exists()


def test_cli_entry_points_help_smoke() -> None:
    """Verify `python -m mtui_mcp --help`, `python -m manga_translator mcp --help`, and root `--help`."""
    res_mcp_pkg = subprocess.run(
        [sys.executable, "-m", "mtui_mcp", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "--transport" in res_mcp_pkg.stdout
    assert "--host" in res_mcp_pkg.stdout
    assert "--port" in res_mcp_pkg.stdout
    assert "8765" in res_mcp_pkg.stdout

    res_mt_mcp = subprocess.run(
        [sys.executable, "-m", "manga_translator", "mcp", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "--transport" in res_mt_mcp.stdout
    assert "--host" in res_mt_mcp.stdout
    assert "--port" in res_mt_mcp.stdout

    res_mt_root = subprocess.run(
        [sys.executable, "-m", "manga_translator", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "mcp" in res_mt_root.stdout
