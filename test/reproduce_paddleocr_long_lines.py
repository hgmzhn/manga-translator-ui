"""Compare legacy width 320 and dynamic preprocessing on public line crops."""
import _bootstrap  # noqa: F401

import argparse
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from test_paddleocr_preprocessing import load_preprocessor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    args = parser.parse_args()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(
        str(args.model_dir / 'latin_PP-OCRv5_rec_mobile_infer.onnx'),
        sess_options=options, providers=['CPUExecutionProvider'],
    )
    chars = (args.model_dir / 'ppocrv5_latin_dict.txt').read_text(encoding='utf-8').splitlines()
    if ' ' not in chars:
        chars.append(' ')
    chars = ['<blank>'] + chars
    ocr = load_preprocessor(_bootstrap.ROOT / 'manga_translator/ocr/model_paddleocr.py')
    ocr.session = session
    fixtures = _bootstrap.ROOT / 'doc/examples/paddleocr_long_lines'
    results = []
    for case in json.loads((fixtures / 'cases.json').read_text(encoding='utf-8')):
        with Image.open(fixtures / case['image']) as image:
            image = image.convert('RGB')
            crops = [np.array(image.crop(box))[:, :, ::-1].copy() for box in case['crop_boxes']]
        result = {'image': case['image']}
        for mode in ('before', 'after'):
            # Explicit width 320 reproduces the original resizing and padding.
            predictions = [None] * len(crops)
            groups = [list(range(len(crops)))] if mode == 'before' else ocr._iter_region_batches(crops)
            for indices in groups:
                batch = (np.concatenate([ocr._preprocess(crops[i], 320) for i in indices])
                         if mode == 'before' else ocr._preprocess_batch([crops[i] for i in indices]))
                output = session.run(None, {session.get_inputs()[0].name: batch})[0]
                for index, prediction in zip(indices, output):
                    predictions[index] = prediction
            rows = []
            for expected, prediction in zip(case['texts'], predictions):
                indices = prediction.argmax(1)
                mask = np.r_[True, indices[1:] != indices[:-1]] & (indices != 0)
                recognized = ''.join(chars[index] for index in indices[mask])
                rows.append({
                    'expected': expected, 'recognized': recognized,
                    'exact_match': recognized == expected,
                    'confidence': float(prediction.max(1)[mask].mean()) if mask.any() else 0,
                })
            result[mode] = rows
        results.append(result)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
