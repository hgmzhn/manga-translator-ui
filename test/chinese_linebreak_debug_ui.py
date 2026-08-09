from __future__ import annotations

import _bootstrap  # noqa: F401

import base64
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Optional


ROOT = _bootstrap.ROOT
# 该脚本是交互式调试窗口，不沿用自动化测试的 offscreen 平台。
os.environ.pop("QT_QPA_PLATFORM", None)


from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    DoubleSpinBox,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    Theme,
    setTheme,
)

from manga_translator.rendering import calc_box_from_font
from manga_translator.rendering.auto_linebreak import solve_no_br_layout
from manga_translator.rendering.chinese_linebreak import (
    BubbleLinebreakEvaluation,
    _bubble_candidate_budgets,
    _semantic_units,
    candidate_semantic_break_penalty,
    choose_chinese_bubble_linebreak_with_trace,
    chinese_linebreak_models_available,
    layout_chinese_cjk,
)


BR_RE = re.compile(r"\s*(?:\[BR\]|<br>|【BR】)\s*", re.IGNORECASE)


@dataclass
class CandidateRow:
    index: int
    budget: object
    text_with_br: str
    n_segments: int
    required_width: float
    required_height: float
    fits: bool
    overflow: int
    semantic_penalty: int
    target_ok: bool
    selected: bool = False
    score: Optional[list] = None
    filter_reason: str = ""


def _display_text(text: str) -> str:
    return BR_RE.sub("↵", text or "")


def _line_count(text: str) -> int:
    return len(BR_RE.split(text or "")) if text else 0


def _rect_overflow(required_width: float, required_height: float, box_width: float, box_height: float) -> int:
    inside_width = min(max(required_width, 0.0), max(box_width, 0.0))
    inside_height = min(max(required_height, 0.0), max(box_height, 0.0))
    total = max(required_width, 0.0) * max(required_height, 0.0)
    inside = inside_width * inside_height
    return int(max(0.0, total - inside))


def _parse_debug_log(text: str) -> dict:
    parsed: dict = {}
    for line in (text or "").splitlines():
        if "[中文语义断句]" not in line:
            continue
        fields = dict(
            (key, value[1:-1] if value.startswith("'") and value.endswith("'") else value)
            for key, value in re.findall(r"(\w+)=('[^']*'|[^\s]+)", line)
        )
        if not fields:
            continue

        ctx = fields.get("ctx", "")
        if fields.get("input"):
            parsed["text"] = fields["input"]
        if fields.get("dir") in {"h", "v"}:
            parsed["horizontal"] = fields["dir"] == "h"

        font_value = fields.get("font") or fields.get("seed_font") or fields.get("out_font")
        if font_value:
            parsed["font_size"] = _parse_int(font_value)

        target_value = fields.get("target") or fields.get("seed_target") or fields.get("init_target")
        if target_value:
            parsed["target_segments"] = _parse_int(target_value)

        box = _parse_box(fields.get("box_budget", ""))
        if box:
            if ctx == "bubble_mask_final" or fields.get("line_budget", "-") != "-":
                parsed["bubble_w"], parsed["bubble_h"] = box
            else:
                parsed["ocr_w"], parsed["ocr_h"] = box

        line_budget = _parse_float(fields.get("line_budget", ""))
        if line_budget is not None:
            parsed["line_budget"] = line_budget

    return parsed


def _load_debug_json_from_input(text: str) -> Optional[dict]:
    value = (text or "").strip()
    if not value:
        return None
    path_text = value.strip().strip('"').strip("'")
    if os.path.isfile(path_text):
        try:
            with open(path_text, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {"records": data}
        except Exception:
            return None
    if value.startswith("{") or value.startswith("["):
        try:
            data = json.loads(value)
            return data if isinstance(data, dict) else {"records": data}
        except Exception:
            return None
    return None


def _records_from_debug_json(data: dict) -> list[dict]:
    records = data.get("records", []) if isinstance(data, dict) else []
    return [record for record in records if isinstance(record, dict)]


def _record_size_text(size: Optional[dict]) -> str:
    if not isinstance(size, dict):
        return "-"
    width = size.get("width")
    height = size.get("height")
    if width is None or height is None:
        return "-"
    return f"{float(width):.1f} x {float(height):.1f}"


def _units_from_json_text(units: object) -> str:
    if not isinstance(units, list):
        return "语义单元: -"
    lines: list[str] = ["语义单元/成分子节点:"]

    def dump(items: list, depth: int = 0) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            lines.append(f"{'  ' * depth}- {item.get('text', '')}")
            children = item.get("children")
            if isinstance(children, list) and children:
                dump(children, depth + 1)

    dump(units)
    return "\n".join(lines)


def _parse_box(value: str) -> Optional[tuple[float, float]]:
    match = re.match(r"([0-9.]+)x([0-9.]+)", value or "")
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def _parse_int(value: str) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_float(value: str) -> Optional[float]:
    if value in {"", "-"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        render=SimpleNamespace(
            semantic_linebreak=True,
            no_hyphenation=False,
            auto_rotate_symbols=False,
        )
    )


def _semantic_tree_text(text: str) -> str:
    units = _semantic_units(text)
    if units is None:
        return "语义单元: HanLP 模型不可用或分词结果无法对齐。"

    lines: list[str] = ["语义单元/成分子节点:"]

    def dump(items, depth: int = 0) -> None:
        for unit in items:
            prefix = "  " * depth
            lines.append(f"{prefix}- {unit.text}")
            if unit.children:
                dump(unit.children, depth + 1)

    dump(units)
    return "\n".join(lines)


class ChineseLinebreakDebugWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("中文语义断句调试")
        self.resize(1280, 820)
        self._debug_json_data: Optional[dict] = None
        self._debug_json_records: list[dict] = []
        self._debug_choice_records: list[dict] = []
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self.run_analysis)
        self._build_ui()
        self._load_default_example()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(10)
        root.addLayout(left, 2)

        title = StrongBodyLabel("输入")
        left.addWidget(title)

        self.input_edit = PlainTextEdit()
        self.input_edit.setPlaceholderText("粘贴中文译文、旧日志、JSON 内容，或 chinese_linebreak_debug.json 文件路径")
        self.input_edit.setMinimumHeight(150)
        self.input_edit.textChanged.connect(self._schedule_auto_run)
        left.addWidget(self.input_edit)

        control_card = CardWidget()
        control_layout = QFormLayout(control_card)
        control_layout.setContentsMargins(16, 16, 16, 16)
        control_layout.setSpacing(10)

        self.direction_combo = ComboBox()
        self.direction_combo.addItems(["竖排", "横排"])
        self.direction_combo.currentIndexChanged.connect(self._schedule_auto_run)
        control_layout.addRow("方向", self.direction_combo)

        self.font_spin = SpinBox()
        self.font_spin.setRange(8, 120)
        self.font_spin.setValue(36)
        self.font_spin.valueChanged.connect(self._schedule_auto_run)
        control_layout.addRow("字号", self.font_spin)

        self.target_spin = SpinBox()
        self.target_spin.setRange(1, 12)
        self.target_spin.setValue(2)
        self.target_spin.valueChanged.connect(self._schedule_auto_run)
        control_layout.addRow("目标行/列数", self.target_spin)

        self.ocr_w_spin = SpinBox()
        self.ocr_w_spin.setRange(1, 3000)
        self.ocr_w_spin.setValue(87)
        self.ocr_w_spin.valueChanged.connect(self._schedule_auto_run)
        control_layout.addRow("OCR 宽", self.ocr_w_spin)

        self.ocr_h_spin = SpinBox()
        self.ocr_h_spin.setRange(1, 3000)
        self.ocr_h_spin.setValue(331)
        self.ocr_h_spin.valueChanged.connect(self._schedule_auto_run)
        control_layout.addRow("OCR 高", self.ocr_h_spin)

        self.bubble_w_spin = SpinBox()
        self.bubble_w_spin.setRange(1, 3000)
        self.bubble_w_spin.setValue(272)
        self.bubble_w_spin.valueChanged.connect(self._schedule_auto_run)
        control_layout.addRow("气泡宽", self.bubble_w_spin)

        self.bubble_h_spin = SpinBox()
        self.bubble_h_spin.setRange(1, 3000)
        self.bubble_h_spin.setValue(272)
        self.bubble_h_spin.valueChanged.connect(self._schedule_auto_run)
        control_layout.addRow("气泡高", self.bubble_h_spin)

        self.letter_spacing_spin = DoubleSpinBox()
        self.letter_spacing_spin.setRange(0.1, 5.0)
        self.letter_spacing_spin.setSingleStep(0.1)
        self.letter_spacing_spin.setValue(1.0)
        self.letter_spacing_spin.valueChanged.connect(self._schedule_auto_run)
        control_layout.addRow("字距倍率", self.letter_spacing_spin)

        self.record_combo = ComboBox()
        self.record_combo.addItems(["-"])
        self.record_combo.setEnabled(False)
        self.record_combo.currentIndexChanged.connect(self._select_debug_record)
        control_layout.addRow("气泡记录", self.record_combo)

        left.addWidget(control_card)

        button_row = QHBoxLayout()
        self.run_button = PrimaryPushButton("分析")
        self.run_button.clicked.connect(self.run_analysis)
        button_row.addWidget(self.run_button)

        parse_button = PushButton("读取/分析")
        parse_button.clicked.connect(self.run_analysis)
        button_row.addWidget(parse_button)

        open_button = PushButton("打开 JSON")
        open_button.clicked.connect(self._open_debug_json)
        button_row.addWidget(open_button)

        example_button = PushButton("例子")
        example_button.clicked.connect(self._load_default_example)
        button_row.addWidget(example_button)

        self.auto_checkbox = CheckBox("粘贴后自动分析")
        self.auto_checkbox.setChecked(True)
        button_row.addWidget(self.auto_checkbox)
        button_row.addStretch(1)
        left.addLayout(button_row)

        hint = BodyLabel(
            "说明: 读取 JSON 时显示真实渲染记录；普通文本模式仍用矩形气泡模拟。"
        )
        hint.setWordWrap(True)
        left.addWidget(hint)
        left.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(10)
        root.addLayout(right, 5)

        right.addWidget(StrongBodyLabel("过程"))
        self.summary_edit = PlainTextEdit()
        self.summary_edit.setReadOnly(True)
        self.summary_edit.setMinimumHeight(220)
        right.addWidget(self.summary_edit, 2)

        self.mask_label = QLabel("气泡 mask: -")
        self.mask_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mask_label.setMinimumHeight(120)
        right.addWidget(self.mask_label)

        right.addWidget(StrongBodyLabel("候选"))
        self.table = QTableWidget(0, 12)
        self.table.setHorizontalHeaderLabels(
            ["#", "来源/预算", "行数", "目标", "尺寸", "fits", "成分代价", "overflow", "score", "选择", "结果", "说明"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(10, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        right.addWidget(self.table, 3)

    def _load_default_example(self) -> None:
        self._clear_debug_record_selector()
        self.input_edit.setPlainText("山吹家会在新年首次书法时写下今年的抱负")
        self.font_spin.setValue(36)
        self.target_spin.setValue(2)
        self.ocr_w_spin.setValue(87)
        self.ocr_h_spin.setValue(331)
        self.bubble_w_spin.setValue(272)
        self.bubble_h_spin.setValue(272)
        self.direction_combo.setCurrentIndex(0)
        self.run_analysis()

    def _schedule_auto_run(self) -> None:
        if self.auto_checkbox.isChecked():
            self._auto_timer.start(650)

    def _clear_debug_record_selector(self) -> None:
        self._debug_json_data = None
        self._debug_json_records = []
        self._debug_choice_records = []
        self.record_combo.blockSignals(True)
        self.record_combo.clear()
        self.record_combo.addItems(["-"])
        self.record_combo.setEnabled(False)
        self.record_combo.blockSignals(False)

    def _select_debug_record(self, *_args) -> None:
        self._render_selected_debug_record()

    def _open_debug_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开中文断句调试 JSON", str(ROOT), "JSON (*.json);;All Files (*)")
        if path:
            self.input_edit.setPlainText(path)
            self.run_analysis()

    def run_analysis(self) -> None:
        raw_input = self.input_edit.toPlainText().strip()
        debug_json = _load_debug_json_from_input(raw_input)
        if debug_json is not None:
            self._debug_json_data = debug_json
            self._show_debug_json(debug_json)
            return

        self._clear_debug_record_selector()
        parsed_log = _parse_debug_log(raw_input)
        if parsed_log:
            self._apply_parsed_log(parsed_log)
        text = parsed_log.get("text") if parsed_log else raw_input
        text = BR_RE.sub("", text.strip())
        if not text:
            self.summary_edit.setPlainText("请输入文本。")
            self.table.setRowCount(0)
            return

        horizontal = self.direction_combo.currentIndex() == 1
        font_size = int(self.font_spin.value())
        target_segments = int(self.target_spin.value())
        ocr_w = float(self.ocr_w_spin.value())
        ocr_h = float(self.ocr_h_spin.value())
        bubble_w = float(self.bubble_w_spin.value())
        bubble_h = float(self.bubble_h_spin.value())
        letter_spacing = float(self.letter_spacing_spin.value())
        line_budget = bubble_w if horizontal else bubble_h
        cfg = _config()

        try:
            ocr_result = solve_no_br_layout(
                text=text,
                horizontal=horizontal,
                seed_segments=target_segments,
                seed_font_size=font_size,
                bubble_width=ocr_w,
                bubble_height=ocr_h,
                min_font_size=1,
                max_font_size=font_size,
                line_spacing_multiplier=1.0,
                target_lang="CHS",
                config=cfg,
                iterations=1,
                letter_spacing_multiplier=letter_spacing,
                adjust_font_size=False,
                debug_context="ui_ocr",
            )

            single_w, single_h, _ = calc_box_from_font(
                font_size,
                text,
                horizontal,
                1.0,
                None,
                "CHS",
                center=None,
                angle=0,
                letter_spacing=letter_spacing,
            )
            total_budget = float(single_w if horizontal else single_h)
            budgets = _bubble_candidate_budgets(total_budget, line_budget, target_segments)
            rows = self._collect_candidates(
                text=text,
                current_text=ocr_result.text_with_br,
                budgets=budgets,
                font_size=font_size,
                target_segments=target_segments,
                horizontal=horizontal,
                bubble_w=bubble_w,
                bubble_h=bubble_h,
                letter_spacing=letter_spacing,
            )

            row_by_text = {row.text_with_br: row for row in rows}

            def evaluate(candidate_text: str) -> Optional[BubbleLinebreakEvaluation]:
                row = row_by_text.get(candidate_text)
                if row is None:
                    row = self._evaluate_candidate(
                        index=len(row_by_text) + 1,
                        budget=0,
                        text_with_br=candidate_text,
                        source_text=text,
                        target_segments=target_segments,
                        font_size=font_size,
                        horizontal=horizontal,
                        bubble_w=bubble_w,
                        bubble_h=bubble_h,
                        letter_spacing=letter_spacing,
                    )
                    row_by_text[candidate_text] = row
                return BubbleLinebreakEvaluation(
                    text_with_br=row.text_with_br,
                    required_width=row.required_width,
                    required_height=row.required_height,
                    n_segments=row.n_segments,
                    dst_points=None,
                    overflow_pixels=row.overflow,
                )

            choice = choose_chinese_bubble_linebreak_with_trace(
                source_text=text,
                current_text=ocr_result.text_with_br,
                font_size=font_size,
                target_segments=target_segments,
                total_budget=total_budget,
                line_budget=line_budget,
                horizontal=horizontal,
                letter_spacing=letter_spacing,
                evaluate=evaluate,
            )
            chosen = choice.selected if choice is not None else None

            if chosen is not None:
                for row in rows:
                    row.selected = row.text_with_br == chosen.text_with_br

            self._fill_summary(
                text=text,
                parsed_log=parsed_log,
                horizontal=horizontal,
                font_size=font_size,
                target_segments=target_segments,
                ocr_w=ocr_w,
                ocr_h=ocr_h,
                bubble_w=bubble_w,
                bubble_h=bubble_h,
                line_budget=line_budget,
                total_budget=total_budget,
                budgets=budgets,
                ocr_result=ocr_result,
                chosen=chosen,
            )
            self._fill_table(rows, target_segments)
        except Exception as exc:
            self.summary_edit.setPlainText(f"分析失败:\n{type(exc).__name__}: {exc}")
            self.table.setRowCount(0)

    def _show_debug_json(self, data: dict, keep_selection: bool = False) -> None:
        records = _records_from_debug_json(data)
        choice_records = [record for record in records if record.get("stage") == "bubble_mask_choice"]
        if not choice_records:
            self.summary_edit.setPlainText("JSON 中没有 bubble_mask_choice 记录。")
            self.table.setRowCount(0)
            self.mask_label.setText("气泡 mask: -")
            self._clear_debug_record_selector()
            return

        self._debug_json_records = records
        self._debug_choice_records = choice_records
        self._sync_debug_record_selector(choice_records, keep_selection=keep_selection)
        self._render_selected_debug_record()

    def _render_selected_debug_record(self) -> None:
        if not self._debug_choice_records:
            return
        index = self.record_combo.currentIndex()
        if index < 0 or index >= len(self._debug_choice_records):
            index = len(self._debug_choice_records) - 1
        self._render_debug_record(self._debug_json_records, self._debug_choice_records[index])

    def _render_debug_record(self, records: list[dict], record: dict) -> None:
        region_index = record.get("region_index")
        ocr_records = [
            item for item in records
            if item.get("stage") == "ocr_box" and item.get("region_index") == region_index
        ]
        ocr_record = ocr_records[-1] if ocr_records else None
        snapshot = record.get("linebreak_snapshot") if isinstance(record.get("linebreak_snapshot"), dict) else {}
        selected = record.get("selected") if isinstance(record.get("selected"), dict) else None
        mask = record.get("mask") if isinstance(record.get("mask"), dict) else {}

        self._set_mask_preview(mask)

        lines = [
            f"JSON 记录数: {len(records)}",
            f"region: {region_index}",
            f"输入: {record.get('input', '')}",
            f"进入气泡阶段断句: {_display_text(str(record.get('current_candidate', '')))}",
            f"方向: {'横排' if record.get('direction') == 'h' else '竖排'}",
            f"字号: {record.get('font_size', '-')}",
            f"目标行/列数: {record.get('target_segments', '-')}",
            f"OCR 框: {_record_size_text(record.get('ocr_box_size'))}",
            f"气泡内接范围: {_record_size_text(record.get('bubble_inscribed_rect'))}",
            f"line_budget: {record.get('bubble_inscribed_rect', {}).get('line_budget', '-') if isinstance(record.get('bubble_inscribed_rect'), dict) else '-'}",
            f"mask: {mask.get('width', 0)} x {mask.get('height', 0)}, pixels={mask.get('nonzero_pixels', 0)}",
        ]
        if ocr_record is not None:
            lines.extend(
                [
                    "",
                    "OCR 初始断句:",
                    f"  输出: {_display_text(str(ocr_record.get('output', '')))}",
                    f"  行数: {ocr_record.get('output_segments', '-')}",
                    f"  required: {_record_size_text(ocr_record.get('required'))}",
                    f"  reason: {ocr_record.get('reason', '-')}",
                ]
            )
        if selected is not None:
            lines.extend(
                [
                    "",
                    "气泡候选选择:",
                    f"  输出: {_display_text(str(selected.get('text_with_br', '')))}",
                    f"  行数: {selected.get('segments', '-')}",
                    f"  required: {_record_size_text(selected.get('required'))}",
                    f"  fits: {selected.get('fits', '-')}",
                    f"  overflow: {selected.get('overflow_pixels', '-')}",
                ]
            )
        else:
            lines.extend(["", "气泡候选选择:", "  没有可用候选。"])

        lines.extend(
            [
                "",
                "粗分词:",
                "  " + " / ".join(str(token) for token in (snapshot.get("coarse_tokens") or [])),
                "",
                "成分树:",
                str(snapshot.get("constituency_tree") or "-"),
                "",
                _units_from_json_text(snapshot.get("semantic_units")),
                "",
                "候选预算生成:",
            ]
        )
        for item in snapshot.get("candidate_layouts") or []:
            if not isinstance(item, dict):
                continue
            if not item.get("available", False):
                lines.append(f"  budget={item.get('budget')}: 无候选")
                continue
            lines.append(
                f"  budget={item.get('budget')}: "
                f"{' / '.join(_display_text(str(line)) for line in item.get('lines', []))} "
                f"metrics={item.get('metrics', [])}"
            )

        self.summary_edit.setPlainText("\n".join(lines))
        self._fill_table(self._rows_from_debug_record(record), int(record.get("target_segments") or 0))

    def _sync_debug_record_selector(self, choice_records: list[dict], *, keep_selection: bool) -> None:
        current_index = self.record_combo.currentIndex() if keep_selection else len(choice_records) - 1
        current_index = max(0, min(current_index, len(choice_records) - 1))
        labels = [self._debug_record_label(index, record) for index, record in enumerate(choice_records)]

        self.record_combo.blockSignals(True)
        self.record_combo.clear()
        self.record_combo.addItems(labels)
        self.record_combo.setEnabled(bool(labels))
        self.record_combo.setCurrentIndex(current_index)
        self.record_combo.blockSignals(False)

    def _debug_record_label(self, index: int, record: dict) -> str:
        text = str(record.get("input", "")).replace("\n", " ")
        if len(text) > 10:
            text = text[:10] + "..."
        direction = "横" if record.get("direction") == "h" else "竖"
        region = record.get("region_index", "-")
        target = record.get("target_segments", "-")
        return f"{index + 1}. R{region} {direction} T{target} {text}"

    def _rows_from_debug_record(self, record: dict) -> list[CandidateRow]:
        evaluations = record.get("candidate_evaluations")
        if not isinstance(evaluations, list) or not evaluations:
            evaluations = record.get("candidates") if isinstance(record.get("candidates"), list) else []

        rows: list[CandidateRow] = []
        for index, item in enumerate(evaluations, start=1):
            if not isinstance(item, dict):
                continue
            required = item.get("required") if isinstance(item.get("required"), dict) else {}
            source = item.get("source", "")
            budget = "current" if source == "current" else item.get("budget", source or "-")
            rows.append(
                CandidateRow(
                    index=int(item.get("index") or item.get("rank") or index),
                    budget=budget,
                    text_with_br=str(item.get("text_with_br") or ""),
                    n_segments=int(item.get("segments") or _line_count(str(item.get("text_with_br") or ""))),
                    required_width=float(required.get("width") or 0.0),
                    required_height=float(required.get("height") or 0.0),
                    fits=bool(item.get("fits", False)),
                    overflow=int(item.get("overflow_pixels") or 0),
                    semantic_penalty=int(item.get("semantic_penalty") or 0),
                    target_ok=bool(item.get("accepted", item.get("selected", False))),
                    selected=bool(item.get("selected", False)),
                    score=item.get("score") if isinstance(item.get("score"), list) else None,
                    filter_reason=str(item.get("filter_reason") or ""),
                )
            )
        return rows

    def _set_mask_preview(self, mask: dict) -> None:
        data = mask.get("data") if isinstance(mask, dict) else ""
        if not data:
            self.mask_label.setText("气泡 mask: -")
            self.mask_label.setPixmap(QPixmap())
            return
        pixmap = QPixmap()
        try:
            pixmap.loadFromData(base64.b64decode(data), "PNG")
        except Exception:
            pixmap = QPixmap()
        if pixmap.isNull():
            self.mask_label.setText("气泡 mask: 读取失败")
            self.mask_label.setPixmap(QPixmap())
            return
        self.mask_label.setText("")
        self.mask_label.setPixmap(
            pixmap.scaled(360, 160, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )

    def _apply_parsed_log(self, parsed: dict) -> None:
        for widget in (
            self.direction_combo,
            self.font_spin,
            self.target_spin,
            self.ocr_w_spin,
            self.ocr_h_spin,
            self.bubble_w_spin,
            self.bubble_h_spin,
        ):
            widget.blockSignals(True)
        try:
            if "horizontal" in parsed:
                self.direction_combo.setCurrentIndex(1 if parsed["horizontal"] else 0)
            if parsed.get("font_size"):
                self.font_spin.setValue(int(parsed["font_size"]))
            if parsed.get("target_segments"):
                self.target_spin.setValue(int(parsed["target_segments"]))
            if parsed.get("ocr_w"):
                self.ocr_w_spin.setValue(max(1, int(round(parsed["ocr_w"]))))
            if parsed.get("ocr_h"):
                self.ocr_h_spin.setValue(max(1, int(round(parsed["ocr_h"]))))
            if parsed.get("bubble_w"):
                self.bubble_w_spin.setValue(max(1, int(round(parsed["bubble_w"]))))
            if parsed.get("bubble_h"):
                self.bubble_h_spin.setValue(max(1, int(round(parsed["bubble_h"]))))
        finally:
            for widget in (
                self.direction_combo,
                self.font_spin,
                self.target_spin,
                self.ocr_w_spin,
                self.ocr_h_spin,
                self.bubble_w_spin,
                self.bubble_h_spin,
            ):
                widget.blockSignals(False)

    def _collect_candidates(
        self,
        *,
        text: str,
        current_text: str,
        budgets: list[int],
        font_size: int,
        target_segments: int,
        horizontal: bool,
        bubble_w: float,
        bubble_h: float,
        letter_spacing: float,
    ) -> list[CandidateRow]:
        rows: list[CandidateRow] = []
        seen: set[str] = set()

        def add_candidate(candidate_text: str, budget: int) -> None:
            if not candidate_text or candidate_text in seen:
                return
            seen.add(candidate_text)
            rows.append(
                self._evaluate_candidate(
                    index=len(rows) + 1,
                    budget=budget,
                    text_with_br=candidate_text,
                    source_text=text,
                    target_segments=target_segments,
                    font_size=font_size,
                    horizontal=horizontal,
                    bubble_w=bubble_w,
                    bubble_h=bubble_h,
                    letter_spacing=letter_spacing,
                )
            )

        add_candidate(current_text, 0)
        for budget in budgets:
            layout = layout_chinese_cjk(
                font_size,
                text,
                budget,
                horizontal=horizontal,
                letter_spacing=letter_spacing,
            )
            if not layout:
                continue
            lines, _metrics = layout
            add_candidate("[BR]".join(lines), budget)
        return rows

    def _evaluate_candidate(
        self,
        *,
        index: int,
        budget: int,
        text_with_br: str,
        source_text: str,
        target_segments: int,
        font_size: int,
        horizontal: bool,
        bubble_w: float,
        bubble_h: float,
        letter_spacing: float,
    ) -> CandidateRow:
        req_w, req_h, n_segments = calc_box_from_font(
            font_size,
            text_with_br,
            horizontal,
            1.0,
            None,
            "CHS",
            center=None,
            angle=0,
            letter_spacing=letter_spacing,
        )
        fits = req_w <= bubble_w and req_h <= bubble_h
        overflow = _rect_overflow(float(req_w), float(req_h), bubble_w, bubble_h)
        semantic_penalty = candidate_semantic_break_penalty(source_text, text_with_br)
        return CandidateRow(
            index=index,
            budget=budget,
            text_with_br=text_with_br,
            n_segments=int(n_segments),
            required_width=float(req_w),
            required_height=float(req_h),
            fits=bool(fits),
            overflow=int(overflow),
            semantic_penalty=int(semantic_penalty),
            target_ok=int(n_segments) == int(target_segments),
        )

    def _fill_summary(
        self,
        *,
        text: str,
        parsed_log: dict,
        horizontal: bool,
        font_size: int,
        target_segments: int,
        ocr_w: float,
        ocr_h: float,
        bubble_w: float,
        bubble_h: float,
        line_budget: float,
        total_budget: float,
        budgets: list[int],
        ocr_result,
        chosen: Optional[BubbleLinebreakEvaluation],
    ) -> None:
        lines = [
            f"模型目录状态: {'已找到' if chinese_linebreak_models_available() else '未找到，会回退普通换行'}",
            f"日志识别: {'是' if parsed_log else '否'}",
            f"方向: {'横排' if horizontal else '竖排'}",
            f"字号: {font_size}",
            f"目标行/列数: {target_segments}",
            f"OCR 框: {ocr_w:.1f} x {ocr_h:.1f}",
            f"气泡矩形: {bubble_w:.1f} x {bubble_h:.1f}",
            f"单行预算: {line_budget:.1f}",
            f"全文预算: {total_budget:.1f}",
            f"候选预算: {', '.join(str(v) for v in budgets)}",
        ]
        if parsed_log:
            detected = []
            for key in (
                "text",
                "horizontal",
                "font_size",
                "target_segments",
                "ocr_w",
                "ocr_h",
                "bubble_w",
                "bubble_h",
                "line_budget",
            ):
                if key in parsed_log:
                    detected.append(f"{key}={parsed_log[key]}")
            lines.extend(["", "日志字段:", "  " + "  ".join(detected)])
        lines.extend([
            "",
            "OCR 阶段:",
            f"  输出: {_display_text(ocr_result.text_with_br)}",
            f"  行数: {ocr_result.n_segments}",
            f"  required: {ocr_result.required_width:.1f} x {ocr_result.required_height:.1f}",
            "",
            "气泡候选选择:",
        ])
        if chosen is None:
            lines.append("  没有同目标行数候选。")
        else:
            lines.extend(
                [
                    f"  输出: {_display_text(chosen.text_with_br)}",
                    f"  行数: {chosen.n_segments}",
                    f"  required: {chosen.required_width:.1f} x {chosen.required_height:.1f}",
                    f"  fits: {chosen.fits}",
                    f"  overflow: {chosen.overflow_pixels}",
                ]
            )
        lines.extend(["", _semantic_tree_text(text)])
        self.summary_edit.setPlainText("\n".join(lines))

    def _fill_table(self, rows: list[CandidateRow], target_segments: int) -> None:
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            notes = []
            if not row.target_ok:
                notes.append(row.filter_reason or "行数不等于目标，过滤")
            elif row.fits:
                notes.append("可进气泡；不看 overflow")
            else:
                notes.append("同目标候选；先比成分代价，再比 overflow")
            if row.score is not None:
                notes.append(f"score={row.score}")

            values = [
                str(row.index),
                "OCR" if row.budget == 0 else str(row.budget),
                str(row.n_segments),
                "OK" if row.target_ok else f"!= {target_segments}",
                f"{row.required_width:.1f} x {row.required_height:.1f}",
                "yes" if row.fits else "no",
                str(row.semantic_penalty),
                str(row.overflow),
                str(row.score or ""),
                "YES" if row.selected else "",
                _display_text(row.text_with_br),
                "; ".join(notes),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if row.selected:
                    item.setBackground(QColor(206, 245, 219))
                elif not row.target_ok:
                    item.setBackground(QColor(240, 240, 240))
                elif not row.fits:
                    item.setBackground(QColor(255, 232, 214))
                self.table.setItem(row_index, col, item)
        self.table.resizeRowsToContents()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        setTheme(Theme.AUTO)
    except Exception:
        pass
    window = ChineseLinebreakDebugWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
