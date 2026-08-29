import _bootstrap  # noqa: F401

"""导出内存直通载荷回归测试。"""

import os
import tempfile
from pathlib import Path

import numpy as np
import pytest
from editor.document_state import ExportBase
from editor.inpaint_state import InpaintKey
from PIL import Image


def _make_regions():
    return [
        {
            "text": "hello",
            "translation": "TEST",
            "lines": [[[40.0, 40.0], [200.0, 40.0], [200.0, 120.0], [40.0, 120.0]]],
            "font_size": 24,
            "direction": "horizontal",
            "alignment": "center",
            "font_color": "#112233",
            "target_lang": "CHS",
            "angle": 0,
        }
    ]


def _make_mask(h=240, w=320):
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[40:120, 40:200] = 255
    return mask


def _make_config_dict():
    return {
        "render": {"renderer": "default", "direction": "auto"},
        "translator": {"translator": "none", "target_lang": "CHS"},
        "cli": {"use_gpu": False, "format": "png"},
        "upscale": {},
        "colorizer": {},
        "inpainter": {},
    }


def _paired_export_base(base, mask, inpainted):
    return ExportBase(
        "paired",
        base,
        inpainted,
        mask,
        InpaintKey(1, 1),
    )


def test_payload_parsing_matches_file_parsing():
    """载荷直通解析与临时 JSON 落盘再读回，得到等价的 regions 与 mask。"""
    from services.export_service import ExportService

    from manga_translator.config import Config, TranslatorConfig
    from manga_translator.manga_translator import MangaTranslator

    service = ExportService()
    regions = _make_regions()
    mask = _make_mask()
    cfg = Config(translator=TranslatorConfig(translator="none"))
    translator = MangaTranslator(params={"load_text": True, "translator": "none"})

    with tempfile.TemporaryDirectory() as temp_dir:
        # 旧路径：写文件再解析
        image_path = os.path.join(temp_dir, "page.png")
        json_path = os.path.join(temp_dir, "page_translations.json")
        Image.new("RGB", (320, 240), "white").save(image_path)
        service._save_regions_data(
            [dict(r) for r in regions], json_path, mask, _make_config_dict()
        )
        file_parsed = translator._load_text_and_regions_from_file(image_path, cfg)

        # 新路径：内存载荷
        base = Image.open(image_path)
        payload = service._build_load_text_payload(
            [dict(r) for r in regions],
            _paired_export_base(
                base, mask, np.full((240, 320, 3), 200, dtype=np.uint8)
            ),
            _make_config_dict(),
            base_size=base.size,
        )
        base.close()
        translator.set_preloaded_load_text_payload(image_path, payload)
        payload_parsed = translator._load_text_and_regions_from_file(image_path, cfg)
        translator.set_preloaded_load_text_payload(image_path, None)

    f_regions, f_mask, f_refined, f_skip_scale, f_skip_repl, f_failures = file_parsed
    p_regions, p_mask, p_refined, p_skip_scale, p_skip_repl, p_failures = payload_parsed

    assert f_failures == 0 and p_failures == 0
    assert len(f_regions) == len(p_regions) == 1
    fr, pr = f_regions[0], p_regions[0]
    assert np.allclose(fr.lines, pr.lines)
    assert fr.translation == pr.translation == "TEST"
    assert fr.font_size == pr.font_size
    assert tuple(fr.fg_colors) == tuple(pr.fg_colors)
    assert (f_refined, f_skip_scale, f_skip_repl) == (
        p_refined,
        p_skip_scale,
        p_skip_repl,
    )
    assert f_mask is not None and p_mask is not None
    assert np.array_equal(f_mask, p_mask)
    print("PASS: payload parsing matches file parsing")


def _run_export(regions, mask, inpainted, tmp, output_name):
    from editor.controller_export_service import ExportJob
    from services.export_service import ExportService

    service = ExportService()
    source_path = os.path.join(tmp, "source.png")
    base = Image.new("RGB", (320, 240), "white")
    base.save(source_path)
    output_path = os.path.join(tmp, "out", output_name)
    export_base = (
        ExportBase("source", base, base, None, None)
        if mask is None
        else _paired_export_base(base, mask, inpainted)
    )
    job = ExportJob(
        automatic=False,
        source_path=source_path,
        output_path=output_path,
        export_base=export_base,
        regions=regions,
        config=_make_config_dict(),
    )
    base.close()
    outcome = service.execute_export_job(job)
    job.release_resources()
    return source_path, output_path, outcome


def test_export_end_to_end_inmemory():
    """带区域+蒙版+编辑器修复图：导出成功、复用修复图、无工作目录副作用。"""
    with tempfile.TemporaryDirectory() as tmp:
        inpainted = np.full((240, 320, 3), 200, dtype=np.uint8)
        _source_path, output_path, outcome = _run_export(
            _make_regions(), _make_mask(), inpainted, tmp, "out.png"
        )
        assert outcome.error is None, f"export failed: {outcome.error}"
        assert os.path.exists(output_path)
        with Image.open(output_path) as out_img:
            assert out_img.size == (320, 240)
            out_rgb = np.asarray(out_img.convert("RGB"))
        # 底图来自编辑器修复图（200 灰）而非原图（白）：取蒙版外一角验证
        assert abs(int(out_rgb[5, 5, 0]) - 200) <= 2, f"corner={out_rgb[5, 5]}"
        # 不回写工作目录（修复图未重新生成、JSON 回写被禁止）
        work_dir = os.path.join(tmp, "manga_translator_work")
        assert not os.path.exists(work_dir), "unexpected work dir writes"
    print("PASS: end-to-end in-memory export")


def test_export_no_regions_no_mask_returns_original():
    """无区域无蒙版：导出原图，不触发 closed-image 崩溃（2026-05 回归）。"""
    with tempfile.TemporaryDirectory() as tmp:
        source_path, output_path, outcome = _run_export([], None, None, tmp, "out.png")
        assert outcome.error is None, f"export failed: {outcome.error}"
        assert os.path.exists(output_path)
        with Image.open(output_path) as out_img, Image.open(source_path) as src_img:
            out_rgb = np.asarray(out_img.convert("RGB"))
            src_rgb = np.asarray(src_img.convert("RGB"))
        assert np.array_equal(out_rgb, src_rgb), "output should equal original image"
    print("PASS: no-regions export returns original")


def test_project_json_marks_replacements_done():
    """编辑器工程 JSON 应带 skip_text_replacements=True（译文已是替换后终稿）。"""
    import json as jsonlib

    from services.export_service import ExportService

    service = ExportService()
    with tempfile.TemporaryDirectory() as tmp:
        img_path = os.path.join(tmp, "src.png")
        Image.new("RGB", (320, 240), "white").save(img_path)
        json_path = service.save_editor_project(
            img_path,
            [dict(r) for r in _make_regions()],
            _make_mask(),
            _make_config_dict(),
        )
        with open(json_path, "r", encoding="utf-8") as f:
            data = jsonlib.load(f)
        image_data = data[os.path.abspath(img_path)]
        assert image_data.get("skip_text_replacements") is True
        assert not list(Path(json_path).parent.glob(".*translations.json.*.tmp"))
    print("PASS: project json marks replacements done")


def test_project_json_omits_redundant_plain_rich_document():
    """仅承载 BR 的无样式文档不写盘，带样式文档仍保留。"""
    from services.export_service import ExportService

    service = ExportService()
    plain_region = _make_regions()[0]
    plain_region["translation"] = "A[BR]B"
    plain_region["translation_rich"] = {
        "format": "richtext.v1",
        "blocks": [
            {
                "type": "paragraph",
                "inlines": [{"type": "text", "text": "A", "style": {}}],
            },
            {
                "type": "paragraph",
                "inlines": [{"type": "text", "text": "B", "style": {}}],
            },
        ],
    }
    styled_region = dict(plain_region)
    styled_region["translation_rich"] = {
        "format": "richtext.v1",
        "blocks": [
            {
                "type": "paragraph",
                "inlines": [
                    {"type": "text", "text": "A", "style": {"bold": True}},
                    {"type": "text", "text": "B", "style": {}},
                ],
            }
        ],
    }

    plain_saved = service._normalize_regions_for_backend([plain_region])[0]
    styled_saved = service._normalize_regions_for_backend([styled_region])[0]

    assert "translation_rich" not in plain_saved
    assert "translation_rich" in styled_saved


def test_project_json_write_is_atomic(monkeypatch):
    """A serialization failure must preserve the previous complete JSON."""
    from services import export_service as export_service_module
    from services.export_service import ExportService

    service = ExportService()
    with tempfile.TemporaryDirectory() as tmp:
        img_path = os.path.join(tmp, "src.png")
        json_path = os.path.join(tmp, "src_translations.json")
        Image.new("RGB", (32, 24), "white").save(img_path)
        original = '{"sentinel": true}\n'
        Path(json_path).write_text(original, encoding="utf-8")

        def fail_dump(*args, **kwargs):
            raise RuntimeError("serialization failed")

        monkeypatch.setattr(export_service_module.json, "dump", fail_dump)
        with pytest.raises(RuntimeError, match="serialization failed"):
            service.save_editor_project(
                img_path,
                [dict(r) for r in _make_regions()],
                _make_mask(),
                _make_config_dict(),
            )

        assert Path(json_path).read_text(encoding="utf-8") == original
        assert not list(Path(tmp).glob(".*translations.json.*.tmp"))


def test_backend_writeback_marks_replacements_only_after_render():
    """后端回写：渲染过（img_rendered 非 None）才写 skip_text_replacements=True。"""
    import json as jsonlib

    from services.export_service import ExportService

    from manga_translator.config import Config, TranslatorConfig
    from manga_translator.manga_translator import MangaTranslator
    from manga_translator.utils import Context
    from manga_translator.utils.path_manager import find_json_path

    service = ExportService()
    translator = MangaTranslator(params={"load_text": True, "translator": "none"})
    cfg = Config(translator=TranslatorConfig(translator="none"))

    def _writeback(tmp, name, rendered):
        img_path = os.path.join(tmp, name)
        Image.new("RGB", (320, 240), "white").save(img_path)
        base = Image.open(img_path)
        mask = _make_mask()
        payload = service._build_load_text_payload(
            [dict(r) for r in _make_regions()],
            _paired_export_base(
                base,
                mask,
                np.full((240, 320, 3), 200, dtype=np.uint8),
            ),
            _make_config_dict(),
            base_size=base.size,
        )
        base.close()
        translator.set_preloaded_load_text_payload(img_path, payload)
        regions, *_ = translator._load_text_and_regions_from_file(img_path, cfg)
        translator.set_preloaded_load_text_payload(img_path, None)

        ctx = Context()
        ctx.text_regions = regions
        ctx.original_size = (320, 240)
        ctx.input = None
        ctx.mask = None
        ctx.mask_raw = None
        ctx.img_rendered = np.zeros((240, 320, 3), dtype=np.uint8) if rendered else None
        translator._save_text_to_file(img_path, ctx, cfg)
        with open(find_json_path(img_path), "r", encoding="utf-8") as f:
            return next(iter(jsonlib.load(f).values()))

    with tempfile.TemporaryDirectory() as tmp:
        rendered_data = _writeback(tmp, "rendered.png", rendered=True)
        assert rendered_data.get("skip_text_replacements") is True, (
            "rendered writeback should mark done"
        )
    with tempfile.TemporaryDirectory() as tmp:
        raw_data = _writeback(tmp, "raw.png", rendered=False)
        assert "skip_text_replacements" not in raw_data, (
            "non-rendered writeback must stay raw"
        )
    print("PASS: backend writeback marks replacements only after render")


def test_empty_text_regions_do_not_persist_detector_mask():
    """无文字区域时，检测器残留的 raw mask 不应写入工程 JSON。"""
    import json as jsonlib

    from manga_translator.config import Config, TranslatorConfig
    from manga_translator.manga_translator import MangaTranslator
    from manga_translator.utils import Context
    from manga_translator.utils.path_manager import find_json_path

    with tempfile.TemporaryDirectory() as tmp:
        image_path = os.path.join(tmp, "no-text.png")
        Image.new("RGB", (32, 24), "white").save(image_path)
        translator = MangaTranslator(params={"save_text": True})
        cfg = Config(translator=TranslatorConfig(translator="none"))
        ctx = Context()
        ctx.image_name = image_path
        ctx.input = None
        ctx.text_regions = []
        ctx.original_size = (32, 24)
        ctx.mask = None
        ctx.mask_raw = np.full((24, 32), 255, dtype=np.uint8)
        translator._save_text_to_file(image_path, ctx, cfg)

        with open(find_json_path(image_path), "r", encoding="utf-8") as handle:
            image_data = next(iter(jsonlib.load(handle).values()))
        assert image_data["regions"] == []
        assert "mask_raw" not in image_data
        assert "mask_is_refined" not in image_data

def main():
    test_payload_parsing_matches_file_parsing()
    test_export_end_to_end_inmemory()
    test_export_no_regions_no_mask_returns_original()
    test_project_json_marks_replacements_done()
    test_project_json_omits_redundant_plain_rich_document()
    test_backend_writeback_marks_replacements_only_after_render()
    test_empty_text_regions_do_not_persist_detector_mask()
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
