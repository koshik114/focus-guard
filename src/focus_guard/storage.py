from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from focus_guard.models import DetectionEvent, FeedbackType


class EventStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS detection_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    task_description TEXT NOT NULL,
                    task_duration_minutes INTEGER,
                    app_name TEXT,
                    process_name TEXT,
                    window_title TEXT,
                    ocr_engine TEXT,
                    ocr_text TEXT,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    reason TEXT,
                    provider TEXT NOT NULL,
                    raw_response TEXT,
                    vision_used INTEGER NOT NULL DEFAULT 0,
                    reminder_shown INTEGER NOT NULL,
                    user_feedback TEXT,
                    feedback_note TEXT
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(detection_events)")
            }
            if "vision_used" not in columns:
                connection.execute(
                    "ALTER TABLE detection_events "
                    "ADD COLUMN vision_used INTEGER NOT NULL DEFAULT 0"
                )

    def add_event(self, event: DetectionEvent) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO detection_events (
                    captured_at,
                    task_description,
                    task_duration_minutes,
                    app_name,
                    process_name,
                    window_title,
                    ocr_engine,
                    ocr_text,
                    status,
                    confidence,
                    reason,
                    provider,
                    raw_response,
                    vision_used,
                    reminder_shown,
                    user_feedback,
                    feedback_note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.window.captured_at.isoformat(),
                    event.task.description,
                    event.task.duration_minutes,
                    event.window.app_name,
                    event.window.process_name,
                    event.window.window_title,
                    event.ocr.engine,
                    event.ocr.text,
                    event.judgment.status.value,
                    event.judgment.confidence,
                    event.judgment.reason,
                    event.judgment.provider,
                    event.judgment.raw_response,
                    int(event.judgment.used_vision),
                    int(event.reminder_shown),
                    event.user_feedback.value if event.user_feedback else None,
                    event.feedback_note,
                ),
            )
            return int(cursor.lastrowid)

    def add_feedback(
        self,
        event: DetectionEvent,
        feedback: FeedbackType,
        note: str | None = None,
    ) -> int:
        return self.add_event(
            replace(
                event,
                user_feedback=feedback,
                feedback_note=note.strip() if note else None,
            )
        )

    def list_recent(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT *
                    FROM detection_events
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            )
