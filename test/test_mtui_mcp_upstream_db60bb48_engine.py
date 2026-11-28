"""Unit & adapter regression tests for upstream manga-translator-ui HEAD db60bb48."""

import _bootstrap  # noqa: F401, I001

import io
import json
import os
import subprocess
import sys
import threading
import time
import types
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from mtui_mcp import server as server_mod
from mtui_mcp.config import get_runtime_settings
from mtui_mcp.gui_bridge import call_live_gui, is_live_gui_session_available
from mtui_mcp.local_inpaint import (
    add_region_to_page,
    apply_incremental_local_repair,
    local_inpaint_crop,
    manage_page_paste_overlays,
    merge_regions_on_page,
)
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import encode_mask_base64, load_page_json, save_page_json_atomic
from mtui_mcp.worker import engine_worker
from mtui_mcp.worker.client import MtuiWorkerClient, get_worker_client


def _make_sample_page(tmp_path: Path, name: str = "001.png", size_hw: tuple[int, int] = (320, 260)) -> Path:
    h, w = size_hw
    page_path = tmp_path / name
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = np.dstack(
        [
            np.clip(180 + (xx * 50) // max(1, w), 0, 255),
            np.clip(195 + (yy * 40) // max(1, h), 0, 255),
            np.full((h, w), 225, dtype=np.int32),
        ]
    ).astype(np.uint8)
    rgb[105:135, 95:145] = (15, 20, 25)
    Image.fromarray(rgb).save(page_path)
    return page_path


def test_local_inpaint_blur_adaptive_explicit_and_clamped(tmp_path: Path) -> None:
    page_path = _make_sample_page(tmp_path)
    base_rgb = np.asarray(Image.open(page_path).convert("RGB"), dtype=np.uint8)
    mask = np.zeros(base_rgb.shape[:2], dtype=np.uint8)
    mask[100:140, 90:150] = 255

    out_auto = local_inpaint_crop(base_rgb, mask, method="blur", blur_radius=0)
    assert out_auto.shape == base_rgb.shape
    assert out_auto.dtype == np.uint8
    assert float(np.mean(out_auto[105:135, 95:145])) > 165.0
    assert np.array_equal(out_auto[0:20, 0:20], base_rgb[0:20, 0:20])

    out_200 = local_inpaint_crop(base_rgb, mask, method="blur", blur_radius=200)
    out_999 = local_inpaint_crop(base_rgb, mask, method="blur", blur_radius=999)
    assert np.array_equal(out_200, out_999)


def test_apply_incremental_local_repair_blur_full_page_and_removed_restore(tmp_path: Path) -> None:
    page_path = _make_sample_page(tmp_path, size_hw=(400, 400))
    base_rgb = np.asarray(Image.open(page_path).convert("RGB"), dtype=np.uint8)

    old_mask = np.zeros((400, 400), dtype=np.uint8)
    old_mask[100:140, 90:150] = 255
    old_mask[280:330, 260:340] = 255
    prev_inpaint = local_inpaint_crop(base_rgb, old_mask, method="blur", blur_radius=90)

    new_mask = np.zeros((400, 400), dtype=np.uint8)
    new_mask[100:145, 90:155] = 255
    added = np.where((new_mask > 0) & (old_mask == 0), 255, 0).astype(np.uint8)
    removed = np.where((old_mask > 0) & (new_mask == 0), 255, 0).astype(np.uint8)

    repaired = apply_incremental_local_repair(
        base_rgb,
        previous_inpainted_rgb=prev_inpaint,
        new_mask=new_mask,
        added_delta=added,
        removed_delta=removed,
        method="blur",
        padding=48,
        blur_radius=90,
    )
    expected_full = local_inpaint_crop(base_rgb, new_mask, method="blur", blur_radius=90)
    assert np.array_equal(repaired, expected_full)
    assert np.array_equal(repaired[260:360, 240:360], base_rgb[260:360, 240:360])


def test_merge_regions_pops_perspective_quad_fields(tmp_path: Path) -> None:
    page_path = _make_sample_page(tmp_path)
    paths = resolve_page_paths(page_path)
    image_data = {
        "regions": [
            {
                "lines": [[[120.0, 60.0], [160.0, 60.0], [160.0, 180.0], [120.0, 180.0]]],
                "texts": ["前半"],
                "text": "前半",
                "translation": "前半句",
                "center": [140.0, 120.0],
                "angle": 0.0,
                "font_size": 24,
                "has_custom_white_frame": True,
                "white_frame_rect_local": [-20.0, -60.0, 20.0, 60.0],
                "white_frame_quad_local": [[-18.0, -58.0], [22.0, -60.0], [20.0, 60.0], [-20.0, 58.0]],
                "render_box_rect_local": [-20.0, -60.0, 20.0, 60.0],
                "render_box_quad_local": [[-18.0, -58.0], [22.0, -60.0], [20.0, 60.0], [-20.0, 58.0]],
            },
            {
                "lines": [[[75.0, 60.0], [115.0, 60.0], [115.0, 180.0], [75.0, 180.0]]],
                "texts": ["後半"],
                "text": "後半",
                "translation": "后半句",
                "center": [95.0, 120.0],
                "angle": 0.0,
                "font_size": 24,
            },
        ]
    }
    save_page_json_atomic(paths, str(page_path.resolve()), image_data, auto_initial_snapshot=False)

    refreshed, _ = merge_regions_on_page(page_path, [0, 1], force_merge=True)
    _, merged_data = load_page_json(refreshed)
    assert len(merged_data["regions"]) == 1
    merged_reg = merged_data["regions"][0]
    assert merged_reg["has_custom_white_frame"] is False
    assert "white_frame_rect_local" not in merged_reg
    assert "white_frame_quad_local" not in merged_reg
    assert "render_box_rect_local" not in merged_reg
    assert "render_box_quad_local" not in merged_reg


def test_add_region_writes_bg_colors_not_legacy_bg_color(tmp_path: Path) -> None:
    page_path = _make_sample_page(tmp_path)
    paths = resolve_page_paths(page_path)
    save_page_json_atomic(paths, str(page_path.resolve()), {"regions": []}, auto_initial_snapshot=False)

    refreshed, summary = add_region_to_page(
        page_path,
        bbox_xyxy=[80, 90, 160, 150],
        source_text="テスト",
        translation="测试",
        stroke_color="#123456",
        auto_mask_ink=False,
    )
    _, saved_data = load_page_json(refreshed)
    reg = saved_data["regions"][0]
    assert reg["bg_colors"] == [0x12, 0x34, 0x56]
    assert "bg_color" not in reg
    assert summary["stroke_color"] == "#123456"


def test_manage_paste_overlays_supports_z_flip_h_flip_v(tmp_path: Path) -> None:
    page_path = _make_sample_page(tmp_path)
    paths = resolve_page_paths(page_path)
    save_page_json_atomic(paths, str(page_path.resolve()), {"regions": []}, auto_initial_snapshot=False)

    refreshed, res = manage_page_paste_overlays(
        page_path,
        "add_clone_patch",
        source_rect_xyxy=[10, 10, 50, 40],
        target_center_xy=[120, 130],
        z=7,
        flip_h=True,
        flip_v=True,
    )
    added = res["added_overlay"]
    assert added["z"] == 7
    assert added["flip_h"] is True
    assert added["flip_v"] is True

    _, saved = load_page_json(refreshed)
    stored = saved["paste_overlays"][0]
    assert stored["z"] == 7
    assert stored["flip_h"] is True
    assert stored["flip_v"] is True


def test_atomic_replace_file_retries_transient_windows_locks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "source.tmp"
    dst = tmp_path / "dest.json"
    src.write_text('{"ok": true}', encoding="utf-8")
    dst.write_text('{"ok": false}', encoding="utf-8")

    real_replace = os.replace
    attempts = {"count": 0}

    def flaky_replace(a: str, b: str) -> None:
        attempts["count"] += 1
        if attempts["count"] <= 3:
            err = PermissionError(13, "Access is denied")
            err.winerror = 5 if attempts["count"] % 2 else 32
            raise err
        real_replace(a, b)

    monkeypatch.setattr(
        engine_worker, "os", types.SimpleNamespace(**{**os.__dict__, "name": "nt", "replace": flaky_replace})
    )
    engine_worker._atomic_replace_file(str(src), str(dst), retry_delays=(0.001, 0.001, 0.001, 0.001))
    assert attempts["count"] == 4
    assert json.loads(dst.read_text(encoding="utf-8")) == {"ok": True}


def test_persist_last_export_dir_also_updates_last_output_path(tmp_path: Path) -> None:
    json_path = tmp_path / "001_translations.json"
    out_file = tmp_path / "out_dir" / "001.webp"
    json_path.write_text(
        json.dumps({"001.png": {"regions": [], "last_output_path": "C:/stale/001.png"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    engine_worker.EngineRuntime._persist_last_export_dir(
        str(json_path),
        str(out_file.parent),
        output_path=str(out_file),
    )
    updated = json.loads(json_path.read_text(encoding="utf-8"))["001.png"]
    assert updated["last_export_dir"] == os.path.normpath(str(out_file.parent))
    assert updated["last_output_path"] == os.path.normpath(str(out_file))


def test_client_error_message_and_gui_bridge_missing_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_runtime_settings()
    orig_root, orig_py = settings.mtui_root, settings.mtui_python
    try:
        settings.mtui_root = None
        settings.mtui_python = None
        client = MtuiWorkerClient()
        with pytest.raises(RuntimeError, match=r"mtui_status\(mtui_root=\.\.\.\)"):
            client.ensure_started()
    finally:
        settings.mtui_root = orig_root
        settings.mtui_python = orig_py

    sess_name = f"nonexistent-test-session-{os.getpid()}"
    assert is_live_gui_session_available(sess_name) is False
    with pytest.raises(RuntimeError, match="GUI session"):
        call_live_gui("list_pages", session_name=sess_name)


def test_setup_environment_and_run_cli_pipeline_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for env_key in (
        "QT_QPA_PLATFORM",
        "TQDM_DISABLE",
        "QT_QPA_FONTDIR",
        "PYTHONIOENCODING",
    ):
        prev_val = os.environ.get(env_key)
        if prev_val is None:
            monkeypatch.setenv(env_key, "")
            monkeypatch.delenv(env_key)
        else:
            monkeypatch.setenv(env_key, prev_val)
    for unset_key in ("PYTORCH_ALLOC_CONF", "QT_IMAGEIO_MAXALLOC"):
        if unset_key not in os.environ:
            monkeypatch.setenv(unset_key, "")
        monkeypatch.delenv(unset_key)
    monkeypatch.setattr(sys, "path", list(sys.path))
    (tmp_path / "fonts").mkdir(exist_ok=True)
    prev_cwd = os.getcwd()
    try:
        engine_worker._setup_environment(str(tmp_path))
        assert os.environ.get("PYTORCH_ALLOC_CONF") == "expandable_segments:True"
        assert os.environ.get("QT_IMAGEIO_MAXALLOC") == "1024"
        assert os.environ.get("QT_QPA_PLATFORM") is not None
        assert os.environ.get("TQDM_DISABLE") is not None
    finally:
        os.chdir(prev_cwd)

    rt = engine_worker.EngineRuntime(str(tmp_path))
    vram_calls = {"count": 0}

    def _count_release_vram() -> dict[str, Any]:
        vram_calls["count"] += 1
        return {"status": "ok"}

    monkeypatch.setattr(rt, "release_vram", _count_release_vram)

    # R2-F53: Invalid CLI arguments must fail BEFORE unloading warm GPU models
    with pytest.raises(ValueError, match="inputs"):
        rt.run_cli_pipeline({"inputs": []})
    with pytest.raises(ValueError, match="memory_percent"):
        rt.run_cli_pipeline({"inputs": [str(tmp_path / "raw")], "memory_percent": float("nan")})
    with pytest.raises(ValueError, match="batch_size"):
        rt.run_cli_pipeline({"inputs": [str(tmp_path / "raw")], "batch_size": 0})
    assert vram_calls["count"] == 0

    captured: dict[str, Any] = {}

    class FakePopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            captured["cmd"] = list(cmd)
            self.stdout = io.StringIO("x" * 5000 + "done\n")
            self.stderr = io.StringIO("")
            self.returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def poll(self) -> int:
            return 0

        def kill(self) -> None:
            pass

        def terminate(self) -> None:
            pass

    # R2-F40: `run_cli_pipeline` always executes `_run_subprocess_bounded_tails` even if `subprocess.run` is patched
    def _unexpected_subprocess_run(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("run_cli_pipeline must use _run_subprocess_bounded_tails, not subprocess.run")

    monkeypatch.setattr(engine_worker.subprocess, "run", _unexpected_subprocess_run)
    monkeypatch.setattr(engine_worker.subprocess, "Popen", FakePopen)
    res = rt.run_cli_pipeline(
        {
            "inputs": [str(tmp_path / "raw")],
            "output_dir": str(tmp_path / "out"),
            "overwrite": True,
            "use_gpu": True,
            "disable_onnx_gpu": True,
            "output_format": "webp",
            "batch_size": 4,
            "attempts": 2,
            "subprocess": True,
            "memory_limit": 8192,
            "memory_percent": 80.0,
            "batch_per_restart": 15,
        }
    )
    assert res["status"] == "ok"
    assert vram_calls["count"] == 1
    assert len(res["stdout_tail"]) == 4000
    assert res["stdout_tail"].endswith("done\n")
    cmd = captured["cmd"]
    assert "--disable-onnx-gpu" in cmd
    assert "--subprocess" in cmd
    assert cmd[cmd.index("--batch-size") + 1] == "4"
    assert cmd[cmd.index("--attempts") + 1] == "2"
    assert cmd[cmd.index("--memory-limit") + 1] == "8192"
    assert cmd[cmd.index("--memory-percent") + 1] == "80"
    assert cmd[cmd.index("--batch-per-restart") + 1] == "15"


def test_render_page_perspective_quad_psd_script_only_and_last_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _make_sample_page(tmp_path)
    inpainted_path = tmp_path / "001_inpainted.png"
    output_path = tmp_path / "rendered" / "001.png"
    json_path = tmp_path / "001_translations.json"
    Image.open(page_path).save(inpainted_path)
    json_path.write_text(json.dumps({str(page_path): {"regions": []}}, ensure_ascii=False), encoding="utf-8")

    quad_local = [[-30.0, -50.0], [32.0, -46.0], [28.0, 52.0], [-34.0, 48.0]]
    reg_persp = {
        "lines": [[[70.0, 50.0], [130.0, 50.0], [130.0, 150.0], [70.0, 150.0]]],
        "center": [100.0, 100.0],
        "angle": 0.0,
        "has_custom_white_frame": True,
        "white_frame_quad_local": quad_local,
        "translation": "透视测试",
    }
    reg_rect = {
        "lines": [[[150.0, 60.0], [200.0, 60.0], [200.0, 160.0], [150.0, 160.0]]],
        "center": [175.0, 110.0],
        "angle": 0.0,
        "translation": "普通框",
    }

    mask = np.zeros((320, 260), dtype=np.uint8)
    mask[60:150, 70:130] = 255
    captured_jobs: list[Any] = []
    wf_called_on: list[dict[str, Any]] = []

    class FakeExportJob:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

        def release_resources(self) -> None:
            pass

    class FakeExportControllerSvc:
        @staticmethod
        def _perspective_quad_world(region: dict[str, Any]) -> Any:
            ql = region.get("white_frame_quad_local")
            if not ql:
                return None
            cx, cy = region["center"]
            return [[cx + p[0], cy + p[1]] for p in ql]

        @staticmethod
        def apply_white_frame_center(region: dict[str, Any]) -> None:
            wf_called_on.append(region)

    class FakeExportBase:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class FakeInpaintKey:
        def __init__(self, document_id: int, mask_revision: int) -> None:
            self.document_id = document_id
            self.mask_revision = mask_revision

    fake_file_svc = types.SimpleNamespace(
        load_translation_json=lambda _p: (
            [reg_persp, reg_rect],
            mask,
            (260, 320),
            {"paint": None, "stamp": None, "paste_overlays": []},
        )
    )
    fake_render_param_svc = types.SimpleNamespace(
        clear_cache=lambda: None,
        export_parameters_for_backend=lambda _idx, _rc: {"font_size": 24},
    )

    monkeypatch.setitem(sys.modules, "manga_translator", types.ModuleType("manga_translator"))
    monkeypatch.setitem(sys.modules, "manga_translator.utils", types.ModuleType("manga_translator.utils"))
    monkeypatch.setitem(
        sys.modules,
        "manga_translator.utils.path_manager",
        types.SimpleNamespace(find_paint_overlay_path=lambda _p: None),
    )
    monkeypatch.setitem(sys.modules, "editor", types.ModuleType("editor"))
    monkeypatch.setitem(
        sys.modules,
        "editor.controller_export_service",
        types.SimpleNamespace(EditorControllerExportService=FakeExportControllerSvc, ExportJob=FakeExportJob),
    )
    monkeypatch.setitem(sys.modules, "editor.document_state", types.SimpleNamespace(ExportBase=FakeExportBase))
    monkeypatch.setitem(sys.modules, "editor.inpaint_state", types.SimpleNamespace(InpaintKey=FakeInpaintKey))
    monkeypatch.setitem(
        sys.modules,
        "services",
        types.SimpleNamespace(
            get_file_service=lambda: fake_file_svc,
            get_render_parameter_service=lambda: fake_render_param_svc,
            get_config_service=lambda: None,
        ),
    )

    rt = engine_worker.EngineRuntime(str(tmp_path))
    rt._initialized = True
    rt._export_service = types.SimpleNamespace(
        execute_export_job=lambda job: (captured_jobs.append(job) or types.SimpleNamespace(error=None))
    )

    res = rt.render_page(
        {
            "source_path": str(page_path),
            "display_base_path": str(page_path),
            "inpainted_path": str(inpainted_path),
            "output_path": str(output_path),
            "json_path": str(json_path),
            "psd_script_only": True,
        }
    )
    assert res["status"] == "ok"
    assert res["export_psd"] is True
    assert res["psd_script_only"] is True
    assert len(captured_jobs) == 1
    job = captured_jobs[0]
    assert job.config["cli"]["export_editable_psd"] is True
    assert job.config["cli"]["psd_script_only"] is True

    r0, r1 = job.regions[0], job.regions[1]
    assert "perspective_quad" in r0
    assert len(r0["perspective_quad"]) == 4
    assert len(wf_called_on) == 1
    assert "perspective_quad" not in r1

    saved_meta = json.loads(json_path.read_text(encoding="utf-8"))[str(page_path)]
    assert saved_meta["last_output_path"] == os.path.normpath(str(output_path))


def test_incremental_neural_inpaint_blur_full_page_and_config_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _make_sample_page(tmp_path, size_hw=(300, 240))
    inpainted_path = tmp_path / "001_inpainted.png"
    Image.fromarray(np.zeros((300, 240, 3), dtype=np.uint8)).save(inpainted_path)

    class FakeInpainter(str, Enum):
        lama_large = "lama_large"
        blur = "blur"

    class FakePrecision(str, Enum):
        fp32 = "fp32"
        bf16 = "bf16"

    class FakeInpainterConfig:
        def __init__(self) -> None:
            self.inpainting_precision = FakePrecision.bf16
            self.force_use_torch_inpainting = False
            self.blur_radius = 0

    dispatched: list[dict[str, Any]] = []

    async def fake_dispatch(**kwargs: Any) -> np.ndarray:
        dispatched.append(kwargs)
        return kwargs["image"].copy()

    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "manga_translator", types.ModuleType("manga_translator"))
    monkeypatch.setitem(
        sys.modules,
        "manga_translator.config",
        types.SimpleNamespace(
            Inpainter=FakeInpainter,
            InpainterConfig=FakeInpainterConfig,
            InpaintPrecision=FakePrecision,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "manga_translator.inpainting",
        types.SimpleNamespace(dispatch=fake_dispatch),
    )

    rt = engine_worker.EngineRuntime(str(tmp_path))
    rt._initialized = True
    monkeypatch.setattr(
        rt,
        "_get_config_dict",
        lambda: {
            "inpainter": {
                "inpainter": "Inpainter.blur",
                "inpainting_precision": "fp32",
                "force_use_torch_inpainting": True,
                "blur_radius": 65,
            },
            "cli": {"use_gpu": False},
        },
    )
    saved_imgs: list[np.ndarray] = []
    rt._export_service = types.SimpleNamespace(
        save_inpainted_image=lambda _p, img, _cfg: saved_imgs.append(img)
    )

    cur_mask = np.zeros((300, 240), dtype=np.uint8)
    cur_mask[100:130, 80:120] = 255
    added_mask = np.zeros((300, 240), dtype=np.uint8)
    added_mask[100:110, 80:90] = 255

    res = rt.incremental_neural_inpaint(
        {
            "display_base_path": str(page_path),
            "inpainted_path": str(inpainted_path),
            "current_mask_b64": encode_mask_base64(cur_mask),
            "added_mask_b64": encode_mask_base64(added_mask),
            "blur_radius": 250,
        }
    )
    assert res["status"] == "ok"
    assert res["inpainter"] == "blur"
    assert res["blur_radius"] == 200
    assert res["crop_box_xyxy"] == [0, 0, 240, 300]
    assert len(dispatched) == 1
    assert dispatched[0]["image"].shape == (300, 240, 3)
    assert dispatched[0]["config"].blur_radius == 200
    assert dispatched[0]["config"].force_use_torch_inpainting is True


def test_ocr_translate_rotated_bbox_editor_ocr_runtime_config_and_release_vram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _make_sample_page(tmp_path, size_hw=(300, 300))
    json_path = tmp_path / "001_translations.json"
    reg = {
        "lines": [[[60.0, 140.0], [240.0, 140.0], [240.0, 160.0], [60.0, 160.0]]],
        "center": [150.0, 150.0],
        "angle": 90.0,
        "text": "",
        "translation": "",
    }
    json_path.write_text(json.dumps({str(page_path): {"regions": [reg]}}, ensure_ascii=False), encoding="utf-8")

    class FakeOcr(str, Enum):
        ocr48px = "48px"
        mocr = "mocr"
        hayai_ocr_v2 = "hayai_ocr_v2"

    class FakeTranslator(str, Enum):
        openai = "openai"
        openai_hq = "openai_hq"

        @classmethod
        def _missing_(cls, value: object) -> Any:
            if isinstance(value, str) and value == "chatgpt":
                return cls.openai
            raise ValueError(value)

    class FakeQuad:
        def __init__(self, pts: np.ndarray, text: str = "", prob: float = 1.0) -> None:
            self.pts = pts
            self.text = text
            self.prob = prob

    ocr_calls: list[dict[str, Any]] = []
    tr_calls: list[dict[str, Any]] = []
    unload_called = {"ocr_svc": 0}

    async def fake_dispatch_ocr(
        ocr_key: Any,
        image: np.ndarray,
        quads: list[Any],
        ocr_cfg: Any,
        device: str,
        verbose: bool = False,
        runtime_config: Any = None,
    ) -> list[Any]:
        ocr_calls.append(
            {
                "ocr_key": ocr_key,
                "quad_pts": quads[0].pts.copy(),
                "runtime_config": runtime_config,
            }
        )
        return [FakeQuad(quads[0].pts, text="縦書きテスト", prob=0.95)]

    class FakeOcrSvc:
        def __init__(self) -> None:
            self.device = "cpu"
            self.model_prepared = False
            self.current_prepared_ocr: Any = None

        def _get_current_config(self) -> Any:
            return types.SimpleNamespace(ocr=FakeOcr.ocr48px)

        async def prepare_model(self, ocr_type: Any) -> None:
            self.model_prepared = True
            self.current_prepared_ocr = ocr_type

        async def unload_current_model(self) -> None:
            unload_called["ocr_svc"] += 1
            self.model_prepared = False
            self.current_prepared_ocr = None

    fake_ocr_svc = FakeOcrSvc()
    fake_runtime_cfg = types.SimpleNamespace(app=types.SimpleNamespace(editor_ocr="mocr"))
    fake_cfg_svc = types.SimpleNamespace(
        _load_configs_with_priority=lambda: None,
        get_config=lambda: fake_runtime_cfg,
    )

    class FakeTransSvc:
        async def translate_text_batch(self, texts: list[str], **kwargs: Any) -> list[Any]:
            tr_calls.append({"texts": texts, **kwargs})
            return [types.SimpleNamespace(translated_text="竖排测试")]

    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=lambda: False,
            empty_cache=lambda: None,
            memory_allocated=lambda: 0,
            memory_reserved=lambda: 0,
        )
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "manga_translator", types.ModuleType("manga_translator"))
    monkeypatch.setitem(
        sys.modules,
        "manga_translator.utils",
        types.SimpleNamespace(Quadrilateral=FakeQuad),
    )
    monkeypatch.setitem(
        sys.modules,
        "manga_translator.config",
        types.SimpleNamespace(Ocr=FakeOcr, Translator=FakeTranslator),
    )
    monkeypatch.setitem(
        sys.modules,
        "manga_translator.ocr",
        types.SimpleNamespace(dispatch=fake_dispatch_ocr, ocr_cache={}),
    )
    monkeypatch.setitem(
        sys.modules,
        "manga_translator.inpainting",
        types.SimpleNamespace(inpainter_cache={}),
    )
    monkeypatch.setitem(
        sys.modules,
        "services",
        types.SimpleNamespace(
            get_config_service=lambda: fake_cfg_svc,
            get_ocr_service=lambda: fake_ocr_svc,
            get_translation_service=lambda: FakeTransSvc(),
        ),
    )

    rt = engine_worker.EngineRuntime(str(tmp_path))
    rt._initialized = True

    res = rt.ocr_or_translate_regions(
        {
            "display_base_path": str(page_path),
            "json_path": str(json_path),
            "region_indices": [0],
            "run_ocr": True,
            "run_translate": True,
            "translator": "chatgpt",
            "commit": True,
        }
    )
    assert res["status"] == "ok"
    assert res["committed"] is True
    assert ocr_calls[-1]["ocr_key"] == FakeOcr.mocr
    assert ocr_calls[-1]["runtime_config"] is fake_runtime_cfg
    pts = ocr_calls[-1]["quad_pts"]
    assert np.allclose(pts[:, 0].min(), 140.0, atol=1e-3)
    assert np.allclose(pts[:, 0].max(), 160.0, atol=1e-3)
    assert np.allclose(pts[:, 1].min(), 60.0, atol=1e-3)
    assert np.allclose(pts[:, 1].max(), 240.0, atol=1e-3)
    assert tr_calls[-1]["translator"] == FakeTranslator.openai

    rt.ocr_or_translate_regions(
        {
            "display_base_path": str(page_path),
            "json_path": str(json_path),
            "region_indices": [0],
            "run_ocr": True,
            "ocr": "hayai_ocr_v2",
        }
    )
    assert ocr_calls[-1]["ocr_key"] == FakeOcr.hayai_ocr_v2

    vram_res = rt.release_vram()
    assert unload_called["ocr_svc"] == 1
    assert "ocr_service:hayai_ocr_v2" in vram_res["unloaded"]


def test_live_worker_db60bb48_perspective_quad_and_blur_inpaint(tmp_path: Path) -> None:
    if not get_runtime_settings().is_engine_available:
        pytest.skip("manga-translator-ui runtime not available on this machine")

    page_path = _make_sample_page(tmp_path, size_hw=(360, 320))
    paths = resolve_page_paths(page_path)
    mask = np.zeros((360, 320), dtype=np.uint8)
    mask[100:140, 90:150] = 255
    mask_b64 = encode_mask_base64(mask)

    worker = get_worker_client()
    try:
        inp_res = worker.call(
            "incremental_neural_inpaint",
            {
                "display_base_path": str(paths.display_base_path),
                "inpainted_path": str(paths.inpainted_path),
                "current_mask_b64": mask_b64,
                "added_mask_b64": mask_b64,
                "inpainter": "blur",
                "blur_radius": 35,
            },
        )
        assert inp_res["status"] == "ok"
        assert inp_res["inpainter"] == "blur"
        assert inp_res["blur_radius"] == 35
        assert inp_res["crop_box_xyxy"] == [0, 0, 320, 360]
        assert paths.inpainted_path.is_file()

        image_data = {
            "mask_raw": mask_b64,
            "mask_is_refined": True,
            "regions": [
                {
                    "lines": [[[85.0, 95.0], [165.0, 95.0], [165.0, 155.0], [85.0, 155.0]]],
                    "texts": ["看板"],
                    "text": "看板",
                    "translation": "透视招牌",
                    "center": [125.0, 125.0],
                    "angle": 0.0,
                    "font_size": 24,
                    "font_color": "#111111",
                    "bg_colors": [255, 255, 255],
                    "stroke_width": 0.08,
                    "direction": "h",
                    "alignment": "center",
                    "target_lang": "CHS",
                    "has_custom_white_frame": True,
                    "white_frame_rect_local": [-45.0, -30.0, 45.0, 30.0],
                    "white_frame_quad_local": [[-40.0, -26.0], [44.0, -32.0], [42.0, 32.0], [-42.0, 24.0]],
                }
            ],
        }
        save_page_json_atomic(paths, str(page_path.resolve()), image_data, auto_initial_snapshot=False)
        refreshed = resolve_page_paths(page_path)

        render_res = worker.call(
            "render_page",
            {
                "source_path": str(refreshed.source_path),
                "display_base_path": str(refreshed.display_base_path),
                "json_path": str(refreshed.json_path),
                "inpainted_path": str(refreshed.inpainted_path),
                "output_path": str(refreshed.rendered_output_path),
                "proof_only": False,
            },
        )
        assert render_res["status"] == "ok"
        assert refreshed.rendered_output_path.is_file()
        _, persisted_json = load_page_json(resolve_page_paths(page_path))
        assert persisted_json["last_output_path"] == os.path.normpath(str(refreshed.rendered_output_path))
    finally:
        worker.shutdown()


# ===========================================================================
# Round 2 Slice 3 Regression Tests (R2-F01, R2-F13..F18, R2-F27, R2-F43..F48,
# R2-F54, R2-F57, R2-F58, R2-F73, R2-F77, R2-F78)
# ===========================================================================


def test_r2_f01_concurrent_same_page_render_and_inpaint_no_lock_inversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `render_page` releases `_runtime_lock` before `page_edit_lock`, preventing same-page deadlock."""
    page_path = _make_sample_page(tmp_path)
    paths = resolve_page_paths(page_path)
    mask = np.zeros((320, 260), dtype=np.uint8)
    mask[100:130, 90:140] = 255
    paths.inpainted_path.parent.mkdir(parents=True, exist_ok=True)
    Image.open(page_path).save(paths.inpainted_path)
    save_page_json_atomic(
        paths,
        str(page_path.resolve()),
        {"mask_raw": encode_mask_base64(mask), "regions": []},
        auto_initial_snapshot=False,
    )
    paths = resolve_page_paths(page_path)

    in_export = threading.Event()
    inpaint_waiting_runtime = threading.Event()

    class FakeExportJob:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

        def release_resources(self) -> None:
            pass

    class FakeExportBase:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class FakeInpaintKey:
        def __init__(self, document_id: int, mask_revision: int) -> None:
            self.document_id = document_id
            self.mask_revision = mask_revision

    fake_file_svc = types.SimpleNamespace(
        load_translation_json=lambda _p: ([], mask, (260, 320), {"paint": None, "stamp": None, "paste_overlays": []})
    )
    fake_render_param_svc = types.SimpleNamespace(
        clear_cache=lambda: None,
        export_parameters_for_backend=lambda _idx, _rc: {},
    )
    monkeypatch.setitem(sys.modules, "manga_translator", types.ModuleType("manga_translator"))
    monkeypatch.setitem(sys.modules, "manga_translator.utils", types.ModuleType("manga_translator.utils"))
    monkeypatch.setitem(
        sys.modules,
        "manga_translator.utils.path_manager",
        types.SimpleNamespace(find_paint_overlay_path=lambda _p: None),
    )
    monkeypatch.setitem(sys.modules, "editor", types.ModuleType("editor"))
    monkeypatch.setitem(
        sys.modules,
        "editor.controller_export_service",
        types.SimpleNamespace(
            EditorControllerExportService=types.SimpleNamespace(
                _perspective_quad_world=lambda _r: None,
                apply_white_frame_center=lambda _r: None,
            ),
            ExportJob=FakeExportJob,
        ),
    )
    monkeypatch.setitem(sys.modules, "editor.document_state", types.SimpleNamespace(ExportBase=FakeExportBase))
    monkeypatch.setitem(sys.modules, "editor.inpaint_state", types.SimpleNamespace(InpaintKey=FakeInpaintKey))
    monkeypatch.setitem(
        sys.modules,
        "services",
        types.SimpleNamespace(
            get_file_service=lambda: fake_file_svc,
            get_render_parameter_service=lambda: fake_render_param_svc,
            get_config_service=lambda: None,
        ),
    )

    rt = engine_worker.EngineRuntime(str(tmp_path))
    rt._initialized = True

    def _slow_export(_job: Any) -> Any:
        in_export.set()
        inpaint_waiting_runtime.wait(timeout=2.0)
        return types.SimpleNamespace(error=None)

    rt._export_service = types.SimpleNamespace(execute_export_job=_slow_export)

    class InProcessWorkerStub:
        is_running = True

        def call(self, action: str, params: dict[str, Any] | None = None, timeout_sec: float = 10.0) -> dict[str, Any]:
            assert action == "incremental_neural_inpaint"
            inpaint_waiting_runtime.set()
            with rt._runtime_lock():
                return {"status": "ok", "inpainted_path": str(paths.inpainted_path)}

    monkeypatch.setattr(server_mod, "get_worker_client", lambda: InProcessWorkerStub())
    monkeypatch.setattr(type(server_mod.get_runtime_settings()), "is_engine_available", property(lambda _s: True))

    render_out: dict[str, Any] = {}
    errors: list[BaseException] = []

    def _run_render() -> None:
        try:
            render_out["res"] = rt.render_page(
                {
                    "source_path": str(paths.source_path),
                    "display_base_path": str(paths.display_base_path),
                    "inpainted_path": str(paths.inpainted_path),
                    "output_path": str(tmp_path / "out" / "001.png"),
                    "json_path": str(paths.json_path),
                }
            )
        except BaseException as exc:
            errors.append(exc)

    t_render = threading.Thread(target=_run_render)
    t_render.start()
    assert in_export.wait(timeout=2.0)

    # Hold `page_edit_lock` inside `mtui_edit_mask_and_inpaint` while `render_page` holds `_runtime_lock`
    inp_payload = json.loads(
        server_mod.mtui_edit_mask_and_inpaint(
            str(page_path),
            add_rects=[[10, 10, 30, 30]],
            inpaint_method="neural",
            re_render=False,
        )
    )
    t_render.join(timeout=3.0)
    assert not t_render.is_alive(), "Deadlock detected between render_page and mtui_edit_mask_and_inpaint"
    assert not errors
    assert inp_payload["inpaint_result"]["status"] == "ok"
    assert render_out["res"]["status"] == "ok"
    assert render_out["res"]["metadata_saved"] is True


def test_r2_f14_f54_page_entry_ownership_and_legacy_list_metadata_persist(tmp_path: Path) -> None:
    """Reject same-basename entries from other chapters (`R2-F14`) and report/upgrade metadata accurately (`R2-F54`)."""
    ch1 = tmp_path / "ch1"
    ch2 = tmp_path / "ch2"
    ch1.mkdir()
    ch2.mkdir()
    p_ch1 = _make_sample_page(ch1, "001.png")
    p_ch2 = _make_sample_page(ch2, "001.png")

    shared_json = tmp_path / "shared_translations.json"
    shared_raw = {str(p_ch1.resolve()): {"regions": [{"text": "ch1_only"}]}}
    shared_json.write_text(json.dumps(shared_raw, ensure_ascii=False), encoding="utf-8")

    # Requesting ch2/001.png against a shared JSON that only has ch1/001.png MUST return (None, [])
    k_miss, regs_miss = engine_worker._select_page_entry_from_raw_json(
        shared_raw,
        p_ch2,
        json_path=shared_json,
    )
    assert k_miss is None
    assert regs_miss == []
    assert (
        engine_worker.EngineRuntime._persist_last_export_dir(
            str(shared_json),
            str(tmp_path / "out"),
            output_path=str(tmp_path / "out" / "001.png"),
            source_path=str(p_ch2),
        )
        is False
    )

    # Dedicated per-page JSON in ch2's work_dir with a migrated (non-existent) old drive key MUST match
    ch2_json_dir = ch2 / "manga_translator_work" / "json"
    ch2_json_dir.mkdir(parents=True, exist_ok=True)
    ch2_json = ch2_json_dir / "001_translations.json"
    migrated_key = str(tmp_path / "unmounted_old_root" / "ch2" / "001.png")
    ch2_raw = {migrated_key: [{"text": "legacy_list_region"}]}
    ch2_json.write_text(json.dumps(ch2_raw, ensure_ascii=False), encoding="utf-8")

    k_hit, regs_hit = engine_worker._select_page_entry_from_raw_json(ch2_raw, p_ch2, json_path=ch2_json)
    assert k_hit == migrated_key
    assert len(regs_hit) == 1

    # R2-F54: Legacy list entry is upgraded to dict so `last_export_dir` and `last_output_path` persist cleanly
    persisted = engine_worker.EngineRuntime._persist_last_export_dir(
        str(ch2_json),
        str(tmp_path / "exp"),
        output_path=str(tmp_path / "exp" / "001.png"),
        source_path=str(p_ch2),
    )
    assert persisted is True
    upgraded = json.loads(ch2_json.read_text(encoding="utf-8"))[migrated_key]
    assert isinstance(upgraded, dict)
    assert upgraded["regions"] == [{"text": "legacy_list_region"}]
    assert upgraded["last_output_path"] == os.path.normpath(str(tmp_path / "exp" / "001.png"))

    # Even inside ch2's per-page JSON, an absolute key pointing to an existing OTHER file (`ch1/001.png`) is rejected
    bad_raw = {str(p_ch1.resolve()): {"regions": [{"text": "wrong_page"}]}}
    k_bad, _ = engine_worker._select_page_entry_from_raw_json(bad_raw, p_ch2, json_path=ch2_json)
    assert k_bad is None


def test_r2_f15_f16_ocr_translate_commit_conflict_and_deleted_region_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `R2-F15` field conflict detection and `R2-F16` deleted-UID / UID-less deletion guards."""
    page_path = _make_sample_page(tmp_path, size_hw=(300, 300))
    json_path = tmp_path / "001_translations.json"

    class FakeTranslator(str, Enum):
        openai = "openai"

    class FakeQuad:
        def __init__(self, pts: np.ndarray, text: str = "", prob: float = 1.0) -> None:
            self.pts = pts
            self.text = text
            self.prob = prob

    monkeypatch.setitem(
        sys.modules,
        "torch",
        types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)),
    )
    monkeypatch.setitem(sys.modules, "manga_translator", types.ModuleType("manga_translator"))
    monkeypatch.setitem(sys.modules, "manga_translator.utils", types.SimpleNamespace(Quadrilateral=FakeQuad))
    monkeypatch.setitem(sys.modules, "manga_translator.config", types.SimpleNamespace(Translator=FakeTranslator))

    # Case 1 (R2-F15 + R2-F16 with UIDs):
    # - Region 0 (`uid_del`) is deleted while translate runs -> must NOT write onto surviving regions!
    # - Region 1 (`uid_edit`) has its source text edited from A -> B while translate runs -> conflict, NOT overwritten!
    # - Region 2 (`uid_ok`) is untouched -> committed cleanly!
    initial_payload = {
        str(page_path): {
            "regions": [
                {
                    "region_uid": "uid_del",
                    "lines": [[[10.0, 10.0], [50.0, 10.0], [50.0, 50.0], [10.0, 50.0]]],
                    "text": "消える台詞",
                    "texts": ["消える台詞"],
                    "translation": "旧译0",
                },
                {
                    "region_uid": "uid_edit",
                    "lines": [[[60.0, 10.0], [110.0, 10.0], [110.0, 50.0], [60.0, 50.0]]],
                    "text": "元の台詞A",
                    "texts": ["元の台詞A"],
                    "translation": "旧译1",
                },
                {
                    "region_uid": "uid_ok",
                    "lines": [[[120.0, 10.0], [180.0, 10.0], [180.0, 50.0], [120.0, 50.0]]],
                    "text": "そのまま台詞",
                    "texts": ["そのまま台詞"],
                    "translation": "旧译2",
                },
            ]
        }
    }
    json_path.write_text(json.dumps(initial_payload, ensure_ascii=False), encoding="utf-8")

    class ConcurrentEditTransSvc:
        async def translate_text_batch(self, texts: list[str], **_kwargs: Any) -> list[Any]:
            # Simulate concurrent editor deletion of `uid_del` and edit of `uid_edit` while LLM runs
            concurrent = {
                str(page_path): {
                    "regions": [
                        {
                            "region_uid": "uid_edit",
                            "lines": [[[60.0, 10.0], [110.0, 10.0], [110.0, 50.0], [60.0, 50.0]]],
                            "text": "編集後の台詞B",
                            "texts": ["編集後の台詞B"],
                            "translation": "用户手动新译1",
                        },
                        {
                            "region_uid": "uid_ok",
                            "lines": [[[120.0, 10.0], [180.0, 10.0], [180.0, 50.0], [120.0, 50.0]]],
                            "text": "そのまま台詞",
                            "texts": ["そのまま台詞"],
                            "translation": "旧译2",
                        },
                    ]
                }
            }
            json_path.write_text(json.dumps(concurrent, ensure_ascii=False), encoding="utf-8")
            return [types.SimpleNamespace(translated_text=f"AI译:{t}") for t in texts]

    monkeypatch.setitem(
        sys.modules,
        "services",
        types.SimpleNamespace(
            get_config_service=lambda: None,
            get_ocr_service=lambda: None,
            get_translation_service=lambda: ConcurrentEditTransSvc(),
        ),
    )

    rt = engine_worker.EngineRuntime(str(tmp_path))
    rt._initialized = True
    res = rt.ocr_or_translate_regions(
        {
            "source_path": str(page_path),
            "display_base_path": str(page_path),
            "json_path": str(json_path),
            "region_indices": [0, 1, 2],
            "run_ocr": False,
            "run_translate": True,
            "commit": True,
        }
    )
    assert res["status"] == "partial"
    assert res["committed"] is True
    assert res["conflict_count"] == 2
    assert res["results"][0]["committed"] is False and res["results"][0]["conflict"] is True
    assert res["results"][1]["committed"] is False and res["results"][1]["conflict"] is True
    assert res["results"][2]["committed"] is True and "conflict" not in res["results"][2]

    saved_regs = json.loads(json_path.read_text(encoding="utf-8"))[str(page_path)]["regions"]
    assert len(saved_regs) == 2
    assert saved_regs[0]["region_uid"] == "uid_edit"
    assert saved_regs[0]["translation"] == "用户手动新译1"
    assert saved_regs[1]["region_uid"] == "uid_ok"
    assert saved_regs[1]["translation"] == "AI译:そのまま台詞"

    # Case 2 (R2-F16 legacy UID-less JSON): deleting index 0 shifts index 1 -> 0; must NOT overwrite shifted region!
    uidless_payload = {
        str(page_path): {
            "regions": [
                {"lines": [[[10.0, 10.0], [40.0, 10.0], [40.0, 40.0], [10.0, 40.0]]], "text": "枠0", "translation": "原0"},
                {"lines": [[[90.0, 90.0], [140.0, 90.0], [140.0, 140.0], [90.0, 140.0]]], "text": "枠1", "translation": "原1"},
            ]
        }
    }
    json_path.write_text(json.dumps(uidless_payload, ensure_ascii=False), encoding="utf-8")

    class DeleteIdx0TransSvc:
        async def translate_text_batch(self, texts: list[str], **_kwargs: Any) -> list[Any]:
            shifted = {
                str(page_path): {
                    "regions": [
                        {
                            "lines": [[[90.0, 90.0], [140.0, 90.0], [140.0, 140.0], [90.0, 140.0]]],
                            "text": "枠1",
                            "translation": "原1",
                        }
                    ]
                }
            }
            json_path.write_text(json.dumps(shifted, ensure_ascii=False), encoding="utf-8")
            return [types.SimpleNamespace(translated_text=f"新译:{t}") for t in texts]

    monkeypatch.setitem(
        sys.modules,
        "services",
        types.SimpleNamespace(
            get_config_service=lambda: None,
            get_ocr_service=lambda: None,
            get_translation_service=lambda: DeleteIdx0TransSvc(),
        ),
    )
    res_uidless = rt.ocr_or_translate_regions(
        {
            "source_path": str(page_path),
            "display_base_path": str(page_path),
            "json_path": str(json_path),
            "region_indices": [0],
            "run_ocr": False,
            "run_translate": True,
            "commit": True,
        }
    )
    assert res_uidless["committed"] is False
    assert res_uidless["results"][0]["conflict"] is True
    surviving = json.loads(json_path.read_text(encoding="utf-8"))[str(page_path)]["regions"]
    assert surviving[0]["translation"] == "原1"


def test_r2_f13_detect_fonts_recomputes_neighbor_propagation_when_text_changes(tmp_path: Path) -> None:
    """Verify `detect_fonts` invalidates stale `regions_state_sig` and updates neighbor font inheritance (`R2-F13`)."""
    from mtui_mcp.font_detector import (
        CACHE_VERSION,
        FAMILY_NAMES,
        load_font_detection_cache,
        region_bbox_signature,
        save_font_detection_cache_atomic,
    )

    # Create fake YuzuMarker model directory with >300MB sparse file stat via mocked Path.stat on ckpt?
    # Better: create real labels file and patch `Path.stat` only for `.ckpt` size check so zero disk bloat!
    mdir = tmp_path / "mtui_root" / "models" / "yuzumarker"
    mdir.mkdir(parents=True)
    labels_path = mdir / "font-labels-ex.json"
    labels_path.write_text("[]", encoding="utf-8")
    ckpt_path = mdir / "ym_test.ckpt"
    ckpt_path.write_bytes(b"stub")

    orig_stat = Path.stat

    def _patched_stat(self_p: Path, *args: Any, **kwargs: Any) -> Any:
        st = orig_stat(self_p, *args, **kwargs)
        if self_p == ckpt_path:
            return types.SimpleNamespace(st_size=350_000_000, st_mtime_ns=st.st_mtime_ns, st_mode=st.st_mode)
        return st

    chapter_dir = tmp_path / "ch_ym"
    chapter_dir.mkdir()
    page_path = _make_sample_page(chapter_dir, "001.png", size_hw=(400, 400))
    paths = resolve_page_paths(page_path)

    reg_short = {
        "region_uid": "u_short",
        "lines": [[[50.0, 50.0], [90.0, 50.0], [90.0, 120.0], [50.0, 120.0]]],
        "center": [70.0, 85.0],
        "text": "あ！",
        "translation": "啊！",
    }
    reg_donor = {
        "region_uid": "u_donor",
        "lines": [[[120.0, 50.0], [170.0, 50.0], [170.0, 180.0], [120.0, 180.0]]],
        "center": [145.0, 115.0],
        "text": "あいうえ",
        "translation": "测试台词",
    }
    save_page_json_atomic(paths, str(page_path.resolve()), {"regions": [reg_short, reg_donor]}, auto_initial_snapshot=False)
    paths = resolve_page_paths(page_path)

    bst = orig_stat(paths.display_base_path)
    lst = orig_stat(labels_path)
    cst = orig_stat(ckpt_path)
    sig_short = region_bbox_signature([50.0, 50.0, 90.0, 120.0])
    sig_donor = region_bbox_signature([120.0, 50.0, 170.0, 180.0])

    fp_short = {f: 0.05 for f in FAMILY_NAMES}
    fp_short["ANTIQUE"] = 0.40
    fp_donor = {f: 0.01 for f in FAMILY_NAMES}
    fp_donor["MINCHO"] = 0.92

    seed_cache = {
        "version": CACHE_VERSION,
        "ensemble_ckpts": ["ym_test.ckpt"],
        "ensemble_sig": [["ym_test.ckpt", 350_000_000, int(cst.st_mtime_ns)]],
        "labels_sig": [int(lst.st_size), int(lst.st_mtime_ns)],
        "pages": {
            "001": {
                "base_image_sig": [int(bst.st_size), int(bst.st_mtime_ns)],
                "display_base_name": "001.png",
                "total_regions": 2,
                "by_bbox_sig": {
                    sig_short: {
                        "ridx": 0,
                        "bbox_sig": sig_short,
                        "n_clean_chars": 1,
                        "center": [70.0, 85.0],
                        "pred_family": "ANTIQUE",
                        "pred_conf": 0.40,
                        "family_probs": fp_short,
                        "bold_prob": 0.2,
                        "heavy_prob": 0.05,
                        "top_fonts": [{"font": "F-Antique.otf", "family": "ANTIQUE", "prob": 0.20}],
                        "neighbor_propagated_family": "MINCHO",
                        "neighbor_source_ridx": 1,
                        "neighbor_dist": 80.8,
                    },
                    sig_donor: {
                        "ridx": 1,
                        "bbox_sig": sig_donor,
                        "n_clean_chars": 4,
                        "center": [145.0, 115.0],
                        "pred_family": "MINCHO",
                        "pred_conf": 0.92,
                        "family_probs": fp_donor,
                        "bold_prob": 0.1,
                        "heavy_prob": 0.02,
                        "top_fonts": [{"font": "F-Mincho.otf", "family": "MINCHO", "prob": 0.85}],
                    },
                },
                "by_ridx": {},
            }
        },
    }
    save_font_detection_cache_atomic(paths.work_dir, seed_cache)

    rt = engine_worker.EngineRuntime(str(tmp_path / "mtui_root"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "stat", _patched_stat)
        # First call populates `regions_state_sig` and confirms MINCHO neighbor propagation while donor has 4 chars
        r1 = rt.detect_fonts({"target_path": str(chapter_dir), "force_refresh": False})
        assert r1["scanned_pages"] == 0
        c1 = load_font_detection_cache(paths.work_dir)["pages"]["001"]["by_bbox_sig"][sig_short]
        assert c1.get("neighbor_propagated_family") == "MINCHO"

        # Now OCR shortens donor text from 4 chars -> 2 chars ("あい") without changing image or bbox!
        _, edited_data = load_page_json(paths)
        edited_data["regions"][1]["text"] = "あい"
        edited_data["regions"][1]["texts"] = ["あい"]
        save_page_json_atomic(paths, str(page_path.resolve()), edited_data, auto_initial_snapshot=False)

        r2 = rt.detect_fonts({"target_path": str(chapter_dir), "force_refresh": False})
        assert r2["scanned_pages"] == 0
        c2 = load_font_detection_cache(paths.work_dir)["pages"]["001"]["by_bbox_sig"][sig_short]
        assert "neighbor_propagated_family" not in c2


def test_r2_f17_f27_f57_subprocess_reap_shutdown_serialization_and_bounded_handshake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify child CLI reaping (`R2-F17`), client shutdown/restart serialization (`R2-F27`), and bounded handshake queue (`R2-F57`)."""
    # 1. R2-F17: EngineRuntime terminates and kills lingering registered child subprocesses
    rt = engine_worker.EngineRuntime(str(tmp_path))
    events: list[str] = []

    class StuckChildProc:
        def __init__(self) -> None:
            self._alive = True

        def poll(self) -> int | None:
            return None if self._alive else -9

        def terminate(self) -> None:
            events.append("terminate")

        def wait(self, timeout: float | None = None) -> int:
            if self._alive:
                raise subprocess.TimeoutExpired(cmd=["cli"], timeout=timeout or 0.01)
            return -9

        def kill(self) -> None:
            events.append("kill")
            self._alive = False

    stuck = StuckChildProc()
    rt.register_subprocess(stuck)
    reaped = rt.terminate_active_subprocesses(grace_sec=0.02)
    assert reaped == 1
    assert events == ["terminate", "kill"]
    assert stuck.poll() == -9

    # 2. R2-F27 & R2-F57: Concurrent `shutdown()` drain blocks replacement `_ensure_started_instance()` until old proc exits,
    #    and 500 noisy pre-READY stdout lines stay bounded in `_stderr_ring` (`maxlen=120`)
    settings = get_runtime_settings()
    monkeypatch.setattr(type(settings), "is_engine_available", property(lambda _s: True))
    monkeypatch.setattr(settings, "mtui_root", tmp_path)
    monkeypatch.setattr(settings, "mtui_python", Path(sys.executable))

    old_exit_gate = threading.Event()
    timeline: list[str] = []

    class DrainingOldProc:
        stdout = None
        stderr = None
        stdin = None
        returncode = 0

        def poll(self) -> int | None:
            return 0 if old_exit_gate.is_set() else None

        def wait(self, timeout: float | None = None) -> int:
            old_exit_gate.wait(timeout=timeout)
            if not old_exit_gate.is_set():
                raise subprocess.TimeoutExpired(cmd=["old"], timeout=timeout or 0.1)
            timeline.append("old_exited")
            return 0

        def kill(self) -> None:
            old_exit_gate.set()

    class ReplacementProc:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            timeline.append("new_spawned")
            noisy_lines = "".join(f"pre-ready-log-{i}\n" for i in range(500))
            self.stdout = io.StringIO(noisy_lines + 'MTUI_WORKER_READY {"port": 43210}\n')
            self.stderr = io.StringIO("")
            self.stdin = None
            self.returncode = None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.returncode = -9

    client = MtuiWorkerClient()
    old_proc = DrainingOldProc()
    client._proc = old_proc  # type: ignore[assignment]
    client._port = 43209
    client._token = "tok"
    client._bound_root = tmp_path
    client._bound_python = Path(sys.executable)

    class FakeResp:
        def __enter__(self) -> "FakeResp":
            return self

        def __exit__(self, *_a: Any) -> None:
            pass

        def read(self) -> bytes:
            return b'{"ok":true}'

    monkeypatch.setattr("mtui_mcp.worker.client._LOOPBACK_OPENER.open", lambda *_a, **_kw: FakeResp())
    monkeypatch.setattr("mtui_mcp.worker.client.subprocess.Popen", ReplacementProc)

    t_shut = threading.Thread(target=client.shutdown)
    t_shut.start()
    # Wait until `shutdown()` has moved `old_proc` into `_shutting_down_procs`
    deadline_s = time.monotonic() + 2.0
    while time.monotonic() < deadline_s:
        with client._lock:
            if old_proc in client._shutting_down_procs:
                break
        time.sleep(0.01)

    started_res: dict[str, Any] = {}

    def _start_replacement() -> None:
        _, p_num, _ = client._ensure_started_instance(startup_timeout_sec=5.0)
        started_res["port"] = p_num

    t_start = threading.Thread(target=_start_replacement)
    t_start.start()
    time.sleep(0.08)
    # Replacement MUST NOT have spawned while `old_proc` is still draining!
    assert "new_spawned" not in timeline

    old_exit_gate.set()
    t_shut.join(timeout=2.0)
    t_start.join(timeout=2.0)
    assert timeline == ["old_exited", "new_spawned"]
    assert started_res["port"] == 43210
    assert len(client._stderr_ring) == 120
    client._proc = None


def test_r2_f18_f43_f44_f45_f46_f47_f48_f58_f77_f78_server_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify server-side security, rollback, mask sync, revision lock, QA path expansion, and R2-F58 partial render status."""
    # 1. R2-F44: `_validate_mtui_python_override` rejects symlinked subdir escape and untrusted symlink targets
    fake_root = tmp_path / "mtui_app"
    (fake_root / ".venv" / "Scripts").mkdir(parents=True)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    untrusted_py = outside_dir / "python.exe"
    untrusted_py.write_bytes(b"untrusted")

    legit_venv_py = fake_root / ".venv" / "Scripts" / Path(sys.executable).name
    legit_venv_py.write_bytes(b"venv-py")
    assert server_mod._validate_mtui_python_override(str(legit_venv_py), fake_root) == str(legit_venv_py.resolve())

    # Simulate `.venv/Scripts/python.exe` symlink resolving to an untrusted external `outside/python.exe`
    orig_resolve = Path.resolve

    def _mock_resolve_untrusted_target(self_p: Path, *a: Any, **kw: Any) -> Path:
        if os.path.normcase(str(self_p)) == os.path.normcase(str(legit_venv_py)):
            return orig_resolve(untrusted_py, *a, **kw)
        return orig_resolve(self_p, *a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "resolve", _mock_resolve_untrusted_target)
        with pytest.raises(ValueError, match="Rejected mtui_python"):
            server_mod._validate_mtui_python_override(str(legit_venv_py), fake_root)

    # Simulate `.venv` directory symlink resolving outside `fake_root`
    def _mock_resolve_escaped_subdir(self_p: Path, *a: Any, **kw: Any) -> Path:
        if os.path.normcase(str(self_p)) in (
            os.path.normcase(str(fake_root / ".venv")),
            os.path.normcase(str(fake_root / ".venv" / "Scripts")),
        ):
            return orig_resolve(outside_dir, *a, **kw)
        return orig_resolve(self_p, *a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "resolve", _mock_resolve_escaped_subdir)
        with pytest.raises(ValueError, match="Rejected mtui_python"):
            server_mod._validate_mtui_python_override(str(legit_venv_py), fake_root)

    # Simulate `.venv/Scripts/<python>` symlink resolving to trusted `sys.executable` -> accepted, keeps venv path!
    def _mock_resolve_trusted_host_py(self_p: Path, *a: Any, **kw: Any) -> Path:
        if os.path.normcase(str(self_p)) == os.path.normcase(str(legit_venv_py)):
            return orig_resolve(Path(sys.executable), *a, **kw)
        return orig_resolve(self_p, *a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "resolve", _mock_resolve_trusted_host_py)
        assert os.path.normcase(server_mod._validate_mtui_python_override(str(legit_venv_py), fake_root)) == os.path.normcase(
            str(legit_venv_py)
        )

    # 2. R2-F18 & R2-F77: `mtui_list_pages` uses `visible_text_of_region` (`translation: null` -> untranslated)
    ch_srv = tmp_path / "ch_srv"
    ch_srv.mkdir(parents=True, exist_ok=True)
    page_p = _make_sample_page(ch_srv, "001.png", size_hw=(200, 200))
    paths = resolve_page_paths(page_p)
    paths.inpainted_path.parent.mkdir(parents=True, exist_ok=True)
    orig_inp_pixels = np.full((200, 200, 3), 240, dtype=np.uint8)
    Image.fromarray(orig_inp_pixels).save(paths.inpainted_path)
    m_init = np.zeros((200, 200), dtype=np.uint8)
    m_init[20:40, 20:40] = 255
    sha_init = server_mod._mask_sha1_hex(m_init, (200, 200))
    stat_init = server_mod._file_stat_sig(paths.inpainted_path)

    raw_json_dir = paths.work_dir / "json"
    raw_json_dir.mkdir(parents=True, exist_ok=True)
    (raw_json_dir / "001_translations.json").write_text(
        json.dumps(
            {
                str(page_p.resolve()): {
                    "mask_raw": encode_mask_base64(m_init),
                    "mask_inpainted_sha1": sha_init,
                    "mask_inpainted_stat": stat_init,
                    "regions": [
                        {
                            "region_uid": "r0",
                            "lines": [[[20.0, 20.0], [60.0, 20.0], [60.0, 60.0], [20.0, 60.0]]],
                            "text": "テスト",
                            "translation": None,
                        }
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # R2-F77: `mtui_list_pages` treats `translation: None` as untranslated (`untranslated_regions == 1`)
    listed = json.loads(server_mod.mtui_list_pages(str(page_p.parent)))
    assert listed["pages"][0]["untranslated_regions"] == 1

    # Normalize region 0 translation for subsequent mask/style tests while preserving `sha_init` & `stat_init`
    save_page_json_atomic(
        resolve_page_paths(page_p),
        str(page_p.resolve()),
        {
            "mask_raw": encode_mask_base64(m_init),
            "mask_inpainted_sha1": sha_init,
            "mask_inpainted_stat": stat_init,
            "regions": [
                {
                    "region_uid": "r0",
                    "lines": [[[20.0, 20.0], [60.0, 20.0], [60.0, 60.0], [20.0, 60.0]]],
                    "text": "テスト",
                    "translation": "初始译文",
                }
            ],
        },
        auto_initial_snapshot=False,
    )
    paths = resolve_page_paths(page_p)

    # R2-F47: Replacing `inpainted_path` with different file size/mtime while `saved_sha1 == prev_mask_sha1` forces `prev_mask_synced=False`
    os.utime(paths.inpainted_path, (1_600_000_000, 1_600_000_000))
    recorded_worker_calls: list[tuple[str, dict[str, Any]]] = []

    class RecordingWorker:
        is_running = True

        def call(self, action: str, params: dict[str, Any] | None = None, timeout_sec: float = 30.0) -> dict[str, Any]:
            recorded_worker_calls.append((action, dict(params or {})))
            if action == "incremental_neural_inpaint":
                Image.fromarray(np.full((200, 200, 3), 123, dtype=np.uint8)).save(paths.inpainted_path)
                return {"status": "ok", "inpainted_path": str(paths.inpainted_path)}
            return {"status": "ok", "output_path": str(paths.rendered_output_path)}

    monkeypatch.setattr(server_mod, "get_worker_client", lambda: RecordingWorker())
    monkeypatch.setattr(type(server_mod.get_runtime_settings()), "is_engine_available", property(lambda _s: True))

    server_mod.mtui_edit_mask_and_inpaint(
        str(page_p),
        add_rects=[[80, 80, 100, 100]],
        inpaint_method="neural",
        re_render=False,
    )
    assert recorded_worker_calls[-1][0] == "incremental_neural_inpaint"
    assert recorded_worker_calls[-1][1]["prev_mask_synced"] is False

    # R2-F48: `mtui_add_region(..., inpaint_method="neural")` passes explicit `prev_mask_synced`
    os.utime(paths.inpainted_path, (1_500_000_000, 1_500_000_000))
    server_mod.mtui_add_region(
        str(page_p),
        bbox_xyxy=[110, 110, 150, 150],
        source_text="追加",
        translation="追加框",
        inpaint_method="neural",
        re_render=False,
    )
    assert recorded_worker_calls[-1][0] == "incremental_neural_inpaint"
    assert recorded_worker_calls[-1][1]["prev_mask_synced"] is False

    # R2-F43: Post-commit `resolve_page_paths` failure MUST NOT roll back newly committed `inpainted_path`
    real_resolve = server_mod.resolve_page_paths

    def _fail_on_saved_json_resolve(target: Any, *a: Any, **kw: Any) -> Any:
        if isinstance(target, Path) and target.name.endswith("_translations.json"):
            raise RuntimeError("simulated post-commit resolve failure")
        return real_resolve(target, *a, **kw)

    Image.fromarray(np.full((200, 200, 3), 210, dtype=np.uint8)).save(paths.inpainted_path)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server_mod, "resolve_page_paths", _fail_on_saved_json_resolve)
        with pytest.raises(RuntimeError, match="simulated post-commit resolve failure"):
            server_mod.mtui_edit_mask_and_inpaint(
                str(page_p),
                add_rects=[[5, 5, 15, 15]],
                inpaint_method="neural",
                re_render=False,
            )
    # `RecordingWorker` wrote pixel value 123 and JSON committed, so `inpainted_path` must stay 123 (NOT rolled back to 210)
    assert int(np.asarray(Image.open(paths.inpainted_path))[0, 0, 0]) == 123

    # R2-F45: When JSON save fails AND rollback replace also fails, backup `.bak.` file is preserved and reported
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server_mod, "save_page_json_atomic", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disk full")))
        mp.setattr(
            server_mod,
            "_atomic_replace_with_retry",
            lambda *_a, **_kw: (_ for _ in ()).throw(PermissionError("locked")),
        )
        with pytest.raises(RuntimeError, match=r"backup preserved at .*\.bak\.") as exc_info:
            server_mod.mtui_edit_mask_and_inpaint(
                str(page_p),
                add_rects=[[30, 30, 45, 45]],
                inpaint_method="neural",
                re_render=False,
            )
    bak_files = list(paths.inpainted_path.parent.glob("*.bak.*"))
    assert len(bak_files) == 1
    assert str(bak_files[0]) in str(exc_info.value)
    bak_files[0].unlink()

    # R2-F46: `mtui_style_region_richtext` computes `revision` inside `_acquire_page_edit_lock`
    server_mod.mtui_update_regions(
        str(page_p),
        updates=[{"region_index": 0, "translation": "加粗测试"}],
        re_render=False,
    )
    expected_rev_holder: dict[str, str] = {}

    def _mutating_render_after_lock(pp: Any, **_kw: Any) -> dict[str, Any]:
        expected_rev_holder["pre_render_rev"] = server_mod.compute_page_revision(pp)
        # Mutate JSON during render (outside `mtui_style_region_richtext`'s page_edit_lock)
        ik, idata = load_page_json(pp)
        idata["last_output_path"] = "C:/mutated_during_render.png"
        save_page_json_atomic(pp, ik, idata, auto_initial_snapshot=False)
        return {"status": "ok", "output_path": str(pp.rendered_output_path)}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server_mod, "_render_single_page_via_worker", _mutating_render_after_lock)
        styled = json.loads(
            server_mod.mtui_style_region_richtext(
                str(page_p),
                region_index=0,
                whole_style={"bold": True},
                re_render=True,
            )
        )
    assert styled["revision"] == expected_rev_holder["pre_render_rev"]

    # R2-F78: `mtui_qa_audit` expands `~` consistently for both `diff_against_json` and `save_json_path`
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    (fake_home / "base_qa.json").write_text(
        json.dumps(
            {"audited_pages": 1, "audited_page_names": ["001.png"], "error_count": 0, "pages": []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", str(fake_home))
        mp.setenv("USERPROFILE", str(fake_home))
        mp.setattr(
            server_mod,
            "run_qa_audit",
            lambda *_a, **_kw: {"audited_pages": 1, "audited_page_names": ["001.png"], "error_count": 0, "pages": []},
        )
        qa_out = json.loads(
            server_mod.mtui_qa_audit(
                str(page_p),
                diff_against_json="~/base_qa.json",
                save_json_path="~/after_qa.json",
            )
        )
    assert "error" not in qa_out["regression_diff"]
    assert (fake_home / "after_qa.json").is_file()

    # R2-F58: `mtui_center_bubbles` and `mtui_render_pages(center_bubbles=True)` surface `"status": "partial"` + `render_results`
    def _fake_closed_loop(target_p: Any, *, passes: int = 2, render_page_fn: Any = None, **kw: Any) -> dict[str, Any]:
        r_res = render_page_fn(resolve_page_paths(page_p)) if callable(render_page_fn) else {"status": "partial"}
        if isinstance(kw.get("render_results_out"), dict):
            kw["render_results_out"]["001.png"] = r_res
        return {"001.png": [{"index": 0, "old_center": [40.0, 40.0], "optical_center": [42.0, 42.0]}]}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server_mod, "run_closed_loop_bubble_centering", _fake_closed_loop)
        mp.setattr(
            server_mod,
            "_render_single_page_via_worker",
            lambda pp, **_kw: {"status": "partial", "metadata_saved": False, "output_path": str(pp.rendered_output_path)},
        )
        cb_out = json.loads(server_mod.mtui_center_bubbles(str(page_p.parent), passes=2))
        assert cb_out["status"] == "partial"
        assert cb_out["render_results"]["001.png"]["status"] == "partial"

        rp_out = json.loads(server_mod.mtui_render_pages(str(page_p.parent), center_bubbles=True))
        assert rp_out["status"] == "partial"
        assert rp_out["render_results"]["001.png"]["status"] == "partial"
