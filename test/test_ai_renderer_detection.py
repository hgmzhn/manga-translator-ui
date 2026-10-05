"""Export real local detection/OCR results for the full-page renderer experiment."""

import _bootstrap  # noqa: F401

import argparse
import asyncio
import json
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

from manga_translator.config import Config
from manga_translator.detection import dispatch as detect
from manga_translator.ocr import dispatch as recognize
from manga_translator.textline_merge import dispatch as merge


def serialize_region(region, index):
    polygons = np.asarray(getattr(region, "pts", getattr(region, "lines", []))).reshape(-1, 4, 2)
    points = polygons.reshape(-1, 2)
    return {"id": index, "source": region.text, "polygons": polygons.tolist(),
            "bbox": [int(np.floor(points[:, 0].min())), int(np.floor(points[:, 1].min())),
                     int(np.ceil(points[:, 0].max())), int(np.ceil(points[:, 1].max()))],
            "prob": float(region.prob), "label": getattr(region, "det_label", None)}


def overlay(source, entries, path):
    output = source.copy()
    draw = ImageDraw.Draw(output)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 21)
    colors = ["#ff2020", "#006cff", "#009c30", "#c000ee"]
    for item in entries:
        color = colors[(item["id"] - 1) % len(colors)]
        for polygon in item["polygons"]:
            draw.line([tuple(p) for p in polygon] + [tuple(polygon[0])], fill=color, width=2)
        x, y = item["bbox"][:2]
        x, y = max(0, min(x, source.width - 45)), max(0, y - 24)
        draw.rectangle((x, y, x + 42, y + 24), fill="white", outline=color)
        draw.text((x + 2, y), str(item["id"]), fill=color, font=font)
    output.save(path)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=_bootstrap.ROOT / "config/config.json")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if (args.output_dir / "regions.json").exists():
        raise RuntimeError("Use a new output directory to preserve previous annotations")
    raw_config = json.loads(args.config.read_text(encoding="utf-8"))
    # Only load local recognition settings; do not export credentials or unrelated config.
    config = Config(detector=raw_config["detector"], ocr=raw_config["ocr"])
    source = Image.open(args.image).convert("RGB")
    pixels = np.asarray(source)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    d = config.detector
    start = time.monotonic()
    print(f"Running local detector={d.detector.value}, YOLO={d.use_yolo_obb}, device={device}", flush=True)
    lines, mask, _ = await detect(
        d.detector, pixels, d.detection_size, d.text_threshold, d.box_threshold,
        d.unclip_ratio, device, False, d.use_yolo_obb, d.yolo_obb_conf,
        d.yolo_obb_overlap_threshold, d.min_box_area_ratio,
        det_rearrange_min_effective_short_side=d.det_rearrange_min_effective_short_side,
        use_sfx_filter=d.use_sfx_filter,
        sfx_filter_include_bubble_text=d.sfx_filter_include_bubble_text,
    )
    detected = [serialize_region(line, i + 1) for i, line in enumerate(lines)]
    (args.output_dir / "detection.json").write_text(json.dumps(detected, ensure_ascii=False, indent=2), encoding="utf-8")
    overlay(source, detected, args.output_dir / "detection.png")
    if mask is not None:
        Image.fromarray(np.asarray(mask).astype(np.uint8)).save(args.output_dir / "mask.png")
    other = [line for line in lines if getattr(line, "det_label", "") == "other"]
    forward = [line for line in lines if getattr(line, "det_label", "") != "other"]
    recognized = await recognize(config.ocr.ocr, pixels, forward, config.ocr, device, False, runtime_config=config)
    ocr_data = [serialize_region(line, i + 1) for i, line in enumerate(recognized)]
    (args.output_dir / "ocr.json").write_text(json.dumps(ocr_data, ensure_ascii=False, indent=2), encoding="utf-8")
    regions = await merge(recognized, source.width, source.height, config, verbose=False,
                          model_assisted_other_textlines=other)
    entries = [serialize_region(region, i + 1) for i, region in enumerate(regions)]
    report = {"source_size": source.size, "device": device,
              "detector": d.model_dump(mode="json"), "ocr": config.ocr.model_dump(mode="json"),
              "detected_count": len(detected), "ocr_count": len(ocr_data),
              "elapsed_seconds": round(time.monotonic() - start, 2), "regions": entries}
    (args.output_dir / "regions.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    overlay(source, entries, args.output_dir / "regions.png")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
