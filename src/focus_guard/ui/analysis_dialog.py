from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from focus_guard.storage import EventStore


@dataclass(frozen=True)
class RatioMetric:
    label: str
    count: int
    ratio: float


class LogAnalysisDialog(QDialog):
    def __init__(self, store: EventStore, parent=None) -> None:
        super().__init__(parent)
        self.store = store

        self.setWindowTitle("日志分析")
        self.resize(1080, 760)

        self._build_ui()
        self._reload_tasks()
        self._refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(16)

        header = QHBoxLayout()
        root.addLayout(header)

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        header.addLayout(title_box, 1)

        eyebrow = QLabel("本地检测数据")
        eyebrow.setObjectName("Eyebrow")
        title_box.addWidget(eyebrow)

        title = QLabel("日志分析")
        title.setObjectName("PageTitle")
        title_box.addWidget(title)

        self.range_combo = QComboBox()
        self.range_combo.addItem("最近 24 小时", 1)
        self.range_combo.addItem("最近 7 天", 7)
        self.range_combo.addItem("最近 30 天", 30)
        self.range_combo.addItem("全部", None)
        self.range_combo.currentIndexChanged.connect(self._refresh)
        header.addWidget(self.range_combo)

        self.task_combo = QComboBox()
        self.task_combo.setMinimumWidth(280)
        self.task_combo.currentIndexChanged.connect(self._refresh)
        header.addWidget(self.task_combo)

        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self._refresh)
        header.addWidget(refresh_button)

        export_button = QPushButton("导出 JSONL")
        export_button.clicked.connect(self._export_jsonl)
        header.addWidget(export_button)

        self.summary_row = QHBoxLayout()
        self.summary_row.setSpacing(12)
        root.addLayout(self.summary_row)

        self.total_value = QLabel("0")
        self.focus_rate_value = QLabel("0%")
        self.distraction_rate_value = QLabel("0%")
        self.false_positive_value = QLabel("0%")
        self.uncertain_value = QLabel("0%")

        self.summary_row.addWidget(self._metric_card("检测次数", self.total_value), 1)
        self.summary_row.addWidget(self._metric_card("专注率", self.focus_rate_value), 1)
        self.summary_row.addWidget(self._metric_card("分心率", self.distraction_rate_value), 1)
        self.summary_row.addWidget(self._metric_card("误判率", self.false_positive_value), 1)
        self.summary_row.addWidget(self._metric_card("不确定率", self.uncertain_value), 1)

        body = QHBoxLayout()
        body.setSpacing(16)
        root.addLayout(body, 1)

        left = QVBoxLayout()
        left.setSpacing(12)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(12)
        body.addLayout(right, 1)

        self.status_bars = self._bar_panel("状态分布")
        self.provider_bars = self._bar_panel("模型来源")
        self.process_bars = self._bar_panel("高频进程")
        left.addWidget(self.status_bars["frame"])
        left.addWidget(self.provider_bars["frame"])
        left.addWidget(self.process_bars["frame"])

        false_positive_title = QLabel("误判样本")
        false_positive_title.setObjectName("SectionTitle")
        right.addWidget(false_positive_title)
        self.false_positive_table = self._make_table(
            ["时间", "进程", "窗口", "用户说明"],
            stretch_column=2,
        )
        right.addWidget(self.false_positive_table, 1)

        distraction_title = QLabel("分心样本")
        distraction_title.setObjectName("SectionTitle")
        right.addWidget(distraction_title)
        self.distraction_table = self._make_table(
            ["时间", "置信度", "进程", "原因"],
            stretch_column=3,
        )
        right.addWidget(self.distraction_table, 1)

    def _reload_tasks(self) -> None:
        current = self.task_combo.currentData()
        self.task_combo.blockSignals(True)
        self.task_combo.clear()
        self.task_combo.addItem("全部任务", None)
        for description in self.store.list_event_tasks():
            label = description.replace("\n", " ")
            if len(label) > 42:
                label = f"{label[:42]}..."
            self.task_combo.addItem(label, description)
        if current is not None:
            index = self.task_combo.findData(current)
            if index >= 0:
                self.task_combo.setCurrentIndex(index)
        self.task_combo.blockSignals(False)

    def _refresh(self) -> None:
        since, task_description = self._current_filters()

        summary = self.store.analysis_summary(since=since, task_description=task_description)
        total = int(summary["total"])
        focused = int(summary["focused"])
        distracted = int(summary["distracted"])
        uncertain = int(summary["uncertain"])
        false_positive = int(summary["false_positive"])

        self.total_value.setText(str(total))
        self.focus_rate_value.setText(self._format_percent(focused, total))
        self.distraction_rate_value.setText(self._format_percent(distracted, total))
        self.false_positive_value.setText(self._format_percent(false_positive, distracted))
        self.uncertain_value.setText(self._format_percent(uncertain, total))

        self._fill_bars(
            self.status_bars,
            [
                RatioMetric("focused", focused, self._safe_ratio(focused, total)),
                RatioMetric("distracted", distracted, self._safe_ratio(distracted, total)),
                RatioMetric("uncertain", uncertain, self._safe_ratio(uncertain, total)),
            ],
        )
        self._fill_bars(
            self.provider_bars,
            self._rows_to_metrics(
                self.store.analysis_counts(
                    "provider",
                    since=since,
                    task_description=task_description,
                    limit=6,
                )
            ),
        )
        self._fill_bars(
            self.process_bars,
            self._rows_to_metrics(
                self.store.analysis_counts(
                    "process_name",
                    since=since,
                    task_description=task_description,
                    limit=8,
                )
            ),
        )
        self._fill_table(
            self.false_positive_table,
            self.store.analysis_false_positives(
                since=since,
                task_description=task_description,
                limit=12,
            ),
            ["captured_at", "process_name", "window_title", "feedback_note"],
        )
        self._fill_table(
            self.distraction_table,
            self.store.analysis_distractions(
                since=since,
                task_description=task_description,
                limit=12,
            ),
            ["captured_at", "confidence", "process_name", "reason"],
        )

    def _export_jsonl(self) -> None:
        since, task_description = self._current_filters()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"focus_guard_events_{timestamp}.jsonl"
        default_path = self.store.db_path.parent / "exports" / default_name
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "导出日志 JSONL",
            str(default_path),
            "JSON Lines (*.jsonl);;All Files (*)",
        )
        if not path_text:
            return

        output_path = Path(path_text)
        try:
            count = self.store.export_evaluation_jsonl(
                output_path=output_path,
                since=since,
                task_description=task_description,
            )
        except Exception as exc:  # noqa: BLE001 - GUI boundary should show storage errors.
            QMessageBox.warning(self, "导出失败", str(exc))
            return

        QMessageBox.information(
            self,
            "导出完成",
            f"已导出 {count} 条日志。\n\n{output_path}",
        )

    def _current_filters(self) -> tuple[datetime | None, str | None]:
        days = self.range_combo.currentData()
        since = (
            datetime.now().astimezone() - timedelta(days=int(days))
            if days is not None
            else None
        )
        task_description = self.task_combo.currentData()
        return since, task_description

    def _metric_card(self, label: str, value_label: QLabel) -> QFrame:
        card = QFrame()
        card.setObjectName("MetricCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        label_widget = QLabel(label)
        label_widget.setObjectName("MetricLabel")
        layout.addWidget(label_widget)

        value_label.setObjectName("MetricValue")
        layout.addWidget(value_label)
        return card

    def _bar_panel(self, title: str) -> dict[str, object]:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        layout.addWidget(title_label)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        layout.addLayout(grid)
        return {"frame": frame, "grid": grid}

    def _fill_bars(self, panel: dict[str, object], metrics: list[RatioMetric]) -> None:
        grid = panel["grid"]
        assert isinstance(grid, QGridLayout)
        self._clear_layout(grid)

        if not metrics:
            empty = QLabel("暂无数据")
            empty.setObjectName("Muted")
            grid.addWidget(empty, 0, 0, 1, 3)
            return

        for row, metric in enumerate(metrics):
            label = QLabel(metric.label or "未知")
            label.setMinimumWidth(88)
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(round(metric.ratio * 100))
            progress.setTextVisible(False)
            value = QLabel(f"{metric.count} · {metric.ratio * 100:.1f}%")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            grid.addWidget(label, row, 0)
            grid.addWidget(progress, row, 1)
            grid.addWidget(value, row, 2)

    @staticmethod
    def _clear_layout(layout: QGridLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _make_table(headers: list[str], stretch_column: int) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(stretch_column, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        return table

    def _fill_table(
        self,
        table: QTableWidget,
        rows: list[sqlite3.Row],
        columns: list[str],
    ) -> None:
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, column in enumerate(columns):
                value = row[column]
                if column == "captured_at" and value:
                    value = str(value)[5:19].replace("T", " ")
                elif column == "confidence" and value is not None:
                    value = f"{float(value):.2f}"
                table.setItem(row_index, column_index, QTableWidgetItem(str(value or "")))

    @staticmethod
    def _format_percent(count: int, total: int) -> str:
        if total <= 0:
            return "0%"
        return f"{count / total * 100:.1f}%"

    @staticmethod
    def _safe_ratio(count: int, total: int) -> float:
        if total <= 0:
            return 0.0
        return count / total

    @staticmethod
    def _rows_to_metrics(rows: list[sqlite3.Row]) -> list[RatioMetric]:
        total = sum(int(row["count"]) for row in rows)
        return [
            RatioMetric(
                label=str(row["label"] or "未知"),
                count=int(row["count"]),
                ratio=LogAnalysisDialog._safe_ratio(int(row["count"]), total),
            )
            for row in rows
        ]
