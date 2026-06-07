from __future__ import annotations

from dataclasses import dataclass

from focus_guard.models import DetectionEvent, FocusStatus, FocusTask
from focus_guard.services.llm import LlmRouter
from focus_guard.services.rules import (
    is_focus_guard_window,
    rule_based_judgment,
    self_window_judgment,
)
from focus_guard.services.ocr import OcrEngine
from focus_guard.services.screenshot import (
    capture_active_window_or_primary_monitor,
    image_to_base64_jpeg,
)
from focus_guard.services.window import get_active_window_snapshot
from focus_guard.models import OcrSnapshot


@dataclass
class FocusDetector:
    ocr_engine: OcrEngine
    llm_router: LlmRouter

    def check(self, task: FocusTask) -> DetectionEvent:
        window = get_active_window_snapshot()
        if is_focus_guard_window(window):
            ocr = OcrSnapshot(text="", engine="skipped")
            judgment = self_window_judgment()
            return DetectionEvent(
                task=task,
                window=window,
                ocr=ocr,
                judgment=judgment,
                reminder_shown=False,
            )

        screenshot = capture_active_window_or_primary_monitor()
        ocr = self.ocr_engine.extract_text(screenshot)
        judgment = rule_based_judgment(task=task, window=window, ocr=ocr)
        if judgment is None:
            vision_mode = self.llm_router.config.vision_mode
            image_base64 = None
            if vision_mode not in {"0", "false", "off", "disabled", "none"}:
                image_base64 = image_to_base64_jpeg(
                    screenshot,
                    max_side=self.llm_router.config.vision_max_image_side,
                    quality=self.llm_router.config.vision_jpeg_quality,
                )
            judgment = self.llm_router.judge(
                task=task,
                window=window,
                ocr=ocr,
                image_base64=image_base64,
            )
        reminder_shown = judgment.status is FocusStatus.DISTRACTED
        return DetectionEvent(
            task=task,
            window=window,
            ocr=ocr,
            judgment=judgment,
            reminder_shown=reminder_shown,
        )
