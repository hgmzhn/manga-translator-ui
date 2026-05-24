# ADR 0001: Editor HTTP API Server

## Status
Accepted

## Context
Claude (external AI agent) needs to read and modify the editor's region data
(e.g. `bg_colors` for stroke color) while the Qt editor is running. No IPC or
remote-control mechanism existed previously.

## Decision
Embed a minimal HTTP REST API server in the editor process:

- **Transport**: HTTP (Python `http.server.ThreadingHTTPServer`)
- **Port**: `54321` fixed, fallback to random port on conflict
- **Lifetime**: started as a daemon thread when the editor initializes
- **Thread model**:
  - HTTP handler runs in a daemon thread
  - Read requests snapshot Python data directly (GIL-safe)
  - Write requests emit a `pyqtSignal` to mutate model data in the Qt main
    thread, ensuring UI signals fire correctly. Responses are returned
    immediately after emitting (fire-and-forget).
- **No auth** — bound to `127.0.0.1` only

## Consequences
- Claude can `curl` or `WebFetch` region data without any special setup.
- Writes bypass the `QUndoCommand` system (no Ctrl+Z for API modifications).
- Minimal code (~200 lines), zero new dependencies.
- Daemon thread exits automatically when the editor process ends.

## Alternatives considered
- **Standalone proxy process**: more complex; would need file-based or socket IPC
  to reach the editor's in-memory model.
- **WebSocket**: overkill for request-response patterns; no push notifications
  needed.
