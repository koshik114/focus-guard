from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class FocusStatus(StrEnum):
    FOCUSED = "focused"
    DISTRACTED = "distracted"
    UNCERTAIN = "uncertain"


class FeedbackType(StrEnum):
    CONFIRMED_DISTRACTION = "confirmed_distraction"
    FALSE_POSITIVE = "false_positive"
    PAUSED = "paused"


@dataclass(frozen=True)
class FocusTask:
    description: str
    duration_minutes: int | None = None
    allowed_processes: tuple[str, ...] = ()
    focus_keywords: tuple[str, ...] = ()
    correction_summary: str | None = None
    feedback_guidance: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskTemplate:
    id: int
    description: str
    default_duration_minutes: int | None
    allowed_processes: tuple[str, ...]
    focus_keywords: tuple[str, ...]
    correction_summary: str | None
    use_count: int
    updated_at: datetime
    last_used_at: datetime | None


@dataclass(frozen=True)
class WindowSnapshot:
    captured_at: datetime
    app_name: str
    process_name: str
    window_title: str


@dataclass(frozen=True)
class OcrSnapshot:
    text: str
    engine: str


@dataclass(frozen=True)
class ModelJudgment:
    status: FocusStatus
    confidence: float
    reason: str
    provider: str
    raw_response: str
    used_vision: bool = False


@dataclass(frozen=True)
class DetectionEvent:
    task: FocusTask
    window: WindowSnapshot
    ocr: OcrSnapshot
    judgment: ModelJudgment
    reminder_shown: bool
    user_feedback: FeedbackType | None = None
    feedback_note: str | None = None
