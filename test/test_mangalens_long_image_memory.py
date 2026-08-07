import _bootstrap  # noqa: F401

from types import SimpleNamespace

import numpy as np

import manga_translator.utils.mangalens_detector as mangalens_detector


def _detector_with_outputs(outputs):
    detector = mangalens_detector.MangaLensBubbleDetector.__new__(
        mangalens_detector.MangaLensBubbleDetector
    )
    detector.logger = SimpleNamespace(info=lambda message: None)
    output_iter = iter(outputs)
    detector._infer_single = lambda **kwargs: next(output_iter)
    return detector


def _long_plan(transpose=False):
    return {
        "transpose": transpose,
        "h": 8,
        "w": 6,
        "patch_size": 6,
        "ph_num": 2,
        "rel_step_list": [0.0, 0.25],
    }


def test_long_image_keeps_only_one_full_resolution_mask(monkeypatch):
    boxes_a = np.array([[0, 2, 2, 5], [4, 0, 6, 2]], dtype=np.float32)
    scores_a = np.array([0.9, 0.95], dtype=np.float32)
    classes_a = np.array([0, 1], dtype=np.int32)
    masks_a = np.zeros((2, 6, 6), dtype=np.uint8)
    masks_a[0, 2:5, 0:2] = 255
    masks_a[1, 0:2, 4:6] = 255

    boxes_b = np.array([[0, 0, 3, 3]], dtype=np.float32)
    scores_b = np.array([0.8], dtype=np.float32)
    classes_b = np.array([0], dtype=np.int32)
    masks_b = np.zeros((1, 6, 6), dtype=np.uint8)
    masks_b[0, 0:3, 0:3] = 255

    detector = _detector_with_outputs(
        [
            (boxes_a, scores_a, classes_a, masks_a),
            (boxes_b, scores_b, classes_b, masks_b),
        ]
    )
    image = np.zeros((8, 6, 3), dtype=np.uint8)

    real_zeros = mangalens_detector.np.zeros
    allocated_shapes = []

    def recording_zeros(shape, *args, **kwargs):
        allocated_shapes.append(tuple(shape))
        return real_zeros(shape, *args, **kwargs)

    monkeypatch.setattr(mangalens_detector.np, "zeros", recording_zeros)

    boxes, scores, classes, mask_data, polygons = detector._infer_long_image(
        img=image,
        target_size=1600,
        conf_thres=0.25,
        iou_thres=0.5,
        rearrange_plan=_long_plan(),
        classes=[0],
    )

    assert boxes.tolist() == [[0.0, 2.0, 2.0, 5.0]]
    np.testing.assert_allclose(scores, [0.9])
    assert classes.tolist() == [0]
    assert mask_data is not None
    assert mask_data.shape == (1, 8, 6)
    expected = np.zeros((8, 6), dtype=np.uint8)
    expected[2:5, 0:2] = 255
    np.testing.assert_array_equal(mask_data[0], expected)
    assert len(polygons) == 1
    assert (8, 6) in allocated_shapes
    assert not any(len(shape) == 3 for shape in allocated_shapes)


def test_long_image_transpose_restores_boxes_and_mask_coordinates():
    boxes = np.array([[1, 2, 3, 5]], dtype=np.float32)
    scores = np.array([0.9], dtype=np.float32)
    classes = np.array([0], dtype=np.int32)
    masks = np.zeros((1, 6, 4), dtype=np.uint8)
    masks[0, 2:5, 1:3] = 255
    detector = _detector_with_outputs([(boxes, scores, classes, masks)])
    plan = _long_plan(transpose=True)
    plan["w"] = 4
    plan["ph_num"] = 1
    plan["rel_step_list"] = [0.0]

    restored_boxes, _, _, mask_data, _ = detector._infer_long_image(
        img=np.zeros((4, 8, 3), dtype=np.uint8),
        target_size=1600,
        conf_thres=0.25,
        iou_thres=0.5,
        rearrange_plan=plan,
    )

    assert restored_boxes.tolist() == [[2.0, 1.0, 5.0, 3.0]]
    assert mask_data is not None
    expected = np.zeros((4, 8), dtype=np.uint8)
    expected[1:3, 2:5] = 255
    np.testing.assert_array_equal(mask_data[0], expected)


def test_detect_long_image_union_mask_remains_compatible_with_mask_builder(monkeypatch):
    boxes = np.array([[1, 1, 4, 4]], dtype=np.float32)
    scores = np.array([0.9], dtype=np.float32)
    classes = np.array([0], dtype=np.int32)
    masks = np.zeros((1, 6, 6), dtype=np.uint8)
    masks[0, 1:4, 1:4] = 255
    detector = _detector_with_outputs([(boxes, scores, classes, masks)])
    detector.default_device = "cpu"
    detector.default_imgsz = 1600
    detector.default_conf = 0.25
    detector.default_iou = 0.7
    detector._device_logged = True
    detector.load_model = lambda device=None: None

    plan = _long_plan()
    plan["ph_num"] = 1
    plan["rel_step_list"] = [0.0]
    monkeypatch.setattr(mangalens_detector, "build_det_rearrange_plan", lambda *args, **kwargs: plan)

    result = detector.detect(np.zeros((8, 6, 3), dtype=np.uint8), classes=[0])
    mask_data = result.raw_result.masks.data

    assert len(result.detections) == 1
    assert mask_data.shape == (1, 8, 6)
    expected = np.zeros((8, 6), dtype=np.uint8)
    expected[1:4, 1:4] = 255
    built_mask = mangalens_detector.build_bubble_mask_from_mangalens_result(result, (8, 6))
    np.testing.assert_array_equal(built_mask, expected)
