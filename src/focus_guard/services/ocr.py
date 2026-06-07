from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
from PIL import Image

from focus_guard.models import OcrSnapshot


class OcrEngine(ABC):
    name: str

    @abstractmethod
    def extract_text(self, image: Image.Image) -> OcrSnapshot:
        raise NotImplementedError


class RapidOcrEngine(OcrEngine):
    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()

    def extract_text(self, image: Image.Image) -> OcrSnapshot:
        array = np.array(image.convert("RGB"))
        result, _ = self._engine(array)
        if not result:
            return OcrSnapshot(text="", engine=self.name)

        lines = [line[1] for line in result if len(line) >= 2 and isinstance(line[1], str)]
        return OcrSnapshot(text="\n".join(lines).strip(), engine=self.name)


class NullOcrEngine(OcrEngine):
    name = "none"

    def extract_text(self, image: Image.Image) -> OcrSnapshot:
        return OcrSnapshot(text="", engine=self.name)


def build_ocr_engine(engine_name: str) -> OcrEngine:
    normalized = engine_name.strip().lower()
    if normalized == "rapidocr":
        return RapidOcrEngine()
    if normalized in {"none", "disabled", "off"}:
        return NullOcrEngine()
    raise ValueError(f"Unsupported OCR engine: {engine_name}")
