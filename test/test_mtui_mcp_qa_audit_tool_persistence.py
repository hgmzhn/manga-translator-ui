"""mtui_qa_audit MCP tool: force_yuzumarker / save_json_path / diff_against_json / summary_only plumbing."""

import _bootstrap  # noqa: F401, I001

import json
from pathlib import Path

import mtui_mcp.server as server


def test_qa_audit_tool_saves_json_diffs_and_summarizes(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict] = []
    reports = iter(
        [
            {"audited_pages": 1, "error_count": 0, "pages": [{"page_name": "p1.png", "issues": []}]},
            {
                "audited_pages": 1,
                "error_count": 1,
                "pages": [
                    {
                        "page_name": "p1.png",
                        "issues": [{"severity": "error", "category": "vertical_alignment_mismatch", "region_index": 0}],
                    }
                ],
            },
        ]
    )

    def fake_run_qa_audit(target_path, **kwargs):
        calls.append(kwargs)
        return next(reports)

    monkeypatch.setattr(server, "run_qa_audit", fake_run_qa_audit)

    before = tmp_path / "qa" / "before.json"
    out1 = json.loads(
        server.mtui_qa_audit(str(tmp_path), force_yuzumarker=True, save_json_path=str(before), summary_only=True)
    )
    assert calls[0]["force_yuzumarker"] is True
    assert "pages" not in out1
    assert out1["saved_json_path"] == str(before)
    assert json.loads(before.read_text(encoding="utf-8"))["pages"][0]["page_name"] == "p1.png"

    after = tmp_path / "qa" / "after.json"
    out2 = json.loads(server.mtui_qa_audit(str(tmp_path), save_json_path=str(after), diff_against_json=str(before)))
    assert "pages" in out2
    diff = out2["regression_diff"]
    assert diff["newly_introduced_error_count"] == 1
    assert diff["passed"] is False


def test_qa_audit_tool_missing_baseline_reports_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "run_qa_audit", lambda target_path, **kw: {"error_count": 0, "pages": []})
    out = json.loads(server.mtui_qa_audit(str(tmp_path), diff_against_json=str(tmp_path / "nope.json")))
    assert "baseline not found" in out["regression_diff"]["error"]
