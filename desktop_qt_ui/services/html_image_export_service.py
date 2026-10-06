"""Create a self-contained HTML viewer for the images from one task."""

from __future__ import annotations

import base64
import html
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


HTML_VIEW_MODES = frozenset({"scroll", "paged"})
_IMAGE_MIME_TYPES = {
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".jfif": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}


@dataclass(frozen=True, slots=True)
class HtmlImageExportResult:
    """Metadata for a generated task HTML document."""

    output_path: str
    image_count: int
    skipped_count: int
    view_mode: str


def _normalize_view_mode(view_mode: str | None) -> str:
    normalized = str(view_mode or "scroll").strip().lower()
    return normalized if normalized in HTML_VIEW_MODES else "scroll"


def _guess_image_mime_type(path: Path) -> str:
    return _IMAGE_MIME_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or ""


def _unique_image_paths(image_paths: Iterable[str | os.PathLike[str]]) -> list[Path]:
    """Keep existing image files in task order and remove duplicate paths."""
    paths: list[Path] = []
    seen: set[str] = set()
    for raw_path in image_paths:
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        if not path.is_file():
            continue
        mime_type = _guess_image_mime_type(path)
        if not mime_type.startswith("image/"):
            continue
        normalized = os.path.normcase(str(path.resolve()))
        if normalized in seen:
            continue
        seen.add(normalized)
        paths.append(path.resolve())
    return paths


def _image_data_uri(path: Path) -> str:
    mime_type = _guess_image_mime_type(path) or "application/octet-stream"
    with path.open("rb") as image_file:
        encoded = base64.b64encode(image_file.read()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_html_document(
    image_paths: Iterable[str | os.PathLike[str]],
    *,
    view_mode: str = "scroll",
    title: str = "Translated Images",
) -> tuple[str, int, int]:
    """Build an offline HTML viewer whose image sources are data URIs.

    The returned document intentionally contains no filesystem image paths. Missing
    or non-image files are ignored so one stale result cannot invalidate a task's
    other pages.
    """
    normalized_mode = _normalize_view_mode(view_mode)
    candidates = list(image_paths)
    paths = _unique_image_paths(candidates)
    skipped_count = max(0, len(candidates) - len(paths))
    if not paths:
        raise ValueError("没有可写入 HTML 的图片结果")

    escaped_title = html.escape(str(title or "Translated Images"), quote=True)
    pages: list[str] = []
    for index, path in enumerate(paths, start=1):
        alt_text = html.escape(path.name, quote=True)
        data_uri = _image_data_uri(path)
        pages.append(
            "        <figure class=\"page\" data-page-index=\"{index}\">\n"
            "          <img src=\"{data_uri}\" alt=\"{alt_text}\" draggable=\"false\">\n"
            "        </figure>".format(
                index=index,
                name=alt_text,
                data_uri=data_uri,
                alt_text=alt_text,
            )
        )

    page_markup = "\n".join(pages)
    document = f"""<!doctype html>
<html lang="zh-CN" data-view-mode="{normalized_mode}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #111318;
      --panel: rgba(30, 34, 43, .94);
      --panel-border: rgba(255, 255, 255, .11);
      --text: #f4f6fb;
      --muted: #aeb5c4;
      --accent: #70b8ff;
      --accent-soft: rgba(112, 184, 255, .16);
      --page-bg: #20242d;
    }}
    * {{ box-sizing: border-box; }}
    html {{ background: var(--bg); scroll-behavior: smooth; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: radial-gradient(circle at top, #202631 0, var(--bg) 42rem);
      color: var(--text);
      font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .toolbar {{
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 12px clamp(14px, 3vw, 36px);
      background: var(--panel);
      border-bottom: 1px solid var(--panel-border);
      backdrop-filter: blur(18px);
    }}
    .toolbar h1 {{
      flex: 1;
      min-width: 0;
      margin: 0 12px 0 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 16px;
      font-weight: 650;
    }}
    .toolbar button {{
      border: 1px solid var(--panel-border);
      border-radius: 8px;
      padding: 7px 11px;
      color: var(--text);
      background: transparent;
      cursor: pointer;
      transition: background .16s ease, border-color .16s ease, transform .16s ease;
    }}
    .toolbar button:hover {{
      border-color: var(--accent);
      background: var(--accent-soft);
      transform: translateY(-1px);
    }}
    .toolbar button[aria-pressed="true"] {{
      border-color: var(--accent);
      background: var(--accent-soft);
      color: #dceeff;
    }}
    .toolbar button:disabled {{ opacity: .4; cursor: not-allowed; transform: none; }}
    .hint {{ color: var(--muted); white-space: nowrap; }}
    .viewer {{
      display: grid;
      gap: 0;
      width: min(100%, 1120px);
      margin: 0 auto;
      padding: 28px clamp(12px, 3vw, 36px) 54px;
    }}
    .page {{
      position: relative;
      margin: 0;
      padding: 0;
      border: 0;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }}
    .page figcaption {{
      display: none;
    }}
    .page img {{
      display: block;
      width: 100%;
      height: auto;
      border-radius: 0;
      user-select: none;
    }}
    body.is-paged .viewer {{
      min-height: calc(100vh - 66px);
      align-items: center;
      padding-top: 24px;
    }}
    body.is-paged .page {{ display: none; width: 100%; }}
    body.is-paged .page.is-active {{ display: block; }}
    @media (max-width: 680px) {{
      .toolbar {{ flex-wrap: wrap; }}
      .toolbar h1 {{ flex-basis: 100%; order: -1; margin-right: 0; }}
      .hint {{ flex: 1; font-size: 12px; }}
      .page {{ padding: 0; border-radius: 0; }}
    }}
  </style>
</head>
<body class="{('is-paged' if normalized_mode == 'paged' else '')}">
  <header class="toolbar" aria-label="HTML viewer controls">
    <h1>{escaped_title}</h1>
    <button type="button" data-mode="scroll" aria-pressed="{'true' if normalized_mode == 'scroll' else 'false'}">连续滚动</button>
    <button type="button" data-mode="paged" aria-pressed="{'true' if normalized_mode == 'paged' else 'false'}">分页查看</button>
    <button type="button" data-nav="prev" aria-label="上一页或上一段">←</button>
    <button type="button" data-nav="next" aria-label="下一页或下一段">→</button>
    <span class="hint" id="viewer-hint"></span>
  </header>
  <main class="viewer" id="viewer" aria-live="polite">
{page_markup}
  </main>
  <script>
    (() => {{
      const pages = [...document.querySelectorAll('.page')];
      const body = document.body;
      const hint = document.getElementById('viewer-hint');
      const modeButtons = [...document.querySelectorAll('[data-mode]')];
      const navButtons = [...document.querySelectorAll('[data-nav]')];
      let mode = document.documentElement.dataset.viewMode === 'paged' ? 'paged' : 'scroll';
      let currentPage = 0;

      function updatePagedPage() {{
        pages.forEach((page, index) => page.classList.toggle('is-active', index === currentPage));
        navButtons[0].disabled = currentPage === 0;
        navButtons[1].disabled = currentPage === pages.length - 1;
        hint.textContent = `${{currentPage + 1}} / ${{pages.length}} · 使用 ← / → 翻页`;
      }}

      function updateMode(nextMode) {{
        mode = nextMode === 'paged' ? 'paged' : 'scroll';
        body.classList.toggle('is-paged', mode === 'paged');
        modeButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === mode)));
        if (mode === 'paged') {{
          updatePagedPage();
          window.scrollTo({{ top: 0, behavior: 'auto' }});
        }} else {{
          pages.forEach(page => page.classList.remove('is-active'));
          navButtons.forEach(button => button.disabled = false);
          hint.textContent = '使用 ↑ / ↓ 滚动查看';
        }}
      }}

      function navigate(delta) {{
        if (mode === 'paged') {{
          currentPage = Math.max(0, Math.min(pages.length - 1, currentPage + delta));
          updatePagedPage();
          window.scrollTo({{ top: 0, behavior: 'smooth' }});
          return;
        }}
        window.scrollBy({{ top: delta * Math.max(window.innerHeight * .82, 240), behavior: 'smooth' }});
      }}

      modeButtons.forEach(button => button.addEventListener('click', () => updateMode(button.dataset.mode)));
      navButtons[0].addEventListener('click', () => navigate(-1));
      navButtons[1].addEventListener('click', () => navigate(1));
      document.addEventListener('keydown', event => {{
        if (event.target instanceof Element && event.target.matches('input, textarea, select, [contenteditable="true"]')) return;
        if (mode === 'paged' && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {{
          event.preventDefault();
          navigate(event.key === 'ArrowRight' ? 1 : -1);
        }} else if (mode === 'scroll' && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {{
          event.preventDefault();
          navigate(event.key === 'ArrowDown' ? 1 : -1);
        }}
      }});
      updateMode(mode);
    }})();
  </script>
</body>
</html>
"""
    return document, len(paths), skipped_count


def write_task_html(
    image_paths: Iterable[str | os.PathLike[str]],
    output_dir: str | os.PathLike[str],
    *,
    task_id: int,
    view_mode: str = "scroll",
) -> HtmlImageExportResult:
    """Write one uniquely named HTML file for a completed translation task."""
    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    normalized_mode = _normalize_view_mode(view_mode)
    document, image_count, skipped_count = build_html_document(
        image_paths,
        view_mode=normalized_mode,
        title=f"Translated task {task_id}",
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"translated_task_{int(task_id):04d}_{timestamp}"
    output_path = output_root / f"{base_name}.html"
    suffix = 2
    while output_path.exists():
        output_path = output_root / f"{base_name}_{suffix}.html"
        suffix += 1
    output_path.write_text(document, encoding="utf-8")
    return HtmlImageExportResult(
        output_path=str(output_path),
        image_count=image_count,
        skipped_count=skipped_count,
        view_mode=normalized_mode,
    )
