#!/usr/bin/env python3
"""Profile the manga translator pipeline or the Qt desktop UI.

This is an opt-in diagnostic script.  It is intentionally outside pytest because
the pipeline mode can load large models and may write translated output.

Examples (run from the repository root)::

    # Profile one real local translation.  Arguments after ``pipeline`` are
    # passed to ``python -m manga_translator local``.
    python tools/profile_performance.py pipeline \
        -i path/to/page.png -o /tmp/manga-profile-output \
        --config config/config.json --use-gpu --overwrite

    # Profile the desktop UI for 60 seconds while panning/zooming/editing.
    python tools/profile_performance.py qt --duration 60

Both modes write a JSON summary and a standard ``cProfile`` file.  The output
paths can be overridden with ``--report-output`` and ``--profile-output``.
"""

from __future__ import annotations

import argparse
import cProfile
import datetime as dt
import functools
import json
import pstats
import shlex
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no resource module.
    resource = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_UI_ROOT = ROOT / "desktop_qt_ui"


def _ensure_import_paths() -> None:
    """Make the script behave like the project's normal development entrypoints."""

    for path in (ROOT, DESKTOP_UI_ROOT):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


def _default_output_paths(mode: str) -> tuple[Path, Path]:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = ROOT / "result" / "profiling"
    return (
        output_dir / f"{mode}-{timestamp}.json",
        output_dir / f"{mode}-{timestamp}.prof",
    )


def _peak_rss_mb() -> float | None:
    """Return max resident memory in MB on both macOS and Linux."""

    if resource is None:
        return None

    try:
        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (AttributeError, OSError):
        return None

    # macOS reports bytes; Linux reports KiB.
    divisor = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
    return round(value / divisor, 2)


class StageTimer:
    """Accumulate inclusive wall-clock time for named async pipeline stages."""

    def __init__(self) -> None:
        self._stats: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {
                "calls": 0,
                "failures": 0,
                "total_seconds": 0.0,
                "max_seconds": 0.0,
            }
        )

    def record(self, name: str, elapsed: float, failed: bool = False) -> None:
        stats = self._stats[name]
        stats["calls"] = int(stats["calls"]) + 1
        stats["failures"] = int(stats["failures"]) + int(failed)
        stats["total_seconds"] = float(stats["total_seconds"]) + elapsed
        stats["max_seconds"] = max(float(stats["max_seconds"]), elapsed)

    def as_dict(self) -> dict[str, dict[str, float | int]]:
        result: dict[str, dict[str, float | int]] = {}
        for name, stats in sorted(
            self._stats.items(),
            key=lambda item: float(item[1]["total_seconds"]),
            reverse=True,
        ):
            calls = int(stats["calls"])
            total = float(stats["total_seconds"])
            result[name] = {
                "calls": calls,
                "failures": int(stats["failures"]),
                "total_seconds": round(total, 6),
                "average_seconds": round(total / calls, 6) if calls else 0.0,
                "max_seconds": round(float(stats["max_seconds"]), 6),
            }
        return result


def _wrap_async_stage(
    cls: type[Any],
    method_name: str,
    stage_name: str,
    timer: StageTimer,
) -> None:
    """Wrap one existing async method without changing its behavior."""

    original = getattr(cls, method_name, None)
    if original is None or getattr(original, "__profile_wrapper__", False):
        return

    @functools.wraps(original)
    async def timed(self: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        failed = False
        try:
            return await original(self, *args, **kwargs)
        except BaseException:
            failed = True
            raise
        finally:
            timer.record(stage_name, time.perf_counter() - started, failed)

    timed.__profile_wrapper__ = True  # type: ignore[attr-defined]
    setattr(cls, method_name, timed)


def _install_pipeline_timers(timer: StageTimer) -> None:
    """Instrument the actual MangaTranslator methods used by local mode."""

    # Keep the same PyTorch-before-PyQt import order as the desktop entrypoint.
    # This matters for the Windows DLL loader even though this mode is mainly
    # intended to profile the Python translation pipeline.
    try:
        import torch  # noqa: F401
    except ImportError:
        pass

    from manga_translator import MangaTranslator

    stages = {
        "translate_batch": "translate_batch_total",
        "_run_detection": "detection",
        "_run_ocr": "ocr",
        "_run_textline_merge": "textline_merge",
        "_batch_translate_contexts": "translation",
        "_run_mask_refinement": "mask_refinement",
        "_run_inpainting": "inpainting",
        "_run_text_rendering": "text_rendering",
        "_run_upscaling": "upscaling",
        "_run_colorizer": "colorization",
    }
    for method_name, stage_name in stages.items():
        _wrap_async_stage(MangaTranslator, method_name, stage_name, timer)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_cprofile(profile: cProfile.Profile | None, path: Path, top: int) -> None:
    if profile is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    profile.dump_stats(str(path))
    print(f"Python cProfile: {path}")
    print(f"Top {top} Python functions by cumulative time:")
    stats = pstats.Stats(profile).strip_dirs().sort_stats("cumulative")
    stats.print_stats(top)


def _print_stage_summary(stages: dict[str, dict[str, float | int]]) -> None:
    if not stages:
        print("No instrumented pipeline stages were observed.")
        return

    print("Pipeline stages (inclusive wall-clock time):")
    for name, stats in stages.items():
        print(
            f"  {name:20s} "
            f"total={float(stats['total_seconds']):8.3f}s "
            f"calls={int(stats['calls']):4d} "
            f"avg={float(stats['average_seconds']):7.3f}s "
            f"max={float(stats['max_seconds']):7.3f}s"
        )
    print("Note: with concurrent pipeline mode, inclusive stage times may overlap.")


def run_pipeline(
    local_argv: list[str],
    report_path: Path,
    profile_path: Path,
    top: int,
    enable_cprofile: bool,
) -> int:
    """Run the real local command with stage timers installed."""

    _ensure_import_paths()
    timer = StageTimer()
    _install_pipeline_timers(timer)

    # The project entrypoint reads sys.argv through manga_translator.args.
    sys.argv = ["python -m manga_translator", "local", *local_argv]
    print(f"Profiling command: {shlex.join(sys.argv)}")

    profile = cProfile.Profile() if enable_cprofile else None
    started = time.perf_counter()
    exit_code = 0
    try:
        from manga_translator.__main__ import main

        if profile is None:
            main()
        else:
            profile.runcall(main)
    except SystemExit as exc:
        exit_code = int(exc.code) if isinstance(exc.code, int) else 1
    except BaseException:
        exit_code = 1
        raise
    finally:
        elapsed = time.perf_counter() - started
        stages = timer.as_dict()
        payload = {
            "mode": "pipeline",
            "started_at": dt.datetime.now().astimezone().isoformat(),
            "command": sys.argv,
            "exit_code": exit_code,
            "elapsed_seconds": round(elapsed, 6),
            "peak_rss_mb": _peak_rss_mb(),
            "stages": stages,
            "profile_output": str(profile_path) if profile is not None else None,
        }
        _write_json(report_path, payload)
        _write_cprofile(profile, profile_path, top)
        _print_stage_summary(stages)
        print(f"JSON summary: {report_path}")

    return exit_code


class EventTimer:
    """Collect timing statistics for selected Qt event types and receivers."""

    _EVENT_NAMES = {
        "Paint",
        "MouseMove",
        "MouseButtonPress",
        "MouseButtonRelease",
        "Wheel",
        "Resize",
        "LayoutRequest",
        "UpdateRequest",
    }

    def __init__(self) -> None:
        self._stats: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0}
        )

    def record(self, event_name: str, receiver_name: str, elapsed: float) -> None:
        key = f"{event_name}:{receiver_name}"
        stats = self._stats[key]
        stats["count"] = int(stats["count"]) + 1
        stats["total_seconds"] = float(stats["total_seconds"]) + elapsed
        stats["max_seconds"] = max(float(stats["max_seconds"]), elapsed)

    def should_record(self, event: Any) -> bool:
        event_name = getattr(event.type(), "name", str(event.type()))
        return event_name in self._EVENT_NAMES

    def event_name(self, event: Any) -> str:
        return getattr(event.type(), "name", str(event.type()))

    def as_dict(self) -> dict[str, dict[str, float | int]]:
        result: dict[str, dict[str, float | int]] = {}
        for key, stats in sorted(
            self._stats.items(),
            key=lambda item: float(item[1]["total_seconds"]),
            reverse=True,
        ):
            count = int(stats["count"])
            total = float(stats["total_seconds"])
            result[key] = {
                "count": count,
                "total_seconds": round(total, 6),
                "average_seconds": round(total / count, 6) if count else 0.0,
                "max_seconds": round(float(stats["max_seconds"]), 6),
            }
        return result


def run_qt(
    report_path: Path,
    profile_path: Path,
    top: int,
    duration: float,
    enable_cprofile: bool,
) -> int:
    """Launch the real Qt app and profile event dispatch while it is used."""

    _ensure_import_paths()

    # Do not force offscreen mode here: this mode is intended for real manual UI
    # interaction.  Set QT_QPA_PLATFORM=offscreen explicitly for a headless run.
    import desktop_qt_ui.main as desktop_main
    from PyQt6.QtCore import QTimer

    QApplication = desktop_main.QApplication

    event_timer = EventTimer()

    class ProfilingApplication(QApplication):
        def __init__(self, argv: list[str]) -> None:
            super().__init__(argv)
            self._event_timer = event_timer
            if duration > 0:
                QTimer.singleShot(round(duration * 1000), self.quit)

        def notify(self, receiver: Any, event: Any) -> bool:
            if not event_timer.should_record(event):
                return super().notify(receiver, event)

            started = time.perf_counter()
            try:
                return super().notify(receiver, event)
            finally:
                receiver_name = type(receiver).__name__
                event_timer.record(
                    event_timer.event_name(event),
                    receiver_name,
                    time.perf_counter() - started,
                )

    # main.py imports QApplication into its module namespace, so replace only
    # that symbol instead of changing the application code on disk.
    desktop_main.QApplication = ProfilingApplication
    sys.argv = ["python tools/profile_performance.py", "qt"]

    profile = cProfile.Profile() if enable_cprofile else None
    started = time.perf_counter()
    exit_code = 0
    try:
        if profile is None:
            desktop_main.main()
        else:
            profile.runcall(desktop_main.main)
    except SystemExit as exc:
        exit_code = int(exc.code) if isinstance(exc.code, int) else 1
    except BaseException:
        exit_code = 1
        raise
    finally:
        elapsed = time.perf_counter() - started
        events = event_timer.as_dict()
        payload = {
            "mode": "qt",
            "started_at": dt.datetime.now().astimezone().isoformat(),
            "exit_code": exit_code,
            "elapsed_seconds": round(elapsed, 6),
            "peak_rss_mb": _peak_rss_mb(),
            "qt_events": events,
            "profile_output": str(profile_path) if profile is not None else None,
        }
        _write_json(report_path, payload)
        _write_cprofile(profile, profile_path, top)
        print("Qt event timing (inclusive event-dispatch time):")
        for name, stats in list(events.items())[:top]:
            print(
                f"  {name:48s} "
                f"total={float(stats['total_seconds']):8.3f}s "
                f"count={int(stats['count']):6d} "
                f"avg={float(stats['average_seconds']) * 1000:7.3f}ms "
                f"max={float(stats['max_seconds']) * 1000:7.3f}ms"
            )
        print(f"JSON summary: {report_path}")

    return exit_code


def _parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Profile the real manga translation pipeline or Qt UI."
    )
    parser.add_argument("mode", choices=("pipeline", "qt"))
    parser.add_argument(
        "--report-output",
        type=Path,
        help="JSON summary path (default: result/profiling/<mode>-<timestamp>.json)",
    )
    parser.add_argument(
        "--profile-output",
        type=Path,
        help="cProfile path (default: result/profiling/<mode>-<timestamp>.prof)",
    )
    parser.add_argument("--top", type=int, default=30, help="Number of top rows to print")
    parser.add_argument(
        "--no-cprofile",
        action="store_true",
        help="Disable Python cProfile collection; useful for less observer overhead.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Qt mode only: automatically quit after this many seconds; 0 means manual quit.",
    )
    args, passthrough = parser.parse_known_args(argv)
    # argparse keeps an explicit ``--`` in the unknown remainder.  It is only
    # a separator for this wrapper and must not reach the project's parser.
    passthrough = [value for value in passthrough if value != "--"]

    if args.top < 1:
        parser.error("--top must be at least 1")
    if args.duration < 0:
        parser.error("--duration cannot be negative")

    if args.mode == "pipeline":
        if not any(value in {"-i", "--input"} for value in passthrough):
            parser.error(
                "pipeline mode needs the local command's -i/--input; "
                "all local arguments are passed through after the mode"
            )
    elif passthrough:
        parser.error(f"unexpected Qt arguments: {passthrough}")

    return args, passthrough


def main(argv: list[str] | None = None) -> int:
    args, passthrough = _parse_args(argv if argv is not None else sys.argv[1:])
    default_report, default_profile = _default_output_paths(args.mode)
    report_path = args.report_output or default_report
    profile_path = args.profile_output or default_profile

    if args.mode == "pipeline":
        return run_pipeline(
            passthrough,
            report_path,
            profile_path,
            args.top,
            not args.no_cprofile,
        )
    return run_qt(
        report_path,
        profile_path,
        args.top,
        args.duration,
        not args.no_cprofile,
    )


if __name__ == "__main__":
    raise SystemExit(main())
