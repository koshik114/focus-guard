from __future__ import annotations

import json
import re

from focus_guard.models import FocusStatus, FocusTask, ModelJudgment, OcrSnapshot, WindowSnapshot


_TASK_PREFIXES = (
    "玩",
    "学习",
    "研究",
    "阅读",
    "写",
    "完成",
    "实现",
    "开发",
    "调试",
    "复习",
    "整理",
    "分析",
    "做",
)


def is_focus_guard_window(window: WindowSnapshot) -> bool:
    title = window.window_title.casefold()
    process = window.process_name.casefold()
    return "focus guard" in title and process in {"python.exe", "pythonw.exe", "focus-guard.exe"}


def self_window_judgment() -> ModelJudgment:
    return ModelJudgment(
        status=FocusStatus.UNCERTAIN,
        confidence=0.0,
        reason="Focus Guard 自身窗口处于前台，跳过本轮专注判断。",
        provider="rules",
        raw_response='{"rule":"self_window_skip"}',
    )


def rule_based_judgment(
    task: FocusTask,
    window: WindowSnapshot,
    ocr: OcrSnapshot,
) -> ModelJudgment | None:
    allowed_process_hits = _match_allowed_processes(task, window)
    if allowed_process_hits:
        return _focused_rule("allowed_process", allowed_process_hits, 0.98)

    keywords = _extract_task_keywords(task.description)
    keywords.extend(task.focus_keywords)
    if not keywords:
        return None

    keywords = _dedupe_keywords(keywords)
    title_process = f"{window.process_name} {window.window_title}".casefold()
    ocr_text = ocr.text.casefold()

    title_hits = [keyword for keyword in keywords if keyword.casefold() in title_process]
    if title_hits:
        return _focused_rule("title_or_process_keyword", title_hits, 0.95)

    ocr_hits = [keyword for keyword in keywords if keyword.casefold() in ocr_text]
    if ocr_hits:
        return _focused_rule("ocr_keyword", ocr_hits, 0.82)

    return None


def _match_allowed_processes(task: FocusTask, window: WindowSnapshot) -> list[str]:
    process = window.process_name.casefold()
    title_process = f"{window.process_name} {window.window_title}".casefold()
    hits: list[str] = []
    for item in task.allowed_processes:
        normalized = item.strip().casefold()
        if not normalized:
            continue
        if normalized == process or normalized in title_process:
            hits.append(item.strip())
    return hits


def _extract_task_keywords(description: str) -> list[str]:
    normalized = re.sub(r"\s+", "", description.strip())
    if not normalized:
        return []

    candidates: list[str] = []
    for prefix in _TASK_PREFIXES:
        if normalized.startswith(prefix) and len(normalized) > len(prefix) + 1:
            candidates.append(normalized[len(prefix) :])
            break

    candidates.append(normalized)
    candidates.extend(re.findall(r"[A-Za-z0-9_\-]{3,}", description))

    deduped: list[str] = []
    for item in candidates:
        cleaned = item.strip("：:，,。.;；（）()[]【】")
        if len(cleaned) >= 2 and cleaned not in deduped:
            deduped.append(cleaned)
    return deduped[:8]


def _dedupe_keywords(keywords: list[str]) -> list[str]:
    deduped: list[str] = []
    for item in keywords:
        cleaned = item.strip()
        if len(cleaned) >= 2 and cleaned.casefold() not in {
            existing.casefold() for existing in deduped
        }:
            deduped.append(cleaned)
    return deduped[:16]


def _focused_rule(rule_name: str, hits: list[str], confidence: float) -> ModelJudgment:
    return ModelJudgment(
        status=FocusStatus.FOCUSED,
        confidence=confidence,
        reason=f"规则命中任务关键词：{', '.join(hits[:3])}",
        provider="rules",
        raw_response=json.dumps({"rule": rule_name, "hits": hits}, ensure_ascii=False),
    )
