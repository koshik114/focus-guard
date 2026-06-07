from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from focus_guard.config import AppConfig
from focus_guard.services.llm import test_deepseek_connection


@dataclass(frozen=True)
class SettingsValues:
    ollama_base_url: str
    ollama_model: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    check_interval_seconds: int
    ocr_engine: str
    vision_mode: str
    vision_min_ocr_chars: int
    vision_max_image_side: int
    vision_jpeg_quality: int

    def to_env(self) -> dict[str, str]:
        return {
            "FOCUS_GUARD_OLLAMA_BASE_URL": self.ollama_base_url,
            "FOCUS_GUARD_OLLAMA_MODEL": self.ollama_model,
            "DEEPSEEK_API_KEY": self.deepseek_api_key,
            "DEEPSEEK_BASE_URL": self.deepseek_base_url,
            "DEEPSEEK_MODEL": self.deepseek_model,
            "FOCUS_GUARD_CHECK_INTERVAL_SECONDS": str(self.check_interval_seconds),
            "FOCUS_GUARD_OCR_ENGINE": self.ocr_engine,
            "FOCUS_GUARD_VISION_MODE": self.vision_mode,
            "FOCUS_GUARD_VISION_MIN_OCR_CHARS": str(self.vision_min_ocr_chars),
            "FOCUS_GUARD_VISION_MAX_IMAGE_SIDE": str(self.vision_max_image_side),
            "FOCUS_GUARD_VISION_JPEG_QUALITY": str(self.vision_jpeg_quality),
        }


class DeepSeekConnectionWorker(QThread):
    finished_test = Signal(bool, str)

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    def run(self) -> None:
        try:
            message = test_deepseek_connection(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
            )
        except Exception as exc:  # noqa: BLE001 - GUI boundary should surface connection errors.
            self.finished_test.emit(False, str(exc))
            return
        self.finished_test.emit(True, message)


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.deepseek_test_worker: DeepSeekConnectionWorker | None = None
        self.setWindowTitle("Focus Guard 设置")
        self.setMinimumWidth(680)
        self.setModal(True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)

        title = QLabel("设置")
        title.setObjectName("AppTitle")
        layout.addWidget(title)

        subtitle = QLabel("这些配置会写入本地 .env；截图仍然不会保存。")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        layout.addLayout(grid)

        model_panel, model_form = self._panel_with_form("模型")

        self.ollama_base_url = QLineEdit(config.ollama_base_url)
        model_form.addRow("Ollama 地址", self.ollama_base_url)

        self.ollama_model = QLineEdit(config.ollama_model)
        model_form.addRow("Ollama 模型", self.ollama_model)

        self.deepseek_api_key = QLineEdit(config.deepseek_api_key or "")
        self.deepseek_api_key.setEchoMode(QLineEdit.Password)
        self.deepseek_api_key.setPlaceholderText("留空则不调用 DeepSeek")
        model_form.addRow("DeepSeek Key", self.deepseek_api_key)

        self.deepseek_base_url = QLineEdit(config.deepseek_base_url)
        model_form.addRow("DeepSeek 地址", self.deepseek_base_url)

        self.deepseek_model = QLineEdit(config.deepseek_model)
        model_form.addRow("DeepSeek 模型", self.deepseek_model)

        deepseek_test_row = QHBoxLayout()
        self.deepseek_test_button = QPushButton("测试 DeepSeek")
        self.deepseek_test_button.clicked.connect(self._test_deepseek)
        deepseek_test_row.addWidget(self.deepseek_test_button)

        self.deepseek_test_label = QLabel("未测试")
        self.deepseek_test_label.setObjectName("Muted")
        self.deepseek_test_label.setWordWrap(True)
        deepseek_test_row.addWidget(self.deepseek_test_label, 1)
        model_form.addRow("连通性", deepseek_test_row)
        grid.addWidget(model_panel, 0, 0)

        runtime_panel, runtime_form = self._panel_with_form("检测")

        self.check_interval = QSpinBox()
        self.check_interval.setRange(15, 600)
        self.check_interval.setValue(config.check_interval_seconds)
        self.check_interval.setSuffix(" 秒")
        runtime_form.addRow("检测间隔", self.check_interval)

        self.ocr_engine = QComboBox()
        self.ocr_engine.addItems(["rapidocr", "none"])
        self.ocr_engine.setCurrentText(config.ocr_engine)
        runtime_form.addRow("OCR 引擎", self.ocr_engine)

        self.vision_mode = QComboBox()
        self.vision_mode.addItem("不使用视觉", "off")
        self.vision_mode.addItem("不确定时使用", "uncertain")
        self.vision_mode.addItem("总是使用", "always")
        index = self.vision_mode.findData(config.vision_mode)
        self.vision_mode.setCurrentIndex(index if index >= 0 else 1)
        runtime_form.addRow("视觉模式", self.vision_mode)

        self.vision_min_chars = QSpinBox()
        self.vision_min_chars.setRange(0, 1000)
        self.vision_min_chars.setValue(config.vision_min_ocr_chars)
        self.vision_min_chars.setSuffix(" 字")
        runtime_form.addRow("OCR 触发阈值", self.vision_min_chars)

        self.vision_max_side = QSpinBox()
        self.vision_max_side.setRange(480, 2560)
        self.vision_max_side.setSingleStep(160)
        self.vision_max_side.setValue(config.vision_max_image_side)
        self.vision_max_side.setSuffix(" px")
        runtime_form.addRow("图像最长边", self.vision_max_side)

        self.vision_quality = QSpinBox()
        self.vision_quality.setRange(40, 95)
        self.vision_quality.setValue(config.vision_jpeg_quality)
        self.vision_quality.setSuffix(" %")
        runtime_form.addRow("JPEG 质量", self.vision_quality)
        grid.addWidget(runtime_panel, 0, 1)

        hint = QLabel(
            "建议保持视觉模式为“不确定时使用”。这样大多数检测仍然只走 OCR 文本，"
            "只有证据不足时才调用 qwen3.5 的图像能力。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("保存", QDialogButtonBox.AcceptRole)
        self.cancel_button = buttons.addButton("取消", QDialogButtonBox.RejectRole)
        self.save_button.setObjectName("PrimaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(buttons)
        layout.addLayout(footer)

    @staticmethod
    def _panel_with_form(title: str) -> tuple[QFrame, QFormLayout]:
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        layout.addWidget(label)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)
        layout.addLayout(form)
        return panel, form

    def values(self) -> SettingsValues:
        return SettingsValues(
            ollama_base_url=self.ollama_base_url.text().strip() or "http://localhost:11434",
            ollama_model=self.ollama_model.text().strip() or "qwen3.5:2b-q4_K_M",
            deepseek_api_key=self.deepseek_api_key.text().strip(),
            deepseek_base_url=self.deepseek_base_url.text().strip() or "https://api.deepseek.com",
            deepseek_model=self.deepseek_model.text().strip() or "deepseek-chat",
            check_interval_seconds=self.check_interval.value(),
            ocr_engine=self.ocr_engine.currentText(),
            vision_mode=str(self.vision_mode.currentData()),
            vision_min_ocr_chars=self.vision_min_chars.value(),
            vision_max_image_side=self.vision_max_side.value(),
            vision_jpeg_quality=self.vision_quality.value(),
        )

    def _test_deepseek(self) -> None:
        values = self.values()
        if not values.deepseek_api_key:
            self.deepseek_test_label.setText("DeepSeek API Key 为空")
            return
        if self.deepseek_test_worker and self.deepseek_test_worker.isRunning():
            return

        self.deepseek_test_button.setEnabled(False)
        self.deepseek_test_label.setText("正在测试...")
        self.deepseek_test_worker = DeepSeekConnectionWorker(
            api_key=values.deepseek_api_key,
            base_url=values.deepseek_base_url,
            model=values.deepseek_model,
        )
        self.deepseek_test_worker.finished_test.connect(self._handle_deepseek_test_result)
        self.deepseek_test_worker.start()

    def _handle_deepseek_test_result(self, ok: bool, message: str) -> None:
        self.deepseek_test_button.setEnabled(True)
        prefix = "连通正常" if ok else "连接失败"
        self.deepseek_test_label.setText(f"{prefix}：{message}")
