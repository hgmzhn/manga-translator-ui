"""Real released-model checks; set PADDLEOCR_TEST_MODEL_DIR to require all weights.

Without the variable, missing models in models/ocr are skipped. No downloads are
performed by tests. An explicit model directory makes missing weights a failure.
"""
import _bootstrap  # noqa: F401

import json
import os
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from test_paddleocr_preprocessing import load_preprocessor


class PaddleOCRInferenceTests(unittest.TestCase):
    def session(self, filename):
        directory = os.environ.get('PADDLEOCR_TEST_MODEL_DIR')
        path = (Path(directory) if directory else _bootstrap.ROOT / 'models/ocr') / filename
        if not path.is_file():
            if directory:
                self.fail(f'Required model missing: {path}')
            self.skipTest(f'Model not installed: {filename}')
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        return ort.InferenceSession(str(path), sess_options=options, providers=['CPUExecutionProvider'])

    def check_model(self, filename):
        ocr = load_preprocessor(_bootstrap.ROOT / 'manga_translator/ocr/model_paddleocr.py')
        ocr.session = self.session(filename)
        # Non-aligned widths and different source heights exercise actual ONNX
        # shape constraints rather than only the preprocessor's output shape.
        rng = np.random.default_rng(0)
        crops = [rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
                 for height, width in [(48, 100), (48, 641), (32, 1067), (48, 350), (48, 1600)]]
        seen = []
        sequence_lengths = []
        for indices in ocr._iter_region_batches(crops):
            batch = ocr._preprocess_batch([crops[i] for i in indices])
            output = ocr.session.run(None, {ocr.session.get_inputs()[0].name: batch})[0]
            self.assertEqual(output.ndim, 3)
            self.assertEqual(output.shape[0], len(indices))
            self.assertGreater(output.shape[1], 0)
            self.assertGreater(output.shape[2], 1)
            self.assertTrue(np.isfinite(output).all())
            sequence_lengths.append(output.shape[1])
            seen.extend(indices)
        self.assertEqual(sorted(seen), list(range(len(crops))))
        if not isinstance(ocr.session.get_inputs()[0].shape[-1], int):
            self.assertGreater(max(sequence_lengths), min(sequence_lengths))

    def test_latin_model(self):
        self.check_model('latin_PP-OCRv5_rec_mobile_infer.onnx')

    def test_korean_model(self):
        self.check_model('korean_PP-OCRv5_rec_mobile_infer.onnx')

    def test_thai_model(self):
        self.check_model('thai_PP-OCRv5_rec_mobile_infer.onnx')

    def test_multilingual_model(self):
        self.check_model('PP-OCRv6_medium_rec.onnx')

    def test_latin_public_dialogue(self):
        ocr = load_preprocessor(_bootstrap.ROOT / 'manga_translator/ocr/model_paddleocr.py')
        ocr.session = self.session('latin_PP-OCRv5_rec_mobile_infer.onnx')
        directory = Path(os.environ.get('PADDLEOCR_TEST_MODEL_DIR', _bootstrap.ROOT / 'models/ocr'))
        chars = (directory / 'ppocrv5_latin_dict.txt').read_text(encoding='utf-8').splitlines()
        if ' ' not in chars:
            chars.append(' ')
        chars = ['<blank>'] + chars
        fixtures = _bootstrap.ROOT / 'doc/examples/paddleocr_long_lines'
        for case in json.loads((fixtures / 'cases.json').read_text(encoding='utf-8')):
            with self.subTest(image=case['image']):
                with Image.open(fixtures / case['image']) as image:
                    rgb = image.convert('RGB')
                    crops = [np.array(rgb.crop(box))[:, :, ::-1].copy() for box in case['crop_boxes']]
                recognized = [None] * len(crops)
                for indices in ocr._iter_region_batches(crops):
                    batch = ocr._preprocess_batch([crops[i] for i in indices])
                    predictions = ocr.session.run(None, {ocr.session.get_inputs()[0].name: batch})[0]
                    for index, prediction in zip(indices, predictions):
                        ids = prediction.argmax(1)
                        mask = np.r_[True, ids[1:] != ids[:-1]] & (ids != 0)
                        recognized[index] = ''.join(chars[i] for i in ids[mask])
                self.assertEqual(recognized, [text.replace('’', "'") for text in case['texts']])


if __name__ == '__main__':
    unittest.main()
