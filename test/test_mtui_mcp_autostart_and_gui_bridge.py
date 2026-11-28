"""End-to-end tests for embedded MCP HTTP/SSE auto-start, Qt live GUI bridge, settings/locales, and CLI modes."""

import _bootstrap  # noqa: F401, I001

import concurrent.futures
import inspect
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.request
import uuid
from multiprocessing.connection import Client, Listener, answer_challenge, deliver_challenge
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

from app_logic import MainAppLogic
from core.config_models import AppSection
from desktop_qt_ui.main import dispatch_internal_subcommand, parse_desktop_mcp_args
from editor.core.resource_manager import ResourceManager
from editor.document_state import DocumentLoadFailure, DocumentSnapshot
from editor.editor_model import EditorModel
from mtui_mcp.__main__ import _run_mcp_transport
from mtui_mcp.config import _discover_mtui_python, _discover_mtui_root, get_runtime_settings
from mtui_mcp.embedded_server import EmbeddedMcpHttpServer, is_loopback_host
from mtui_mcp.gui_bridge import (
    MAX_GUI_REQUEST_BYTES,
    abort_ipc_connection,
    call_live_gui,
    gui_session_paths,
    is_live_gui_session_available,
    wait_for_live_gui_translation,
)
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import page_edit_lock, save_page_json_atomic
from mtui_mcp.server import mtui_live_gui_session
from services import ServiceContainer, ServiceManager
from services.config_service import ConfigService
from services.i18n_service import I18nManager
from services.mcp_service import (
    McpGuiBridgeServer,
    McpService,
    _acquire_session_lockfile,
    _MainThreadDispatcher,
    _release_session_lockfile,
)

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

    rt_settings = get_runtime_settings()
    monkeypatch.setattr(rt_settings, "allow_runtime_python_override", True)

    streamable_client = _import_streamable_http_client()
    srv = EmbeddedMcpHttpServer(host="127.0.0.1", port=0, mtui_root=ROOT, warm_up=False)
    try:
        assert srv.start(timeout=5.0) is True
        assert srv.is_running is True
        assert srv.bound_port is not None and srv.bound_port > 0
        assert rt_settings.allow_runtime_python_override is False

        # 1. GET /health check
        with urllib.request.urlopen(srv.health_url, timeout=5.0) as resp:
            assert resp.status == 200
            health = json.loads(resp.read().decode("utf-8"))
        assert health["status"] == "ok"
        assert health["server"] == "mtui-mcp"
        assert health["port"] == srv.bound_port
        assert health["endpoints"]["streamable_http"] == "/mcp"
        assert health["endpoints"]["sse"] == "/sse"
        assert health["mtui_root"] == str(ROOT)

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

        # 4. Port-in-use conflict returns False gracefully without crashing, disturbing active server, or warming worker (R2-F05)
        warm_up_calls: list[int] = []
        monkeypatch.setattr(
            "mtui_mcp.embedded_server.get_worker_client",
            lambda: types.SimpleNamespace(
                ensure_started=lambda: warm_up_calls.append(1),
                is_running=False,
                shutdown=lambda: None,
            ),
        )
        conflict_srv = EmbeddedMcpHttpServer(
            host="127.0.0.1",
            port=srv.bound_port,
            mtui_root=ROOT,
            warm_up=True,
        )
        assert conflict_srv.start(timeout=2.0) is False
        assert conflict_srv.is_running is False
        assert warm_up_calls == []
        assert srv.is_running is True

        warm_srv = EmbeddedMcpHttpServer(host="127.0.0.1", port=0, mtui_root=ROOT, warm_up=True)
        try:
            assert warm_srv.start(timeout=5.0) is True
            assert warm_up_calls == [1]
        finally:
            warm_srv.stop()
    finally:
        srv.stop()

    # 5. Clean idempotent stop()
    assert srv.is_running is False
    assert srv.bound_port is None
    srv.stop()


@pytest.mark.asyncio
async def test_non_loopback_http_requires_bearer_token_and_redacts_health_mtui_root(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """F13 & F54: Non-loopback binds require MTUI_MCP_AUTH_TOKEN, guard /mcp + /sse + /messages/, and omit mtui_root on unauth /health."""
    from mcp import ClientSession

    for proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(proxy_key, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("127.0.0.2") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("[::1]") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("") is False
    assert is_loopback_host("   ") is False
    assert is_loopback_host("[]") is False
    assert is_loopback_host("0.0.0.0") is False
    assert is_loopback_host("::") is False
    assert is_loopback_host("192.168.1.25") is False

    # 1. Refuses non-loopback or wildcard ("") bind when MTUI_MCP_AUTH_TOKEN is unset (R2-F12)
    monkeypatch.delenv("MTUI_MCP_AUTH_TOKEN", raising=False)
    for wildcard_host in ("0.0.0.0", ""):
        unauth_srv = EmbeddedMcpHttpServer(host=wildcard_host, port=0, mtui_root=ROOT, warm_up=False)
        assert unauth_srv.start(timeout=2.0) is False
        assert unauth_srv.is_running is False

        with pytest.raises(RuntimeError, match="MTUI_MCP_AUTH_TOKEN"):
            _run_mcp_transport("streamable-http", wildcard_host, 0)

    # R2-F52: Bracketed IPv6 loopback `--host "[::1]"` normalizes to `"::1"` in both unauthenticated and authenticated branches
    recorded_mcp_runs: list[tuple[str, Any, Any]] = []
    recorded_uvicorn_runs: list[tuple[Any, Any]] = []
    with monkeypatch.context() as mp_ipv6:
        mp_ipv6.setattr(
            "mtui_mcp.__main__.mcp.run",
            lambda transport="stdio", host=None, port=None, **_kw: recorded_mcp_runs.append(
                (transport, host, port)
            ),
        )
        mp_ipv6.setattr(
            "mtui_mcp.__main__.uvicorn.run",
            lambda _app, host=None, port=None, **_kw: recorded_uvicorn_runs.append((host, port)),
        )
        _run_mcp_transport("streamable-http", "[::1]", 8765)
        assert recorded_mcp_runs == [("streamable-http", "::1", 8765)]
        import mtui_mcp.__main__ as main_mod

        if getattr(main_mod.mcp, "settings", None) is not None and hasattr(main_mod.mcp.settings, "host"):
            assert main_mod.mcp.settings.host == "::1"

        _run_mcp_transport("streamable-http", "[::1]", 8765, auth_token="ipv6-token")
        assert recorded_uvicorn_runs == [("::1", 8765)]

    # 2. Starts cleanly when MTUI_MCP_AUTH_TOKEN is configured and enforces Bearer token
    secret_token = f"mtui-test-token-{uuid.uuid4().hex}"
    monkeypatch.setenv("MTUI_MCP_AUTH_TOKEN", secret_token)
    srv = EmbeddedMcpHttpServer(host="0.0.0.0", port=0, mtui_root=ROOT, warm_up=False)
    try:
        assert srv.start(timeout=5.0) is True
        port = srv.bound_port
        assert port is not None and port > 0
        base_local = f"http://127.0.0.1:{port}"

        # Unauthenticated /health succeeds but omits sensitive mtui_root
        with urllib.request.urlopen(f"{base_local}/health", timeout=5.0) as resp:
            assert resp.status == 200
            unauth_health = json.loads(resp.read().decode("utf-8"))
        assert unauth_health["status"] == "ok"
        assert unauth_health["auth_required"] is True
        assert "mtui_root" not in unauth_health

        # Authenticated /health includes mtui_root
        auth_req = urllib.request.Request(
            f"{base_local}/health",
            headers={"Authorization": f"Bearer {secret_token}"},
        )
        with urllib.request.urlopen(auth_req, timeout=5.0) as resp:
            auth_health = json.loads(resp.read().decode("utf-8"))
        assert auth_health["mtui_root"] == str(ROOT)

        # Unauthenticated or wrong-token requests to /mcp, /sse, /messages/ are rejected with 401
        for endpoint in ("/mcp", "/sse", "/messages/"):
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"{base_local}{endpoint}", timeout=5.0)
            assert exc_info.value.code == 401

            bad_req = urllib.request.Request(
                f"{base_local}{endpoint}",
                headers={"Authorization": "Bearer wrong-token"},
            )
            with pytest.raises(urllib.error.HTTPError) as bad_exc:
                urllib.request.urlopen(bad_req, timeout=5.0)
            assert bad_exc.value.code == 401

        # Valid Bearer token allows full MCP Streamable HTTP session + enforces F54/R2-F82 python override policy block
        async def _verify_authenticated_mcp_session(session: ClientSession) -> None:
            init_res = await session.initialize()
            srv_info = getattr(init_res, "server_info", None) or getattr(init_res, "serverInfo")
            assert srv_info.name == "mtui-mcp"

            ok_status = await session.call_tool("mtui_status", {"warm_up_worker": False})
            assert not getattr(ok_status, "is_error", getattr(ok_status, "isError", False))
            assert "engine_available" in json.loads(ok_status.content[0].text)

            caplog.clear()
            py_override_res = await session.call_tool(
                "mtui_status",
                {"mtui_python": sys.executable, "warm_up_worker": False},
            )
            assert getattr(py_override_res, "is_error", getattr(py_override_res, "isError", False))
            err_text = "\n".join(getattr(c, "text", "") for c in py_override_res.content)
            logged_causes = "\n".join(
                f"{rec.getMessage()}\n{rec.exc_info[1]!r}\n{rec.exc_info[1].__cause__!r}"
                if rec.exc_info and rec.exc_info[1] is not None
                else rec.getMessage()
                for rec in caplog.records
            )
            combined_diag = f"{err_text}\n{caplog.text}\n{logged_causes}"
            assert "mtui_python" in combined_diag
            assert "disabled over HTTP/SSE transport" in combined_diag

        streamable_client = _import_streamable_http_client()
        sig_params = inspect.signature(streamable_client).parameters
        auth_headers = {"Authorization": f"Bearer {secret_token}"}
        if "http_client" in sig_params:
            async with httpx.AsyncClient(headers=auth_headers, trust_env=False) as http_client:
                async with streamable_client(f"{base_local}/mcp", http_client=http_client) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await _verify_authenticated_mcp_session(session)
        else:
            async with streamable_client(f"{base_local}/mcp", headers=auth_headers) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await _verify_authenticated_mcp_session(session)
    finally:
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

    def clear(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._rev = 0
        self._clean_rev = 0

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


def _run_in_worker_pumping_qt(fn, timeout_sec: float = 15.0):
    """Run blocking IPC client callable on a background thread while pumping Qt events on the main thread."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn)
        deadline = time.monotonic() + timeout_sec
        while not fut.done() and time.monotonic() < deadline:
            _QAPP.processEvents()
            time.sleep(0.005)
        _QAPP.processEvents()
        return fut.result(timeout=2.0)


def test_mcp_gui_bridge_server_qt_main_thread_roundtrip_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify McpGuiBridgeServer + gui_bridge.py / mtui_live_gui_session roundtrip, generation/failure/handshake guards, and cleanup."""
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
    auto_save_flag = {"enabled": True}
    load_hook: dict[str, Any] = {
        "fail_path": None,
        "stall_path": None,
        "started": threading.Event(),
        "release": threading.Event(),
        "finished": threading.Event(),
    }
    aux_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    class _DocServiceStub:
        def __init__(self, controller: Any) -> None:
            self.controller = controller
            self._aux_load_executor = aux_executor
            self._load_generation = 0

        def _cancel_pending_load(self) -> int:
            self._load_generation += 1
            return self._load_generation

        @staticmethod
        def resolve_editor_image_paths(candidate_path: str) -> tuple[str, str]:
            norm = str(Path(candidate_path).resolve())
            return norm, norm

        @staticmethod
        def _auto_save_on_switch_enabled() -> bool:
            return auto_save_flag["enabled"]

        @staticmethod
        def _auto_export_on_switch_enabled() -> bool:
            return False

        def clear_editor_state(self) -> None:
            self._cancel_pending_load()
            editor_model.clear_document()
            history.clear()

        def apply_load_result(self, payload: tuple[int, Any]) -> None:
            gen, res = payload
            if gen != self._load_generation:
                return
            if isinstance(res, DocumentSnapshot):
                self.apply_loaded_data_to_model(res)

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
        src = Path(worker_self.image_path).resolve()
        was_stalled = bool(
            load_hook["stall_path"]
            and os.path.normcase(str(src)) == os.path.normcase(str(load_hook["stall_path"]))
        )
        try:
            if was_stalled:
                load_hook["started"].set()
                load_hook["release"].wait(timeout=5.0)
            if load_hook["fail_path"] and os.path.normcase(str(src)) == os.path.normcase(str(load_hook["fail_path"])):
                return DocumentLoadFailure("simulated decode failure")
            arr = np.asarray(Image.open(src).convert("RGB"), dtype=np.uint8)
            pp = resolve_page_paths(src)
            raw = json.loads(pp.json_path.read_text(encoding="utf-8"))
            img_data = next(iter(raw.values()))
            return DocumentSnapshot(
                source_path=str(src),
                image=arr,
                display_image_path=str(src),
                regions=img_data.get("regions", []),
            )
        finally:
            if was_stalled:
                load_hook["finished"].set()

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
        handshake_timeout=0.4,
    )
    expected_addr, expected_key = gui_session_paths(session_name)

    try:
        assert bridge.start() is True
        assert bridge.is_running is True
        assert bridge.address == expected_addr
        assert bridge.key_file == expected_key
        assert expected_key.is_file()
        assert len(expected_key.read_bytes()) == 32

        # F07: Even when Qt main thread is NOT pumping (busy >750ms), ownership lockfile prevents takeover
        assert _acquire_session_lockfile(expected_key.with_suffix(".lock")) is None
        dup_bridge = McpGuiBridgeServer(
            app_logic=app_logic,
            editor_controller=controller,
            editor_model=editor_model,
            session_name=session_name,
        )
        assert dup_bridge.start() is False

        # F08 & R2-F80: Stalled client handshake must NOT block accept loop while the first handshake is STILL blocked
        import services.mcp_service as mcp_svc_mod

        orig_deliver_challenge = mcp_svc_mod.deliver_challenge
        handshake_count_lock = threading.Lock()
        handshake_call_count = {"n": 0}
        first_handshake_started = threading.Event()
        release_first_handshake = threading.Event()
        first_handshake_finished = threading.Event()

        def _gated_deliver_challenge(conn_obj: Any, key_bytes: bytes) -> None:
            with handshake_count_lock:
                handshake_call_count["n"] += 1
                idx = handshake_call_count["n"]
            if idx == 1:
                first_handshake_started.set()
                try:
                    release_first_handshake.wait(timeout=5.0)
                    return orig_deliver_challenge(conn_obj, key_bytes)
                finally:
                    first_handshake_finished.set()
            return orig_deliver_challenge(conn_obj, key_bytes)

        bridge.handshake_timeout = 3.0
        stalled_raw_client = None
        with monkeypatch.context() as mp_hs:
            mp_hs.setattr(mcp_svc_mod, "deliver_challenge", _gated_deliver_challenge)
            stalled_raw_client = Client(expected_addr, authkey=None)
            try:
                assert first_handshake_started.wait(timeout=2.0) is True
                assert first_handshake_finished.is_set() is False
                observed_threads.clear()
                status_json = _run_in_worker_pumping_qt(
                    lambda: json.loads(mtui_live_gui_session(action="status"))
                )
                assert first_handshake_finished.is_set() is False
                assert status_json["live_gui_connected"] is True
                gui_state = status_json["gui_state"]
                assert gui_state["status"] == "ok"
                assert gui_state["total_pages"] == 2
                assert gui_state["translation_running"] is True
                assert os.path.normcase(gui_state["active_page"]) == os.path.normcase(str(p1.resolve()))
                assert observed_threads and all(t == _QAPP.thread() for t in observed_threads)
            finally:
                release_first_handshake.set()
                if stalled_raw_client is not None:
                    try:
                        stalled_raw_client.close()
                    except Exception:
                        pass
                bridge.handshake_timeout = 0.4

        # R2-F50 & R3-F41: Post-auth stalled request read is genuinely blocked and aborted within request_read_timeout
        bridge.request_read_timeout = 0.18
        post_auth_entered = threading.Event()
        orig_auth_conn = bridge._authenticate_connection

        def _spy_auth_conn(*a: Any, **kw: Any) -> bool:
            ok = orig_auth_conn(*a, **kw)
            if ok:
                post_auth_entered.set()
            return ok

        partial_peer = Client(expected_addr, authkey=None)
        with monkeypatch.context() as mp_read:
            mp_read.setattr(bridge, "_authenticate_connection", _spy_auth_conn)
            try:
                auth_bytes = expected_key.read_bytes()
                answer_challenge(partial_peer, auth_bytes)
                deliver_challenge(partial_peer, auth_bytes)
                assert post_auth_entered.wait(timeout=2.0) is True
                if os.name != "nt":
                    os.write(partial_peer.fileno(), b"\x00\x00")
                # On Windows Named Pipes, keep partial_peer open with zero request bytes sent so poll/read genuinely blocks;
                # verify request_read_timeout aborts the blocked handler thread cleanly.
                deadline_partial = time.monotonic() + 1.5
                while time.monotonic() < deadline_partial:
                    time.sleep(0.05)
                    conn_threads = [
                        t for t in threading.enumerate() if t.name == "mtui-gui-bridge-conn" and t.is_alive()
                    ]
                    if not conn_threads:
                        break
                assert [
                    t for t in threading.enumerate() if t.name == "mtui-gui-bridge-conn" and t.is_alive()
                ] == []
            finally:
                bridge.request_read_timeout = 5.0
                try:
                    partial_peer.close()
                except Exception:
                    pass

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

        before_regs = editor_model.get_regions()
        history.record_edit(before_regs)
        editor_model.update_region(0, {**before_regs[0], "translation": "GUI新改台词"})

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

        # 5. F73: Failed `open_page(..., force=True)` must preserve current page, undo history, and unsaved edits!
        auto_save_flag["enabled"] = False
        p2_regs_before = editor_model.get_regions()
        history.record_edit(p2_regs_before)
        editor_model.update_region(0, {**p2_regs_before[0], "translation": "P2未保存修改"})
        assert history.can_undo() is True
        load_hook["fail_path"] = p1.resolve()
        try:
            with pytest.raises(RuntimeError, match="Failed to load page"):
                _run_in_worker_pumping_qt(
                    lambda: call_live_gui("open_page", session_name=session_name, path=str(p1), force=True)
                )
        finally:
            load_hook["fail_path"] = None
        assert os.path.normcase(editor_model.get_source_image_path()) == os.path.normcase(str(p2.resolve()))
        assert editor_model.get_regions()[0]["translation"] == "P2未保存修改"
        assert history.can_undo() is True

        # 6. F02/F09: Stale background `open_page(p1)` must NOT overwrite newer switch/edits on `p2`!
        load_hook["stall_path"] = p1.resolve()
        load_hook["started"].clear()
        load_hook["release"].clear()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as slow_pool:
            slow_fut = slow_pool.submit(
                call_live_gui,
                "open_page",
                session_name=session_name,
                path=str(p1),
                force=True,
            )
            deadline = time.monotonic() + 5.0
            while not load_hook["started"].is_set() and time.monotonic() < deadline:
                _QAPP.processEvents()
                time.sleep(0.005)
            assert load_hook["started"].is_set()

            # Newer switch/reload of p2 + fresh user edit happens while p1 is still decoding
            reopened_p2 = _run_in_worker_pumping_qt(
                lambda: call_live_gui("open_page", session_name=session_name, path=str(p2), reload=True, force=True)
            )
            assert reopened_p2["page_name"] == "002.png"
            fresh_regs = editor_model.get_regions()
            history.record_edit(fresh_regs)
            editor_model.update_region(0, {**fresh_regs[0], "translation": "最新P2编辑不可被覆盖"})

            # Release stale p1 decode; it must abort as superseded!
            load_hook["stall_path"] = None
            load_hook["release"].set()
            with pytest.raises(RuntimeError, match="superseded"):
                _run_in_worker_pumping_qt(slow_fut.result)

        assert os.path.normcase(editor_model.get_source_image_path()) == os.path.normcase(str(p2.resolve()))
        assert editor_model.get_regions()[0]["translation"] == "最新P2编辑不可被覆盖"

        # 6b. R2-F09 & R3-F40: Timed-out `open_page(p1)` must NEVER switch pages late after background load & server handler finish!
        load_hook["stall_path"] = p1.resolve()
        load_hook["started"].clear()
        load_hook["release"].clear()
        load_hook["finished"].clear()
        try:
            with pytest.raises(TimeoutError):
                _run_in_worker_pumping_qt(
                    lambda: call_live_gui(
                        "open_page",
                        session_name=session_name,
                        path=str(p1),
                        force=True,
                        timeout_sec=0.12,
                    )
                )
            assert load_hook["started"].is_set()
            # Release worker AFTER client deadline expired, then wait for background decode + server handler to finish
            load_hook["stall_path"] = None
            load_hook["release"].set()
            wait_deadline = time.monotonic() + 3.0
            while time.monotonic() < wait_deadline:
                _QAPP.processEvents()
                active_conn_threads = [
                    t for t in threading.enumerate() if t.name == "mtui-gui-bridge-conn" and t.is_alive()
                ]
                if load_hook["finished"].is_set() and not active_conn_threads:
                    break
                time.sleep(0.01)
            assert load_hook["finished"].is_set() is True
            assert [
                t for t in threading.enumerate() if t.name == "mtui-gui-bridge-conn" and t.is_alive()
            ] == []
            _QAPP.processEvents()
            assert os.path.normcase(editor_model.get_source_image_path()) == os.path.normcase(str(p2.resolve()))
            assert editor_model.get_regions()[0]["translation"] == "最新P2编辑不可被覆盖"
        finally:
            load_hook["stall_path"] = None
            load_hook["release"].set()

        # 6c. R2-F11: Reloading active page while concurrent GUI or disk edits occur must reject stale snapshot!
        for auto_save_mode, edited_text in ((True, "R2F11自动保存期间新编辑"), (False, "R2F11未保存期间新编辑")):
            auto_save_flag["enabled"] = auto_save_mode
            if auto_save_mode:
                history.mark_clean()
            load_hook["stall_path"] = p2.resolve()
            load_hook["started"].clear()
            load_hook["release"].clear()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as reload_pool:
                reload_fut = reload_pool.submit(
                    call_live_gui,
                    "open_page",
                    session_name=session_name,
                    path=str(p2),
                    reload=True,
                    force=True,
                )
                deadline_rl = time.monotonic() + 5.0
                while not load_hook["started"].is_set() and time.monotonic() < deadline_rl:
                    _QAPP.processEvents()
                    time.sleep(0.005)
                assert load_hook["started"].is_set()

                # User edits p2 in GUI while DocumentLoadWorker.load(p2) is mid-flight
                mid_regs = editor_model.get_regions()
                history.record_edit(mid_regs)
                editor_model.update_region(0, {**mid_regs[0], "translation": edited_text})

                load_hook["stall_path"] = None
                load_hook["release"].set()
                with pytest.raises(RuntimeError, match="changed while loading"):
                    _run_in_worker_pumping_qt(reload_fut.result)

            assert os.path.normcase(editor_model.get_source_image_path()) == os.path.normcase(str(p2.resolve()))
            assert editor_model.get_regions()[0]["translation"] == edited_text

        # Also verify concurrent on-disk JSON update during load is rejected before overwriting
        auto_save_flag["enabled"] = False
        history.mark_clean()
        load_hook["stall_path"] = p2.resolve()
        load_hook["started"].clear()
        load_hook["release"].clear()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as disk_pool:
            disk_fut = disk_pool.submit(
                call_live_gui,
                "open_page",
                session_name=session_name,
                path=str(p2),
                reload=True,
                force=True,
            )
            deadline_dk = time.monotonic() + 5.0
            while not load_hook["started"].is_set() and time.monotonic() < deadline_dk:
                _QAPP.processEvents()
                time.sleep(0.005)
            assert load_hook["started"].is_set()

            pp2 = resolve_page_paths(p2)
            save_page_json_atomic(
                pp2,
                str(p2.resolve()),
                {
                    "regions": [
                        {
                            "lines": [[[20.0, 20.0], [100.0, 20.0], [100.0, 120.0], [20.0, 120.0]]],
                            "center": [60.0, 70.0],
                            "text": "二頁目",
                            "translation": "磁盘并发更新台词",
                            "font_family": "FZHei-B01",
                            "font_size": 24,
                            "direction": "v",
                            "alignment": "left",
                        }
                    ]
                },
                auto_initial_snapshot=False,
            )

            load_hook["stall_path"] = None
            load_hook["release"].set()
            with pytest.raises(RuntimeError, match="changed while loading"):
                _run_in_worker_pumping_qt(disk_fut.result)

        assert os.path.normcase(editor_model.get_source_image_path()) == os.path.normcase(str(p2.resolve()))
        assert editor_model.get_regions()[0]["translation"] == "R2F11未保存期间新编辑"

        # 7. F06: Disabling/stopping bridge invalidates in-flight `open_page` before GUI side effects!
        load_hook["stall_path"] = p1.resolve()
        load_hook["started"].clear()
        load_hook["release"].clear()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as stop_pool:
            inflight_fut = stop_pool.submit(
                call_live_gui,
                "open_page",
                session_name=session_name,
                path=str(p1),
                force=True,
            )
            deadline = time.monotonic() + 5.0
            while not load_hook["started"].is_set() and time.monotonic() < deadline:
                _QAPP.processEvents()
                time.sleep(0.005)
            assert load_hook["started"].is_set()

            bridge.stop()
            load_hook["stall_path"] = None
            load_hook["release"].set()
            with pytest.raises(RuntimeError, match="stopped or restarted"):
                _run_in_worker_pumping_qt(inflight_fut.result)

        assert os.path.normcase(editor_model.get_source_image_path()) == os.path.normcase(str(p2.resolve()))
        assert editor_model.get_regions()[0]["translation"] == "R2F11未保存期间新编辑"
    finally:
        load_hook["stall_path"] = None
        load_hook["release"].set()
        bridge.stop()
        aux_executor.shutdown(wait=True)

    # 8. Clean shutdown removes key file, releases lockfile, and reports disconnected cleanly
    assert bridge.is_running is False
    assert not expected_key.exists()
    reacquired_fd = _acquire_session_lockfile(expected_key.with_suffix(".lock"))
    assert reacquired_fd is not None
    _release_session_lockfile(reacquired_fd)
    assert is_live_gui_session_available(session_name) is False
    disconnected = json.loads(mtui_live_gui_session(action="status"))
    assert disconnected == {"live_gui_connected": False, "gui_state": None}


def test_gui_bridge_client_handshake_timeout_and_dispatcher_thread_safety(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F10, F11, F12, F71: Client IPC timeout covers handshake, wait_for_translation passes remaining budget, and dispatcher never runs Qt calls on worker threads."""
    # 1. F12 & F71: Listener that accepts raw connections and stalls without sending HMAC challenge
    session_name = f"pytest-stall-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MTUI_MCP_SESSION", session_name)
    addr, key_file = gui_session_paths(session_name)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(b"x" * 32)

    stop_stall = threading.Event()
    raw_listener = Listener(addr, authkey=None)
    accepted_conns: list[Any] = []

    def _stalled_server() -> None:
        while not stop_stall.is_set():
            try:
                c = raw_listener.accept()
                accepted_conns.append(c)
                stop_stall.wait(timeout=5.0)
            except Exception:
                break

    srv_thread = threading.Thread(target=_stalled_server, daemon=True)
    srv_thread.start()
    try:
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            call_live_gui("list_pages", session_name=session_name, timeout_sec=0.25)
        assert time.monotonic() - t0 < 1.5

        t1 = time.monotonic()
        with pytest.raises(TimeoutError):
            wait_for_live_gui_translation(timeout_seconds=1, session_name=session_name)
        assert time.monotonic() - t1 < 2.0
    finally:
        stop_stall.set()
        try:
            raw_listener.close()
        except Exception:
            pass
        for c in accepted_conns:
            try:
                c.close()
            except Exception:
                pass
        key_file.unlink(missing_ok=True)

    # 2. F11: Cross-thread dispatch without event loop pumping cancels and NEVER executes callback on worker thread
    dispatcher = _MainThreadDispatcher()
    ran_on_threads: list[QThread] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(
            dispatcher.dispatch,
            lambda: ran_on_threads.append(QThread.currentThread()),
            0.08,
        )
        with pytest.raises(TimeoutError):
            fut.result(timeout=2.0)
    _QAPP.processEvents()
    assert ran_on_threads == []

    # 3. F10: Started `save_page` does not report wait timeout as save failure, while queued `PENDING` call cancels on time
    save_session = f"pytest-slowsave-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MTUI_MCP_SESSION", save_session)
    model = EditorModel()
    model.apply_document_snapshot(
        DocumentSnapshot(
            source_path=str(ROOT / "README.md"),
            image=np.zeros((16, 16, 3), dtype=np.uint8),
            display_image_path=str(ROOT / "README.md"),
            regions=[],
        )
    )
    hist = _GuiHistoryStub(model)
    save_completed = threading.Event()
    queued_call_ran = []

    class _SlowSaveController:
        history_service = hist
        export_service = types.SimpleNamespace(unfinished_count=lambda: 0, has_unsaved_changes=lambda: False)
        document_service = None

        @staticmethod
        def commit_pending_edits() -> None:
            pass

        @staticmethod
        def save_editor_state() -> bool:
            time.sleep(0.35)
            save_completed.set()
            return True

    bridge = McpGuiBridgeServer(
        editor_controller=_SlowSaveController(),
        editor_model=model,
        session_name=save_session,
        dispatch_timeout=0.05,
    )
    try:
        assert bridge.start() is True
        save_reply = _run_in_worker_pumping_qt(
            lambda: call_live_gui("save_page", session_name=save_session, timeout_sec=2.0)
        )
        assert save_reply["status"] in ("ok", "in_progress")
        assert save_completed.wait(timeout=1.0) is True

        # Queue a PENDING callback while blocking Qt main thread -> cancels promptly and never runs afterward
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            queued_fut = pool.submit(
                bridge._dispatcher.dispatch,
                lambda: queued_call_ran.append(True),
                0.05,
            )
            time.sleep(0.12)  # Main thread intentionally busy / not pumping past timeout
            with pytest.raises(TimeoutError):
                queued_fut.result(timeout=1.0)
            _QAPP.processEvents()
        assert queued_call_ran == []
    finally:
        bridge.stop()

    # 4. R2-F34 & R3-F39: abort_ipc_connection skips already-closed connections without touching fileno()
    fileno_calls: list[bool] = []

    class _ClosedConnSpy:
        closed = True

        @staticmethod
        def fileno() -> int:
            fileno_calls.append(True)
            return 999

    abort_ipc_connection(_ClosedConnSpy())
    assert fileno_calls == []

    # 5. R2-F72: On Windows, busy Named Pipe connection waits respect caller's deadline without accumulating 20s threads
    if os.name == "nt":
        import _winapi

        busy_sess = f"pytest-busypipe-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        busy_addr, busy_key = gui_session_paths(busy_sess)
        busy_key.parent.mkdir(parents=True, exist_ok=True)
        busy_key.write_bytes(b"k" * 32)
        srv_pipe = _winapi.CreateNamedPipe(
            busy_addr,
            _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED,
            _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | _winapi.PIPE_WAIT,
            1,
            8192,
            8192,
            0,
            _winapi.NULL,
        )
        occupied_client = None
        try:
            occupied_client = _winapi.CreateFile(
                busy_addr,
                _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                0,
                _winapi.NULL,
                _winapi.OPEN_EXISTING,
                _winapi.FILE_FLAG_OVERLAPPED,
                _winapi.NULL,
            )
            before_client_threads = {
                t for t in threading.enumerate() if t.name == "mtui-gui-bridge-client" and t.is_alive()
            }
            for _ in range(3):
                t_busy = time.monotonic()
                with pytest.raises(TimeoutError):
                    call_live_gui("list_pages", session_name=busy_sess, timeout_sec=0.1)
                assert time.monotonic() - t_busy < 0.8
            time.sleep(0.15)
            after_client_threads = {
                t for t in threading.enumerate() if t.name == "mtui-gui-bridge-client" and t.is_alive()
            }
            assert after_client_threads - before_client_threads == set()
        finally:
            if occupied_client is not None:
                _winapi.CloseHandle(occupied_client)
            _winapi.CloseHandle(srv_pipe)
            busy_key.unlink(missing_ok=True)

    # 6. R2-F10 & R3-F18: Lock directory failure cleans up _ACTIVE_BRIDGE_ADDRESSES inside tmp_path and does NOT abort independent HTTP startup
    lockfail_sess = f"pytest-lockfail-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    lockfail_addr = (
        f"\\\\.\\pipe\\{lockfail_sess}"
        if os.name == "nt"
        else str(tmp_path / f"{lockfail_sess}.sock")
    )
    blocked_parent = tmp_path / "lockfail_blocked_file"
    blocked_parent.write_bytes(b"not-a-dir")
    bad_key_path = blocked_parent / "sub" / "bridge.key"
    assert _acquire_session_lockfile(bad_key_path.with_suffix(".lock")) is None
    with monkeypatch.context() as mp_lf:
        mp_lf.setattr(
            "services.mcp_service.gui_session_paths",
            lambda _s=None: (lockfail_addr, bad_key_path),
        )
        lf_svc = McpService(str(ROOT), session_name=lockfail_sess)
        try:
            assert lf_svc.start(host="127.0.0.1", port=0) is True
            assert lf_svc.http_running is True
            assert lf_svc.gui_bridge_running is False
        finally:
            lf_svc.shutdown()

    # Once the parent directory is writable again, the exact same lockfail_addr binds cleanly in the same process
    repaired_dir = tmp_path / "lockfail_repaired_dir"
    repaired_dir.mkdir(parents=True, exist_ok=True)
    good_key_path = repaired_dir / "bridge.key"
    with monkeypatch.context() as mp_ok:
        mp_ok.setattr(
            "services.mcp_service.gui_session_paths",
            lambda _s=None: (lockfail_addr, good_key_path),
        )
        recovered_bridge = McpGuiBridgeServer(session_name=lockfail_sess)
        try:
            assert recovered_bridge.start() is True
        finally:
            recovered_bridge.stop()


def test_mcp_service_config_defaults_locales_cli_args_and_live_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify AppSection validation, locales, ConfigService reload sync (F39), CLI args (F68/F69), and CLI override preservation (F72)."""
    # 1. AppSection defaults & F67 port range validation (1..65535)
    app_defaults = AppSection()
    assert app_defaults.mcp_server_enabled is True
    assert app_defaults.mcp_server_host == "127.0.0.1"
    assert app_defaults.mcp_server_port == 8765
    assert AppSection(mcp_server_port=1).mcp_server_port == 1
    assert AppSection(mcp_server_port=65535).mcp_server_port == 65535
    with pytest.raises(ValidationError):
        AppSection(mcp_server_port=0)
    with pytest.raises(ValidationError):
        AppSection(mcp_server_port=65536)

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
        assert example_saved["app"]["mcp_server_enabled"] is True
        assert example_saved["app"]["mcp_server_port"] == 8765
    finally:
        cfg_svc.shutdown()

    # 4. Desktop CLI arg parser: F68 (allow_abbrev=False) & F69 (port bounds 0..65535)
    parsed_on, qt_rest = parse_desktop_mcp_args(["--mcp", "--mcp-host", "127.0.0.1", "--mcp-port", "8899", "-style", "fusion"])
    assert parsed_on.mcp_enabled is True
    assert parsed_on.mcp_host == "127.0.0.1"
    assert parsed_on.mcp_port == 8899
    assert qt_rest == ["-style", "fusion"]

    parsed_ephemeral, _ = parse_desktop_mcp_args(["--mcp-port", "0"])
    assert parsed_ephemeral.mcp_port == 0

    parsed_off, _ = parse_desktop_mcp_args(["--no-mcp"])
    assert parsed_off.mcp_enabled is False

    # F68: `--no-mc` must NOT be swallowed as an abbreviation of `--no-mcp`
    parsed_prefix, prefix_rest = parse_desktop_mcp_args(["--no-mc", "-platform", "offscreen"])
    assert parsed_prefix.mcp_enabled is None
    assert prefix_rest == ["--no-mc", "-platform", "offscreen"]

    # F69: Out-of-range `--mcp-port` fails fast at parse time
    for bad_port in ("-1", "65536", "notaport"):
        with pytest.raises(SystemExit):
            parse_desktop_mcp_args(["--mcp-port", bad_port])

    # 5. McpService pytest suppression guard + live start / toggle off / toggle on / F72 CLI override preservation
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

        live_bound_port = svc.bound_port
        assert live_bound_port is not None and live_bound_port > 0

        # F72 & R2-F81: CLI --mcp-port override survives toggling enabled off/on without TOCTOU released-port races
        ovr_svc = McpService(str(ROOT), session_name=f"{session_name}-ovr")
        recorded_starts: list[tuple[str, int]] = []
        shutdown_count = {"n": 0}
        monkeypatch.setattr(
            ovr_svc,
            "start",
            lambda host="127.0.0.1", port=8765, **_kw: recorded_starts.append((host, int(port))) or True,
        )
        monkeypatch.setattr(
            ovr_svc,
            "shutdown",
            lambda **_kw: shutdown_count.__setitem__("n", shutdown_count["n"] + 1),
        )
        ovr_svc.set_cli_overrides(host="127.0.0.1", port=19101)
        assert ovr_svc.apply_config(enabled=False, host="127.0.0.1", port=8765) is False
        assert shutdown_count["n"] == 1
        assert recorded_starts == []

        assert ovr_svc.apply_config(enabled=True, host="127.0.0.1", port=8765) is True
        assert recorded_starts[-1] == ("127.0.0.1", 19101)

        # Explicit user port edit (`override_port=True`) replaces CLI port override
        assert ovr_svc.apply_config(enabled=True, host="127.0.0.1", port=19202, override_port=True) is True
        assert recorded_starts[-1] == ("127.0.0.1", 19202)

        # R2-F02 & R2-F08: MTUI_MCP_ENABLED / MTUI_MCP_HOST / MTUI_MCP_PORT env overrides survive unrelated config_changed reloads
        env_root = tmp_path / "env_reload_root"
        (env_root / "config").mkdir(parents=True)
        shutil.copy2(ROOT / "config" / "config-example.json", env_root / "config" / "config-example.json")
        env_cfg_svc = ConfigService(str(env_root))
        env_svc = McpService(str(ROOT), session_name=f"{session_name}-env")
        env_starts: list[tuple[str, int]] = []
        monkeypatch.setattr(
            env_svc,
            "start",
            lambda host="127.0.0.1", port=8765, **_kw: env_starts.append((host, int(port))) or True,
        )
        monkeypatch.setattr(env_svc, "shutdown", lambda **_kw: None)
        with monkeypatch.context() as mp_env:
            mp_env.setenv("MTUI_MCP_FORCE_AUTOSTART", "1")
            mp_env.setenv("MTUI_MCP_ENABLED", "false")
            mp_env.setenv("MTUI_MCP_HOST", "127.0.0.2")
            mp_env.setenv("MTUI_MCP_PORT", "19345")
            mp_env.setattr("services.get_config_service", lambda: env_cfg_svc)
            mp_env.setattr("app_logic.get_config_service", lambda: env_cfg_svc)
            mp_env.setattr(
                "app_logic.get_state_manager",
                lambda: types.SimpleNamespace(set_translating=lambda *_: None),
            )
            mp_env.setattr("services.get_mcp_service", lambda: env_svc)
            env_logic = MainAppLogic()
            try:
                assert env_svc.start_from_config(env_cfg_svc.get_config()) is False
                assert env_starts == []

                # Unrelated config update (e.g. opening file updates last_open_dir -> emits config_changed) must NOT start MCP
                cfg_copy = env_cfg_svc.get_config()
                cfg_copy.app.last_open_dir = str(tmp_path)
                env_cfg_svc.set_config(cfg_copy)
                env_cfg_svc.reload_config()
                assert env_starts == []

                # Explicit user toggle on (`app.mcp_server_enabled=True`) overrides MTUI_MCP_ENABLED=false while preserving MTUI_MCP_HOST/PORT!
                env_logic.update_single_config("app.mcp_server_enabled", True)
                assert env_starts[-1] == ("127.0.0.2", 19345)

                # Subsequent unrelated config_changed still preserves MTUI_MCP_HOST and MTUI_MCP_PORT
                env_cfg_svc.reload_config()
                assert env_starts[-1] == ("127.0.0.2", 19345)

                # Explicit user port edit (`app.mcp_server_port=19456`) overrides MTUI_MCP_PORT while still preserving MTUI_MCP_HOST!
                env_logic.update_single_config("app.mcp_server_port", 19456)
                assert env_starts[-1] == ("127.0.0.2", 19456)
            finally:
                env_logic.shutdown()
                env_cfg_svc.shutdown()

        # F39: External config reload (`ConfigService.reload_config`) syncs running McpService without double-calling on update_single_config
        reload_root = tmp_path / "reload_root"
        (reload_root / "config").mkdir(parents=True)
        shutil.copy2(ROOT / "config" / "config-example.json", reload_root / "config" / "config-example.json")
        reload_cfg_svc = ConfigService(str(reload_root))
        apply_calls: list[tuple[bool, int]] = []
        orig_apply = svc.apply_config

        def _spy_apply(enabled: bool, host: Any = None, port: Any = None, **kw: Any) -> bool:
            apply_calls.append((bool(enabled), int(port) if port is not None else -1))
            return orig_apply(enabled=enabled, host=host, port=port, **kw)

        monkeypatch.setattr(svc, "apply_config", _spy_apply)
        monkeypatch.setattr("services.get_config_service", lambda: reload_cfg_svc)
        monkeypatch.setattr("app_logic.get_config_service", lambda: reload_cfg_svc)
        monkeypatch.setattr("app_logic.get_state_manager", lambda: types.SimpleNamespace(set_translating=lambda *_: None))
        monkeypatch.setattr("services.get_mcp_service", lambda: svc)
        logic = MainAppLogic()
        try:
            apply_calls.clear()
            logic.update_single_config("app.mcp_server_port", live_bound_port)
            assert len(apply_calls) == 1  # No duplicate apply_config from set_config signal!
            assert reload_cfg_svc.flush_pending_writes() is True

            # External edit sets mcp_server_enabled = False on disk -> reload_config() stops running MCP server
            user_cfg_file = reload_root / "config" / "config.json"
            disk_data = json.loads(user_cfg_file.read_text(encoding="utf-8"))
            disk_data["app"]["mcp_server_enabled"] = False
            user_cfg_file.write_text(json.dumps(disk_data), encoding="utf-8")
            reload_cfg_svc.reload_config()
            assert svc.is_running is False
        finally:
            logic.shutdown()
            reload_cfg_svc.shutdown()
    finally:
        svc.shutdown()

    assert svc.is_running is False
    _, final_key = gui_session_paths(session_name)
    assert not final_key.exists()


def test_service_manager_failure_does_not_cache_broken_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F86-part: ServiceManager.initialize clears _container on failure so retries actually re-initialize."""
    saved_container = ServiceManager._container
    monkeypatch.setattr(ServiceManager, "_container", None)
    try:
        attempts = {"n": 0}
        shutdown_calls: list[int] = []

        def _flaky_init(self_container: ServiceContainer, root_widget: Any = None) -> bool:
            attempts["n"] += 1
            attempt_no = attempts["n"]
            self_container.register_service(
                "async",
                types.SimpleNamespace(shutdown=lambda: shutdown_calls.append(attempt_no)),
            )
            if attempt_no == 1:
                return False
            if attempt_no == 2:
                raise RuntimeError("simulated heavyweight service init failure")
            self_container.initialized = True
            return True

        monkeypatch.setattr(ServiceContainer, "initialize_services", _flaky_init)
        assert ServiceManager.initialize(str(tmp_path)) is False
        assert ServiceManager._container is None
        assert shutdown_calls == [1]

        with pytest.raises(RuntimeError, match="simulated heavyweight"):
            ServiceManager.initialize(str(tmp_path))
        assert ServiceManager._container is None
        assert shutdown_calls == [1, 2]

        assert ServiceManager.initialize(str(tmp_path)) is True
        assert ServiceManager._container is not None and ServiceManager._container.initialized is True
        assert shutdown_calls == [1, 2]
    finally:
        ServiceManager._container = saved_container


def test_python_symlink_preservation_and_frozen_bundle_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F14 & F15: _discover_mtui_python preserves venv symlink path without .resolve(), and frozen PyInstaller builds discover root/exe + subcommands."""
    # 1. F14: _discover_mtui_python must NOT call Path.resolve() (preserving POSIX .venv/bin/python symlinks)
    fake_root = tmp_path / "mtui_repo"
    (fake_root / "manga_translator").mkdir(parents=True)
    (fake_root / "manga_translator" / "__init__.py").write_text("", encoding="utf-8")
    (fake_root / "manga_translator" / "manga_translator.py").write_text("", encoding="utf-8")
    venv_py = (
        fake_root / ".venv" / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else fake_root / ".venv" / "bin" / "python"
    )
    venv_py.parent.mkdir(parents=True)
    venv_py.write_bytes(b"fake-python")

    def _forbid_resolve(self_path: Path, *args: Any, **kwargs: Any) -> Path:
        raise AssertionError(f"Path.resolve() must not be called on python candidate: {self_path}")

    monkeypatch.delenv("MTUI_PYTHON", raising=False)
    monkeypatch.setattr(Path, "resolve", _forbid_resolve)
    discovered_py = _discover_mtui_python(fake_root)
    assert discovered_py == Path(os.path.abspath(str(venv_py)))
    monkeypatch.undo()

    # 2. F15: Simulated frozen PyInstaller installation without loose manga_translator/*.py in exe_dir
    frozen_dir = tmp_path / "frozen_dist"
    frozen_dir.mkdir(parents=True)
    fake_exe = frozen_dir / ("app.exe" if sys.platform == "win32" else "app")
    fake_exe.write_bytes(b"frozen-app")

    for env_key in ("MTUI_ROOT", "MANGA_TRANSLATOR_UI_ROOT", "MANGA_TRANSLATOR_ROOT", "MTUI_PYTHON"):
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen_dir / "_internal"), raising=False)

    frozen_root = _discover_mtui_root(explicit=frozen_dir)
    assert frozen_root == frozen_dir.resolve()
    frozen_py = _discover_mtui_python(frozen_root)
    assert frozen_py == Path(os.path.abspath(str(fake_exe)))

    # R2-F59: Frozen PyInstaller bundle launched via directory junction / symlink resolves install root cleanly
    link_dir = tmp_path / "frozen_junction"
    link_created = False
    if sys.platform == "win32":
        mklink_res = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_dir), str(frozen_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        link_created = mklink_res.returncode == 0
    else:
        try:
            os.symlink(frozen_dir, link_dir, target_is_directory=True)
            link_created = True
        except OSError:
            link_created = False

    if link_created:
        try:
            monkeypatch.setattr(sys, "executable", str(link_dir / fake_exe.name))
            assert _discover_mtui_root(explicit=None) == frozen_dir.resolve()
            assert _discover_mtui_root(explicit=link_dir) == frozen_dir.resolve()
        finally:
            monkeypatch.setattr(sys, "executable", str(fake_exe))
            if sys.platform == "win32":
                subprocess.run(["cmd", "/c", "rmdir", str(link_dir)], capture_output=True, check=False)
            else:
                link_dir.unlink(missing_ok=True)

    # 3. F15: dispatch_internal_subcommand handles --mtui-engine-worker, -u .../engine_worker.py, and --mtui-cli-local
    dispatched: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "mtui_mcp.worker.engine_worker.main",
        lambda: dispatched.append(("worker", list(sys.argv))),
    )
    monkeypatch.setattr(
        "manga_translator.__main__.main",
        lambda: dispatched.append(("cli", list(sys.argv))),
    )

    assert dispatch_internal_subcommand(["--mcp-port", "8765"]) is None
    assert dispatch_internal_subcommand(["--mtui-engine-worker", "--port", "0", "--token", "abc"]) == 0
    assert dispatched[-1][0] == "worker" and dispatched[-1][1][1:] == ["--port", "0", "--token", "abc"]

    assert dispatch_internal_subcommand(["-u", "C:/dist/_internal/mtui_mcp/worker/engine_worker.py", "--port", "0"]) == 0
    assert dispatched[-1][0] == "worker" and dispatched[-1][1][1:] == ["--port", "0"]

    assert dispatch_internal_subcommand(["--mtui-cli-local", "-i", "page.png"]) == 0
    assert dispatched[-1][0] == "cli" and dispatched[-1][1][1:] == ["local", "-i", "page.png"]


def test_cli_entry_points_help_smoke() -> None:
    """Verify `python -m mtui_mcp --help`, `python -m manga_translator mcp --help` (even with bad MT_WEB_PORT, F74), and root `--help`."""
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
    assert "--auth-token" in res_mcp_pkg.stdout
    assert "8765" in res_mcp_pkg.stdout

    # F74: Invalid or empty MT_WEB_PORT must NOT crash `python -m manga_translator mcp --help`
    for bad_web_port in ("", "bad"):
        env = os.environ.copy()
        env["MT_WEB_PORT"] = bad_web_port
        res_mt_mcp = subprocess.run(
            [sys.executable, "-m", "manga_translator", "mcp", "--help"],
            cwd=ROOT,
            env=env,
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


def test_r3_round3_desktop_and_transport_regressions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover R3-F04, R3-F09, R3-F10, R3-F13, R3-F14, R3-F35, R3-F36, and R3-F37."""
    # ------------------------------------------------------------------
    # 1. R3-F04: Empty mcp_server_host normalizes to 127.0.0.1 in both update_single_config and _on_config_changed
    # ------------------------------------------------------------------
    f04_root = tmp_path / "f04_cfg"
    (f04_root / "config").mkdir(parents=True)
    shutil.copy2(ROOT / "config" / "config-example.json", f04_root / "config" / "config-example.json")
    f04_cfg_svc = ConfigService(str(f04_root))
    f04_svc = McpService(str(ROOT), session_name=f"pytest-f04-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    f04_starts: list[tuple[str, int]] = []
    monkeypatch.setattr(
        f04_svc,
        "start",
        lambda host="127.0.0.1", port=8765, **_kw: (
            setattr(f04_svc, "_host", host),
            setattr(f04_svc, "_port", int(port)),
            f04_starts.append((host, int(port))),
        )
        and True,
    )
    monkeypatch.setattr(f04_svc, "shutdown", lambda **_kw: None)
    with monkeypatch.context() as mp_f04:
        mp_f04.setenv("MTUI_MCP_FORCE_AUTOSTART", "1")
        mp_f04.delenv("MTUI_MCP_HOST", raising=False)
        mp_f04.delenv("MTUI_MCP_PORT", raising=False)
        mp_f04.delenv("MTUI_MCP_ENABLED", raising=False)
        mp_f04.setattr("services.get_config_service", lambda: f04_cfg_svc)
        mp_f04.setattr("app_logic.get_config_service", lambda: f04_cfg_svc)
        mp_f04.setattr(
            "app_logic.get_state_manager",
            lambda: types.SimpleNamespace(set_translating=lambda *_: None),
        )
        mp_f04.setattr("services.get_mcp_service", lambda: f04_svc)
        f04_logic = MainAppLogic()
        try:
            # Start service on 0.0.0.0 first, then save empty host via update_single_config -> must normalize to 127.0.0.1!
            f04_logic.update_single_config("app.mcp_server_host", "0.0.0.0")
            assert f04_starts[-1][0] == "0.0.0.0"

            f04_logic.update_single_config("app.mcp_server_host", "")
            assert f04_starts[-1][0] == "127.0.0.1"

            # Also verify _on_config_changed fallback branch (non-dict payload reads cfg_obj.app.mcp_server_host == "")
            f04_svc._host = "0.0.0.0"
            f04_logic._on_config_changed({})
            assert f04_starts[-1][0] == "127.0.0.1"
        finally:
            f04_logic.shutdown()
            f04_cfg_svc.shutdown()

    # ------------------------------------------------------------------
    # 2. R3-F10: accept_thread.start() failure rolls back key_file, lock_fd, and ownership so stop() cannot clobber takeover
    # ------------------------------------------------------------------
    f10_sess = f"pytest-f10-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    f10_addr, f10_key = gui_session_paths(f10_sess)
    bridge_a = McpGuiBridgeServer(session_name=f10_sess)
    orig_thread_start = threading.Thread.start

    def _fail_accept_thread_start(self_thread: threading.Thread) -> None:
        if self_thread.name == "mtui-gui-bridge-accept":
            raise RuntimeError("simulated accept_thread.start failure")
        orig_thread_start(self_thread)

    with monkeypatch.context() as mp_f10:
        mp_f10.setattr(threading.Thread, "start", _fail_accept_thread_start)
        with pytest.raises(RuntimeError, match="simulated accept_thread.start failure"):
            bridge_a.start()

    assert bridge_a.is_running is False
    assert bridge_a.address is None
    assert bridge_a.key_file is None
    assert bridge_a._lock_fd is None
    assert bridge_a._owns_key_file is False
    assert not f10_key.exists()

    # Another instance takes over the same session cleanly, and calling bridge_a.stop() afterward does NOT disturb bridge_b!
    f10_model = EditorModel()
    bridge_b = McpGuiBridgeServer(
        app_logic=types.SimpleNamespace(
            source_files=[],
            state_manager=types.SimpleNamespace(is_translating=lambda: False),
            current_worker=None,
        ),
        editor_model=f10_model,
        session_name=f10_sess,
    )
    try:
        assert bridge_b.start() is True
        b_key_bytes = f10_key.read_bytes()
        assert len(b_key_bytes) == 32
        bridge_a.stop()
        assert bridge_b.is_running is True
        assert f10_key.is_file() and f10_key.read_bytes() == b_key_bytes
        if os.name != "nt":
            assert os.path.exists(f10_addr)
        pages_res = _run_in_worker_pumping_qt(
            lambda: call_live_gui("list_pages", session_name=f10_sess, timeout_sec=2.0)
        )
        assert pages_res["status"] == "ok"
    finally:
        bridge_b.stop()

    # ------------------------------------------------------------------
    # 3. R3-F35, R3-F09, R3-F13, R3-F36, R3-F37: IPC framing, deadlines, image freshness, and page_edit_lock
    # ------------------------------------------------------------------
    r3_sess = f"pytest-r3-ipc-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MTUI_MCP_SESSION", r3_sess)
    ch_dir = tmp_path / "r3_ch"
    ch_dir.mkdir(parents=True)
    p1 = ch_dir / "001.png"
    p2 = ch_dir / "002.png"
    Image.fromarray(np.full((64, 64, 3), 240, dtype=np.uint8)).save(p1)
    Image.fromarray(np.full((64, 64, 3), 200, dtype=np.uint8)).save(p2)
    for page_p, txt in ((p1, "P1初版"), (p2, "P2初版")):
        pp = resolve_page_paths(page_p)
        save_page_json_atomic(
            pp,
            str(page_p.resolve()),
            {
                "regions": [
                    {
                        "lines": [[[8.0, 8.0], [56.0, 8.0], [56.0, 56.0], [8.0, 56.0]]],
                        "center": [32.0, 32.0],
                        "text": "原文",
                        "translation": txt,
                        "font_family": "FZHei-B01",
                        "font_size": 20,
                        "direction": "v",
                    }
                ]
            },
            auto_initial_snapshot=False,
        )

    r3_model = EditorModel()
    r3_model.apply_document_snapshot(
        DocumentSnapshot(
            source_path=str(p2.resolve()),
            image=np.full((64, 64, 3), 200, dtype=np.uint8),
            display_image_path=str(p2.resolve()),
            regions=[{"text": "原文", "translation": "P2初版"}],
        )
    )
    r3_hist = _GuiHistoryStub(r3_model)
    r3_hist.record_edit([{"text": "原文", "translation": "P2旧版"}])
    r3_model.replace_regions([{"text": "原文", "translation": "P2已编辑"}])

    save_calls: list[str] = []
    slow_save_delay = {"sec": 0.0, "started": threading.Event(), "finished": threading.Event()}
    res_mgr = ResourceManager()
    aux_exec = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    class _R3DocService:
        def __init__(self) -> None:
            self.controller = None
            self._aux_load_executor = aux_exec
            self._load_generation = 0
            self.resource_manager = res_mgr
            self.invalidated_prefetch: list[str] = []

        def _cancel_pending_load(self) -> int:
            self._load_generation += 1
            return self._load_generation

        def invalidate_prefetched_document(self, source_path: str) -> None:
            self.invalidated_prefetch.append(source_path)

        @staticmethod
        def resolve_editor_image_paths(candidate_path: str) -> tuple[str, str]:
            norm = str(Path(candidate_path).resolve())
            return norm, norm

        @staticmethod
        def _auto_save_on_switch_enabled() -> bool:
            return False

        @staticmethod
        def _auto_export_on_switch_enabled() -> bool:
            return False

        def clear_editor_state(self) -> None:
            self._cancel_pending_load()
            r3_model.clear_document()
            r3_hist.clear()

        def apply_load_result(self, payload: tuple[int, Any]) -> None:
            gen, snap = payload
            if gen == self._load_generation and isinstance(snap, DocumentSnapshot):
                r3_model.apply_document_snapshot(snap)
                r3_hist.mark_clean()

    r3_doc_service = _R3DocService()

    class _R3Controller:
        history_service = r3_hist
        export_service = types.SimpleNamespace(
            unfinished_count=lambda: 0,
            has_unsaved_changes=lambda: not r3_hist.is_clean(),
        )
        document_service = r3_doc_service

        @staticmethod
        def commit_pending_edits() -> None:
            pass

        @staticmethod
        def undo() -> None:
            r3_hist.step_undo()

        @staticmethod
        def redo() -> None:
            r3_hist.step_redo()

        @staticmethod
        def save_editor_state() -> bool:
            slow_save_delay["started"].set()
            if slow_save_delay["sec"] > 0:
                time.sleep(slow_save_delay["sec"])
            save_calls.append(str(r3_model.get_source_image_path()))
            r3_hist.mark_clean()
            slow_save_delay["finished"].set()
            return True

    r3_load_gate: dict[str, Any] = {
        "stall": False,
        "started": threading.Event(),
        "release": threading.Event(),
        "post_load_sleep": 0.0,
    }

    def _r3_worker_load(worker_self):
        src = Path(worker_self.image_path).resolve()
        img_res = worker_self.service.resource_manager.load_image(str(src))
        if r3_load_gate["stall"]:
            r3_load_gate["started"].set()
            r3_load_gate["release"].wait(timeout=5.0)
        if r3_load_gate["post_load_sleep"] > 0:
            time.sleep(r3_load_gate["post_load_sleep"])
        arr = np.asarray(img_res.image.convert("RGB"), dtype=np.uint8)
        pp = resolve_page_paths(src)
        raw = json.loads(pp.json_path.read_text(encoding="utf-8"))
        img_data = next(iter(raw.values()))
        return DocumentSnapshot(
            source_path=str(src),
            image=arr,
            display_image_path=str(src),
            regions=img_data.get("regions", []),
        )

    monkeypatch.setattr("editor.document_load_worker.DocumentLoadWorker.load", _r3_worker_load)

    r3_bridge = McpGuiBridgeServer(
        editor_controller=_R3Controller(),
        editor_model=r3_model,
        session_name=r3_sess,
        dispatch_timeout=25.0,
    )
    r3_addr, r3_key = gui_session_paths(r3_sess)
    try:
        assert r3_bridge.start() is True
        auth_bytes = r3_key.read_bytes()

        # 3a. R3-F35: Oversized frame over real Connection triggers OSError("bad message length") -> sends explicit 256 KiB error reply
        # Step 1: Fit within 8 KiB Windows Named Pipe buffer so client send_bytes returns and reads the full JSON error reply end-to-end
        r3_bridge.max_request_bytes = 2048
        oversize_peer = Client(r3_addr, authkey=None)
        try:
            answer_challenge(oversize_peer, auth_bytes)
            deliver_challenge(oversize_peer, auth_bytes)
            oversize_peer.send_bytes(b"x" * 4096)
            assert oversize_peer.poll(2.0) is True
            oversize_reply = json.loads(oversize_peer.recv_bytes().decode("utf-8"))
            assert "exceeds 256 KiB" in oversize_reply["error"]
        finally:
            r3_bridge.max_request_bytes = MAX_GUI_REQUEST_BYTES
            oversize_peer.close()

        # Step 2: Also verify default 256 KiB threshold (`MAX_GUI_REQUEST_BYTES + 128`) triggers _send_reply({"error": "...exceeds 256 KiB"})
        sent_replies_f35: list[dict[str, Any]] = []
        orig_send_reply = r3_bridge._send_reply
        with monkeypatch.context() as mp_f35:
            mp_f35.setattr(
                r3_bridge,
                "_send_reply",
                lambda conn_obj, payload: (sent_replies_f35.append(dict(payload)), orig_send_reply(conn_obj, payload)),
            )
            oversize_peer2 = Client(r3_addr, authkey=None)
            try:
                answer_challenge(oversize_peer2, auth_bytes)
                deliver_challenge(oversize_peer2, auth_bytes)
                try:
                    oversize_peer2.send_bytes(b"x" * (MAX_GUI_REQUEST_BYTES + 128))
                except BrokenPipeError:
                    # Expected on Windows Named Pipes (8 KiB kernel buffer aborts 256 KiB+ WriteFile once server rejects & closes)
                    pass
                else:
                    assert oversize_peer2.poll(2.0) is True
                    reply2 = json.loads(oversize_peer2.recv_bytes().decode("utf-8"))
                    assert "exceeds 256 KiB" in reply2["error"]
                dl_f35 = time.monotonic() + 2.0
                while not sent_replies_f35 and time.monotonic() < dl_f35:
                    time.sleep(0.01)
                assert sent_replies_f35 and "exceeds 256 KiB" in sent_replies_f35[-1]["error"]
            finally:
                oversize_peer2.close()

        # 3b. R3-F09: Expired queued `history_step` (undo) and unstarted `save_page` NEVER execute after blocked Qt main thread unblocks
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as blocked_pool:
            fut_undo = blocked_pool.submit(
                call_live_gui,
                "history_step",
                session_name=r3_sess,
                action="undo",
                timeout_sec=0.08,
            )
            fut_save = blocked_pool.submit(
                call_live_gui,
                "save_page",
                session_name=r3_sess,
                timeout_sec=0.08,
            )
            time.sleep(0.20)  # Intentionally keep Qt main thread blocked past both 80ms deadlines!
            with pytest.raises((TimeoutError, RuntimeError)):
                fut_undo.result(timeout=1.5)
            with pytest.raises((TimeoutError, RuntimeError)):
                fut_save.result(timeout=1.5)

        # Now pump Qt events: neither queued undo nor unstarted save_page may execute!
        for _ in range(10):
            _QAPP.processEvents()
            time.sleep(0.01)
        assert r3_model.get_regions()[0]["translation"] == "P2已编辑"
        assert save_calls == []

        # Meanwhile, an already-RUNNING `save_page` that exceeds request_deadline still returns `"in_progress"` and finishes saving!
        slow_save_delay["sec"] = 0.25
        slow_save_delay["started"].clear()
        slow_save_delay["finished"].clear()
        try:
            def _raw_save_with_short_deadline() -> dict[str, Any]:
                peer = Client(r3_addr, authkey=None)
                try:
                    answer_challenge(peer, auth_bytes)
                    deliver_challenge(peer, auth_bytes)
                    pkt = json.dumps(
                        {
                            "method": "save_page",
                            "args": {},
                            "timeout_sec": 0.08,
                            "deadline_monotonic": time.monotonic() + 0.08,
                        }
                    ).encode("utf-8")
                    peer.send_bytes(pkt)
                    assert peer.poll(2.0) is True
                    return json.loads(peer.recv_bytes().decode("utf-8"))
                finally:
                    peer.close()

            in_prog_reply = _run_in_worker_pumping_qt(_raw_save_with_short_deadline)
            assert in_prog_reply["result"]["status"] == "in_progress"
            assert in_prog_reply["result"]["save_in_progress"] is True
            assert slow_save_delay["finished"].wait(timeout=1.0) is True
            assert len(save_calls) == 1
        finally:
            slow_save_delay["sec"] = 0.0

        # 3c. R3-F13: IPC transit delay must NOT extend caller's fixed monotonic deadline on `open_page`
        orig_execute_rpc = r3_bridge._execute_rpc
        observed_deadlines: list[float] = []

        def _delayed_transit_execute_rpc(
            method: str,
            args: dict[str, Any],
            *,
            bridge_generation: Any = None,
            request_deadline: Any = None,
        ) -> dict[str, Any]:
            if request_deadline is not None:
                observed_deadlines.append(float(request_deadline))
            return orig_execute_rpc(
                method,
                args,
                bridge_generation=bridge_generation,
                request_deadline=request_deadline,
            )

        # Delay server post-WELCOME transit by 120ms so client sends timeout_sec≈0.17 while only ~50ms remain before deadline_monotonic!
        orig_auth_f13 = r3_bridge._authenticate_connection

        def _auth_then_transit_lag(*a: Any, **kw: Any) -> bool:
            ok = orig_auth_f13(*a, **kw)
            if ok:
                time.sleep(0.12)
            return ok

        r3_load_gate["post_load_sleep"] = 0.09
        try:
            with monkeypatch.context() as mp_f13:
                mp_f13.setattr(r3_bridge, "_execute_rpc", _delayed_transit_execute_rpc)
                mp_f13.setattr(r3_bridge, "_authenticate_connection", _auth_then_transit_lag)
                t_f13 = time.monotonic()
                with pytest.raises((TimeoutError, RuntimeError)):
                    _run_in_worker_pumping_qt(
                        lambda: call_live_gui(
                            "open_page",
                            session_name=r3_sess,
                            path=str(p1),
                            force=True,
                            timeout_sec=0.18,
                        )
                    )
                # Wait for server handler thread to finish and pump Qt events: page MUST remain p2!
                dl_f13 = time.monotonic() + 2.0
                while time.monotonic() < dl_f13:
                    _QAPP.processEvents()
                    if not [t for t in threading.enumerate() if t.name == "mtui-gui-bridge-conn" and t.is_alive()]:
                        break
                    time.sleep(0.01)
                _QAPP.processEvents()
                assert observed_deadlines and observed_deadlines[-1] <= t_f13 + 0.22
                assert os.path.normcase(r3_model.get_source_image_path()) == os.path.normcase(str(p2.resolve()))

            # Also verify near-256 KiB payload in call_live_gui preserves deadline_monotonic and oversized >256 KiB raises ValueError
            with pytest.raises(ValueError, match="exceeds 256 KiB"):
                call_live_gui("list_pages", session_name=r3_sess, pad="a" * (MAX_GUI_REQUEST_BYTES + 10))
            near_limit_pad = "b" * (MAX_GUI_REQUEST_BYTES - 140)
            t_near_start = time.monotonic()
            near_res = _run_in_worker_pumping_qt(
                lambda: call_live_gui(
                    "list_pages",
                    session_name=r3_sess,
                    timeout_sec=1.0,
                    pad=near_limit_pad,
                )
            )
            assert near_res["status"] == "ok"
            assert time.monotonic() - t_near_start < 2.0
        finally:
            r3_load_gate["post_load_sleep"] = 0.0

        # 3d. R3-F36: Source-image replacement during background decode is rejected, and subsequent reload evicts ResourceManager cache
        opened_p1 = _run_in_worker_pumping_qt(
            lambda: call_live_gui("open_page", session_name=r3_sess, path=str(p1), force=True)
        )
        assert opened_p1["page_name"] == "001.png"
        assert int(np.asarray(r3_model.get_image())[0, 0, 0]) == 240

        r3_load_gate["stall"] = True
        r3_load_gate["started"].clear()
        r3_load_gate["release"].clear()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as img_race_pool:
            img_race_fut = img_race_pool.submit(
                call_live_gui,
                "open_page",
                session_name=r3_sess,
                path=str(p1),
                reload=True,
                force=True,
            )
            deadline_ir = time.monotonic() + 5.0
            while not r3_load_gate["started"].is_set() and time.monotonic() < deadline_ir:
                _QAPP.processEvents()
                time.sleep(0.005)
            assert r3_load_gate["started"].is_set()

            # Replace ONLY source image pixels on disk (JSON untouched!) while worker is mid-load
            Image.fromarray(np.full((64, 64, 3), 95, dtype=np.uint8)).save(p1)
            r3_load_gate["stall"] = False
            r3_load_gate["release"].set()
            with pytest.raises(RuntimeError, match="changed while loading"):
                _run_in_worker_pumping_qt(img_race_fut.result)

        # Retry reload: ResourceManager cache must be invalidated so fresh 95-valued pixels are installed!
        reloaded_p1 = _run_in_worker_pumping_qt(
            lambda: call_live_gui("open_page", session_name=r3_sess, path=str(p1), reload=True, force=True)
        )
        assert reloaded_p1["page_name"] == "001.png"
        assert int(np.asarray(r3_model.get_image())[0, 0, 0]) == 95

        # 3e. R3-F37: `_install_on_main` synchronizes with `page_edit_lock` so an external MCP writer holding the lock cannot race between check and install
        writer_entered_lock = threading.Event()
        release_writer_lock = threading.Event()
        writer_finished = threading.Event()

        def _external_locked_writer() -> None:
            with page_edit_lock(p1, timeout_sec=5.0):
                writer_entered_lock.set()
                release_writer_lock.wait(timeout=5.0)
                pp_w = resolve_page_paths(p1)
                save_page_json_atomic(
                    pp_w,
                    str(p1.resolve()),
                    {
                        "regions": [
                            {
                                "lines": [[[8.0, 8.0], [56.0, 8.0], [56.0, 56.0], [8.0, 56.0]]],
                                "center": [32.0, 32.0],
                                "text": "原文",
                                "translation": "锁内原子新写入",
                                "font_family": "FZHei-B01",
                                "font_size": 20,
                                "direction": "v",
                            }
                        ]
                    },
                    auto_initial_snapshot=False,
                )
            writer_finished.set()

        r3_load_gate["stall"] = True
        r3_load_gate["started"].clear()
        r3_load_gate["release"].clear()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as lock_race_pool:
            open_fut = lock_race_pool.submit(
                call_live_gui,
                "open_page",
                session_name=r3_sess,
                path=str(p1),
                reload=True,
                force=True,
            )
            deadline_lr = time.monotonic() + 5.0
            while not r3_load_gate["started"].is_set() and time.monotonic() < deadline_lr:
                _QAPP.processEvents()
                time.sleep(0.005)
            assert r3_load_gate["started"].is_set()

            # Start external writer holding page_edit_lock(p1), release decode so _install_on_main reaches page_edit_lock
            writer_fut = lock_race_pool.submit(_external_locked_writer)
            assert writer_entered_lock.wait(timeout=2.0) is True
            r3_load_gate["stall"] = False
            r3_load_gate["release"].set()
            time.sleep(0.04)
            release_writer_lock.set()
            writer_fut.result(timeout=2.0)
            assert writer_finished.is_set() is True

            with pytest.raises(RuntimeError, match="changed while loading"):
                _run_in_worker_pumping_qt(open_fut.result)

        assert r3_model.get_regions()[0]["translation"] == "P1初版"
    finally:
        r3_load_gate["stall"] = False
        r3_load_gate["release"].set()
        r3_bridge.stop()
        res_mgr.cleanup_all()
        aux_exec.shutdown(wait=True)

    # ------------------------------------------------------------------
    # 4. R3-F14: EmbeddedMcpHttpServer(warm_up=True) cancels/cleans up warm-up if stop() races with start()
    # ------------------------------------------------------------------
    for proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(proxy_key, raising=False)

    warmup_entered = threading.Event()
    release_warmup = threading.Event()
    worker_events: list[str] = []

    def _racing_ensure_started() -> None:
        worker_events.append("ensure_started")
        warmup_entered.set()
        release_warmup.wait(timeout=5.0)

    def _racing_shutdown() -> None:
        worker_events.append("shutdown")

    rt_settings = get_runtime_settings()
    with monkeypatch.context() as mp_f14:
        mp_f14.setattr(rt_settings, "mtui_root", ROOT)
        mp_f14.setattr(
            "mtui_mcp.embedded_server.get_worker_client",
            lambda: types.SimpleNamespace(
                ensure_started=_racing_ensure_started,
                is_running=False,
                shutdown=_racing_shutdown,
            ),
        )
        race_srv = EmbeddedMcpHttpServer(host="127.0.0.1", port=0, mtui_root=ROOT, warm_up=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as srv_pool:
            start_fut = srv_pool.submit(race_srv.start, 5.0)
            assert warmup_entered.wait(timeout=5.0) is True
            # Call stop() while start() is inside ensure_started()!
            race_srv.stop(timeout=2.0)
            assert race_srv.is_running is False
            release_warmup.set()
            assert start_fut.result(timeout=3.0) is False
        assert race_srv.is_running is False
        # Both stop() and post-warmup rollback shutdown() ran so no orphan worker remains
        assert worker_events[-1] == "shutdown"
        assert worker_events.count("shutdown") >= 2

        # Sub-case B: stop() completes right as server.started flips True BEFORE ensure_started() runs -> skips warm-up altogether
        worker_events.clear()
        race_srv2 = EmbeddedMcpHttpServer(host="127.0.0.1", port=0, mtui_root=ROOT, warm_up=True)
        orig_gen_check = race_srv2._is_start_generation_active
        stopped_before_warmup = {"done": False}

        def _stop_right_before_warmup(gen: int, srv_obj: Any, thr_obj: Any) -> bool:
            if srv_obj.started and not stopped_before_warmup["done"]:
                stopped_before_warmup["done"] = True
                race_srv2.stop(timeout=2.0)
            return orig_gen_check(gen, srv_obj, thr_obj)

        mp_f14.setattr(race_srv2, "_is_start_generation_active", _stop_right_before_warmup)
        assert race_srv2.start(timeout=5.0) is False
        assert race_srv2.is_running is False
        assert "ensure_started" not in worker_events
