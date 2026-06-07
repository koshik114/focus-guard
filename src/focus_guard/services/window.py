from __future__ import annotations

from datetime import datetime

import psutil
import win32gui
import win32process

from focus_guard.models import WindowSnapshot


def get_active_window_snapshot() -> WindowSnapshot:
    hwnd = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(hwnd)
    _, pid = win32process.GetWindowThreadProcessId(hwnd)

    process_name = ""
    app_name = ""
    try:
        process = psutil.Process(pid)
        process_name = process.name()
        app_name = process.exe()
    except (psutil.Error, OSError):
        process_name = f"pid:{pid}"

    return WindowSnapshot(
        captured_at=datetime.now().astimezone(),
        app_name=app_name,
        process_name=process_name,
        window_title=title,
    )
