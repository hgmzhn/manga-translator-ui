"""Regression tests without importing model loaders or downloading weights.

Extract the real preprocessing methods from the source AST to avoid unrelated
GPU/model imports. Run with unittest or pytest from the repository root.
"""
import _bootstrap  # noqa: F401

import ast
import math
from types import SimpleNamespace
import unittest
from typing import List

import cv2
import numpy as np


def load_preprocessor(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ModelPaddleOCR')
    methods = [n for n in original.body if isinstance(n, ast.FunctionDef)
               and n.name in {'_preprocess', '_preprocess_batch', '_iter_region_batches'}]
    cls = ast.ClassDef(name='Preprocessor', bases=[], keywords=[], body=methods, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    namespace = {'math': math, 'cv2': cv2, 'np': np, 'List': List}
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace['Preprocessor']()


class PaddleOCRWidthTests(unittest.TestCase):
    def setUp(self):
        self.ocr = load_preprocessor(_bootstrap.ROOT /
                                     'manga_translator/ocr/model_paddleocr.py')

    def batch(self, images, width='dynamic'):
        self.ocr.session = SimpleNamespace(get_inputs=lambda: [SimpleNamespace(shape=[None, 3, 48, width])])
        return self.ocr._preprocess_batch(images)

    def test_long_line_preserves_horizontal_detail(self):
        image = np.zeros((48, 1600, 3), dtype=np.uint8)
        image[:, ::2] = 255
        actual = self.ocr._preprocess(image)
        self.assertEqual(actual.shape, (1, 3, 48, 1600))
        np.testing.assert_array_equal(actual[0], image.transpose(2, 0, 1) / 127.5 - 1)

    def test_short_line_keeps_legacy_padding(self):
        image = np.full((48, 100, 3), 255, dtype=np.uint8)
        actual = self.ocr._preprocess(image)
        self.assertEqual(actual.shape, (1, 3, 48, 320))
        np.testing.assert_array_equal(actual[:, :, :, :100], 1)
        np.testing.assert_array_equal(actual[:, :, :, 100:], 0)

    def test_mixed_batch_pads_short_line_without_stretching(self):
        short = np.full((48, 100, 3), 255, dtype=np.uint8)
        long = np.full((48, 1600, 3), 255, dtype=np.uint8)
        actual = self.batch([short, long])
        self.assertEqual(actual.shape, (2, 3, 48, 1600))
        np.testing.assert_array_equal(actual[0, :, :, :100], 1)
        np.testing.assert_array_equal(actual[0, :, :, 100:], 0)
        np.testing.assert_array_equal(actual[1], 1)

    def test_none_dynamic_dimension_and_nonstandard_height(self):
        actual = self.batch([np.zeros((24, 1000, 3), dtype=np.uint8)], None)
        self.assertEqual(actual.shape, (1, 3, 48, 2000))

    def test_fixed_width_model_retains_required_shape(self):
        image = np.full((48, 1600, 3), 255, dtype=np.uint8)
        actual = self.batch([image], 320)
        self.assertEqual(actual.shape, (1, 3, 48, 320))
        np.testing.assert_array_equal(actual, 1)

    def test_outlier_isolated_and_original_indices_preserved(self):
        regions = [np.zeros((48, 100, 3), dtype=np.uint8) for _ in range(16)]
        regions[7] = np.zeros((48, 9600, 3), dtype=np.uint8)
        self.batch(regions[:1])
        groups = list(self.ocr._iter_region_batches(regions))
        self.assertEqual(groups, [[i for i in range(16) if i != 7], [7]])
        sizes = [self.ocr._preprocess_batch([regions[i] for i in group]).nbytes
                 for group in groups]
        self.assertEqual(max(sizes), 9600 * 3 * 48 * 4)

    def test_dynamic_batches_obey_width_budget_and_ratio(self):
        widths = [900, 321, 2400, 100, 600, 1000, 320, 640, 1100, 3000] * 3
        regions = [np.zeros((48, width, 3), dtype=np.uint8) for width in widths]
        self.batch(regions[:1], None)
        groups = list(self.ocr._iter_region_batches(regions))
        self.assertEqual(sorted(i for group in groups for i in group), list(range(len(regions))))
        for group in groups:
            effective = [max(320, widths[i]) for i in group]
            self.assertLessEqual(len(group), 16)
            self.assertLessEqual(max(effective), 2 * min(effective))
            self.assertTrue(len(group) == 1 or max(effective) * len(group) <= 16 * 320)

    def test_fixed_width_grouping_keeps_order_and_chunk_size(self):
        regions = [np.zeros((48, 100, 3), dtype=np.uint8) for _ in range(33)]
        self.batch(regions[:1], 320)
        self.assertEqual(list(self.ocr._iter_region_batches(regions)),
                         [list(range(16)), list(range(16, 32)), [32]])

    def test_empty_groups(self):
        self.batch([np.zeros((48, 100, 3), dtype=np.uint8)])
        self.assertEqual(list(self.ocr._iter_region_batches([])), [])


if __name__ == '__main__':
    unittest.main()
