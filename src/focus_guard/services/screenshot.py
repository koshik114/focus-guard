from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image
from mss import mss
import win32gui


def capture_primary_monitor() -> Image.Image:
    with mss() as screen:
        monitor = screen.monitors[1]
        raw = screen.grab(monitor)
        image = Image.frombytes("RGB", raw.size, raw.rgb)
        return image


def capture_active_window_or_primary_monitor() -> Image.Image:
    hwnd = win32gui.GetForegroundWindow()
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = right - left
        height = bottom - top
        if width >= 120 and height >= 80:
            with mss() as screen:
                raw = screen.grab({"left": left, "top": top, "width": width, "height": height})
                return Image.frombytes("RGB", raw.size, raw.rgb)
    except Exception:
        pass

    return capture_primary_monitor()


def image_to_base64_jpeg(
    image: Image.Image,
    max_side: int = 1280,
    quality: int = 72,
) -> str:
    """Encode a screenshot for in-memory Ollama vision calls without saving it."""
    prepared = image.convert("RGB")
    width, height = prepared.size
    longest = max(width, height)
    if longest > max_side:
        scale = max_side / longest
        resized_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        prepared = prepared.resize(resized_size, Image.Resampling.LANCZOS)

    buffer = BytesIO()
    prepared.save(buffer, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")
