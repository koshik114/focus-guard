from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QMenu,
    QApplication,
    QStyle,
)

from focus_guard.config import AppConfig
from focus_guard.models import DetectionEvent, FeedbackType, FocusStatus, FocusTask
from focus_guard.services.detector import FocusDetector
from focus_guard.services.llm import LlmRouter
from focus_guard.services.ocr import OcrEngine, build_ocr_engine
from focus_guard.storage import EventStore
from focus_guard.ui.reminder_dialog import ReminderDialog


class DetectionWorker(QThread):
    finished_event = Signal(object)
    failed = Signal(str)

    def __init__(self, detector: FocusDetector, task: FocusTask) -> None:
        super().__init__()
        self.detector = detector
        self.task = task

    def run(self) -> None:
        try:
            self.finished_event.emit(self.detector.check(self.task))
        except Exception as exc:  # noqa: BLE001 - GUI boundary should surface all errors.
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, store: EventStore) -> None:
        super().__init__()
        self.config = config
        self.store = store
        self.ocr_engine: OcrEngine | None = None
        self.detector: FocusDetector | None = None
        self.current_task: FocusTask | None = None
        self.session_ends_at: datetime | None = None
        self.pause_until: datetime | None = None
        self.worker: DetectionWorker | None = None

        self.setWindowTitle("Focus Guard")
        self.resize(1120, 720)

        self.timer = QTimer(self)
        self.timer.setInterval(self.config.check_interval_seconds * 1000)
        self.timer.timeout.connect(self._run_detection)

        self._build_ui()
        self._build_tray()
        self._refresh_recent_events()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(22, 22, 22, 22)
        root_layout.setSpacing(18)
        self.setCentralWidget(root)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(248)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(20, 20, 20, 20)
        sidebar_layout.setSpacing(16)

        title = QLabel("Focus Guard")
        title.setObjectName("AppTitle")
        sidebar_layout.addWidget(title)

        subtitle = QLabel("本地优先的专注状态检测器")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        sidebar_layout.addWidget(subtitle)

        local_badge = QLabel("OCR + Ollama + 视觉兜底")
        local_badge.setObjectName("SoftBadge")
        sidebar_layout.addWidget(local_badge)
        sidebar_layout.addSpacing(10)

        self.status_label = QLabel("未开始")
        self.status_label.setObjectName("StatusBadgeIdle")
        sidebar_layout.addWidget(self.status_label)

        self.model_label = QLabel(
            f"模型\n{self.config.ollama_model}\n\n"
            f"OCR\n{self.config.ocr_engine}\n\n"
            f"视觉模式\n{self.config.vision_mode}"
        )
        self.model_label.setObjectName("Muted")
        self.model_label.setWordWrap(True)
        sidebar_layout.addWidget(self.model_label)
        sidebar_layout.addStretch()

        tray_hint = QLabel("关闭窗口会最小化到托盘")
        tray_hint.setObjectName("Muted")
        tray_hint.setWordWrap(True)
        sidebar_layout.addWidget(tray_hint)

        root_layout.addWidget(sidebar)

        content = QVBoxLayout()
        content.setSpacing(18)
        root_layout.addLayout(content, 1)

        header = QFrame()
        header.setObjectName("HeaderBand")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(2, 0, 2, 0)
        header_layout.setSpacing(4)
        content.addWidget(header)

        eyebrow = QLabel("本次会话")
        eyebrow.setObjectName("Eyebrow")
        header_layout.addWidget(eyebrow)

        headline = QLabel("设定当前任务，然后让 Focus Guard 按周期验证前台工作内容")
        headline.setObjectName("PageTitle")
        headline.setWordWrap(True)
        header_layout.addWidget(headline)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        content.addLayout(metrics)

        self.metric_state_value = QLabel("未开始")
        metrics.addWidget(self._make_metric_card("状态", self.metric_state_value), 1)

        self.metric_model_value = QLabel(self.config.ollama_model)
        metrics.addWidget(self._make_metric_card("本地模型", self.metric_model_value), 1)

        self.metric_vision_value = QLabel(self.config.vision_mode)
        metrics.addWidget(self._make_metric_card("视觉输入", self.metric_vision_value), 1)

        self.metric_next_value = QLabel(f"{self.config.check_interval_seconds}s")
        metrics.addWidget(self._make_metric_card("检测周期", self.metric_next_value), 1)

        session_panel = QFrame()
        session_panel.setObjectName("Panel")
        session_layout = QGridLayout(session_panel)
        session_layout.setContentsMargins(20, 18, 20, 18)
        session_layout.setHorizontalSpacing(14)
        session_layout.setVerticalSpacing(12)
        content.addWidget(session_panel)

        session_title = QLabel("任务设置")
        session_title.setObjectName("SectionTitle")
        session_layout.addWidget(session_title, 0, 0, 1, 4)

        self.task_edit = QTextEdit()
        self.task_edit.setPlaceholderText("输入本次任务，例如：完成 Focus Guard 的 OCR 和模型判断模块")
        self.task_edit.setFixedHeight(92)
        session_layout.addWidget(self.task_edit, 1, 0, 1, 4)

        self.duration_enabled = QCheckBox("设置持续时间")
        session_layout.addWidget(self.duration_enabled, 2, 0)

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(5, 480)
        self.duration_spin.setValue(60)
        self.duration_spin.setSuffix(" 分钟")
        session_layout.addWidget(self.duration_spin, 2, 1)

        self.interval_label = QLabel(f"每 {self.config.check_interval_seconds} 秒检测一次")
        self.interval_label.setObjectName("Muted")
        session_layout.addWidget(self.interval_label, 2, 2)

        self.start_button = QPushButton("开始")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self._start_session)
        session_layout.addWidget(self.start_button, 3, 0)

        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self._stop_session)
        self.stop_button.setEnabled(False)
        session_layout.addWidget(self.stop_button, 3, 1)

        self.check_now_button = QPushButton("立即检测")
        self.check_now_button.clicked.connect(self._run_detection)
        self.check_now_button.setEnabled(False)
        session_layout.addWidget(self.check_now_button, 3, 2)

        status_panel = QFrame()
        status_panel.setObjectName("Panel")
        status_layout = QGridLayout(status_panel)
        status_layout.setContentsMargins(20, 18, 20, 18)
        status_layout.setVerticalSpacing(12)
        content.addWidget(status_panel)

        status_title = QLabel("当前检测结果")
        status_title.setObjectName("SectionTitle")
        status_layout.addWidget(status_title, 0, 0)

        self.last_result_label = QLabel("尚无检测结果")
        self.last_result_label.setObjectName("ResultText")
        self.last_result_label.setWordWrap(True)
        status_layout.addWidget(self.last_result_label, 1, 0)

        self.ocr_preview = QPlainTextEdit()
        self.ocr_preview.setPlaceholderText("OCR 文本只保存在本地日志中，可用于后续误判分析和微调数据整理。")
        self.ocr_preview.setReadOnly(True)
        self.ocr_preview.setFixedHeight(120)
        status_layout.addWidget(self.ocr_preview, 2, 0)

        table_title = QLabel("检测日志")
        table_title.setObjectName("SectionTitle")
        content.addWidget(table_title)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["时间", "状态", "置信度", "来源", "进程", "窗口标题", "反馈"])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        content.addWidget(self.table, 1)

    def _make_metric_card(self, label: str, value_label: QLabel) -> QFrame:
        card = QFrame()
        card.setObjectName("MetricCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        label_widget = QLabel(label)
        label_widget.setObjectName("MetricLabel")
        layout.addWidget(label_widget)

        value_label.setObjectName("MetricValue")
        value_label.setWordWrap(True)
        layout.addWidget(value_label)
        return card

    def _set_state(self, text: str, badge_style: str = "StatusBadgeIdle") -> None:
        self.status_label.setText(text)
        self.status_label.setObjectName(badge_style)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.metric_state_value.setText(text)

    def _build_tray(self) -> None:
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Focus Guard")

        menu = QMenu()
        show_action = QAction("显示 Focus Guard", self)
        show_action.triggered.connect(self._restore_from_tray)
        menu.addAction(show_action)

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._restore_from_tray()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        self.tray.show()

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.hide()
        self.tray.showMessage("Focus Guard", "已最小化到系统托盘，检测会继续运行。")

    def _restore_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _start_session(self) -> None:
        description = self.task_edit.toPlainText().strip()
        if not description:
            self._set_state("请先输入任务", "StatusBadgeWarn")
            return

        duration = self.duration_spin.value() if self.duration_enabled.isChecked() else None
        self.current_task = FocusTask(description=description, duration_minutes=duration)
        self.session_ends_at = (
            datetime.now().astimezone() + timedelta(minutes=duration) if duration else None
        )
        self.pause_until = None

        try:
            self.ocr_engine = build_ocr_engine(self.config.ocr_engine)
        except Exception as exc:  # noqa: BLE001
            self._set_state("OCR 初始化失败", "StatusBadgeWarn")
            self.last_result_label.setText(str(exc))
            return

        self.detector = FocusDetector(
            ocr_engine=self.ocr_engine,
            llm_router=LlmRouter(self.config),
        )

        self.timer.start()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.check_now_button.setEnabled(True)
        self._set_state("已开始", "StatusBadgeActive")
        self.last_result_label.setText(
            "任务已开始。请切换到目标任务窗口；首次自动检测将在下一个周期执行。"
        )

    def _stop_session(self) -> None:
        self.timer.stop()
        self.current_task = None
        self.session_ends_at = None
        self.pause_until = None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.check_now_button.setEnabled(False)
        self._set_state("已停止", "StatusBadgeIdle")

    def _run_detection(self) -> None:
        if self.current_task is None or self.detector is None:
            return
        if self.worker and self.worker.isRunning():
            return

        now = datetime.now().astimezone()
        if self.session_ends_at and now >= self.session_ends_at:
            self._stop_session()
            self._set_state("任务已结束", "StatusBadgeIdle")
            return
        if self.pause_until and now < self.pause_until:
            self._set_state(f"暂停到 {self.pause_until.strftime('%H:%M')}", "StatusBadgeWarn")
            return

        self._set_state("检测中...", "StatusBadgeActive")
        self.worker = DetectionWorker(self.detector, self.current_task)
        self.worker.finished_event.connect(self._handle_detection_event)
        self.worker.failed.connect(self._handle_detection_error)
        self.worker.start()

    def _handle_detection_event(self, event: DetectionEvent) -> None:
        if event.judgment.status is FocusStatus.DISTRACTED:
            dialog = ReminderDialog(event, self)
            dialog.exec()
            result = dialog.reminder_result
            if result:
                self.store.add_feedback(event, result.feedback, result.note)
                if result.feedback is FeedbackType.PAUSED:
                    self.pause_until = datetime.now().astimezone() + timedelta(minutes=5)
        else:
            self.store.add_event(event)

        status_text = {
            FocusStatus.FOCUSED: "专注",
            FocusStatus.DISTRACTED: "可能分心",
            FocusStatus.UNCERTAIN: "不确定",
        }[event.judgment.status]
        status_style = {
            FocusStatus.FOCUSED: "StatusBadgeFocused",
            FocusStatus.DISTRACTED: "StatusBadgeDistracted",
            FocusStatus.UNCERTAIN: "StatusBadgeWarn",
        }[event.judgment.status]
        self._set_state(status_text, status_style)
        vision_text = "视觉已用" if event.judgment.used_vision else "文本/OCR"
        self.metric_vision_value.setText(vision_text)
        self.last_result_label.setText(
            f"{status_text} | {event.judgment.confidence:.2f} | "
            f"{event.judgment.provider}\n{event.judgment.reason}"
        )
        self.ocr_preview.setPlainText(event.ocr.text[:3000])
        self._refresh_recent_events()

    def _handle_detection_error(self, message: str) -> None:
        self._set_state("检测失败", "StatusBadgeWarn")
        self.last_result_label.setText(message)

    def _refresh_recent_events(self) -> None:
        rows = self.store.list_recent(30)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row["captured_at"][11:19] if row["captured_at"] else "",
                row["status"],
                f"{row['confidence']:.2f}",
                f"{row['provider']}{' / vision' if row['vision_used'] else ''}",
                row["process_name"] or "",
                row["window_title"] or "",
                row["user_feedback"] or "",
            ]
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(str(value)))
