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
            lck = rt._runtime_lock()
            if not lck.acquire(timeout=4.0):
                raise TimeoutError("Timed out acquiring _runtime_lock in InProcessWorkerStub")
            try:
                return {"status": "ok", "inpainted_path": str(paths.inpainted_path)}
            finally:
                lck.release()

    monkeypatch.setattr(server_mod, "get_worker_client", lambda: InProcessWorkerStub())
    monkeypatch.setattr(type(server_mod.get_runtime_settings()), "is_engine_available", property(lambda _s: True))

    render_out: dict[str, Any] = {}
    inpaint_out: dict[str, Any] = {}
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

    def _run_inpaint() -> None:
        try:
            inpaint_out["res"] = json.loads(
                server_mod.mtui_edit_mask_and_inpaint(
                    str(page_path),
                    add_rects=[[10, 10, 30, 30]],
                    inpaint_method="neural",
                    re_render=False,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    # R3-F52 & R4-F35: Run BOTH concurrent operations on bounded daemon threads sharing one test deadline,
    # and only join threads in `finally:` that were actually started.
    t_render = threading.Thread(target=_run_render, daemon=True)
    t_inpaint = threading.Thread(target=_run_inpaint, daemon=True)
    render_started = False
    inpaint_started = False
    try:
        t_render.start()
        render_started = True
        assert in_export.wait(timeout=2.0)

        test_deadline = time.monotonic() + 3.0
        t_inpaint.start()
        inpaint_started = True
        t_inpaint.join(timeout=max(0.0, test_deadline - time.monotonic()))
        t_render.join(timeout=max(0.0, test_deadline - time.monotonic()))
        assert not t_inpaint.is_alive() and not t_render.is_alive(), (
            "Deadlock detected between render_page and mtui_edit_mask_and_inpaint"
        )
        assert not errors, f"Unexpected concurrent error(s): {errors!r}"
        assert inpaint_out["res"]["inpaint_result"]["status"] == "ok"
        assert render_out["res"]["status"] == "ok"
        assert render_out["res"]["metadata_saved"] is True
    finally:
        inpaint_waiting_runtime.set()
        if inpaint_started:
            t_inpaint.join(timeout=0.5)
        if render_started:
            t_render.join(timeout=0.5)


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

    # R2-F58 & R3-F53: `mtui_center_bubbles` and `mtui_render_pages(center_bubbles=True)` surface `"status": "partial"`
    # from closed-loop re-renders even when `mtui_render_pages`'s initial render returns `"status": "ok"`.
    in_closed_loop = {"active": False}

    def _fake_closed_loop(target_p: Any, *, passes: int = 2, render_page_fn: Any = None, **kw: Any) -> dict[str, Any]:
        in_closed_loop["active"] = True
        try:
            r_res = render_page_fn(resolve_page_paths(page_p)) if callable(render_page_fn) else {"status": "partial"}
        finally:
            in_closed_loop["active"] = False
        if isinstance(kw.get("render_results_out"), dict):
            kw["render_results_out"]["001.png"] = r_res
        return {"001.png": [{"index": 0, "old_center": [40.0, 40.0], "optical_center": [42.0, 42.0]}]}

    def _selective_render_stub(pp: Any, **_kw: Any) -> dict[str, Any]:
        if in_closed_loop["active"]:
            return {"status": "partial", "metadata_saved": False, "output_path": str(pp.rendered_output_path)}
        return {"status": "ok", "metadata_saved": True, "output_path": str(pp.rendered_output_path)}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server_mod, "run_closed_loop_bubble_centering", _fake_closed_loop)
        mp.setattr(server_mod, "_render_single_page_via_worker", _selective_render_stub)
        cb_out = json.loads(server_mod.mtui_center_bubbles(str(page_p.parent), passes=2))
        assert cb_out["status"] == "partial"
        assert cb_out["render_results"]["001.png"]["status"] == "partial"

        rp_out = json.loads(server_mod.mtui_render_pages(str(page_p.parent), center_bubbles=True))
        assert rp_out["results"][0]["status"] == "ok"
        assert rp_out["status"] == "partial"
        assert rp_out["render_results"]["001.png"]["status"] == "partial"


# ===========================================================================
# Round 3 Slice 3 Regression Tests (R3-F01, R3-F03, R3-F11, R3-F12,
# R3-F15..F17, R3-F34, R3-F38, R3-F42)
# ===========================================================================


def test_r3_f01_first_render_advances_revision_while_preserving_concurrent_edit_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `mtui_style_region_richtext` & `mtui_update_regions` include their own export metadata write in `revision` (`R3-F01`) without swallowing concurrent edits (`R2-F46`)."""
    ch_dir = tmp_path / "ch_r3_f01"
    ch_dir.mkdir()
    page_p = _make_sample_page(ch_dir, "001.png", size_hw=(160, 160))
    paths = resolve_page_paths(page_p)
    save_page_json_atomic(
        paths,
        str(page_p.resolve()),
        {
            "regions": [
                {
                    "region_uid": "r0",
                    "lines": [[[20.0, 20.0], [100.0, 20.0], [100.0, 80.0], [20.0, 80.0]]],
                    "text": "初版",
                    "translation": "初版译文",
                }
            ]
        },
        auto_initial_snapshot=False,
    )

    # 1. Normal first render (writes `last_export_dir` & `last_output_path` for the first time):
    #    returned `revision` MUST match post-render on-disk revision so next `expected_revision` edit succeeds!
    def _first_export_render(pp: Any, *, output_dir: str | None = None, proof_only: bool = False, **_kw: Any) -> dict[str, Any]:
        resolved = resolve_page_paths(pp.source_path, explicit_output_dir=output_dir)
        out_path = resolved.rendered_output_path
        rev_info: dict[str, str] = {}
        if not proof_only:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            Image.open(page_p).save(out_path)
            engine_worker.EngineRuntime._persist_last_export_dir(
                str(resolved.json_path),
                str(out_path.parent),
                output_path=str(out_path),
                source_path=str(resolved.source_path),
                revisions_out=rev_info,
            )
        return {"status": "ok", "output_path": str(out_path), "metadata_saved": True, **rev_info}

    monkeypatch.setattr(server_mod, "_render_single_page_via_worker", _first_export_render)

    styled = json.loads(
        server_mod.mtui_style_region_richtext(
            str(page_p),
            region_index=0,
            whole_style={"bold": True},
            re_render=True,
        )
    )
    disk_rev_after_style = server_mod.compute_page_revision(resolve_page_paths(page_p))
    assert styled["revision"] == disk_rev_after_style
    assert styled["render_result"]["pre_persist_revision"] != styled["render_result"]["post_persist_revision"]

    # Chained edit carrying `expected_revision=styled["revision"]` + new `output_dir` succeeds without conflict!
    alt_out = tmp_path / "custom_export_dir"
    upd = json.loads(
        server_mod.mtui_update_regions(
            str(page_p),
            updates=[{"region_index": 0, "translation": "第二次编辑"}],
            expected_revision=styled["revision"],
            output_dir=str(alt_out),
            re_render=True,
        )
    )
    assert upd["revision"] == server_mod.compute_page_revision(resolve_page_paths(page_p))

    # 2. Concurrent external edit BEFORE `_persist_last_export_dir`: `pre_persist_revision != committed_revision`
    #    -> `mtui_style_region_richtext` MUST NOT advance to `post_persist_revision`, preserving conflict detection!
    def _concurrent_before_persist_render(pp: Any, **_kw: Any) -> dict[str, Any]:
        resolved = resolve_page_paths(pp.source_path)
        server_mod.apply_region_updates_to_page(
            resolved.source_path,
            [{"region_index": 0, "translation": "其他客户端并发修改A"}],
        )
        rev_info: dict[str, str] = {}
        out_p = tmp_path / "exp2" / "001.png"
        engine_worker.EngineRuntime._persist_last_export_dir(
            str(resolved.json_path),
            str(out_p.parent),
            output_path=str(out_p),
            source_path=str(resolved.source_path),
            revisions_out=rev_info,
        )
        return {"status": "ok", "output_path": str(out_p), "metadata_saved": True, **rev_info}

    monkeypatch.setattr(server_mod, "_render_single_page_via_worker", _concurrent_before_persist_render)
    styled_race_before = json.loads(
        server_mod.mtui_style_region_richtext(
            str(page_p),
            region_index=0,
            whole_style={"italic": True},
            re_render=True,
        )
    )
    assert styled_race_before["revision"] != server_mod.compute_page_revision(resolve_page_paths(page_p))
    with pytest.raises(ValueError, match="Revision conflict"):
        server_mod.mtui_update_regions(
            str(page_p),
            updates=[{"region_index": 0, "font_size": 30}],
            expected_revision=styled_race_before["revision"],
            re_render=False,
        )

    # 3. Concurrent external edit AFTER `_persist_last_export_dir`: `mtui_style_region_richtext` advances ONLY
    #    to its own `post_persist_revision` and NEVER swallows the later external edit!
    def _concurrent_after_persist_render(pp: Any, **_kw: Any) -> dict[str, Any]:
        resolved = resolve_page_paths(pp.source_path)
        rev_info: dict[str, str] = {}
        out_p = tmp_path / "exp3" / "001.png"
        engine_worker.EngineRuntime._persist_last_export_dir(
            str(resolved.json_path),
            str(out_p.parent),
            output_path=str(out_p),
            source_path=str(resolved.source_path),
            revisions_out=rev_info,
        )
        server_mod.apply_region_updates_to_page(
            resolved.source_path,
            [{"region_index": 0, "translation": "其他客户端并发修改B"}],
        )
        return {"status": "ok", "output_path": str(out_p), "metadata_saved": True, **rev_info}

    monkeypatch.setattr(server_mod, "_render_single_page_via_worker", _concurrent_after_persist_render)
    styled_race_after = json.loads(
        server_mod.mtui_style_region_richtext(
            str(page_p),
            region_index=0,
            whole_style={"underline": True},
            re_render=True,
        )
    )
    assert styled_race_after["revision"] == styled_race_after["render_result"]["post_persist_revision"]
    assert styled_race_after["revision"] != server_mod.compute_page_revision(resolve_page_paths(page_p))
    with pytest.raises(ValueError, match="Revision conflict"):
        server_mod.mtui_update_regions(
            str(page_p),
            updates=[{"region_index": 0, "font_size": 32}],
            expected_revision=styled_race_after["revision"],
            re_render=False,
        )


def test_r3_f03_unchanged_and_not_requested_closed_loop_renders_report_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `render_status='unchanged'` / `'not_requested'` in closed-loop centering returns `status='ok'` (`R3-F03`)."""
    ch_dir = tmp_path / "ch_r3_f03"
    ch_dir.mkdir()
    page_p = _make_sample_page(ch_dir, "001.png", size_hw=(160, 160))
    paths = resolve_page_paths(page_p)
    save_page_json_atomic(paths, str(page_p.resolve()), {"regions": []}, auto_initial_snapshot=False)

    monkeypatch.setattr(
        server_mod,
        "_render_single_page_via_worker",
        lambda pp, **_kw: {"status": "ok", "metadata_saved": True, "output_path": str(pp.rendered_output_path)},
    )
    monkeypatch.setattr(
        server_mod,
        "run_closed_loop_bubble_centering",
        lambda *_a, **_kw: {
            "001.png": [
                {"index": 0, "old_center": [50.0, 50.0], "optical_center": [50.2, 50.1], "render_status": "unchanged"},
                {"index": 1, "old_center": [90.0, 50.0], "optical_center": [90.0, 50.0], "render_status": "not_requested"},
            ]
        },
    )

    cb_ok = json.loads(server_mod.mtui_center_bubbles(str(ch_dir), passes=2))
    assert cb_ok["status"] == "ok"
    assert cb_ok["render_results"] == {}

    rp_ok = json.loads(server_mod.mtui_render_pages(str(ch_dir), center_bubbles=True))
    assert rp_ok["status"] == "ok"
    assert rp_ok["render_results"] == {}

    # Genuine incomplete render_status ("partial" / "error") MUST still report `"partial"`
    monkeypatch.setattr(
        server_mod,
        "run_closed_loop_bubble_centering",
        lambda *_a, **_kw: {
            "001.png": [
                {"index": 0, "old_center": [50.0, 50.0], "optical_center": [55.0, 50.0], "render_status": "error"}
            ]
        },
    )
    cb_err = json.loads(server_mod.mtui_center_bubbles(str(ch_dir), passes=2))
    assert cb_err["status"] == "partial"


def test_r3_f12_f34_unsynced_imported_inpaint_runs_full_repair_and_unlink_failure_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify unsynced imported pages run full-mask repair (`R3-F12`) and new-file rollback unlink errors surface (`R3-F34`)."""
    # 1. R3-F12: Imported page has mask A (`[20:50, 20:50]`), an existing `001_inpainted.png` that STILL has dark strokes at A,
    #    and NO `mask_inpainted_sha1` / `mask_inpainted_stat` evidence in JSON.
    ch_dir = tmp_path / "ch_r3_f12"
    ch_dir.mkdir()
    page_p = ch_dir / "001.png"
    base_rgb = np.full((180, 180, 3), 255, dtype=np.uint8)
    base_rgb[25:45, 25:45] = (10, 10, 10)  # Un-erased original ink A
    base_rgb[125:145, 125:145] = (10, 10, 10)  # Un-erased original ink B
    Image.fromarray(base_rgb).save(page_p)

    paths = resolve_page_paths(page_p)
    paths.inpainted_path.parent.mkdir(parents=True, exist_ok=True)
    # Pre-existing unsynced `inpainted_path` still has ink A & B untouched!
    Image.fromarray(base_rgb).save(paths.inpainted_path)

    mask_a = np.zeros((180, 180), dtype=np.uint8)
    mask_a[20:50, 20:50] = 255
    save_page_json_atomic(
        paths,
        str(page_p.resolve()),
        {"mask_raw": encode_mask_base64(mask_a), "regions": []},
        auto_initial_snapshot=False,
    )

    # Adding mask B (`[120, 120, 150, 150]`) via local `solid_fill` MUST detect `prev_mask_synced=False` and repair BOTH A and B!
    server_mod.mtui_edit_mask_and_inpaint(
        str(page_p),
        add_rects=[[120, 120, 150, 150]],
        inpaint_method="solid_fill",
        re_render=False,
    )
    repaired_rgb = np.asarray(Image.open(paths.inpainted_path).convert("RGB"))
    assert float(np.mean(repaired_rgb[25:45, 25:45])) > 245.0, "Mask A must also be repaired when sync proof was missing"
    assert float(np.mean(repaired_rgb[125:145, 125:145])) > 245.0, "Newly added mask B must be repaired"

    _, synced_json = load_page_json(resolve_page_paths(page_p))
    cur_m = np.zeros((180, 180), dtype=np.uint8)
    cur_m[20:50, 20:50] = 255
    cur_m[120:150, 120:150] = 255
    assert server_mod._is_prev_mask_synced(
        synced_json,
        server_mod._mask_sha1_hex(cur_m, (180, 180)),
        paths.inpainted_path,
    ) is True

    # 2. R3-F34: Fresh page WITHOUT pre-existing `inpainted_path`: if JSON save fails AND unlinking the newly
    #    created `inpainted_path` raises `OSError`, caller gets both original save error and residual file path!
    page2 = ch_dir / "002.png"
    Image.fromarray(base_rgb).save(page2)
    paths2 = resolve_page_paths(page2)
    save_page_json_atomic(paths2, str(page2.resolve()), {"regions": []}, auto_initial_snapshot=False)
    assert not paths2.inpainted_path.exists()

    orig_unlink = Path.unlink

    def _fail_inpaint_unlink(self_p: Path, *a: Any, **kw: Any) -> None:
        if os.path.normcase(os.path.abspath(str(self_p))) == os.path.normcase(os.path.abspath(str(paths2.inpainted_path))):
            raise PermissionError("locked by external viewer")
        orig_unlink(self_p, *a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            server_mod,
            "save_page_json_atomic",
            lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disk quota exceeded")),
        )
        mp.setattr(Path, "unlink", _fail_inpaint_unlink)
        with pytest.raises(RuntimeError, match="disk quota exceeded") as exc_info:
            server_mod.mtui_edit_mask_and_inpaint(
                str(page2),
                add_rects=[[20, 20, 50, 50]],
                inpaint_method="solid_fill",
                re_render=False,
            )
    err_str = str(exc_info.value)
    assert str(paths2.inpainted_path) in err_str
    assert "locked by external viewer" in err_str
    assert isinstance(exc_info.value.__cause__, OSError)
    orig_unlink(paths2.inpainted_path)


def test_r3_f11_f38_worker_client_lingering_proc_blocks_spawn_and_pre_spawn_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify unconfirmed worker exit blocks replacement spawn (`R3-F11`) and expired post-drain budget skips `Popen` (`R3-F38`)."""
    settings = get_runtime_settings()
    monkeypatch.setattr(type(settings), "is_engine_available", property(lambda _s: True))
    monkeypatch.setattr(settings, "mtui_root", tmp_path)
    monkeypatch.setattr(settings, "mtui_python", Path(sys.executable))

    spawn_count = {"n": 0}

    class ReadyReplacementProc:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            spawn_count["n"] += 1
            self.pid = 55555
            self.stdout = io.StringIO('MTUI_WORKER_READY {"port": 44556}\n')
            self.stderr = io.StringIO("")
            self.stdin = None
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.returncode = -9

    monkeypatch.setattr("mtui_mcp.worker.client.subprocess.Popen", ReadyReplacementProc)

    # 1. R3-F11: `shutdown()` cannot confirm exit (`wait(timeout=2.0)` times out & `poll()` remains `None`)
    class LingeringGpuProc:
        pid = 98765
        stdout = None
        stderr = None
        stdin = None

        def __init__(self) -> None:
            self.alive = True

        def poll(self) -> int | None:
            return None if self.alive else -9

        def kill(self) -> None:
            # Simulates uninterruptible kernel/driver sleep where SIGKILL does not reap within 2s
            pass

        def wait(self, timeout: float | None = None) -> int:
            if self.alive:
                raise subprocess.TimeoutExpired(cmd=["worker"], timeout=0.01)
            return -9

    client = MtuiWorkerClient()
    lingering = LingeringGpuProc()
    client._proc = lingering  # type: ignore[assignment]
    client._port = 44550
    client._token = ""
    client._bound_root = tmp_path
    client._bound_python = Path(sys.executable)

    client.shutdown()
    assert lingering in client._shutting_down_procs

    with pytest.raises(TimeoutError, match="98765"):
        client._ensure_started_instance(startup_timeout_sec=0.12)
    assert spawn_count["n"] == 0, "Must NEVER spawn a replacement worker while lingering GPU worker is still alive"

    # Once lingering process finally exits in OS, subsequent start reaps it and spawns cleanly!
    lingering.alive = False
    _, new_port, _ = client._ensure_started_instance(startup_timeout_sec=2.0)
    assert new_port == 44556
    assert client._shutting_down_procs == set()
    assert spawn_count["n"] == 1
    client._proc = None

    # 2. R3-F38 & R4-F34: Deterministic controlled-clock & drain-double test where draining old workers succeeds
    #    right as `deadline` expires, proving the pre-`Popen` budget check raises `TimeoutError` without calling `Popen`.
    spawn_count["n"] = 0
    fake_clock = {"now": 1000.0}
    late_exit_proc = LingeringGpuProc()
    client._shutting_down_procs.add(late_exit_proc)  # type: ignore[arg-type]

    def _drain_succeeds_right_at_deadline(drain_deadline: float) -> None:
        assert drain_deadline == pytest.approx(1000.1)
        late_exit_proc.alive = False
        client._reap_shutting_down_procs_locked()
        assert client._shutting_down_procs == set()
        # Advance monotonic clock to `deadline` AFTER drain succeeded so only the pre-spawn check can fire!
        fake_clock["now"] = drain_deadline

    with pytest.MonkeyPatch.context() as mp_clock:
        mp_clock.setattr("mtui_mcp.worker.client.time.monotonic", lambda: fake_clock["now"])
        mp_clock.setattr(client, "_wait_for_shutting_down_procs_locked", _drain_succeeds_right_at_deadline)
        with pytest.raises(TimeoutError, match="before process spawn"):
            client._ensure_started_instance(startup_timeout_sec=0.1)
    assert spawn_count["n"] == 0, "Must NOT call Popen after startup budget was already exhausted waiting for drain"
    client._shutting_down_procs.clear()


def test_r3_f15_f16_f17_f42_engine_worker_integrity_and_cli_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `translation_raw` conflict check (`R3-F15`), `translation_map.json` preservation (`R3-F16`), relative migrated key rejection (`R3-F17`), and `memory_percent` rounding guard (`R3-F42`)."""
    # 1. R3-F17: Relative migrated key pointing to ANOTHER existing page (`../../../other/ch2/001.png`) is rejected,
    #    while migrated relative key whose old path no longer exists on disk is still supported!
    book_ch2 = tmp_path / "book" / "ch2"
    other_ch2 = tmp_path / "other" / "ch2"
    book_other_ch2 = tmp_path / "book" / "other" / "ch2"
    for d in (book_ch2, other_ch2, book_other_ch2):
        d.mkdir(parents=True, exist_ok=True)
    p_target = _make_sample_page(book_ch2, "001.png", size_hw=(80, 80))
    _make_sample_page(other_ch2, "001.png", size_hw=(80, 80))
    _make_sample_page(book_other_ch2, "001.png", size_hw=(80, 80))

    ch2_json_dir = book_ch2 / "manga_translator_work" / "json"
    ch2_json_dir.mkdir(parents=True, exist_ok=True)
    ch2_json = ch2_json_dir / "001_translations.json"

    for bad_rel_key in (
        "../../../other/ch2/001.png",
        "../../../../other/ch2/001.png",
        "../../other/ch2/001.png",
    ):
        k_bad, regs_bad = engine_worker._select_page_entry_from_raw_json(
            {bad_rel_key: {"regions": [{"text": "wrong_page"}]}},
            p_target,
            json_path=ch2_json,
        )
        assert k_bad is None and regs_bad == [], f"Expected {bad_rel_key!r} pointing to another live page to be rejected"

    gone_rel_key = "../../../unmounted_archive/ch2/001.png"
    k_ok, regs_ok = engine_worker._select_page_entry_from_raw_json(
        {gone_rel_key: {"regions": [{"text": "migrated_ok"}]}},
        p_target,
        json_path=ch2_json,
    )
    assert k_ok == gone_rel_key and len(regs_ok) == 1

    # 2. R3-F15: Concurrent edit to ONLY `translation_raw` when `orig_translation` is non-empty MUST trigger conflict!
    tr_json = tmp_path / "tr_raw_test_translations.json"
    tr_page = _make_sample_page(tmp_path, "tr_raw_test.png", size_hw=(100, 100))
    tr_json.write_text(
        json.dumps(
            {
                str(tr_page): {
                    "regions": [
                        {
                            "region_uid": "u_raw",
                            "lines": [[[10.0, 10.0], [60.0, 10.0], [60.0, 60.0], [10.0, 60.0]]],
                            "text": "原文",
                            "texts": ["原文"],
                            "translation": "非空原译",
                            "translation_raw": "非空原译",
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeTranslator(str, Enum):
        openai = "openai"

    class FakeQuad:
        def __init__(self, pts: np.ndarray, text: str = "", prob: float = 1.0) -> None:
            self.pts = pts
            self.text = text
            self.prob = prob

    class MutateOnlyRawTransSvc:
        async def translate_text_batch(self, texts: list[str], **_kw: Any) -> list[Any]:
            cur_doc = json.loads(tr_json.read_text(encoding="utf-8"))
            cur_doc[str(tr_page)]["regions"][0]["translation_raw"] = "并发仅改translation_raw"
            tr_json.write_text(json.dumps(cur_doc, ensure_ascii=False), encoding="utf-8")
            return [types.SimpleNamespace(translated_text=f"AI新译:{t}") for t in texts]

    monkeypatch.setitem(
        sys.modules,
        "torch",
        types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)),
    )
    monkeypatch.setitem(sys.modules, "manga_translator", types.ModuleType("manga_translator"))
    monkeypatch.setitem(sys.modules, "manga_translator.utils", types.SimpleNamespace(Quadrilateral=FakeQuad))
    monkeypatch.setitem(sys.modules, "manga_translator.config", types.SimpleNamespace(Translator=FakeTranslator))
    monkeypatch.setitem(
        sys.modules,
        "services",
        types.SimpleNamespace(
            get_config_service=lambda: None,
            get_ocr_service=lambda: None,
            get_translation_service=lambda: MutateOnlyRawTransSvc(),
        ),
    )

    rt = engine_worker.EngineRuntime(str(tmp_path))
    rt._initialized = True
    tr_res = rt.ocr_or_translate_regions(
        {
            "source_path": str(tr_page),
            "display_base_path": str(tr_page),
            "json_path": str(tr_json),
            "region_indices": [0],
            "run_ocr": False,
            "run_translate": True,
            "commit": True,
        }
    )
    assert tr_res["status"] == "partial"
    assert tr_res["committed"] is False
    assert tr_res["conflict_count"] == 1
    assert tr_res["results"][0]["conflict_reason"] == "source_or_translation_modified_during_translate"
    after_tr_reg = json.loads(tr_json.read_text(encoding="utf-8"))[str(tr_page)]["regions"][0]
    assert after_tr_reg["translation_raw"] == "并发仅改translation_raw"

    # 3. R3-F16: Unreadable/corrupt existing `translation_map.json` MUST NOT be overwritten; `render_page` reports `"partial"`!
    out_dir = tmp_path / "map_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    map_file = out_dir / "translation_map.json"
    original_map_content = '{"C:/existing/pageA.png": "C:/raw/pageA.png"}\n'
    map_file.write_text(original_map_content, encoding="utf-8")

    real_open = open

    def _deny_reading_map(file_arg: Any, mode: str = "r", *a: Any, **kw: Any) -> Any:
        if "r" in mode and os.path.normcase(os.path.abspath(str(file_arg))) == os.path.normcase(str(map_file)):
            raise PermissionError("transient lock on translation_map.json")
        return real_open(file_arg, mode, *a, **kw)

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
            ExportJob=type("FakeJob", (), {"__init__": lambda s, **k: s.__dict__.update(k), "release_resources": lambda s: None}),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "editor.document_state",
        types.SimpleNamespace(ExportBase=type("FakeBase", (), {"__init__": lambda s, **k: s.__dict__.update(k)})),
    )
    monkeypatch.setitem(
        sys.modules,
        "editor.inpaint_state",
        types.SimpleNamespace(InpaintKey=lambda **_k: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "services",
        types.SimpleNamespace(
            get_file_service=lambda: types.SimpleNamespace(
                load_translation_json=lambda _p: ([], None, (100, 100), {"paint": None, "stamp": None, "paste_overlays": []})
            ),
            get_render_parameter_service=lambda: types.SimpleNamespace(
                clear_cache=lambda: None,
                export_parameters_for_backend=lambda _i, _r: {},
            ),
            get_config_service=lambda: None,
        ),
    )

    out_b = out_dir / "tr_raw_test.png"
    rt._export_service = types.SimpleNamespace(
        execute_export_job=lambda _job: (Image.open(tr_page).save(out_b) or types.SimpleNamespace(error=None))
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("builtins.open", _deny_reading_map)
        render_partial = rt.render_page(
            {
                "source_path": str(tr_page),
                "display_base_path": str(tr_page),
                "inpainted_path": str(tmp_path / "tr_raw_test_inpainted.png"),
                "output_path": str(out_b),
                "json_path": str(tr_json),
            }
        )
    assert render_partial["status"] == "partial"
    assert render_partial["metadata_saved"] is False
    assert "translation_map:" in render_partial["metadata_error"]
    assert out_b.is_file(), "Rendered image on disk must NOT be deleted when translation_map update fails"
    assert map_file.read_text(encoding="utf-8") == original_map_content, "Existing Page A map entry must be preserved!"

    # Also verify corrupt JSON in `translation_map.json` is preserved rather than clobbered
    corrupt_bytes = "{corrupt-json-do-not-clobber"
    map_file.write_text(corrupt_bytes, encoding="utf-8")
    render_corrupt = rt.render_page(
        {
            "source_path": str(tr_page),
            "display_base_path": str(tr_page),
            "inpainted_path": str(tmp_path / "tr_raw_test_inpainted.png"),
            "output_path": str(out_b),
            "json_path": str(tr_json),
        }
    )
    assert render_corrupt["status"] == "partial"
    assert render_corrupt["metadata_saved"] is False
    assert map_file.read_text(encoding="utf-8") == corrupt_bytes

    # 4. R3-F42: `memory_percent` in `(0, 0.5]` rounds to `0` and MUST fail validation BEFORE `release_vram()`!
    vram_evictions = {"count": 0}
    monkeypatch.setattr(rt, "release_vram", lambda: vram_evictions.__setitem__("count", vram_evictions["count"] + 1) or {})
    for sub_one_pct in (0.1, 0.4, 0.5):
        with pytest.raises(ValueError, match="rounds to 0"):
            rt.run_cli_pipeline({"inputs": [str(tmp_path / "raw")], "memory_percent": sub_one_pct})
    assert vram_evictions["count"] == 0


# ===========================================================================
# Round 4 Slice 3 Regression Tests (R4-F01, R4-F22, R4-F32, R4-F34, R4-F35, R4-F41)
# ===========================================================================


def test_r4_f01_desktop_untracked_inpaint_preserves_neural_background_and_repairs_raw_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify desktop/CLI pages without sync metadata preserve neural LaMa backgrounds while repairing un-inpainted components (`R4-F01` vs `R3-F12`)."""
    ch_dir = tmp_path / "ch_r4_f01"
    ch_dir.mkdir()
    page_p = ch_dir / "001.png"

    # Build a 220x220 comic page:
    # - Bubble 1 (`[20:70, 20:70]`): complex colored artwork background + dark Japanese ink
    # - Bubble 2 (`[120:190, 120:190]`): pure white speech bubble + dark text + leftover speck `[172:184, 140:155]`
    # - Bubble 3 (`[20:60, 130:180]`): manually added mask component still holding raw un-erased ink in `inpainted_path`
    yy, xx = np.mgrid[0:220, 0:220]
    base_rgb = np.full((220, 220, 3), 255, dtype=np.uint8)
    # Textured artwork region around Bubble 1 so any flat `solid_fill` would destroy the gradient/checkerboard
    base_rgb[10:85, 10:85, 0] = np.clip(90 + (xx[10:85, 10:85] * 2) % 80, 0, 255).astype(np.uint8)
    base_rgb[10:85, 10:85, 1] = np.clip(110 + (yy[10:85, 10:85] * 3) % 70, 0, 255).astype(np.uint8)
    base_rgb[10:85, 10:85, 2] = 175
    # Original ink strokes inside Bubble 1, Bubble 2, speck, and Bubble 3
    base_rgb[30:60, 30:60] = (12, 14, 18)
    base_rgb[135:165, 135:170] = (15, 15, 15)
    base_rgb[172:184, 140:155] = (20, 20, 20)
    base_rgb[28:52, 140:170] = (10, 10, 10)
    Image.fromarray(base_rgb).save(page_p)

    paths = resolve_page_paths(page_p)
    paths.inpainted_path.parent.mkdir(parents=True, exist_ok=True)

    # Desktop-saved `001_inpainted.png`:
    # - Bubble 1 (`[20:70, 20:70]`) has complex neural LaMa reconstructed gradient (`prev_inpaint != base_rgb`)
    # - Bubble 2 (`[130:168, 130:175]`) was cleaned to white
    # - Bubble 3 (`[25:55, 135:175]`) was NOT inpainted yet (`prev_inpaint == base_rgb` on that component!)
    desktop_inpaint = base_rgb.copy()
    neural_lama_patch = np.dstack(
        [
            np.clip(120 + (xx[20:70, 20:70] + yy[20:70, 20:70]) % 60, 0, 255),
            np.clip(145 + (xx[20:70, 20:70] * 2) % 50, 0, 255),
            np.full((50, 50), 205, dtype=np.int32),
        ]
    ).astype(np.uint8)
    desktop_inpaint[20:70, 20:70] = neural_lama_patch
    desktop_inpaint[130:168, 130:175] = 255
    Image.fromarray(desktop_inpaint).save(paths.inpainted_path)

    prev_mask = np.zeros((220, 220), dtype=np.uint8)
    prev_mask[20:70, 20:70] = 255  # Component 1: already neural-inpainted (`prev != base`)
    prev_mask[130:168, 130:175] = 255  # Component 2: already inpainted (`prev != base`)
    prev_mask[25:55, 135:175] = 255  # Component 3: un-inpainted raw component (`prev == base`)

    # Normal desktop/CLI JSON does NOT write `mask_inpainted_sha1` or `mask_inpainted_stat`!
    save_page_json_atomic(
        paths,
        str(page_p.resolve()),
        {"mask_raw": encode_mask_base64(prev_mask), "regions": []},
        auto_initial_snapshot=False,
    )

    _, unloaded_json = load_page_json(resolve_page_paths(page_p))
    assert "mask_inpainted_sha1" not in unloaded_json
    assert "mask_inpainted_stat" not in unloaded_json
    assert (
        server_mod._prev_mask_sync_state(
            unloaded_json,
            server_mod._mask_sha1_hex(prev_mask, (220, 220)),
            paths.inpainted_path,
        )
        == "untracked"
    )

    # 1. Single-bubble `solid_fill` on Bubble 2's speck (`full_reinpaint=False`):
    #    - MUST preserve Bubble 1's complex neural LaMa artwork bit-for-bit!
    #    - MUST clean Bubble 2's newly added speck!
    #    - MUST ALSO repair Bubble 3's un-inpainted raw component (`prev_inpaint == base_rgb`)!
    server_mod.mtui_edit_mask_and_inpaint(
        str(page_p),
        add_rects=[[140, 172, 155, 184]],
        inpaint_method="solid_fill",
        full_reinpaint=False,
        re_render=False,
    )
    after_local = np.asarray(Image.open(paths.inpainted_path).convert("RGB"))
    assert np.array_equal(after_local[20:70, 20:70], neural_lama_patch), (
        "Single-bubble solid_fill MUST NOT overwrite neural LaMa artwork on other desktop bubbles!"
    )
    assert float(np.mean(after_local[172:184, 140:155])) > 245.0, "Newly masked speck in Bubble 2 must be filled"
    assert float(np.mean(after_local[28:52, 140:170])) > 245.0, "Un-inpainted raw component 3 must also be repaired"

    # 2. Desktop page without sync metadata using `inpaint_method="neural"`:
    #    passes `prev_mask_synced=True` and incremental `added_mask_b64` rather than discarding `prev_inpaint`!
    _, post_json = load_page_json(resolve_page_paths(page_p))
    post_json.pop("mask_inpainted_sha1", None)
    post_json.pop("mask_inpainted_stat", None)
    save_page_json_atomic(resolve_page_paths(page_p), str(page_p.resolve()), post_json, auto_initial_snapshot=False)

    neural_calls: list[dict[str, Any]] = []

    class DesktopNeuralWorkerStub:
        is_running = True

        def call(self, action: str, params: dict[str, Any] | None = None, timeout_sec: float = 30.0) -> dict[str, Any]:
            assert action == "incremental_neural_inpaint"
            neural_calls.append(dict(params or {}))
            return {"status": "ok", "inpainted_path": str(paths.inpainted_path)}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server_mod, "get_worker_client", lambda: DesktopNeuralWorkerStub())
        mp.setattr(type(server_mod.get_runtime_settings()), "is_engine_available", property(lambda _s: True))
        server_mod.mtui_edit_mask_and_inpaint(
            str(page_p),
            add_rects=[[140, 185, 155, 195]],
            inpaint_method="neural",
            full_reinpaint=False,
            re_render=False,
        )
    assert len(neural_calls) == 1
    assert neural_calls[0]["prev_mask_synced"] is True
    sent_added = server_mod.decode_mask_base64(neural_calls[0]["added_mask_b64"], (220, 220))
    assert sent_added is not None
    assert int(np.sum(sent_added[20:70, 20:70] > 0)) == 0, "Already-inpainted Bubble 1 must not be in added_mask_b64"
    assert int(np.sum(sent_added[185:195, 140:155] > 0)) == 150

    # 3. Explicit `full_reinpaint=True` DOES re-inpaint the whole mask from scratch when explicitly requested
    server_mod.mtui_edit_mask_and_inpaint(
        str(page_p),
        inpaint_method="solid_fill",
        full_reinpaint=True,
        re_render=False,
    )
    after_full = np.asarray(Image.open(paths.inpainted_path).convert("RGB"))
    assert not np.array_equal(after_full[20:70, 20:70], neural_lama_patch)

    # 4. Direct `engine_worker.incremental_neural_inpaint(..., prev_mask_sync_state="untracked")` preserves
    #    already-inpainted components while expanding `added_mask` to include un-inpainted raw components!
    Image.fromarray(desktop_inpaint).save(paths.inpainted_path)

    class FakeInpainter(str, Enum):
        lama_large = "lama_large"
        blur = "blur"

    class FakePrecision(str, Enum):
        fp32 = "fp32"

    class FakeInpainterConfig:
        def __init__(self) -> None:
            self.inpainting_precision = FakePrecision.fp32
            self.force_use_torch_inpainting = False
            self.blur_radius = 0

    worker_dispatches: list[dict[str, Any]] = []

    async def _fake_worker_dispatch(**kwargs: Any) -> np.ndarray:
        worker_dispatches.append(kwargs)
        out_crop = kwargs["image"].copy()
        out_crop[kwargs["mask"] > 0] = 250
        return out_crop

    with pytest.MonkeyPatch.context() as mp_ew:
        mp_ew.setitem(
            sys.modules,
            "torch",
            types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)),
        )
        mp_ew.setitem(sys.modules, "manga_translator", types.ModuleType("manga_translator"))
        mp_ew.setitem(
            sys.modules,
            "manga_translator.config",
            types.SimpleNamespace(
                Inpainter=FakeInpainter,
                InpainterConfig=FakeInpainterConfig,
                InpaintPrecision=FakePrecision,
            ),
        )
        mp_ew.setitem(sys.modules, "manga_translator.inpainting", types.SimpleNamespace(dispatch=_fake_worker_dispatch))
        rt = engine_worker.EngineRuntime(str(tmp_path))
        rt._initialized = True
        mp_ew.setattr(rt, "_get_config_dict", lambda: {"inpainter": {"inpainter": "lama_large"}, "cli": {"use_gpu": False}})
        saved_worker_imgs: list[np.ndarray] = []
        rt._export_service = types.SimpleNamespace(
            save_inpainted_image=lambda _p, img, _cfg: (saved_worker_imgs.append(img.copy()) or str(paths.inpainted_path))
        )
        cur_m_ew = prev_mask.copy()
        add_m_ew = np.zeros((220, 220), dtype=np.uint8)
        add_m_ew[172:184, 140:155] = 255
        cur_m_ew[172:184, 140:155] = 255
        ew_res = rt.incremental_neural_inpaint(
            {
                "display_base_path": str(paths.display_base_path),
                "inpainted_path": str(paths.inpainted_path),
                "current_mask_b64": encode_mask_base64(cur_m_ew),
                "added_mask_b64": encode_mask_base64(add_m_ew),
                "prev_mask_b64": encode_mask_base64(prev_mask),
                "prev_mask_sync_state": "untracked",
            }
        )
    assert ew_res["status"] == "ok"
    assert len(saved_worker_imgs) == 1
    assert np.array_equal(saved_worker_imgs[0][20:70, 20:70], neural_lama_patch)
    assert float(np.mean(saved_worker_imgs[0][28:52, 140:170])) >= 249.0
    assert float(np.mean(saved_worker_imgs[0][172:184, 140:155])) >= 249.0


def test_r4_f22_worker_shutdown_does_not_deadlock_closing_pipes_with_blocked_readers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `shutdown()` never calls `pipe.close()` synchronously while worker/readers hold `BufferedReader` lock (`R4-F22`, `R5-F21`)."""
    monkeypatch.setattr("mtui_mcp.worker.client._SHUTDOWN_DRAIN_WAIT_SEC", 0.05)

    r_out_fd = -1
    w_out_fd = -1
    r_err_fd = -1
    w_err_fd = -1
    stdout_pipe: io.TextIOWrapper | None = None
    stderr_pipe: io.TextIOWrapper | None = None
    t_out: threading.Thread | None = None
    t_err: threading.Thread | None = None
    t_shutdown: threading.Thread | None = None
    out_handed_to_reader = False
    err_handed_to_reader = False

    try:
        r_out_fd, w_out_fd = os.pipe()
        stdout_pipe = io.TextIOWrapper(io.BufferedReader(io.FileIO(r_out_fd, "rb")), encoding="utf-8")
        r_out_fd = -1

        r_err_fd, w_err_fd = os.pipe()
        stderr_pipe = io.TextIOWrapper(io.BufferedReader(io.FileIO(r_err_fd, "rb")), encoding="utf-8")
        r_err_fd = -1

        class HungPipeWorkerProc:
            pid = 77701
            stdin = None

            def __init__(self) -> None:
                self.stdout = stdout_pipe
                self.stderr = stderr_pipe
                self.alive = True

            def poll(self) -> int | None:
                return None if self.alive else -9

            def kill(self) -> None:
                # Process stays stuck in uninterruptible sleep; write fds stay open so readers block in `readline()`
                pass

            def wait(self, timeout: float | None = None) -> int:
                if self.alive:
                    raise subprocess.TimeoutExpired(cmd=["worker"], timeout=0.02)
                return -9

        client = MtuiWorkerClient()
        hung_proc = HungPipeWorkerProc()

        stdout_entered = threading.Event()
        stderr_entered = threading.Event()

        def _blocked_stdout_reader() -> None:
            assert stdout_pipe is not None
            stdout_entered.set()
            try:
                for _ in iter(stdout_pipe.readline, ""):
                    pass
            finally:
                stdout_pipe.close()

        def _blocked_stderr_reader() -> None:
            assert stderr_pipe is not None
            stderr_entered.set()
            try:
                for _ in iter(stderr_pipe.readline, ""):
                    pass
            finally:
                stderr_pipe.close()

        t_out = threading.Thread(target=_blocked_stdout_reader, daemon=True)
        t_err = threading.Thread(target=_blocked_stderr_reader, daemon=True)
        t_out.start()
        out_handed_to_reader = True
        t_err.start()
        err_handed_to_reader = True
        assert stdout_entered.wait(timeout=1.0) and stderr_entered.wait(timeout=1.0)
        # Give both threads a brief moment to enter CPython's blocking `BufferedReader.readline()`
        time.sleep(0.03)

        client._proc = hung_proc  # type: ignore[assignment]
        client._port = 45678
        client._token = ""
        client._proc_pipe_readers[hung_proc] = {"stdout": t_out, "stderr": t_err}

        t_shutdown = threading.Thread(target=client.shutdown, daemon=True)
        t_shutdown.start()
        t_shutdown.join(timeout=1.5)
        assert not t_shutdown.is_alive(), "client.shutdown() deadlocked closing active stdout/stderr pipes!"
        assert hung_proc in client._shutting_down_procs, "Still-alive worker must remain in _shutting_down_procs"
        assert not stdout_pipe.closed and not stderr_pipe.closed

        # Now simulate delayed worker OS exit (closing write ends of both pipes):
        hung_proc.alive = False
        os.close(w_out_fd)
        w_out_fd = -1
        os.close(w_err_fd)
        w_err_fd = -1

        t_out.join(timeout=1.5)
        t_err.join(timeout=1.5)
        assert not t_out.is_alive() and not t_err.is_alive()
        assert stdout_pipe.closed and stderr_pipe.closed

        with client._lock:
            client._reap_shutting_down_procs_locked()
        assert hung_proc not in client._shutting_down_procs
        assert hung_proc not in client._proc_pipe_readers
    finally:
        for fd in (w_out_fd, w_err_fd, r_out_fd, r_err_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        for t in (t_out, t_err, t_shutdown):
            if t is not None and t.ident is not None:
                t.join(timeout=0.5)
        if not out_handed_to_reader and stdout_pipe is not None and not stdout_pipe.closed:
            try:
                stdout_pipe.close()
            except OSError:
                pass
        if not err_handed_to_reader and stderr_pipe is not None and not stderr_pipe.closed:
            try:
                stderr_pipe.close()
            except OSError:
                pass


def test_r4_f32_config_switch_respects_startup_timeout_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify config-switch shutdown in `_ensure_started_instance` obeys `startup_timeout_sec` (`R4-F32`)."""
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_c = tmp_path / "root_c"
    for d in (root_a, root_b, root_c):
        d.mkdir()

    settings = get_runtime_settings()
    monkeypatch.setattr(type(settings), "is_engine_available", property(lambda _s: True))
    monkeypatch.setattr(settings, "mtui_root", root_b)
    monkeypatch.setattr(settings, "mtui_python", Path(sys.executable))

    old_exit_gate = threading.Event()
    spawned_roots: list[str] = []

    class SlowDrainingOldProc:
        pid = 88001
        stdout = None
        stderr = None
        stdin = None

        def poll(self) -> int | None:
            return 0 if old_exit_gate.is_set() else None

        def wait(self, timeout: float | None = None) -> int:
            if old_exit_gate.wait(timeout=timeout):
                return 0
            raise subprocess.TimeoutExpired(cmd=["old_worker"], timeout=timeout or 0.1)

        def kill(self) -> None:
            pass

    class FastReadyNewProc:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            spawned_roots.append(str(kwargs.get("cwd") or ""))
            self.pid = 88002 + len(spawned_roots)
            self.stdout = io.StringIO(f'MTUI_WORKER_READY {{"port": {46000 + len(spawned_roots)}}}\n')
            self.stderr = io.StringIO("")
            self.stdin = None
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.returncode = -9

    class FakeShutdownResp:
        def __enter__(self) -> "FakeShutdownResp":
            return self

        def __exit__(self, *_a: Any) -> None:
            pass

        def read(self) -> bytes:
            return b'{"ok":true}'

    monkeypatch.setattr("mtui_mcp.worker.client._LOOPBACK_OPENER.open", lambda *_a, **_kw: FakeShutdownResp())
    monkeypatch.setattr("mtui_mcp.worker.client.subprocess.Popen", FastReadyNewProc)

    client = MtuiWorkerClient()
    old_proc = SlowDrainingOldProc()
    client._proc = old_proc  # type: ignore[assignment]
    client._port = 45999
    client._token = "tok_old"
    client._bound_root = root_a
    client._bound_python = Path(sys.executable)

    # 1. Old worker does NOT exit within `startup_timeout_sec=0.12`:
    #    caller MUST get `TimeoutError` promptly (~0.12s, NOT `_SHUTDOWN_DRAIN_WAIT_SEC` = 14s!),
    #    old worker MUST stay in `_shutting_down_procs`, and `Popen` MUST NOT run.
    t0 = time.monotonic()
    with pytest.raises(TimeoutError, match="88001"):
        client._ensure_started_instance(startup_timeout_sec=0.12)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"Config switch ignored startup_timeout_sec=0.12 (took {elapsed:.2f}s)"
    assert old_proc in client._shutting_down_procs
    assert spawned_roots == []

    # 2. Once old worker exits, replacement worker for `root_b` spawns cleanly!
    old_exit_gate.set()
    _, port_b, _ = client._ensure_started_instance(startup_timeout_sec=2.0)
    assert port_b == 46001
    assert client._shutting_down_procs == set()
    assert spawned_roots == [str(root_b)]

    # 3. Config switch to `root_c` when `port_b` worker exits well within `startup_timeout_sec` succeeds in one call!
    monkeypatch.setattr(settings, "mtui_root", root_c)
    _, port_c, _ = client._ensure_started_instance(startup_timeout_sec=2.0)
    assert port_c == 46002
    assert spawned_roots == [str(root_b), str(root_c)]
    client._proc = None


def test_r4_f35_unstarted_inpaint_thread_cleanup_does_not_mask_timeout_error() -> None:
    """Verify `R4-F35` thread-started flags prevent `RuntimeError('cannot join thread before it is started')`."""
    render_done = threading.Event()
    t_render = threading.Thread(target=lambda: render_done.wait(timeout=0.5), daemon=True)
    t_inpaint = threading.Thread(target=lambda: None, daemon=True)
    render_started = False
    inpaint_started = False

    with pytest.raises(AssertionError, match="simulated_export_wait_timeout"):
        try:
            t_render.start()
            render_started = True
            raise AssertionError("simulated_export_wait_timeout")
        finally:
            render_done.set()
            if inpaint_started:
                t_inpaint.join(timeout=0.5)
            if render_started:
                t_render.join(timeout=0.5)
    assert not t_render.is_alive()


def test_r4_f41_mtui_batch_edit_chapter_renders_committed_pages_on_partial_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `mtui_batch_edit_chapter` renders already-committed pages before re-raising partial errors (`R4-F41`)."""
    ch_dir = tmp_path / "ch_r4_f41"
    ch_dir.mkdir()
    p1 = _make_sample_page(ch_dir, "001.png", size_hw=(120, 120))
    p2 = _make_sample_page(ch_dir, "002.png", size_hw=(120, 120))
    paths1 = resolve_page_paths(p1)
    paths2 = resolve_page_paths(p2)
    for pp in (paths1, paths2):
        save_page_json_atomic(pp, str(pp.source_path.resolve()), {"regions": []}, auto_initial_snapshot=False)
    paths1 = resolve_page_paths(p1)
    paths2 = resolve_page_paths(p2)

    rendered_calls: list[str] = []

    # 1. Partial error carrying `committed_page_paths` (`failure_stage='post_commit_summary'`):
    #    both `001.png` and `002.png` are rendered before `RuntimeError` is re-raised with `failure_stage` preserved!
    def _failing_batch_with_paths(*_a: Any, **_kw: Any) -> Any:
        err = RuntimeError(
            "Batch edit committed 002.png but post-commit summary reload failed "
            "(committed_pages=['001.png', '002.png'], failed_page='002.png', failure_stage='post_commit_summary'): boom"
        )
        err.committed_pages = ["001.png", "002.png"]  # type: ignore[attr-defined]
        err.committed_page_paths = [paths1, paths2]  # type: ignore[attr-defined]
        err.failed_page = "002.png"  # type: ignore[attr-defined]
        err.page_committed = True  # type: ignore[attr-defined]
        err.failure_stage = "post_commit_summary"  # type: ignore[attr-defined]
        raise err

    def _render_ok(pp: Any, **_kw: Any) -> dict[str, Any]:
        rendered_calls.append(pp.source_path.name)
        return {"status": "ok", "output_path": str(pp.rendered_output_path)}

    monkeypatch.setattr(server_mod, "batch_edit_chapter_pages", _failing_batch_with_paths)
    monkeypatch.setattr(server_mod, "_render_single_page_via_worker", _render_ok)

    with pytest.raises(RuntimeError, match="failure_stage='post_commit_summary'") as exc_info:
        server_mod.mtui_batch_edit_chapter(str(ch_dir), find_text="旧", replace_text="新", re_render=True)
    assert rendered_calls == ["001.png", "002.png"]
    assert getattr(exc_info.value, "failure_stage", None) == "post_commit_summary"
    assert getattr(exc_info.value, "rendered_outputs", None) == [
        str(paths1.rendered_output_path),
        str(paths2.rendered_output_path),
    ]
    assert "rendered_outputs=" in str(exc_info.value)

    # 2. Fallback when `committed_page_paths` is absent (only `committed_pages`) AND one render fails:
    #    renders resolved committed pages, records `render_errors` note + attribute, and preserves original `RuntimeError`!
    rendered_calls.clear()

    def _failing_batch_names_only(*_a: Any, **_kw: Any) -> Any:
        err = RuntimeError(
            "Batch edit failed on 002.png after committing committed_pages=['001.png', '002.png'] "
            "(failed_page='002.png', failure_stage='commit'): disk error"
        )
        err.committed_pages = ["001.png", "002.png"]  # type: ignore[attr-defined]
        err.failed_page = "002.png"  # type: ignore[attr-defined]
        err.page_committed = False  # type: ignore[attr-defined]
        err.failure_stage = "commit"  # type: ignore[attr-defined]
        raise err

    def _render_flaky(pp: Any, **_kw: Any) -> dict[str, Any]:
        rendered_calls.append(pp.source_path.name)
        if pp.source_path.name == "002.png":
            raise RuntimeError("worker render timeout")
        return {"status": "ok", "output_path": str(pp.rendered_output_path)}

    monkeypatch.setattr(server_mod, "batch_edit_chapter_pages", _failing_batch_names_only)
    monkeypatch.setattr(server_mod, "_render_single_page_via_worker", _render_flaky)

    with pytest.raises(RuntimeError, match="failure_stage='commit'") as exc_info2:
        server_mod.mtui_batch_edit_chapter(str(ch_dir), find_text="旧", replace_text="新", re_render=True)
    assert rendered_calls == ["001.png", "002.png"]
    assert getattr(exc_info2.value, "failure_stage", None) == "commit"
    assert getattr(exc_info2.value, "rendered_outputs", None) == [str(paths1.rendered_output_path)]
    assert getattr(exc_info2.value, "render_errors", None) == ["002.png: worker render timeout"]
    assert "render_errors=" in str(exc_info2.value)
    assert any("worker render timeout" in n for n in getattr(exc_info2.value, "__notes__", []))

    # 3. `re_render=False` skips rendering on partial error
    rendered_calls.clear()
    with pytest.raises(RuntimeError, match="failure_stage='commit'"):
        server_mod.mtui_batch_edit_chapter(str(ch_dir), find_text="旧", replace_text="新", re_render=False)
    assert rendered_calls == []


# ===========================================================================
# Round 5 Slice 3 Regression Tests (R5-F01, R5-F11, R5-F16, R5-F17,
# R5-F19, R5-F20, R5-F21, R5-F22, R5-F35, R5-F36, R5-F37)
# ===========================================================================


def test_r5_f01_jpeg_recompressed_untracked_inpaint_repairs_raw_mask_and_preserves_repaired(
    tmp_path: Path,
) -> None:
    """Verify JPEG Q85/Q95 re-encoded untracked pages do not mistake compression artifacts for completed inpainting (`R5-F01`)."""
    for quality in (85, 95):
        ch_dir = tmp_path / f"ch_r5_f01_q{quality}"
        ch_dir.mkdir()
        page_p = ch_dir / "001.jpg"

        yy, xx = np.mgrid[0:220, 0:220]
        raw_canvas = np.full((220, 220, 3), 255, dtype=np.uint8)
        # Colored artwork banner with sharp chroma transitions (triggers 4:2:0 subsampling diffs up to ~60!)
        raw_canvas[10:80, 10:80] = (230, 35, 45)
        raw_canvas[25:55, 25:55] = (20, 180, 220)
        # Bubble A (`[30:65, 130:185]`): white bubble with sharp dark Japanese stroke lines + colored punctuation
        raw_canvas[36:58:4, 136:178] = (12, 12, 12)
        raw_canvas[36:58, 148:151] = (200, 20, 20)
        # Bubble B (`[140:175, 130:185]`): unmasked speck to be added in the edit call
        raw_canvas[148:168, 138:176] = (15, 15, 15)
        Image.fromarray(raw_canvas).save(page_p, format="JPEG", quality=95)

        base_rgb = np.asarray(Image.open(page_p).convert("RGB"), dtype=np.uint8)
        paths = resolve_page_paths(page_p)
        inp_jpg = paths.inpainted_path.with_suffix(".jpg")
        inp_jpg.parent.mkdir(parents=True, exist_ok=True)
        # Simulate Desktop UI saving `001_inpainted.jpg` before background inpaint finished (JPEG re-encode of untouched base_rgb)
        Image.fromarray(base_rgb).save(inp_jpg, format="JPEG", quality=quality)
        paths = resolve_page_paths(page_p)
        assert paths.inpainted_path == inp_jpg

        recomp_rgb = np.asarray(Image.open(inp_jpg).convert("RGB"), dtype=np.uint8)
        prev_mask = np.zeros((220, 220), dtype=np.uint8)
        prev_mask[30:65, 130:185] = 255
        assert not np.array_equal(recomp_rgb[prev_mask > 0], base_rgb[prev_mask > 0]), (
            "Test setup must produce real JPEG pixel differences inside prev_mask"
        )

        save_page_json_atomic(
            paths,
            str(page_p.resolve()),
            {"mask_raw": encode_mask_base64(prev_mask), "regions": []},
            auto_initial_snapshot=False,
        )

        # 1. Server local `solid_fill`: MUST repair BOTH pre-existing JPEG-recompressed Bubble A AND new Bubble B!
        server_mod.mtui_edit_mask_and_inpaint(
            str(page_p),
            add_rects=[[130, 140, 185, 175]],
            inpaint_method="solid_fill",
            full_reinpaint=False,
            re_render=False,
        )
        repaired_local = np.asarray(Image.open(resolve_page_paths(page_p).inpainted_path).convert("RGB"), dtype=np.uint8)
        assert float(np.mean(repaired_local[35:59, 135:179])) > 245.0, (
            f"JPEG Q{quality} re-saved raw text in Bubble A was falsely skipped as already inpainted!"
        )
        assert float(np.mean(repaired_local[148:168, 138:176])) > 245.0

        # 2. Worker `incremental_neural_inpaint(..., prev_mask_sync_state="untracked")` on JPEG-recompressed raw copy:
        Image.fromarray(base_rgb).save(inp_jpg, format="JPEG", quality=quality)

        class FakeInpainter(str, Enum):
            lama_large = "lama_large"
            blur = "blur"

        class FakePrecision(str, Enum):
            fp32 = "fp32"

        class FakeInpainterConfig:
            def __init__(self) -> None:
                self.inpainting_precision = FakePrecision.fp32
                self.force_use_torch_inpainting = False
                self.blur_radius = 0

        async def _fake_dispatch(**kwargs: Any) -> np.ndarray:
            out_c = kwargs["image"].copy()
            out_c[kwargs["mask"] > 0] = 252
            return out_c

        saved_worker_imgs: list[np.ndarray] = []
        with pytest.MonkeyPatch.context() as mp_ew:
            mp_ew.setitem(
                sys.modules,
                "torch",
                types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)),
            )
            mp_ew.setitem(sys.modules, "manga_translator", types.ModuleType("manga_translator"))
            mp_ew.setitem(
                sys.modules,
                "manga_translator.config",
                types.SimpleNamespace(
                    Inpainter=FakeInpainter,
                    InpainterConfig=FakeInpainterConfig,
                    InpaintPrecision=FakePrecision,
                ),
            )
            mp_ew.setitem(sys.modules, "manga_translator.inpainting", types.SimpleNamespace(dispatch=_fake_dispatch))
            rt = engine_worker.EngineRuntime(str(tmp_path))
            rt._initialized = True
            mp_ew.setattr(
                rt, "_get_config_dict", lambda: {"inpainter": {"inpainter": "lama_large"}, "cli": {"use_gpu": False}}
            )
            rt._export_service = types.SimpleNamespace(
                save_inpainted_image=lambda _p, img, _cfg: (saved_worker_imgs.append(img.copy()) or str(inp_jpg))
            )
            add_m = np.zeros((220, 220), dtype=np.uint8)
            add_m[140:175, 130:185] = 255
            cur_m = np.where((prev_mask > 0) | (add_m > 0), 255, 0).astype(np.uint8)
            ew_res = rt.incremental_neural_inpaint(
                {
                    "display_base_path": str(paths.display_base_path),
                    "inpainted_path": str(inp_jpg),
                    "current_mask_b64": encode_mask_base64(cur_m),
                    "added_mask_b64": encode_mask_base64(add_m),
                    "prev_mask_b64": encode_mask_base64(prev_mask),
                    "prev_mask_sync_state": "untracked",
                }
            )
        assert ew_res["status"] == "ok"
        assert len(saved_worker_imgs) == 1
        assert float(np.mean(saved_worker_imgs[0][35:59, 135:179])) >= 250.0, (
            f"Worker skipped JPEG Q{quality} re-compressed raw text in Bubble A!"
        )
        assert float(np.mean(saved_worker_imgs[0][148:168, 138:176])) >= 250.0
        _ = (xx, yy)


def test_r5_f11_f22_partially_repaired_bridged_component_repairs_residual_ink_preserving_repaired(
    tmp_path: Path,
) -> None:
    """Verify bridged/partially-repaired mask components clean residual ink while preserving repaired pixels (`R5-F11`, `R5-F22`)."""
    import cv2

    ch_dir = tmp_path / "ch_r5_f11_f22"
    ch_dir.mkdir()
    page_p = ch_dir / "001.png"

    yy, xx = np.mgrid[0:220, 0:240]
    base_rgb = np.full((220, 240, 3), 255, dtype=np.uint8)
    # Textured artwork under Left Lobe `[20:70, 20:70]`
    base_rgb[15:80, 15:80, 0] = np.clip(85 + (xx[15:80, 15:80] * 2) % 75, 0, 255).astype(np.uint8)
    base_rgb[15:80, 15:80, 1] = np.clip(105 + (yy[15:80, 15:80] * 3) % 65, 0, 255).astype(np.uint8)
    base_rgb[15:80, 15:80, 2] = 170
    # Raw ink in Left Lobe (`[30:60, 30:60]`), Right Lobe (`[30:60, 145:180]`), and Third Rect (`[150:175, 50:90]`)
    base_rgb[30:60, 30:60] = (12, 14, 18)
    base_rgb[30:60, 145:180] = (14, 14, 14)
    base_rgb[150:175, 50:90] = (16, 16, 16)
    Image.fromarray(base_rgb).save(page_p)

    paths = resolve_page_paths(page_p)
    paths.inpainted_path.parent.mkdir(parents=True, exist_ok=True)

    # Left Lobe `[20:70, 20:70]` WAS repaired with neural LaMa gradient; Right Lobe `[20:70, 135:190]` still has raw ink!
    partial_inpaint = base_rgb.copy()
    lama_patch = np.dstack(
        [
            np.clip(125 + (xx[20:70, 20:70] + yy[20:70, 20:70]) % 55, 0, 255),
            np.clip(150 + (xx[20:70, 20:70] * 2) % 45, 0, 255),
            np.full((50, 50), 205, dtype=np.int32),
        ]
    ).astype(np.uint8)
    partial_inpaint[20:70, 20:70] = lama_patch
    Image.fromarray(partial_inpaint).save(paths.inpainted_path)

    # Single 8-connected component bridging Left Lobe `[20:70, 20:70]` and Right Lobe `[20:70, 135:190]` via `[43:47, 70:135]`
    bridged_mask = np.zeros((220, 240), dtype=np.uint8)
    bridged_mask[20:70, 20:70] = 255
    bridged_mask[43:47, 70:135] = 255
    bridged_mask[20:70, 135:190] = 255
    n_cc, _ = cv2.connectedComponents((bridged_mask > 0).astype(np.uint8), connectivity=8)
    assert n_cc == 2, "Left and Right lobes must form a single connected component in prev_mask"

    save_page_json_atomic(
        paths,
        str(page_p.resolve()),
        {"mask_raw": encode_mask_base64(bridged_mask), "regions": []},
        auto_initial_snapshot=False,
    )

    # 1. R5-F11 (`mtui_add_region` with `solid_fill`): must NOT falsely mark whole mask synced while Right Lobe still has ink!
    server_mod.mtui_add_region(
        str(page_p),
        bbox_xyxy=[45, 145, 95, 180],
        source_text="三",
        translation="第三处",
        inpaint_method="solid_fill",
        re_render=False,
    )
    _, after_add_json = load_page_json(resolve_page_paths(page_p))
    assert not after_add_json.get("mask_inpainted_sha1"), (
        "mtui_add_region(solid_fill) must not claim full-mask sync when bridged Right Lobe still holds raw ink!"
    )

    # 2. R5-F11 (`mtui_edit_mask_and_inpaint` with `solid_fill` adding Third Rect):
    #    repairs BOTH bridged Right Lobe AND Third Rect while keeping Left Lobe's `lama_patch` bit-for-bit intact!
    Image.fromarray(partial_inpaint).save(paths.inpainted_path)
    save_page_json_atomic(
        resolve_page_paths(page_p),
        str(page_p.resolve()),
        {"mask_raw": encode_mask_base64(bridged_mask), "regions": []},
        auto_initial_snapshot=False,
    )
    server_mod.mtui_edit_mask_and_inpaint(
        str(page_p),
        add_rects=[[45, 145, 95, 180]],
        inpaint_method="solid_fill",
        full_reinpaint=False,
        re_render=False,
    )
    after_srv = np.asarray(Image.open(paths.inpainted_path).convert("RGB"), dtype=np.uint8)
    assert np.array_equal(after_srv[20:70, 20:70], lama_patch), "Left Lobe neural LaMa pixels must stay untouched!"
    assert float(np.mean(after_srv[30:60, 145:180])) > 245.0, "Bridged Right Lobe raw ink must be repaired!"
    assert float(np.mean(after_srv[150:175, 50:90])) > 245.0, "Newly added Third Rect must be repaired!"

    # 3. R5-F22 (`engine_worker.incremental_neural_inpaint` lossless bridged component + third added rect):
    Image.fromarray(partial_inpaint).save(paths.inpainted_path)

    class FakeInpainter(str, Enum):
        lama_large = "lama_large"
        blur = "blur"

    class FakePrecision(str, Enum):
        fp32 = "fp32"

    class FakeInpainterConfig:
        def __init__(self) -> None:
            self.inpainting_precision = FakePrecision.fp32
            self.force_use_torch_inpainting = False
            self.blur_radius = 0

    async def _fake_worker_dispatch(**kwargs: Any) -> np.ndarray:
        out_crop = kwargs["image"].copy()
        out_crop[kwargs["mask"] > 0] = 251
        return out_crop

    saved_worker_imgs: list[np.ndarray] = []
    with pytest.MonkeyPatch.context() as mp_ew:
        mp_ew.setitem(
            sys.modules,
            "torch",
            types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)),
        )
        mp_ew.setitem(sys.modules, "manga_translator", types.ModuleType("manga_translator"))
        mp_ew.setitem(
            sys.modules,
            "manga_translator.config",
            types.SimpleNamespace(
                Inpainter=FakeInpainter,
                InpainterConfig=FakeInpainterConfig,
                InpaintPrecision=FakePrecision,
            ),
        )
        mp_ew.setitem(
            sys.modules,
            "manga_translator.inpainting",
            types.SimpleNamespace(dispatch=_fake_worker_dispatch),
        )
        rt = engine_worker.EngineRuntime(str(tmp_path))
        rt._initialized = True
        mp_ew.setattr(
            rt, "_get_config_dict", lambda: {"inpainter": {"inpainter": "lama_large"}, "cli": {"use_gpu": False}}
        )
        rt._export_service = types.SimpleNamespace(
            save_inpainted_image=lambda _p, img, _cfg: (
                saved_worker_imgs.append(img.copy()) or str(paths.inpainted_path)
            )
        )
        third_add = np.zeros((220, 240), dtype=np.uint8)
        third_add[145:180, 45:95] = 255
        cur_m = np.where((bridged_mask > 0) | (third_add > 0), 255, 0).astype(np.uint8)
        ew_res = rt.incremental_neural_inpaint(
            {
                "display_base_path": str(paths.display_base_path),
                "inpainted_path": str(paths.inpainted_path),
                "current_mask_b64": encode_mask_base64(cur_m),
                "added_mask_b64": encode_mask_base64(third_add),
                "prev_mask_b64": encode_mask_base64(bridged_mask),
                "prev_mask_sync_state": "untracked",
            }
        )
    assert ew_res["status"] == "ok"
    assert len(saved_worker_imgs) == 1
    assert np.array_equal(saved_worker_imgs[0][20:70, 20:70], lama_patch), (
        "Worker must preserve Left Lobe neural pixels bit-for-bit!"
    )
    assert float(np.mean(saved_worker_imgs[0][30:60, 145:180])) >= 250.0, (
        "Worker must repair bridged Right Lobe residual ink!"
    )
    assert float(np.mean(saved_worker_imgs[0][150:175, 50:90])) >= 250.0

    # 4. Contiguous zero-margin half-repaired rectangle `[30:60, 30:130]` (`[30:80]` cleaned, `[80:130]` raw ink):
    half_base = np.full((120, 180, 3), 255, dtype=np.uint8)
    half_base[30:60, 30:130] = (10, 10, 10)
    half_prev = half_base.copy()
    half_prev[30:60, 30:80] = (255, 255, 255)
    half_mask = np.zeros((120, 180), dtype=np.uint8)
    half_mask[30:60, 30:130] = 255
    half_unrep, half_any = server_mod._unrepaired_prev_mask_components(half_base, half_prev, half_mask)
    assert half_any is True
    assert int(np.sum(half_unrep[30:60, 30:80] > 0)) == 0, "Repaired left half must stay 0"
    assert np.all(half_unrep[30:60, 80:130] == 255), "Un-repaired right half must be 255 right from x=80"


def test_r5_f17_f35_vectorized_unrepaired_components_scales_to_1000_components() -> None:
    """Verify single-pass `np.bincount` + LUT classifies 1,000 disjoint components in `O(H * W)` (`R5-F17`, `R5-F35`)."""
    h, w = 1000, 1200
    base_rgb = np.full((h, w, 3), 250, dtype=np.uint8)
    prev_inpaint = base_rgb.copy()
    prev_mask = np.zeros((h, w), dtype=np.uint8)

    comp_idx = 0
    repaired_boxes: list[tuple[int, int, int, int]] = []
    unrepaired_boxes: list[tuple[int, int, int, int]] = []
    for row in range(25):
        for col in range(40):
            y1 = 15 + row * 38
            x1 = 15 + col * 29
            y2, x2 = y1 + 8, x1 + 8
            prev_mask[y1:y2, x1:x2] = 255
            base_rgb[y1:y2, x1:x2] = (15, 15, 15)
            if comp_idx % 2 == 0:
                prev_inpaint[y1:y2, x1:x2] = (250, 250, 250)
                repaired_boxes.append((y1, y2, x1, x2))
            else:
                prev_inpaint[y1:y2, x1:x2] = (15, 15, 15)
                unrepaired_boxes.append((y1, y2, x1, x2))
            comp_idx += 1

    assert len(repaired_boxes) == 500 and len(unrepaired_boxes) == 500
    t0 = time.perf_counter()
    unrepaired, any_inpainted = server_mod._unrepaired_prev_mask_components(base_rgb, prev_inpaint, prev_mask)
    elapsed = time.perf_counter() - t0

    assert any_inpainted is True
    assert elapsed < 1.5, f"Vectorized 1000-component scan took too long ({elapsed:.3f}s)"
    for y1, y2, x1, x2 in repaired_boxes:
        assert int(np.sum(unrepaired[y1:y2, x1:x2] > 0)) == 0
    for y1, y2, x1, x2 in unrepaired_boxes:
        assert np.all(unrepaired[y1:y2, x1:x2] == 255)


def test_r5_f16_f19_f20_worker_client_thread_start_rollback_and_pipe_reader_unregister(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify config-switch `Thread.start()` rollback (`R5-F16`), post-`Popen` reader failure cleanup (`R5-F19`), and reader-exit unregistration (`R5-F20`)."""
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()

    settings = get_runtime_settings()
    monkeypatch.setattr(type(settings), "is_engine_available", property(lambda _s: True))
    monkeypatch.setattr(settings, "mtui_root", root_b)
    monkeypatch.setattr(settings, "mtui_python", Path(sys.executable))

    # 1. R5-F16: `_terminate_detached_proc` Thread.start() failure restores old worker ownership & clears `_shutting_down_procs`
    class SwitchableOldProc:
        pid = 91001
        stdout = None
        stderr = None
        stdin = None

        def __init__(self) -> None:
            self.returncode: int | None = None
            self.killed = False

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def wait(self, timeout: float | None = None) -> int:
            if self.returncode is None:
                raise subprocess.TimeoutExpired(cmd=["old"], timeout=0.01)
            return self.returncode

    client_f16 = MtuiWorkerClient()
    old_a = SwitchableOldProc()
    client_f16._proc = old_a  # type: ignore[assignment]
    client_f16._port = 45111
    client_f16._token = "tok_a"
    client_f16._bound_root = root_a
    client_f16._bound_python = Path(sys.executable)

    real_thread_start = threading.Thread.start

    def _fail_term_thread_start(self_t: threading.Thread) -> None:
        if getattr(self_t, "_target", None) == client_f16._terminate_detached_proc:
            raise RuntimeError("simulated_term_thread_exhausted")
        real_thread_start(self_t)

    with pytest.MonkeyPatch.context() as mp_t:
        mp_t.setattr(threading.Thread, "start", _fail_term_thread_start)
        with pytest.raises(RuntimeError, match="simulated_term_thread_exhausted"):
            client_f16._ensure_started_instance(startup_timeout_sec=1.0)

    assert client_f16._proc is old_a
    assert client_f16._port == 45111
    assert client_f16._token == "tok_a"
    assert client_f16._bound_root == root_a
    assert client_f16._bound_python == Path(sys.executable)
    assert old_a not in client_f16._shutting_down_procs

    # Once thread resource recovers, `shutdown()` still owns `old_a` and terminates it cleanly!
    client_f16.shutdown()
    assert old_a.killed is True
    assert client_f16._proc is None
    assert client_f16._shutting_down_procs == set()

    # 2. R5-F19: Post-`Popen` reader `Thread.start()` failure cleans up spawned `proc` without `join()` on unstarted thread
    spawned_f19: list[Any] = []

    class ClosableStringPipe(io.StringIO):
        pass

    class SpawnedProcF19:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            self.pid = 92000 + len(spawned_f19)
            self.stdout = ClosableStringPipe('MTUI_WORKER_READY {"port": 47111}\n')
            self.stderr = ClosableStringPipe("warn line\n")
            self.stdin = None
            self.returncode: int | None = None
            self.killed = False
            spawned_f19.append(self)

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = -9 if self.killed else 0
            return self.returncode

    client_f19 = MtuiWorkerClient()
    start_attempt = {"count": 0}

    def _fail_first_then_second_reader_start(self_t: threading.Thread) -> None:
        start_attempt["count"] += 1
        if start_attempt["count"] == 1:
            raise RuntimeError("stderr_reader_start_failed")
        if start_attempt["count"] == 3:
            raise RuntimeError("stdout_reader_start_failed")
        real_thread_start(self_t)

    with pytest.MonkeyPatch.context() as mp_f19:
        mp_f19.setattr("mtui_mcp.worker.client.subprocess.Popen", SpawnedProcF19)
        mp_f19.setattr(threading.Thread, "start", _fail_first_then_second_reader_start)

        # Attempt 1: `t_stderr.start()` fails immediately after `Popen`
        with pytest.raises(RuntimeError, match="stderr_reader_start_failed"):
            client_f19._ensure_started_instance(startup_timeout_sec=1.0)
        assert len(spawned_f19) == 1
        assert spawned_f19[0].killed is True
        assert spawned_f19[0].stdout.closed and spawned_f19[0].stderr.closed
        assert client_f19._proc is None
        assert client_f19._proc_pipe_readers == {}

        # Attempt 2: `t_stderr.start()` succeeds (`count == 2`), then `t_stdout.start()` fails (`count == 3`)
        with pytest.raises(RuntimeError, match="stdout_reader_start_failed"):
            client_f19._ensure_started_instance(startup_timeout_sec=1.0)
        assert len(spawned_f19) == 2
        assert spawned_f19[1].killed is True
        assert spawned_f19[1].stdout.closed and spawned_f19[1].stderr.closed
        assert client_f19._proc is None
        assert client_f19._proc_pipe_readers == {}

    # 3. R5-F20: Exited worker whose pipe reader finishes AFTER `_cleanup_proc_pipes` timeout self-unregisters from `_proc_pipe_readers`
    r_out, w_out = os.pipe()
    r_err, w_err = os.pipe()
    try:
        out_stream = io.TextIOWrapper(io.BufferedReader(io.FileIO(r_out, "rb")), encoding="utf-8")
        r_out = -1
        err_stream = io.TextIOWrapper(io.BufferedReader(io.FileIO(r_err, "rb")), encoding="utf-8")
        r_err = -1
        os.write(w_out, b'MTUI_WORKER_READY {"port": 47222}\n')
        os.close(w_out)
        w_out = -1

        class DelayedStderrEofProc:
            pid = 93001
            stdin = None

            def __init__(self, *_a: Any, **_kw: Any) -> None:
                self.stdout = out_stream
                self.stderr = err_stream
                self.returncode: int | None = None

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:
                self.returncode = -9

            def wait(self, timeout: float | None = None) -> int:
                self.returncode = -9 if self.returncode is not None else 0
                return self.returncode

        client_f20 = MtuiWorkerClient()
        created_proc_holder: list[DelayedStderrEofProc] = []

        def _spawn_f20(*a: Any, **kw: Any) -> DelayedStderrEofProc:
            p = DelayedStderrEofProc(*a, **kw)
            created_proc_holder.append(p)
            return p

        with pytest.MonkeyPatch.context() as mp_f20:
            mp_f20.setattr("mtui_mcp.worker.client.subprocess.Popen", _spawn_f20)
            mp_f20.setattr("mtui_mcp.worker.client._LOOPBACK_OPENER.open", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("down")))
            _, port_f20, _ = client_f20._ensure_started_instance(startup_timeout_sec=2.0)
            assert port_f20 == 47222
            proc_f20 = created_proc_holder[0]
            assert proc_f20 in client_f20._proc_pipe_readers

            # Shutdown kills `proc_f20` (`poll() == -9`), removing it from `_shutting_down_procs`, while `w_err` is still open
            # so `t_stderr` outlives `_cleanup_proc_pipes`'s 50ms join window!
            client_f20.shutdown()
            assert proc_f20 not in client_f20._shutting_down_procs
            assert proc_f20 in client_f20._proc_pipe_readers

            # Now close `w_err`: `t_stderr` hits EOF and MUST self-unregister `proc_f20` without any further client call!
            os.close(w_err)
            w_err = -1
            deadline_u = time.monotonic() + 1.5
            while time.monotonic() < deadline_u and proc_f20 in client_f20._proc_pipe_readers:
                time.sleep(0.01)
            assert proc_f20 not in client_f20._proc_pipe_readers
            assert err_stream.closed
    finally:
        for fd in (r_out, w_out, r_err, w_err):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass


def test_r5_f36_f37_add_region_none_skips_corrupt_inpaint_and_batch_recovery_checks_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify `mtui_add_region(inpaint_method='none')` ignores unreadable `inpainted_path` (`R5-F36`) and batch recovery checks `_is_render_result_incomplete` (`R5-F37`)."""
    # 1. R5-F36: `mtui_add_region(..., inpaint_method="none", re_render=False)` does NOT decode `pre_paths.inpainted_path`
    #    on either untracked or synced pages, while still invalidating `mask_inpainted_sha1` when mask changes!
    ch_f36 = tmp_path / "ch_r5_f36"
    ch_f36.mkdir()
    page_p = ch_f36 / "001.png"
    base_rgb = np.full((160, 160, 3), 255, dtype=np.uint8)
    base_rgb[30:55, 30:65] = (10, 10, 10)
    base_rgb[95:125, 90:130] = (12, 12, 12)
    Image.fromarray(base_rgb).save(page_p)

    paths = resolve_page_paths(page_p)
    paths.inpainted_path.parent.mkdir(parents=True, exist_ok=True)
    # Write corrupt non-image bytes to `inpainted_path`!
    paths.inpainted_path.write_bytes(b"NOT_A_VALID_IMAGE_HEADER")

    init_mask = np.zeros((160, 160), dtype=np.uint8)
    init_mask[25:60, 25:70] = 255

    # Sub-case 1a: Untracked page (`mask_inpainted_sha1` absent) + corrupt `inpainted_path`
    save_page_json_atomic(
        paths,
        str(page_p.resolve()),
        {"mask_raw": encode_mask_base64(init_mask), "regions": []},
        auto_initial_snapshot=False,
    )
    res_untracked = json.loads(
        server_mod.mtui_add_region(
            str(page_p),
            bbox_xyxy=[85, 90, 135, 130],
            source_text="新枠",
            translation="新框",
            auto_mask_ink=True,
            inpaint_method="none",
            re_render=False,
        )
    )
    assert res_untracked["added_region"]["translation"] == "新框"

    # Sub-case 1b: Synced page (`mask_inpainted_sha1` + `mask_inpainted_stat` matching corrupt file) -> invalidates `mask_inpainted_sha1`!
    base_rgb[43:50, 106:124] = (8, 8, 8)
    Image.fromarray(base_rgb).save(page_p)
    cur_stat = server_mod._file_stat_sig(paths.inpainted_path)
    save_page_json_atomic(
        resolve_page_paths(page_p),
        str(page_p.resolve()),
        {
            "mask_raw": encode_mask_base64(init_mask),
            "mask_inpainted_sha1": server_mod._mask_sha1_hex(init_mask, (160, 160)),
            "mask_inpainted_stat": cur_stat,
            "regions": [],
        },
        auto_initial_snapshot=False,
    )
    res_synced = json.loads(
        server_mod.mtui_add_region(
            str(page_p),
            bbox_xyxy=[95, 35, 135, 60],
            source_text="二枠",
            translation="二框",
            auto_mask_ink=True,
            inpaint_method="none",
            re_render=False,
        )
    )
    assert res_synced["added_region"]["translation"] == "二框"
    _, post_none_json = load_page_json(resolve_page_paths(page_p))
    assert post_none_json["mask_inpainted_sha1"] == "", (
        "inpaint_method='none' must still invalidate mask_inpainted_sha1 when mask changes!"
    )

    # 2. R5-F37: `mtui_batch_edit_chapter` recovery re-render routes incomplete render dicts (`status='partial'` / `metadata_saved=False`)
    #    into `render_errors` instead of `rendered_outputs`!
    ch_f37 = tmp_path / "ch_r5_f37"
    ch_f37.mkdir()
    p1 = _make_sample_page(ch_f37, "001.png", size_hw=(100, 100))
    p2 = _make_sample_page(ch_f37, "002.png", size_hw=(100, 100))
    pp1 = resolve_page_paths(p1)
    pp2 = resolve_page_paths(p2)
    for pp in (pp1, pp2):
        save_page_json_atomic(pp, str(pp.source_path.resolve()), {"regions": []}, auto_initial_snapshot=False)
    pp1 = resolve_page_paths(p1)
    pp2 = resolve_page_paths(p2)

    def _partial_commit_batch(*_a: Any, **_kw: Any) -> Any:
        err = RuntimeError("Batch edit failed on 003.png after committing 001.png and 002.png")
        err.committed_pages = ["001.png", "002.png"]  # type: ignore[attr-defined]
        err.committed_page_paths = [pp1, pp2]  # type: ignore[attr-defined]
        err.failed_page = "003.png"  # type: ignore[attr-defined]
        err.failure_stage = "commit"  # type: ignore[attr-defined]
        raise err

    def _render_with_incomplete_second(pp: Any, **_kw: Any) -> dict[str, Any]:
        if pp.source_path.name == "002.png":
            return {
                "status": "partial",
                "metadata_saved": False,
                "metadata_error": "translation_map: permission denied",
                "output_path": str(pp.rendered_output_path),
            }
        return {"status": "ok", "metadata_saved": True, "output_path": str(pp.rendered_output_path)}

    monkeypatch.setattr(server_mod, "batch_edit_chapter_pages", _partial_commit_batch)
    monkeypatch.setattr(server_mod, "_render_single_page_via_worker", _render_with_incomplete_second)

    with pytest.raises(RuntimeError, match="Batch edit failed on 003.png") as exc_info:
        server_mod.mtui_batch_edit_chapter(str(ch_f37), find_text="A", replace_text="B", re_render=True)

    assert getattr(exc_info.value, "rendered_outputs", None) == [str(pp1.rendered_output_path)]
    r_errs = getattr(exc_info.value, "render_errors", None)
    assert isinstance(r_errs, list) and len(r_errs) == 1
    assert "002.png:" in r_errs[0]
    assert "status='partial'" in r_errs[0]
    assert "metadata_saved=False" in r_errs[0]
    assert "render_errors=" in str(exc_info.value)
