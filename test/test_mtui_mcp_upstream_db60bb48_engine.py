"""Unit & adapter regression tests for upstream manga-translator-ui HEAD db60bb48."""

import _bootstrap  # noqa: F401, I001

import json
import os
import sys
import types
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

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
    monkeypatch.delenv("PYTORCH_ALLOC_CONF", raising=False)
    monkeypatch.delenv("QT_IMAGEIO_MAXALLOC", raising=False)
    prev_cwd = os.getcwd()
    prev_sys_path = list(sys.path)
    try:
        engine_worker._setup_environment(str(tmp_path))
        assert os.environ.get("PYTORCH_ALLOC_CONF") == "expandable_segments:True"
        assert os.environ.get("QT_IMAGEIO_MAXALLOC") == "1024"
    finally:
        os.chdir(prev_cwd)
        sys.path[:] = prev_sys_path

    rt = engine_worker.EngineRuntime(str(tmp_path))
    monkeypatch.setattr(rt, "release_vram", lambda: {"status": "ok"})
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="done\n", stderr="")

    monkeypatch.setattr(engine_worker.subprocess, "run", fake_run)
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
