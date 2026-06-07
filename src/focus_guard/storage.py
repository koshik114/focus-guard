from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from focus_guard.models import DetectionEvent, FeedbackType, TaskTemplate


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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    description TEXT NOT NULL UNIQUE,
                    default_duration_minutes INTEGER,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT
                )
                """
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

    def upsert_task_template(
        self,
        description: str,
        default_duration_minutes: int | None,
    ) -> int:
        normalized = description.strip()
        if not normalized:
            raise ValueError("任务描述不能为空")

        now = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO task_templates (
                    description,
                    default_duration_minutes,
                    use_count,
                    created_at,
                    updated_at,
                    last_used_at
                )
                VALUES (?, ?, 0, ?, ?, NULL)
                ON CONFLICT(description) DO UPDATE SET
                    default_duration_minutes = excluded.default_duration_minutes,
                    updated_at = excluded.updated_at
                """,
                (normalized, default_duration_minutes, now, now),
            )
            row = connection.execute(
                "SELECT id FROM task_templates WHERE description = ?",
                (normalized,),
            ).fetchone()
            return int(row["id"])

    def record_task_used(
        self,
        description: str,
        default_duration_minutes: int | None,
    ) -> int:
        template_id = self.upsert_task_template(description, default_duration_minutes)
        now = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_templates
                SET use_count = use_count + 1,
                    updated_at = ?,
                    last_used_at = ?
                WHERE id = ?
                """,
                (now, now, template_id),
            )
        return template_id

    def delete_task_template(self, template_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM task_templates WHERE id = ?",
                (template_id,),
            )

    def list_task_templates(self, limit: int = 30) -> list[TaskTemplate]:
        with self._connect() as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT *
                    FROM task_templates
                    ORDER BY
                        CASE WHEN last_used_at IS NULL THEN 1 ELSE 0 END,
                        last_used_at DESC,
                        updated_at DESC,
                        use_count DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

        return [
            TaskTemplate(
                id=int(row["id"]),
                description=str(row["description"]),
                default_duration_minutes=row["default_duration_minutes"],
                use_count=int(row["use_count"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                last_used_at=datetime.fromisoformat(row["last_used_at"])
                if row["last_used_at"]
                else None,
            )
            for row in rows
        ]
