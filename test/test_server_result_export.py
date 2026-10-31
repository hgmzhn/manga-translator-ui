import _bootstrap  # noqa: F401

import base64
import io
import json
import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from manga_translator import Config
import manga_translator.server.core.group_management_service as group_mgmt_module
import manga_translator.server.core.middleware as middleware_module
import manga_translator.server.core.result_export_service as export_service
import manga_translator.server.core.task_manager as task_manager_module
import manga_translator.server.request_extraction as request_extraction_module
import manga_translator.server.routes.config as config_routes
import manga_translator.server.routes.translation as translation_routes

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_WEB_EXPORT_I18N_KEYS = (
    "web_server_output_title",
    "web_save_to_server_result",
    "web_server_output_folder_placeholder",
    "web_save_to_server_result_hint",
    "web_saved_to_server_batch",
    "web_can_save_to_server_result",
    "web_can_save_to_server_result_hint",
)


def _png_bytes(color: str = "red", mode: str = "RGB", size: tuple[int, int] = (12, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


def _png_data_uri(color: str = "blue") -> str:
    return "data:image/png;base64," + base64.b64encode(_png_bytes(color=color)).decode("ascii")


def _unpack_stream_frames(raw_stream: bytes) -> list[tuple[int, bytes]]:
    frames: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(raw_stream):
        assert offset + 5 <= len(raw_stream), "truncated stream frame header"
        status = raw_stream[offset]
        payload_len = struct.unpack(">I", raw_stream[offset + 1 : offset + 5])[0]
        end = offset + 5 + payload_len
        assert end <= len(raw_stream), "truncated stream frame payload"
        frames.append((status, raw_stream[offset + 5 : end]))
        offset = end
    return frames


class _FakeAccountService:
    def __init__(self, users: dict[str, SimpleNamespace]) -> None:
        self._users = users

    def get_user(self, username: str):
        return self._users.get(username)


class _FakeSessionService:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def verify_token(self, token: str):
        username = self._tokens.get(token)
        if not username:
            return None
        return SimpleNamespace(username=username)

    def update_activity(self, token: str) -> bool:
        return token in self._tokens


class _FakePermissionService:
    def increment_task_count(self, username: str) -> None:
        del username

    def decrement_task_count(self, username: str) -> None:
        del username

    def check_concurrent_limit(self, username: str) -> bool:
        del username
        return True

    def check_daily_quota(self, username: str) -> bool:
        del username
        return True

    def check_offline_translation_permission(self, username: str) -> bool:
        del username
        return False


class _FakeGroupService:
    def __init__(self, groups: dict[str, dict]) -> None:
        self._groups = groups

    def get_group(self, group_id: str):
        return self._groups.get(group_id)


def test_sanitize_relative_output_path_preserves_hierarchy_and_casing():
    assert export_service.sanitize_relative_output_path("MangaTitle/Chapter01/001.jpg") == [
        "MangaTitle",
        "Chapter01",
        "001.jpg",
    ]
    assert export_service.sanitize_relative_output_path(r"MangaTitle\Chapter01\001.jpg") == [
        "MangaTitle",
        "Chapter01",
        "001.jpg",
    ]
    assert export_service.sanitize_relative_output_path(None, allow_empty=True) == []
    assert export_service.sanitize_relative_output_path("   ", allow_empty=True) == []

    with pytest.raises(ValueError):
        export_service.sanitize_relative_output_path("", allow_empty=False)

    # Windows reserved device names and long segment truncation by UTF-8 bytes
    assert export_service.sanitize_relative_output_path("CON/lpt1.png") == ["_CON", "_lpt1.png"]
    long_name = ("A" * 240) + ".png"
    sanitized_long = export_service.sanitize_relative_output_path(long_name)
    assert len(sanitized_long) == 1
    assert len(sanitized_long[0].encode("utf-8")) <= export_service._MAX_SEGMENT_BYTES
    assert sanitized_long[0].endswith(".png")

    # Long Chinese character segment truncation by UTF-8 bytes (< 255 bytes for Linux ext4)
    long_chinese = ("漫画" * 50) + ".png"  # 100 Chinese chars = 300+ bytes in UTF-8
    sanitized_chinese = export_service.sanitize_relative_output_path(long_chinese)
    assert len(sanitized_chinese) == 1
    encoded_bytes = len(sanitized_chinese[0].encode("utf-8"))
    assert encoded_bytes <= export_service._MAX_SEGMENT_BYTES
    assert sanitized_chinese[0].endswith(".png")


@pytest.mark.parametrize(
    "malicious_path",
    [
        "../escape.png",
        "Chapter01/../../escape.png",
        "/etc/passwd",
        r"\Windows\System32\cmd.exe",
        r"C:\Windows\win.ini",
        "D:secret.png",
        "bad\x00name.png",
    ],
)
def test_sanitize_relative_output_path_rejects_traversal_and_absolute_paths(malicious_path: str):
    with pytest.raises(ValueError):
        export_service.sanitize_relative_output_path(malicious_path)


def test_resolve_server_result_file_path_rejects_symlink_escape(tmp_path: Path):
    result_root = tmp_path / "result"
    result_root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    escape_link = result_root / "linked_out"
    try:
        escape_link.symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation requires privilege on this host: {exc}")

    with pytest.raises(ValueError):
        export_service.resolve_server_result_file_path(
            source_path="001.png",
            output_folder="linked_out",
            result_root=result_root,
        )


def test_build_archive_entry_name_preserves_subfolders():
    assert (
        export_service.build_archive_entry_name("Chapter01/001.jpg", None, fallback_stem="translated_1")
        == "Chapter01/001.jpg"
    )
    assert (
        export_service.build_archive_entry_name("Chapter02/001.jpg", "png", fallback_stem="translated_2")
        == "Chapter02/001.png"
    )
    assert (
        export_service.build_archive_entry_name("../evil.jpg", "png", fallback_stem="translated_3")
        == "translated_3.png"
    )


def test_save_image_to_server_result_formats_subfolder_and_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    users = {
        "admin": SimpleNamespace(role="admin", group="admin", parameter_config={}),
        "reader": SimpleNamespace(role="user", group="default", parameter_config={}),
    }
    groups = {
        "admin": {"parameter_config": {"permissions": {"can_save_to_server_result": True}}},
        "default": {"parameter_config": {"permissions": {"can_save_to_server_result": False}}},
    }
    monkeypatch.setattr(
        middleware_module,
        "get_services",
        lambda: (_FakeAccountService(users), _FakeSessionService({}), _FakePermissionService()),
    )
    monkeypatch.setattr(
        group_mgmt_module,
        "get_group_management_service",
        lambda: _FakeGroupService(groups),
    )

    rgba_image = Image.new("RGBA", (16, 12), (255, 0, 0, 128))
    cfg = Config()
    cfg.cli.format = "jpg"
    cfg.cli.save_quality = 92

    with pytest.raises(PermissionError):
        export_service.save_image_to_server_result(
            rgba_image,
            username="reader",
            config=cfg,
            source_path="Chapter01/001.png",
            output_folder="MyManga",
            result_root=tmp_path,
        )

    saved_first = export_service.save_image_to_server_result(
        rgba_image,
        username="admin",
        config=cfg,
        source_path="Chapter01/001.png",
        output_folder="MyManga",
        result_root=tmp_path,
        overwrite=False,
    )
    assert saved_first == "MyManga/Chapter01/001.jpg"
    first_file = tmp_path / "MyManga" / "Chapter01" / "001.jpg"
    assert first_file.is_file()
    with Image.open(first_file) as saved_img:
        assert saved_img.format == "JPEG"
        assert saved_img.size == (16, 12)

    saved_second = export_service.save_image_to_server_result(
        rgba_image,
        username="admin",
        config=cfg,
        source_path="Chapter01/001.png",
        output_folder="MyManga",
        result_root=tmp_path,
        overwrite=False,
    )
    assert saved_second == "MyManga/Chapter01/001_1.jpg"
    assert (tmp_path / "MyManga" / "Chapter01" / "001_1.jpg").is_file()

    saved_overwrite = export_service.save_image_to_server_result(
        Image.new("RGB", (20, 20), "green"),
        username="admin",
        config=cfg,
        source_path="Chapter01/001.png",
        output_folder="MyManga",
        result_root=tmp_path,
        overwrite=True,
    )
    assert saved_overwrite == "MyManga/Chapter01/001.jpg"
    with Image.open(first_file) as overwritten_img:
        assert overwritten_img.size == (20, 20)


def test_permission_hierarchy_and_user_settings_endpoint(monkeypatch: pytest.MonkeyPatch):
    users = {
        "root_admin": SimpleNamespace(role="admin", group="default", parameter_config={}),
        "fresh_docker_admin_member": SimpleNamespace(role="user", group="admin", parameter_config={}),
        "regular_user": SimpleNamespace(role="user", group="default", parameter_config={}),
        "exporter_member": SimpleNamespace(role="user", group="exporters", parameter_config={}),
        "unlocked_user": SimpleNamespace(
            role="user",
            group="default",
            parameter_config={"permissions": {"can_save_to_server_result": True}},
        ),
        "revoked_exporter": SimpleNamespace(
            role="user",
            group="exporters",
            parameter_config={"permissions": {"can_save_to_server_result": False}},
        ),
    }
    groups = {
        "admin": {"parameter_config": {}},
        "default": {"parameter_config": {"permissions": {"can_save_to_server_result": False}}},
        "exporters": {"parameter_config": {"permissions": {"can_save_to_server_result": True}}},
    }
    tokens = {
        "tok-admin": "root_admin",
        "tok-regular": "regular_user",
        "tok-unlocked": "unlocked_user",
        "tok-revoked": "revoked_exporter",
    }

    fake_services = (_FakeAccountService(users), _FakeSessionService(tokens), _FakePermissionService())
    monkeypatch.setattr(middleware_module, "get_services", lambda: fake_services)
    monkeypatch.setattr(config_routes, "get_services", lambda: fake_services)
    monkeypatch.setattr(group_mgmt_module, "get_group_management_service", lambda: _FakeGroupService(groups))

    settings_dict = {"permissions": {"can_save_to_server_result": False}}
    assert export_service.check_save_to_server_result_permission("root_admin", settings_dict) is True
    assert export_service.check_save_to_server_result_permission("fresh_docker_admin_member", settings_dict) is True
    assert export_service.check_save_to_server_result_permission("regular_user", settings_dict) is False
    assert export_service.check_save_to_server_result_permission("exporter_member", settings_dict) is True
    assert export_service.check_save_to_server_result_permission("unlocked_user", settings_dict) is True
    assert export_service.check_save_to_server_result_permission("revoked_exporter", settings_dict) is False
    assert export_service.check_save_to_server_result_permission(None, settings_dict) is False

    app = FastAPI()
    app.include_router(config_routes.router)
    client = TestClient(app)

    defaults_resp = client.get("/config/defaults")
    assert defaults_resp.status_code == 200
    assert defaults_resp.json()["permissions"]["can_save_to_server_result"] is False

    anon_resp = client.get("/user/settings")
    assert anon_resp.status_code == 200
    assert anon_resp.json()["can_save_to_server_result"] is False

    admin_resp = client.get("/user/settings", headers={"X-Session-Token": "tok-admin"})
    assert admin_resp.status_code == 200
    assert admin_resp.json()["can_save_to_server_result"] is True

    regular_resp = client.get("/user/settings", headers={"X-Session-Token": "tok-regular"})
    assert regular_resp.status_code == 200
    assert regular_resp.json()["can_save_to_server_result"] is False

    unlocked_resp = client.get("/user/settings", headers={"X-Session-Token": "tok-unlocked"})
    assert unlocked_resp.status_code == 200
    assert unlocked_resp.json()["can_save_to_server_result"] is True

    revoked_resp = client.get("/user/settings", headers={"X-Session-Token": "tok-revoked"})
    assert revoked_resp.status_code == 200
    assert revoked_resp.json()["can_save_to_server_result"] is False


def test_batch_images_endpoint_permission_validation_and_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    users = {
        "admin": SimpleNamespace(role="admin", group="admin", parameter_config={}),
        "regular": SimpleNamespace(role="user", group="default", parameter_config={}),
    }
    groups = {
        "admin": {"parameter_config": {"permissions": {"can_save_to_server_result": True}}},
        "default": {"parameter_config": {"permissions": {"can_save_to_server_result": False}}},
    }
    fake_services = (_FakeAccountService(users), _FakeSessionService({}), _FakePermissionService())
    monkeypatch.setattr(middleware_module, "get_services", lambda: fake_services)
    monkeypatch.setattr(group_mgmt_module, "get_group_management_service", lambda: _FakeGroupService(groups))
    monkeypatch.setattr(export_service, "get_application_dir", lambda: str(tmp_path))

    current_user = {"name": "regular"}
    batch_calls: list[int] = []

    async def fake_verify_auth(req, config):
        del req, config
        return current_user["name"], "127.0.0.1"

    async def fake_apply_env(user_env_vars, config, admin_settings, username):
        del user_env_vars, config, admin_settings, username
        return {}

    async def fake_get_batch_ctx(req, config, images, batch_size, workflow, task_id=None):
        del req, config, batch_size, workflow, task_id
        batch_calls.append(len(images))
        return [
            SimpleNamespace(result=Image.new("RGB", (14, 14), "navy"), text_regions=[]),
            SimpleNamespace(result=Image.new("RGB", (18, 18), "teal"), text_regions=[]),
        ][: len(images)]

    async def fake_save_history(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(translation_routes, "verify_translation_auth", fake_verify_auth)
    monkeypatch.setattr(translation_routes, "apply_user_env_vars", fake_apply_env)
    monkeypatch.setattr(translation_routes, "track_task_start", lambda u: None)
    monkeypatch.setattr(translation_routes, "track_task_end", lambda u: None)
    monkeypatch.setattr(translation_routes, "log_translation_task_created", lambda *a, **kw: None)
    monkeypatch.setattr(translation_routes, "get_batch_ctx", fake_get_batch_ctx)
    monkeypatch.setattr(request_extraction_module, "save_translation_to_history", fake_save_history)

    app = FastAPI()
    app.include_router(translation_routes.router)
    client = TestClient(app)

    # 1. Unauthorized regular user -> 403 before running translation
    resp_forbidden = client.post(
        "/translate/batch/images",
        json={
            "images": [_png_data_uri("red")],
            "config": {},
            "filenames": ["Ch01/001.jpg"],
            "save_to_server": True,
            "output_folder": "MySeries",
        },
    )
    assert resp_forbidden.status_code == 403
    assert batch_calls == []
    assert not (tmp_path / "result").exists()

    # 2. Authorized admin with path traversal in output_folder or filenames -> 400 before running translation
    current_user["name"] = "admin"
    for bad_payload in (
        {
            "images": [_png_data_uri("red")],
            "config": {},
            "filenames": ["Ch01/001.jpg"],
            "save_to_server": True,
            "output_folder": "../escape",
        },
        {
            "images": [_png_data_uri("red")],
            "config": {},
            "filenames": ["../escape.jpg"],
            "save_to_server": True,
            "output_folder": "MySeries",
        },
    ):
        resp_bad = client.post("/translate/batch/images", json=bad_payload)
        assert resp_bad.status_code == 400
        assert batch_calls == []

    # 3. Authorized admin with multi-chapter folder hierarchy -> 200, writes files & preserves subfolder ZIP entries
    resp_ok = client.post(
        "/translate/batch/images",
        json={
            "images": [_png_data_uri("red"), _png_data_uri("green")],
            "config": {},
            "batch_size": 2,
            "filenames": ["Chapter01/001.jpg", "Chapter02/001.jpg"],
            "save_to_server": True,
            "output_folder": "OnePiece",
        },
    )
    assert resp_ok.status_code == 200
    assert resp_ok.headers.get("X-Server-Saved-Count") == "2"
    assert batch_calls == [2]

    saved_ch1 = tmp_path / "result" / "OnePiece" / "Chapter01" / "001.jpg"
    saved_ch2 = tmp_path / "result" / "OnePiece" / "Chapter02" / "001.jpg"
    assert saved_ch1.is_file()
    assert saved_ch2.is_file()

    with zipfile.ZipFile(io.BytesIO(resp_ok.content), "r") as zf:
        assert sorted(zf.namelist()) == ["Chapter01/001.jpg", "Chapter02/001.jpg"]


def test_stream_image_form_endpoint_emits_saved_to_server_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    users = {
        "admin": SimpleNamespace(role="admin", group="admin", parameter_config={}),
        "regular": SimpleNamespace(role="user", group="default", parameter_config={}),
    }
    groups = {
        "admin": {"parameter_config": {"permissions": {"can_save_to_server_result": True}}},
        "default": {"parameter_config": {"permissions": {"can_save_to_server_result": False}}},
    }
    fake_services = (_FakeAccountService(users), _FakeSessionService({}), _FakePermissionService())
    monkeypatch.setattr(middleware_module, "get_services", lambda: fake_services)
    monkeypatch.setattr(group_mgmt_module, "get_group_management_service", lambda: _FakeGroupService(groups))
    monkeypatch.setattr(export_service, "get_application_dir", lambda: str(tmp_path))

    current_user = {"name": "regular"}

    async def fake_verify_auth(req, config):
        del req, config
        return current_user["name"], "127.0.0.1"

    async def fake_apply_env(user_env_vars, config, admin_settings, username):
        del user_env_vars, config, admin_settings, username
        return {}

    async def fake_run_in_translator_thread(func, pil_image, config, task_id, cancel_callback):
        del func, pil_image, config, task_id, cancel_callback
        return SimpleNamespace(
            result=Image.new("RGB", (24, 16), "purple"),
            text_regions=[],
            success=True,
        )

    async def fake_save_history(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(translation_routes, "verify_translation_auth", fake_verify_auth)
    monkeypatch.setattr(translation_routes, "apply_user_env_vars", fake_apply_env)
    monkeypatch.setattr(translation_routes, "log_translation_task_created", lambda *a, **kw: None)
    monkeypatch.setattr(task_manager_module, "run_in_translator_thread", fake_run_in_translator_thread)
    monkeypatch.setattr(request_extraction_module, "save_translation_to_history", fake_save_history)

    app = FastAPI()
    app.include_router(translation_routes.router)
    client = TestClient(app)

    # 1. Unauthorized regular user -> 403
    resp_forbidden = client.post(
        "/translate/with-form/image/stream",
        files={"image": ("005.png", _png_bytes(), "image/png")},
        data={
            "config": "{}",
            "user_env_vars": "{}",
            "save_to_server": "true",
            "output_folder": "SoloManga",
            "relative_path": "Vol01/005.png",
        },
    )
    assert resp_forbidden.status_code == 403

    # 2. Authorized admin with traversal relative_path -> 400
    current_user["name"] = "admin"
    resp_bad = client.post(
        "/translate/with-form/image/stream",
        files={"image": ("005.png", _png_bytes(), "image/png")},
        data={
            "config": "{}",
            "user_env_vars": "{}",
            "save_to_server": "true",
            "output_folder": "SoloManga",
            "relative_path": "../005.png",
        },
    )
    assert resp_bad.status_code == 400

    # 3. Authorized admin valid streaming export -> 200, writes file, emits saved_to_server frame + PNG result frame
    resp_ok = client.post(
        "/translate/with-form/image/stream",
        files={"image": ("005.png", _png_bytes(), "image/png")},
        data={
            "config": "{}",
            "user_env_vars": "{}",
            "save_to_server": "true",
            "output_folder": "SoloManga",
            "relative_path": "Vol01/005.png",
        },
    )
    assert resp_ok.status_code == 200

    expected_file = tmp_path / "result" / "SoloManga" / "Vol01" / "005.png"
    assert expected_file.is_file()

    frames = _unpack_stream_frames(resp_ok.content)
    progress_payloads = [
        json.loads(payload.decode("utf-8"))
        for status, payload in frames
        if status == 1
    ]
    saved_frames = [p for p in progress_payloads if p.get("stage") == "saved_to_server"]
    assert len(saved_frames) == 1
    assert saved_frames[0]["saved_path"] == "result/SoloManga/Vol01/005.png"

    result_frames = [payload for status, payload in frames if status == 0]
    assert len(result_frames) == 1
    with Image.open(io.BytesIO(result_frames[0])) as streamed_img:
        assert streamed_img.size == (24, 16)


def test_server_result_export_failure_propagation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    users = {
        "admin": SimpleNamespace(role="admin", group="admin", parameter_config={}),
    }
    groups = {
        "admin": {"parameter_config": {"permissions": {"can_save_to_server_result": True}}},
    }
    fake_services = (_FakeAccountService(users), _FakeSessionService({}), _FakePermissionService())
    monkeypatch.setattr(middleware_module, "get_services", lambda: fake_services)
    monkeypatch.setattr(group_mgmt_module, "get_group_management_service", lambda: _FakeGroupService(groups))
    monkeypatch.setattr(export_service, "get_application_dir", lambda: str(tmp_path))

    async def fake_verify_auth(req, config):
        del req, config
        return "admin", "127.0.0.1"

    async def fake_apply_env(user_env_vars, config, admin_settings, username):
        del user_env_vars, config, admin_settings, username
        return {}

    async def fake_run_in_translator_thread(*args, **kwargs):
        return SimpleNamespace(
            result=Image.new("RGB", (20, 20), "cyan"),
            text_regions=[],
            success=True,
        )

    async def fake_get_batch_ctx(req, config, images, batch_size, workflow, task_id=None):
        del req, config, batch_size, workflow, task_id
        return [
            SimpleNamespace(result=Image.new("RGB", (10, 10), "yellow"), text_regions=[]),
            SimpleNamespace(result=Image.new("RGB", (10, 10), "orange"), text_regions=[]),
        ][: len(images)]

    async def fake_save_history(*args, **kwargs):
        return None

    monkeypatch.setattr(translation_routes, "verify_translation_auth", fake_verify_auth)
    monkeypatch.setattr(translation_routes, "apply_user_env_vars", fake_apply_env)
    monkeypatch.setattr(translation_routes, "track_task_start", lambda u: None)
    monkeypatch.setattr(translation_routes, "track_task_end", lambda u: None)
    monkeypatch.setattr(translation_routes, "log_translation_task_created", lambda *a, **kw: None)
    monkeypatch.setattr(task_manager_module, "run_in_translator_thread", fake_run_in_translator_thread)
    monkeypatch.setattr(translation_routes, "get_batch_ctx", fake_get_batch_ctx)
    monkeypatch.setattr(request_extraction_module, "save_translation_to_history", fake_save_history)

    # 1. 模拟磁盘写满或无权限时，流式接口应当发出 stage="save_to_server" 的 status=2 错误帧，并中止发送结果
    def fail_save(*args, **kwargs):
        raise OSError("Disk full: no space left on device")

    monkeypatch.setattr(export_service, "save_image_to_server_result", fail_save)
    monkeypatch.setattr(translation_routes, "save_image_to_server_result", fail_save)

    app = FastAPI()
    app.include_router(translation_routes.router)
    client = TestClient(app)

    stream_resp = client.post(
        "/translate/with-form/image/stream",
        files={"image": ("001.png", _png_bytes(), "image/png")},
        data={
            "config": "{}",
            "user_env_vars": "{}",
            "save_to_server": "true",
            "output_folder": "FullDiskTest",
            "relative_path": "001.png",
        },
    )
    assert stream_resp.status_code == 200
    frames = _unpack_stream_frames(stream_resp.content)
    error_frames = [
        json.loads(payload.decode("utf-8"))
        for status, payload in frames
        if status == 2
    ]
    assert len(error_frames) == 1
    assert error_frames[0].get("stage") == "save_to_server"
    assert "Disk full" in error_frames[0].get("error", "")
    # 不应发送结果图片帧
    assert not any(status == 0 for status, _ in frames)

    # 2. 批量接口全部保存失败时，应当向客户端返回 500
    batch_resp = client.post(
        "/translate/batch/images",
        json={
            "images": [_png_data_uri("red")],
            "config": {},
            "filenames": ["001.jpg"],
            "save_to_server": True,
            "output_folder": "FullDiskBatch",
        },
    )
    assert batch_resp.status_code == 500
    assert "Disk full" in batch_resp.json().get("detail", "")

    # 3. 批量接口部分成功、部分失败时，返回 200 并携带 X-Server-Save-Error 与已保存计数
    saved_counter = {"calls": 0}

    def partial_save(*args, **kwargs):
        saved_counter["calls"] += 1
        if saved_counter["calls"] == 1:
            return "FullDiskBatch/001.jpg"
        raise OSError("Write error on image 2")

    monkeypatch.setattr(translation_routes, "save_image_to_server_result", partial_save)
    monkeypatch.setattr(export_service, "save_image_to_server_result", partial_save)
    batch_partial_resp = client.post(
        "/translate/batch/images",
        json={
            "images": [_png_data_uri("red"), _png_data_uri("green")],
            "config": {},
            "filenames": ["001.jpg", "002.jpg"],
            "save_to_server": True,
            "output_folder": "FullDiskBatch",
        },
    )
    assert batch_partial_resp.status_code == 200
    assert batch_partial_resp.headers.get("X-Server-Saved-Count") == "1"
    assert "Write error on image 2" in batch_partial_resp.headers.get("X-Server-Save-Error", "")

    # 4. 非流式单图接口保存失败时返回 500
    monkeypatch.setattr(translation_routes, "save_image_to_server_result", fail_save)
    monkeypatch.setattr(export_service, "save_image_to_server_result", fail_save)
    single_resp = client.post(
        "/translate/with-form/image",
        files={"image": ("001.png", _png_bytes(), "image/png")},
        data={
            "config": "{}",
            "save_to_server": "true",
            "output_folder": "FullDiskSingle",
            "relative_path": "001.png",
        },
    )
    assert single_resp.status_code == 500
    assert "Disk full" in single_resp.json().get("detail", "")


def test_all_locale_files_and_group_config_have_server_output_keys():
    locales_dir = ROOT / "desktop_qt_ui" / "locales"
    locale_names = ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES")
    for locale_name in locale_names:
        locale_path = locales_dir / f"{locale_name}.json"
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        for key in EXPECTED_WEB_EXPORT_I18N_KEYS:
            assert key in data, f"Missing {key} in {locale_path.name}"
            assert isinstance(data[key], str) and data[key].strip(), f"Empty {key} in {locale_path.name}"

    group_config_path = ROOT / "manga_translator" / "server" / "data" / "group_config.json"
    group_config = json.loads(group_config_path.read_text(encoding="utf-8"))
    admin_perms = group_config["groups"]["admin"]["parameter_config"]["permissions"]
    default_perms = group_config["groups"]["default"]["parameter_config"]["permissions"]
    assert admin_perms["can_save_to_server_result"] is True
    assert default_perms["can_save_to_server_result"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
