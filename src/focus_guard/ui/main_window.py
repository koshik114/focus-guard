from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
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

from focus_guard.config import AppConfig, write_env_settings
from focus_guard.models import (
    DetectionEvent,
    FeedbackType,
    FocusStatus,
    FocusTask,
    TaskTemplate,
)
from focus_guard.services.detector import FocusDetector
from focus_guard.services.llm import LlmRouter, summarize_false_positive_guidance
from focus_guard.services.ocr import OcrEngine, build_ocr_engine
from focus_guard.storage import EventStore
from focus_guard.ui.reminder_dialog import ReminderDialog
from focus_guard.ui.settings_dialog import SettingsDialog


SIMILAR_FALSE_POSITIVE_COOLDOWN_MINUTES = 15


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


class DeepSeekReviewWorker(QThread):
    finished_review = Signal(object, object, object)

    def __init__(self, router: LlmRouter, event: DetectionEvent, note: str | None) -> None:
        super().__init__()
        self.router = router
        self.event = event
        self.note = note

    def run(self) -> None:
        judgment = self.router.review_with_deepseek(
            task=self.event.task,
            window=self.event.window,
            ocr=self.event.ocr,
        )
        self.finished_review.emit(self.event, judgment, self.note)


class FalsePositiveSummaryWorker(QThread):
    finished_summary = Signal(bool, str)

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        task_description: str,
        examples: tuple[str, ...],
    ) -> None:
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.task_description = task_description
        self.examples = examples

    def run(self) -> None:
        try:
            summary = summarize_false_positive_guidance(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
                task_description=self.task_description,
                examples=self.examples,
            )
        except Exception as exc:  # noqa: BLE001 - GUI boundary should surface all failures.
            self.finished_summary.emit(False, str(exc))
            return
        self.finished_summary.emit(True, summary)


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
        self.suppressed_reminder_signature: tuple[str, str, str] | None = None
        self.suppressed_reminder_until: datetime | None = None
        self.next_detection_at: datetime | None = None
        self.worker: DetectionWorker | None = None
        self.deepseek_review_worker: DeepSeekReviewWorker | None = None
        self.false_positive_summary_worker: FalsePositiveSummaryWorker | None = None
        self.false_positive_summary_task_description: str | None = None
        self.false_positive_summary_user_requested = False
        self.task_templates: dict[int, TaskTemplate] = {}

        self.setWindowTitle("Focus Guard")
        self.resize(1180, 820)

        self.timer = QTimer(self)
        self.timer.setInterval(self.config.check_interval_seconds * 1000)
        self.timer.timeout.connect(self._run_detection)

        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._update_countdown)
        self.countdown_timer.start()

        self._build_ui()
        self._build_tray()
        self._refresh_recent_events()
        self._refresh_task_templates()

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

        self.settings_button = QPushButton("设置")
        self.settings_button.clicked.connect(self._open_settings)
        sidebar_layout.addWidget(self.settings_button)
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
        metrics.addWidget(self._make_metric_card("下次检测", self.metric_next_value), 1)

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

        self.allowed_processes_edit = QLineEdit()
        self.allowed_processes_edit.setPlaceholderText("允许进程，例如：Code.exe, chrome.exe")
        session_layout.addWidget(self.allowed_processes_edit, 2, 0, 1, 2)

        self.focus_keywords_edit = QLineEdit()
        self.focus_keywords_edit.setPlaceholderText("专注关键词，例如：Focus Guard, Codex, PySide")
        session_layout.addWidget(self.focus_keywords_edit, 2, 2, 1, 2)

        self.correction_summary_edit = QLineEdit()
        self.correction_summary_edit.setPlaceholderText("纠错规则摘要，可由 DeepSeek 根据误判说明归纳")
        session_layout.addWidget(self.correction_summary_edit, 3, 0, 1, 3)

        self.summarize_false_positive_button = QPushButton("归纳误判规则")
        self.summarize_false_positive_button.clicked.connect(self._summarize_false_positive_rules)
        session_layout.addWidget(self.summarize_false_positive_button, 3, 3)

        self.template_combo = QComboBox()
        self.template_combo.setMinimumContentsLength(28)
        session_layout.addWidget(self.template_combo, 4, 0)

        self.use_template_button = QPushButton("使用")
        self.use_template_button.clicked.connect(self._use_selected_template)
        session_layout.addWidget(self.use_template_button, 4, 1)

        self.save_template_button = QPushButton("保存模板")
        self.save_template_button.clicked.connect(self._save_current_template)
        session_layout.addWidget(self.save_template_button, 4, 2)

        self.delete_template_button = QPushButton("删除")
        self.delete_template_button.clicked.connect(self._delete_selected_template)
        session_layout.addWidget(self.delete_template_button, 4, 3)

        self.duration_enabled = QCheckBox("设置持续时间")
        session_layout.addWidget(self.duration_enabled, 5, 0)

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(5, 480)
        self.duration_spin.setValue(60)
        self.duration_spin.setSuffix(" 分钟")
        session_layout.addWidget(self.duration_spin, 5, 1)

        self.interval_label = QLabel(f"每 {self.config.check_interval_seconds} 秒检测一次")
        self.interval_label.setObjectName("Muted")
        session_layout.addWidget(self.interval_label, 5, 2, 1, 2)

        self.start_button = QPushButton("开始")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self._start_session)
        session_layout.addWidget(self.start_button, 6, 0)

        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self._stop_session)
        self.stop_button.setEnabled(False)
        session_layout.addWidget(self.stop_button, 6, 1)

        self.check_now_button = QPushButton("立即检测")
        self.check_now_button.clicked.connect(self._run_detection)
        self.check_now_button.setEnabled(False)
        session_layout.addWidget(self.check_now_button, 6, 2)

        status_panel = QFrame()
        status_panel.setObjectName("Panel")
        status_panel.setMaximumHeight(250)
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
        self.ocr_preview.setFixedHeight(88)
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
        self.table.setMinimumHeight(260)
        content.addWidget(self.table, 3)

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

    def _update_config_labels(self) -> None:
        self.model_label.setText(
            f"模型\n{self.config.ollama_model}\n\n"
            f"OCR\n{self.config.ocr_engine}\n\n"
            f"视觉模式\n{self.config.vision_mode}"
        )
        self.metric_model_value.setText(self.config.ollama_model)
        self.metric_vision_value.setText(self.config.vision_mode)
        self.metric_next_value.setText(f"{self.config.check_interval_seconds}s")
        self.interval_label.setText(f"每 {self.config.check_interval_seconds} 秒检测一次")
        if self.current_task is not None:
            self._schedule_next_detection()

    def _schedule_next_detection(self) -> None:
        self.next_detection_at = datetime.now().astimezone() + timedelta(
            seconds=self.config.check_interval_seconds
        )
        self._update_countdown()

    def _update_countdown(self) -> None:
        now = datetime.now().astimezone()
        if self.current_task is None:
            self.metric_next_value.setText(f"{self.config.check_interval_seconds}s")
            return
        if self.pause_until and now < self.pause_until:
            self.metric_next_value.setText(f"暂停 {self._format_remaining(self.pause_until - now)}")
            return
        if self.worker and self.worker.isRunning():
            self.metric_next_value.setText("检测中")
            return
        if self.next_detection_at is None:
            self._schedule_next_detection()
            return

        remaining = self.next_detection_at - now
        if remaining.total_seconds() <= 0:
            self.metric_next_value.setText("即将检测")
            return
        self.metric_next_value.setText(self._format_remaining(remaining))

    @staticmethod
    def _format_remaining(delta: timedelta) -> str:
        total_seconds = max(0, int(delta.total_seconds()))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _refresh_task_templates(self) -> None:
        templates = self.store.list_task_templates(40)
        self.task_templates = {template.id: template for template in templates}

        self.template_combo.clear()
        if not templates:
            self.template_combo.addItem("暂无历史任务或模板", None)
            self.use_template_button.setEnabled(False)
            self.delete_template_button.setEnabled(False)
            return

        self.template_combo.addItem("选择历史任务或模板", None)
        for template in templates:
            self.template_combo.addItem(self._format_template_label(template), template.id)
        self.use_template_button.setEnabled(True)
        self.delete_template_button.setEnabled(True)

    @staticmethod
    def _format_template_label(template: TaskTemplate) -> str:
        duration = (
            f"{template.default_duration_minutes} 分钟"
            if template.default_duration_minutes
            else "不限时"
        )
        description = template.description.replace("\n", " ")
        if len(description) > 42:
            description = f"{description[:42]}..."
        return f"{description} · {duration} · {template.use_count} 次"

    @staticmethod
    def _parse_rule_text(text: str) -> tuple[str, ...]:
        items: list[str] = []
        for raw in text.replace("，", ",").replace("\n", ",").split(","):
            cleaned = raw.strip()
            if cleaned and cleaned not in items:
                items.append(cleaned)
        return tuple(items)

    @staticmethod
    def _format_rule_text(values: tuple[str, ...]) -> str:
        return ", ".join(values)

    def _selected_template(self) -> TaskTemplate | None:
        template_id = self.template_combo.currentData()
        if template_id is None:
            return None
        return self.task_templates.get(int(template_id))

    def _use_selected_template(self) -> None:
        template = self._selected_template()
        if template is None:
            self._set_state("请选择任务模板", "StatusBadgeWarn")
            return

        self.task_edit.setPlainText(template.description)
        self.allowed_processes_edit.setText(self._format_rule_text(template.allowed_processes))
        self.focus_keywords_edit.setText(self._format_rule_text(template.focus_keywords))
        self.correction_summary_edit.setText(template.correction_summary or "")
        if template.default_duration_minutes is None:
            self.duration_enabled.setChecked(False)
        else:
            self.duration_enabled.setChecked(True)
            self.duration_spin.setValue(template.default_duration_minutes)
        self.last_result_label.setText("已填入历史任务。")

    def _save_current_template(self) -> None:
        description = self.task_edit.toPlainText().strip()
        if not description:
            self._set_state("请先输入任务", "StatusBadgeWarn")
            return

        duration = self.duration_spin.value() if self.duration_enabled.isChecked() else None
        allowed_processes = self._parse_rule_text(self.allowed_processes_edit.text())
        focus_keywords = self._parse_rule_text(self.focus_keywords_edit.text())
        correction_summary = self.correction_summary_edit.text().strip() or None
        try:
            self.store.upsert_task_template(
                description,
                duration,
                allowed_processes,
                focus_keywords,
                correction_summary,
            )
        except Exception as exc:  # noqa: BLE001 - GUI boundary should surface storage errors.
            QMessageBox.warning(self, "保存模板失败", str(exc))
            return

        self._refresh_task_templates()
        self.last_result_label.setText("任务模板已保存。")

    def _delete_selected_template(self) -> None:
        template = self._selected_template()
        if template is None:
            self._set_state("请选择任务模板", "StatusBadgeWarn")
            return

        result = QMessageBox.question(
            self,
            "删除任务模板",
            f"确定删除这个任务模板吗？\n\n{template.description}",
        )
        if result != QMessageBox.StandardButton.Yes:
            return

        self.store.delete_task_template(template.id)
        self._refresh_task_templates()
        self.last_result_label.setText("任务模板已删除。")

    def _summarize_false_positive_rules(self) -> None:
        description = self.task_edit.toPlainText().strip()
        self._start_false_positive_summary(description, user_requested=True)

    def _start_false_positive_summary(
        self,
        description: str,
        user_requested: bool,
    ) -> None:
        if not description:
            if user_requested:
                self._set_state("请先输入任务", "StatusBadgeWarn")
            return
        if not self.config.deepseek_api_key:
            if user_requested:
                self.last_result_label.setText("DeepSeek API Key 未配置，无法归纳误判规则。")
            return
        if self.false_positive_summary_worker and self.false_positive_summary_worker.isRunning():
            if user_requested:
                self.last_result_label.setText("已有误判规则归纳任务正在运行。")
            return

        examples = self.store.list_false_positive_guidance(description, limit=10)
        if not examples:
            if user_requested:
                self.last_result_label.setText("当前任务还没有可归纳的用户误判说明。")
            return

        self.false_positive_summary_task_description = description
        self.false_positive_summary_user_requested = user_requested
        self.summarize_false_positive_button.setEnabled(False)
        self.false_positive_summary_worker = FalsePositiveSummaryWorker(
            api_key=self.config.deepseek_api_key,
            base_url=self.config.deepseek_base_url,
            model=self.config.deepseek_model,
            task_description=description,
            examples=examples,
        )
        self.false_positive_summary_worker.finished_summary.connect(
            self._handle_false_positive_summary
        )
        self.false_positive_summary_worker.start()
        self.last_result_label.setText("正在调用 DeepSeek 归纳误判规则。")

    def _handle_false_positive_summary(self, ok: bool, message: str) -> None:
        self.summarize_false_positive_button.setEnabled(True)
        description = self.false_positive_summary_task_description
        user_requested = self.false_positive_summary_user_requested
        self.false_positive_summary_task_description = None
        self.false_positive_summary_user_requested = False

        if not ok:
            if user_requested:
                QMessageBox.warning(self, "归纳误判规则失败", message)
            else:
                self.last_result_label.setText(f"任务结束后的误判规则归纳失败：{message}")
            return

        result = QMessageBox.question(
            self,
            "保存误判规则摘要",
            f"DeepSeek 归纳出以下规则，是否保存到当前任务模板？\n\n{message}",
        )
        if result != QMessageBox.StandardButton.Yes:
            self.last_result_label.setText("误判规则摘要未保存。")
            return

        self.correction_summary_edit.setText(message)
        if description:
            self.store.update_task_template_correction_summary(description, message)
            self._refresh_task_templates()
            if self.current_task and self.current_task.description == description:
                self.current_task = replace(self.current_task, correction_summary=message)
        self.last_result_label.setText("误判规则摘要已保存到任务模板。")

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if not dialog.exec():
            return

        try:
            write_env_settings(self.config.env_path, dialog.values().to_env())
            self.config = AppConfig.from_env()
            self.timer.setInterval(self.config.check_interval_seconds * 1000)
            self._update_config_labels()
            self._rebuild_detector_if_running()
        except Exception as exc:  # noqa: BLE001 - settings save should surface all failures.
            QMessageBox.warning(self, "设置保存失败", str(exc))
            return

        self.last_result_label.setText("设置已保存，并已应用到当前运行实例。")

    def _rebuild_detector_if_running(self) -> None:
        if self.current_task is None:
            return
        self.ocr_engine = build_ocr_engine(self.config.ocr_engine)
        self.detector = FocusDetector(
            ocr_engine=self.ocr_engine,
            llm_router=LlmRouter(self.config),
        )

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
        allowed_processes = self._parse_rule_text(self.allowed_processes_edit.text())
        focus_keywords = self._parse_rule_text(self.focus_keywords_edit.text())
        correction_summary = self.correction_summary_edit.text().strip() or None
        feedback_guidance = self.store.list_false_positive_guidance(description)
        self.current_task = FocusTask(
            description=description,
            duration_minutes=duration,
            allowed_processes=allowed_processes,
            focus_keywords=focus_keywords,
            correction_summary=correction_summary,
            feedback_guidance=feedback_guidance,
        )
        self.session_ends_at = (
            datetime.now().astimezone() + timedelta(minutes=duration) if duration else None
        )
        self.pause_until = None
        self.suppressed_reminder_signature = None
        self.suppressed_reminder_until = None
        self.next_detection_at = None

        try:
            self.store.record_task_used(
                description,
                duration,
                allowed_processes,
                focus_keywords,
                correction_summary,
            )
            self._refresh_task_templates()
        except Exception as exc:  # noqa: BLE001
            self.last_result_label.setText(f"任务历史记录失败：{exc}")

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
        self._schedule_next_detection()
        self.last_result_label.setText(
            "任务已开始。请切换到目标任务窗口；首次自动检测将在下一个周期执行。"
        )

    def _stop_session(self) -> None:
        ending_task_description = self.current_task.description if self.current_task else ""
        self._start_false_positive_summary(ending_task_description, user_requested=False)
        self.timer.stop()
        self.current_task = None
        self.session_ends_at = None
        self.pause_until = None
        self.suppressed_reminder_signature = None
        self.suppressed_reminder_until = None
        self.next_detection_at = None
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
        self.next_detection_at = None
        self.timer.start(self.config.check_interval_seconds * 1000)
        self.worker = DetectionWorker(self.detector, self.current_task)
        self.worker.finished_event.connect(self._handle_detection_event)
        self.worker.failed.connect(self._handle_detection_error)
        self.worker.start()

    def _handle_detection_event(self, event: DetectionEvent) -> None:
        deepseek_review_request: tuple[DetectionEvent, str | None] | None = None
        if event.judgment.status is FocusStatus.DISTRACTED:
            signature = self._reminder_signature(event)
            now = datetime.now().astimezone()
            if self._is_similar_reminder_suppressed(signature, now):
                self.store.add_event(replace(event, reminder_shown=False))
            else:
                dialog = ReminderDialog(event, self)
                dialog.exec()
                result = dialog.reminder_result
                if result:
                    self.store.add_feedback(event, result.feedback, result.note)
                    if result.feedback is FeedbackType.PAUSED:
                        self.pause_until = now + timedelta(minutes=5)
                    elif result.feedback is FeedbackType.FALSE_POSITIVE:
                        self._suppress_similar_reminders(signature, now)
                        self._refresh_current_feedback_guidance()
                        deepseek_review_request = (event, result.note)
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
        if self.current_task is not None:
            self._schedule_next_detection()
        if deepseek_review_request is not None:
            self._start_deepseek_review(*deepseek_review_request)

    @staticmethod
    def _reminder_signature(event: DetectionEvent) -> tuple[str, str, str]:
        return (
            event.task.description.strip().lower(),
            event.window.process_name.strip().lower(),
            event.window.window_title.strip().lower(),
        )

    def _is_similar_reminder_suppressed(
        self,
        signature: tuple[str, str, str],
        now: datetime,
    ) -> bool:
        return (
            self.suppressed_reminder_signature == signature
            and self.suppressed_reminder_until is not None
            and now < self.suppressed_reminder_until
        )

    def _suppress_similar_reminders(
        self,
        signature: tuple[str, str, str],
        now: datetime,
    ) -> None:
        self.suppressed_reminder_signature = signature
        self.suppressed_reminder_until = now + timedelta(
            minutes=SIMILAR_FALSE_POSITIVE_COOLDOWN_MINUTES
        )

    def _refresh_current_feedback_guidance(self) -> None:
        if self.current_task is None:
            return
        self.current_task = replace(
            self.current_task,
            feedback_guidance=self.store.list_false_positive_guidance(
                self.current_task.description
            ),
        )

    def _start_deepseek_review(self, event: DetectionEvent, note: str | None) -> None:
        if not self.config.deepseek_api_key:
            self.last_result_label.setText(
                "误判已记录；DeepSeek API Key 未配置，未进行云端复核。"
            )
            return
        if self.deepseek_review_worker and self.deepseek_review_worker.isRunning():
            self.last_result_label.setText("误判已记录；已有 DeepSeek 复核任务正在运行。")
            return

        self.deepseek_review_worker = DeepSeekReviewWorker(
            router=LlmRouter(self.config),
            event=event,
            note=note,
        )
        self.deepseek_review_worker.finished_review.connect(self._handle_deepseek_review)
        self.deepseek_review_worker.start()
        self.last_result_label.setText("误判已记录；正在调用 DeepSeek 复核本次判断。")

    def _handle_deepseek_review(
        self,
        original_event: DetectionEvent,
        judgment,
        note: str | None,
    ) -> None:
        feedback_note = "DeepSeek 误判复核"
        if note:
            feedback_note = f"{feedback_note}；用户说明：{note}"
        review_event = replace(
            original_event,
            judgment=judgment,
            reminder_shown=False,
            user_feedback=FeedbackType.FALSE_POSITIVE,
            feedback_note=feedback_note,
        )
        self.store.add_event(review_event)
        self.last_result_label.setText(
            f"DeepSeek 复核完成：{judgment.status.value} | "
            f"{judgment.confidence:.2f}\n{judgment.reason}"
        )
        self._refresh_recent_events()

    def _handle_detection_error(self, message: str) -> None:
        self._set_state("检测失败", "StatusBadgeWarn")
        self.last_result_label.setText(message)
        if self.current_task is not None:
            self._schedule_next_detection()

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
