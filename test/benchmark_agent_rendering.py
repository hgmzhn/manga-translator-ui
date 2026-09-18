import _bootstrap  # noqa: F401

import argparse
import asyncio
import copy
import io
import json
import threading
import time
from pathlib import Path

import numpy as np
import psutil
from PIL import Image

from manga_translator.agent.integrations.project import load_project_page
from manga_translator.agent.integrations.rendering import BackendRenderer


def stats(rows, field="elapsed_ms"):
    values = [row[field] for row in rows]
    return {"p50_ms": round(float(np.percentile(values, 50)), 2),
            "p95_ms": round(float(np.percentile(values, 95)), 2),
            "images_per_second": round(1000 / float(np.mean(values)), 2)}


async def benchmark(args):
    page = load_project_page(str(args.source.resolve()), page_id="benchmark-page",
                             work_id="benchmark", chapter_id="chapter", order=0)
    renderer = BackendRenderer()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    peak = [0]
    stop = threading.Event()

    def sample_memory():
        process = psutil.Process()
        while not stop.wait(0.02):
            processes = [process, *process.children(recursive=True)]
            try:
                peak[0] = max(peak[0], sum(p.memory_info().rss for p in processes))
            except psutil.Error:
                pass

    sampler = threading.Thread(target=sample_memory, daemon=True)
    sampler.start()
    try:
        start = time.perf_counter()
        await renderer.warmup()
        startup_ms = (time.perf_counter() - start) * 1000
        first = await renderer.observe(page, max_dimension=args.dimension)
        (output / "before.png").write_bytes(first["image"])
        # Probe for an isolated region on this real page; each probe is a revision.
        target = None
        for index, region in enumerate(page["regions"]):
            if float(region.get("angle", 0) or 0) != 0 or not region.get("translation"):
                continue
            trial = copy.deepcopy(page)
            trial["revision"] += 1
            trial["regions"][index]["font_size"] = max(1, region["font_size"] + 1)
            probe = await renderer.observe(trial, max_dimension=args.dimension)
            page = trial
            if probe["status"] == "fast":
                target = index
                break
        if target is None:
            raise RuntimeError("No isolated fast-path region on this page; inspect fallback reasons")
        full_rows, fast_rows, cached_rows = [], [], []
        initial_size = page["regions"][target]["font_size"]
        for index in range(args.iterations):
            page["revision"] += 1
            page["regions"][target]["font_size"] = initial_size + (index % 2 + 1) * 2
            fast = await renderer.observe(page, max_dimension=args.dimension)
            full = await renderer.observe(page, max_dimension=args.dimension, force_full=True)
            with Image.open(io.BytesIO(fast["image"])) as a, Image.open(io.BytesIO(full["image"])) as b:
                if not np.array_equal(np.asarray(a), np.asarray(b)):
                    raise AssertionError("Incremental/full pixels differ")
            if fast["status"] != "fast":
                raise AssertionError(f"Unexpected fallback: {fast.get('fallback_reason')}")
            fast_rows.append({k: fast[k] for k in ("elapsed_ms", "render_ms", "encode_ms")})
            full_rows.append({k: full[k] for k in ("elapsed_ms", "render_ms", "encode_ms")})
            cached = await renderer.observe(page, max_dimension=args.dimension)
            assert cached["status"] == "cached"
            cached_rows.append({k: cached[k] for k in ("elapsed_ms", "render_ms", "encode_ms")})
        (output / "after.png").write_bytes(fast["image"])
        report = {
            "page_size": [page["width"], page["height"]], "regions": len(page["regions"]),
            "output_max_dimension": args.dimension, "iterations": args.iterations,
            "worker_startup_ms": round(startup_ms, 2), "first_image_ms": round(first["elapsed_ms"], 2),
            "incremental": stats(fast_rows), "full": stats(full_rows), "cached": stats(cached_rows),
            "incremental_render": stats(fast_rows, "render_ms"),
            "full_render": stats(full_rows, "render_ms"),
            "png_encoding": stats(fast_rows, "encode_ms"),
            "peak_host_and_worker_rss_mb": round(peak[0] / 1024 ** 2, 2),
            "pixel_comparisons_passed": args.iterations,
            "rows": {"incremental": fast_rows, "full": full_rows, "cached": cached_rows},
        }
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))
    finally:
        await renderer.close()
        stop.set()
        sampler.join()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=_bootstrap.ROOT / "test/artifacts/agent-rendering")
    parser.add_argument("--dimension", type=int, default=1600)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    asyncio.run(benchmark(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
