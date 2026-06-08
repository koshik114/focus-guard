from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from focus_guard.models import DetectionEvent, FeedbackType, TaskTemplate


def _join_rules(values: tuple[str, ...]) -> str:
    return "\n".join(item.strip() for item in values if item.strip())


def _split_rules(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    parts: list[str] = []
    for raw in value.replace(",", "\n").replace("，", "\n").splitlines():
        cleaned = raw.strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    return tuple(parts)


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
                    allowed_processes TEXT,
                    focus_keywords TEXT,
                    correction_summary TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT
                )
                """
            )
            template_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(task_templates)")
            }
            if "allowed_processes" not in template_columns:
                connection.execute(
                    "ALTER TABLE task_templates ADD COLUMN allowed_processes TEXT"
                )
            if "focus_keywords" not in template_columns:
                connection.execute("ALTER TABLE task_templates ADD COLUMN focus_keywords TEXT")
            if "correction_summary" not in template_columns:
                connection.execute(
                    "ALTER TABLE task_templates ADD COLUMN correction_summary TEXT"
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

    def list_false_positive_guidance(
        self,
        task_description: str,
        limit: int = 6,
    ) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT process_name, window_title, reason, feedback_note
                    FROM detection_events
                    WHERE task_description = ?
                      AND user_feedback = ?
                      AND feedback_note IS NOT NULL
                      AND TRIM(feedback_note) != ''
                      AND feedback_note NOT LIKE 'DeepSeek 误判复核%'
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (task_description, FeedbackType.FALSE_POSITIVE.value, limit),
                )
            )

        guidance: list[str] = []
        for row in rows:
            process = row["process_name"] or ""
            title = row["window_title"] or ""
            reason = row["reason"] or ""
            note = row["feedback_note"] or ""
            guidance.append(
                f"曾误判：进程={process}；窗口={title}；模型原因={reason}；用户说明={note}"
            )
        return tuple(guidance)

    def upsert_task_template(
        self,
        description: str,
        default_duration_minutes: int | None,
        allowed_processes: tuple[str, ...] = (),
        focus_keywords: tuple[str, ...] = (),
        correction_summary: str | None = None,
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
                    allowed_processes,
                    focus_keywords,
                    correction_summary,
                    use_count,
                    created_at,
                    updated_at,
                    last_used_at
                )
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, NULL)
                ON CONFLICT(description) DO UPDATE SET
                    default_duration_minutes = excluded.default_duration_minutes,
                    allowed_processes = excluded.allowed_processes,
                    focus_keywords = excluded.focus_keywords,
                    correction_summary = excluded.correction_summary,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized,
                    default_duration_minutes,
                    _join_rules(allowed_processes),
                    _join_rules(focus_keywords),
                    correction_summary.strip() if correction_summary else None,
                    now,
                    now,
                ),
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
        allowed_processes: tuple[str, ...] = (),
        focus_keywords: tuple[str, ...] = (),
        correction_summary: str | None = None,
    ) -> int:
        template_id = self.upsert_task_template(
            description,
            default_duration_minutes,
            allowed_processes,
            focus_keywords,
            correction_summary,
        )
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

    def update_task_template_correction_summary(
        self,
        description: str,
        correction_summary: str,
    ) -> int:
        normalized = description.strip()
        if not normalized:
            raise ValueError("任务描述不能为空")

        now = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM task_templates WHERE description = ?",
                (normalized,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO task_templates (
                        description,
                        default_duration_minutes,
                        allowed_processes,
                        focus_keywords,
                        correction_summary,
                        use_count,
                        created_at,
                        updated_at,
                        last_used_at
                    )
                    VALUES (?, NULL, NULL, NULL, ?, 0, ?, ?, NULL)
                    """,
                    (normalized, correction_summary.strip(), now, now),
                )
                row = connection.execute(
                    "SELECT id FROM task_templates WHERE description = ?",
                    (normalized,),
                ).fetchone()
                return int(row["id"])

            template_id = int(row["id"])
            connection.execute(
                """
                UPDATE task_templates
                SET correction_summary = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (correction_summary.strip(), now, template_id),
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
                allowed_processes=_split_rules(row["allowed_processes"]),
                focus_keywords=_split_rules(row["focus_keywords"]),
                correction_summary=row["correction_summary"],
                use_count=int(row["use_count"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                last_used_at=datetime.fromisoformat(row["last_used_at"])
                if row["last_used_at"]
                else None,
            )
            for row in rows
        ]
