from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from focus_guard.models import DetectionEvent, FeedbackType


@dataclass(frozen=True)
class ReminderResult:
    feedback: FeedbackType
    note: str | None


class ReminderDialog(QDialog):
    def __init__(self, event: DetectionEvent, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Focus Guard Reminder")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setMinimumWidth(520)
        self._result: ReminderResult | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        title = QLabel("检测到可能偏离任务")
        title.setObjectName("AppTitle")
        layout.addWidget(title)

        task = QLabel(f"当前任务：{event.task.description}")
        task.setWordWrap(True)
        layout.addWidget(task)

        reason = QLabel(
            f"判断：{event.judgment.status.value}，置信度 {event.judgment.confidence:.2f}\n"
            f"原因：{event.judgment.reason}"
        )
        reason.setObjectName("Muted")
        reason.setWordWrap(True)
        layout.addWidget(reason)

        context = QLabel(
            f"窗口：{event.window.window_title or '(无标题)'}\n"
            f"进程：{event.window.process_name}\n"
            f"模型：{event.judgment.provider}"
        )
        context.setWordWrap(True)
        layout.addWidget(context)

        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("如果这是误判，可以写一段简短评价，用于后续分析和微调数据整理。")
        self.note_edit.setFixedHeight(92)
        layout.addWidget(self.note_edit)

        buttons = QHBoxLayout()
        layout.addLayout(buttons)

        distracted_btn = QPushButton("我确实分心了")
        distracted_btn.setObjectName("DangerButton")
        distracted_btn.clicked.connect(
            lambda: self._finish(FeedbackType.CONFIRMED_DISTRACTION)
        )
        buttons.addWidget(distracted_btn)

        false_positive_btn = QPushButton("这是误判")
        false_positive_btn.clicked.connect(lambda: self._finish(FeedbackType.FALSE_POSITIVE))
        buttons.addWidget(false_positive_btn)

        pause_btn = QPushButton("暂停 5 分钟")
        pause_btn.clicked.connect(lambda: self._finish(FeedbackType.PAUSED))
        buttons.addWidget(pause_btn)

    @property
    def reminder_result(self) -> ReminderResult | None:
        return self._result

    def reject(self) -> None:
        # Force an explicit classification instead of allowing Escape/close to skip feedback.
        return

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()

    def _finish(self, feedback: FeedbackType) -> None:
        note = self.note_edit.toPlainText().strip()
        self._result = ReminderResult(feedback=feedback, note=note or None)
        self.accept()
