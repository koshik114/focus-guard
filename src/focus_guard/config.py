from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class AppConfig:
    ollama_base_url: str
    ollama_model: str
    deepseek_api_key: str | None
    deepseek_base_url: str
    deepseek_model: str
    check_interval_seconds: int
    ocr_engine: str
    vision_mode: str
    vision_min_ocr_chars: int
    vision_max_image_side: int
    vision_jpeg_quality: int
    data_dir: Path
    env_path: Path

    @classmethod
    def from_env(cls) -> "AppConfig":
        env_path = Path(".env").resolve()
        load_dotenv(dotenv_path=env_path, override=True)
        data_dir = Path(os.getenv("FOCUS_GUARD_DATA_DIR", "data")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)

        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        return cls(
            ollama_base_url=os.getenv("FOCUS_GUARD_OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_model=os.getenv("FOCUS_GUARD_OLLAMA_MODEL", "qwen3:1.7b"),
            deepseek_api_key=deepseek_key if deepseek_key else None,
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            check_interval_seconds=max(
                15,
                _env_int("FOCUS_GUARD_CHECK_INTERVAL_SECONDS", 60),
            ),
            ocr_engine=os.getenv("FOCUS_GUARD_OCR_ENGINE", "rapidocr"),
            vision_mode=os.getenv("FOCUS_GUARD_VISION_MODE", "uncertain").strip().lower(),
            vision_min_ocr_chars=max(0, _env_int("FOCUS_GUARD_VISION_MIN_OCR_CHARS", 80)),
            vision_max_image_side=max(480, _env_int("FOCUS_GUARD_VISION_MAX_IMAGE_SIDE", 1280)),
            vision_jpeg_quality=max(40, min(95, _env_int("FOCUS_GUARD_VISION_JPEG_QUALITY", 72))),
            data_dir=data_dir,
            env_path=env_path,
        )


def write_env_settings(env_path: Path, settings: dict[str, str]) -> None:
    existing: dict[str, str] = {}
    order: list[str] = []
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                existing[key] = value.strip()
                order.append(key)

    existing.update(settings)
    for key in settings:
        if key not in order:
            order.append(key)

    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={existing[key]}" for key in order if key in existing]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
