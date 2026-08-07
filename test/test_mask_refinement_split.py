import _bootstrap  # noqa: F401

import numpy as np

from manga_translator.mask_refinement import text_mask_utils as tmu


def _line(y0, y1):
    return tmu.Quadrilateral(
        np.asarray([[4, y0], [16, y0], [16, y1], [4, y1]], dtype=np.float32),
        "",
        1.0,
    )


def _horizontal_line(x0, x1):
    return tmu.Quadrilateral(
        np.asarray([[x0, 4], [x1, 4], [x1, 16], [x0, 16]], dtype=np.float32),
        "",
        1.0,
    )


def test_textline_straddling_rearrange_seam_is_assigned_to_both_stripes():
    plan = {
        "h": 100,
        "patch_size": 52,
        "ph_num": 2,
        "rel_step_list": [0.0, 0.48],
    }
    line = _line(40, 60)

    buckets, intervals = tmu._assign_textlines_to_rearrange_stripes([line], plan)

    assert intervals == [(0, 52, 26.0), (48, 100, 74.0)]
    assert buckets[0] == [line]
    assert buckets[1] == [line]


def test_seam_textline_is_reconstructed_from_an_uncut_context_crop(monkeypatch):
    for transpose in (False, True):
        if transpose:
            image = np.zeros((20, 100, 3), dtype=np.uint8)
            mask = np.zeros((20, 100), dtype=np.uint8)
            mask[4:17, 40:61] = 255
            line = _horizontal_line(40, 60)
        else:
            image = np.zeros((100, 20, 3), dtype=np.uint8)
            mask = np.zeros((100, 20), dtype=np.uint8)
            mask[40:61, 4:17] = 255
            line = _line(40, 60)

        split_image = tmu.det_rearrange_split_view(image, transpose)
        plan = {
            "transpose": transpose,
            "h": 100,
            "w": 20,
            "pw_num": 1,
            "patch_size": 52,
            "ph_num": 2,
            "ph_step": 48,
            "rel_step_list": [0.0, 0.48],
            "patch_list": [split_image[0:52].copy(), split_image[48:100].copy()],
            "p_num": 2,
            "pad_num": 0,
        }
        core_calls = []

        def fake_core(_image, local_mask, textlines, *args, **kwargs):
            core_calls.append((len(textlines), kwargs.get("show_progress", True)))
            return local_mask.copy()

        monkeypatch.setattr(tmu, "build_det_rearrange_plan", lambda *_args, **_kwargs: plan)
        monkeypatch.setattr(tmu, "_complete_mask_core", fake_core)

        result = tmu._complete_mask_with_det_rearrange(
            image,
            mask,
            [line],
            keep_threshold=1e-2,
            dilation_offset=0,
            kernel_size=3,
        )

        assert core_calls == [(1, False)]
        np.testing.assert_array_equal(result, mask)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
