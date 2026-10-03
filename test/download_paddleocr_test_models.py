"""Explicitly download and verify the released weights used by ONNX tests."""
import _bootstrap  # noqa: F401

import argparse
import ast
import hashlib
import shutil
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    args = parser.parse_args()
    args.model_dir.mkdir(parents=True, exist_ok=True)
    source = ast.parse((_bootstrap.ROOT / 'manga_translator/ocr/model_paddleocr.py').read_text(encoding='utf-8'))
    cls = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'ModelPaddleOCR')
    constants = {node.targets[0].id: ast.literal_eval(node.value)
                 for node in cls.body if isinstance(node, ast.Assign)
                 and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in {'_MODEL_MAPPING', '_MODELS'}}
    mapping = constants['_MODEL_MAPPING']
    files = [(f'{language}_onnx', model['onnx']) for language, model in constants['_MODELS'].items()]
    files.append(('latin_dict', constants['_MODELS']['latin']['dict']))
    for key, filename in files:
        target = args.model_dir / filename
        expected = mapping[key]['hash']
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            continue
        temporary = target.with_suffix(target.suffix + '.part')
        for url in mapping[key]['url']:
            try:
                with urllib.request.urlopen(url, timeout=120) as response, temporary.open('wb') as output:
                    shutil.copyfileobj(response, output)
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
                    raise ValueError(f'Checksum mismatch: {filename}')
                temporary.replace(target)
                break
            except (OSError, ValueError):
                temporary.unlink(missing_ok=True)
                if url == mapping[key]['url'][-1]:
                    raise
        print(f'Verified {filename}')


if __name__ == '__main__':
    main()
