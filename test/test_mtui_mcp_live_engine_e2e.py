"""Live end-to-end integration test with manga-translator-ui's engine worker and STDIO MCP wire."""

import _bootstrap  # noqa: F401, I001

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from mtui_mcp.config import get_runtime_settings
from mtui_mcp.path_resolver import resolve_page_paths
from mtui_mcp.project_store import (
    compute_page_revision,
    encode_mask_base64,
    open_rgb_image,
    save_page_json_atomic,
)
from mtui_mcp.server import (
    mtui_edit_mask_and_inpaint,
    mtui_inspect_page_visual,
    mtui_inspect_region_visual,
    mtui_list_fonts,
    mtui_render_pages,
    mtui_style_region_richtext,
    mtui_update_regions,
)
from mtui_mcp.worker.client import get_worker_client


def test_live_mtui_gpu_inpaint_richtext_layout_and_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_runtime_settings()
    if not settings.is_engine_available:
        pytest.skip("manga-translator-ui runtime not available on this machine")

    orig_load_cfg = settings.load_mtui_config_dict

    def _isolated_mtui_config() -> dict:
        cfg = dict(orig_load_cfg() or {})
        app_cfg = dict(cfg.get("app") or {})
        app_cfg["last_output_path"] = ""
        cfg["app"] = app_cfg
        return cfg

    monkeypatch.setattr(settings, "load_mtui_config_dict", _isolated_mtui_config)

    has_lama_weights = bool(
        settings.mtui_root
        and (settings.mtui_root / "models" / "inpainting" / "lama_large_512px.ckpt").is_file()
    )

    chapter_dir = tmp_path / "live_chapter"
    chapter_dir.mkdir(parents=True)
    page_path = chapter_dir / "001.png"
    isolated_result_dir = chapter_dir / "manga_translator_work" / "result"
    isolated_result_dir.mkdir(parents=True, exist_ok=True)

    # Build a crisp 900x700 comic page with two speech bubbles + dark Japanese strokes
    canvas = np.full((900, 700, 3), 215, dtype=np.uint8)
    # Panel frame
    cv2.rectangle(canvas, (30, 30), (670, 870), (20, 20, 20), 4)
    # Right vertical speech bubble (center 510, 250, rx=95, ry=155)
    cv2.ellipse(canvas, (510, 250), (95, 155), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(canvas, (510, 250), (95, 155), 0, 0, 360, (15, 15, 15), 3)
    # Simulated original text strokes + a leftover speck at (560..570, 180..205)
    canvas[150:340, 495:525] = (25, 25, 25)
    canvas[180:205, 560:570] = (30, 30, 30)

    # Left horizontal speech bubble (center 220, 560, rx=140, ry=90)
    cv2.ellipse(canvas, (220, 560), (140, 90), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(canvas, (220, 560), (140, 90), 0, 0, 360, (15, 15, 15), 3)
    canvas[535:580, 130:310] = (25, 25, 25)

    Image.fromarray(canvas).save(page_path)
    paths = resolve_page_paths(page_path)

    mask = np.zeros((900, 700), dtype=np.uint8)
    mask[145:345, 490:530] = 255
    mask[530:585, 125:315] = 255

    inpainted = canvas.copy()
    inpainted[mask > 0] = 255
    paths.inpainted_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(inpainted).save(paths.inpainted_path)

    image_data = {
        "last_export_dir": str(isolated_result_dir.resolve()),
        "last_output_path": str((isolated_result_dir / "001.png").resolve()),
        "mask_raw": encode_mask_base64(mask),
        "mask_is_refined": True,
        "regions": [
            {
                "lines": [[[455.0, 130.0], [565.0, 130.0], [565.0, 370.0], [455.0, 370.0]]],
                "texts": ["これが私の超電磁砲よ！？"],
                "text": "これが私の超電磁砲よ！？",
                "translation": "这就是我的超电磁炮！？",
                "font_size": 28,
                "font_color": "#111111",
                "bg_color": [255, 255, 255],
                "stroke_width": 0.10,
                "direction": "v",
                "alignment": "center",
                "target_lang": "CHS",
            },
            {
                "lines": [[[110.0, 505.0], [330.0, 505.0], [330.0, 615.0], [110.0, 615.0]]],
                "texts": ["絶対に負けないからな！"],
                "text": "絶対に負けないからな！",
                "translation": "绝对不会输给你的！",
                "font_size": 24,
                "font_color": "#1b365d",
                "bg_color": [255, 255, 255],
                "stroke_width": 0.08,
                "direction": "h",
                "alignment": "center",
                "target_lang": "CHS",
            },
        ],
    }
    save_page_json_atomic(paths, str(page_path.resolve()), image_data, auto_initial_snapshot=False)

    try:
        # 1. Check font listing & glyph coverage
        fonts_res = json.loads(mtui_list_fonts(cjk_only=True, check_text_glyphs="超电磁炮绝对不会输"))
        assert fonts_res["count"] >= 1
        chosen_font = next(
            (f["selector"] for f in fonts_res["fonts"] if f.get("all_glyphs_present")),
            fonts_res["fonts"][0]["selector"],
        )

        # 2. Repair the leftover speck at [556, 176, 574, 210] via the warm engine worker
        #    (uses real `lama_large` weights if downloaded locally, or weight-free `blur` in clean CI checkouts)
        inpaint_res = json.loads(
            mtui_edit_mask_and_inpaint(
                page_path=str(page_path),
                add_rects=[[556, 176, 574, 210]],
                inpaint_method="neural",
                neural_inpainter="lama_large" if has_lama_weights else "blur",
                blur_radius=None if has_lama_weights else 45,
                re_render=False,
            )
        )
        assert inpaint_res["inpaint_result"]["status"] == "ok"
        repaired_inp = open_rgb_image(resolve_page_paths(page_path).inpainted_path)
        min_expected_luma = 235.0 if has_lama_weights else 190.0
        assert float(np.mean(repaired_inp[180:205, 560:570])) > min_expected_luma

        # 3. Agent-judged line breaks (`\n`) + font family & size on both regions
        mtui_update_regions(
            page_path=str(page_path),
            updates=[
                {
                    "region_index": 0,
                    "translation": "这就是我的\n超电磁炮！？",
                    "font_family": chosen_font,
                    "font_size": 28,
                },
                {
                    "region_index": 1,
                    "translation": "绝对不会\n输给你的！",
                    "font_family": chosen_font,
                    "font_size": 24,
                },
            ],
            re_render=False,
        )

        style_res = json.loads(
            mtui_style_region_richtext(
                page_path=str(page_path),
                region_index=0,
                spans=[
                    {
                        "match": "超电磁炮",
                        "bold": True,
                        "color": "#d92d20",
                        "ruby": "Railgun",
                    },
                    {
                        "regex": r"[！？]{2}",
                        "tcy": True,
                    },
                ],
                re_render=True,
            )
        )
        assert style_res["render_result"]["status"] == "ok"
        assert style_res["revision"] == compute_page_revision(resolve_page_paths(page_path))

        final_paths = resolve_page_paths(page_path)
        assert final_paths.rendered_output_exists
        assert final_paths.rendered_output_path.resolve().is_relative_to(tmp_path.resolve())
        rendered_rgb = open_rgb_image(final_paths.rendered_output_path)
        # Crop strictly inside each bubble's pure-white interior (excluding ellipse borders near x=443)
        # and verify newly rendered text pixels relative to `repaired_inp`.
        right_pre = repaired_inp[165:335, 455:565]
        right_post = rendered_rgb[165:335, 455:565]
        assert np.min(right_pre) > 180, "Right interior reference crop must exclude bubble border"
        right_darkening = np.max(right_pre.astype(np.int16) - right_post.astype(np.int16), axis=2)
        right_new_dark_px = (right_darkening >= 60) & (np.min(right_post, axis=2) < 150)
        assert int(np.sum(right_new_dark_px)) >= 50, (
            "Expected newly rendered vertical rich text inside right bubble interior"
        )
        # R3-F64: Verify rich-text span color `#d92d20` actually rendered red-tinted glyph pixels (rejecting plain black text)
        right_r = right_post[:, :, 0].astype(np.int16)
        right_g = right_post[:, :, 1].astype(np.int16)
        right_b = right_post[:, :, 2].astype(np.int16)
        right_red_glyph_px = (right_r >= 140) & ((right_r - right_g) >= 60) & ((right_r - right_b) >= 60)
        assert int(np.sum(right_red_glyph_px)) >= 15, (
            "Expected red-tinted rich-text glyph pixels (#d92d20) inside right bubble interior"
        )

        left_pre = repaired_inp[520:600, 130:310]
        left_post = rendered_rgb[520:600, 130:310]
        assert np.min(left_pre) > 180, "Left interior reference crop must exclude bubble border"
        left_darkening = np.max(left_pre.astype(np.int16) - left_post.astype(np.int16), axis=2)
        left_new_dark_px = (left_darkening >= 60) & (np.min(left_post, axis=2) < 150)
        assert int(np.sum(left_new_dark_px)) >= 50, (
            "Expected newly rendered horizontal text inside left bubble interior"
        )

        tmap_file = final_paths.rendered_output_path.parent / "translation_map.json"
        assert tmap_file.is_file()

        vis_page = mtui_inspect_page_visual(str(page_path), mode="side_by_side", return_inline_image=True)
        assert len(vis_page) == 2
        vis_loupe = mtui_inspect_region_visual(str(page_path), region_indices=[0, 1], return_inline_image=True)
        assert len(vis_loupe) == 2

        batch_res = json.loads(mtui_render_pages(str(chapter_dir), output_dir=str(isolated_result_dir)))
        assert batch_res["rendered_count"] == 1
        assert batch_res["status"] == "ok"
        assert batch_res["results"][0]["status"] == "ok"
    finally:
        get_worker_client().shutdown()


@pytest.mark.asyncio
async def test_stdio_mcp_protocol_handshake_and_calls() -> None:
    """Verify `python -m mtui_mcp` over real MCP STDIO JSON-RPC wire."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mtui_mcp"],
        cwd=str(_bootstrap.ROOT),
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init_res = await session.initialize()
            srv_info = getattr(init_res, "server_info", None) or getattr(init_res, "serverInfo")
            assert srv_info.name == "mtui-mcp"

            tools_res = await session.list_tools()
            tool_names = {t.name for t in tools_res.tools}
            assert len(tool_names) == 24
            assert "mtui_qa_audit" in tool_names
            assert "mtui_edit_mask_and_inpaint" in tool_names
            assert "mtui_style_region_richtext" in tool_names
            assert "mtui_inspect_region_visual" in tool_names
            assert "mtui_live_gui_session" in tool_names

            prompts_res = await session.list_prompts()
            prompt_names = {p.name for p in prompts_res.prompts}
            assert "proofread_and_polish_chapter" in prompt_names

            status_call = await session.call_tool("mtui_status", {"warm_up_worker": False})
            assert not getattr(status_call, "is_error", getattr(status_call, "isError", False))
            status_payload = json.loads(status_call.content[0].text)
            assert "engine_available" in status_payload
