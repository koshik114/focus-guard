from __future__ import annotations

import json
from dataclasses import dataclass

import requests

from focus_guard.config import AppConfig
from focus_guard.models import FocusStatus, FocusTask, ModelJudgment, OcrSnapshot, WindowSnapshot


SYSTEM_PROMPT = """你是一个专注状态分类器。
请根据用户设定任务、当前前台应用、窗口标题和 OCR 文本，判断用户是否大概率仍在执行指定任务。
只输出 JSON，不要输出 Markdown，不要输出额外解释。
JSON 格式：
{"status":"focused|distracted|uncertain","confidence":0.0,"reason":"简短中文原因"}
要求：
1. 查资料、阅读文档、看课程视频可能属于 focused，不能机械判定为 distracted。
2. 游戏、短视频娱乐、社交闲聊、购物等和任务无关时通常属于 distracted。
3. 证据不足时输出 uncertain。
4. allowed_processes、focus_keywords、task_correction_summary 是当前任务规则，可作为判断依据。
5. previous_false_positive_guidance 是用户过去确认的误判纠错提示，只能作为参考，不能当作当前屏幕事实。
6. 第一字符必须是 {，最后字符必须是 }。
"""


def test_deepseek_connection(
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: int = 20,
) -> str:
    if not api_key.strip():
        raise ValueError("DeepSeek API Key 为空")

    payload = {
        "model": model.strip() or "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是一个连通性测试助手。"},
            {"role": "user", "content": "只输出 ok。"},
        ],
        "temperature": 0,
        "max_tokens": 8,
    }
    response = requests.post(
        f"{base_url.strip().rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    return f"DeepSeek 连通正常，模型返回：{content.strip()[:40]}"


def summarize_false_positive_guidance(
    api_key: str,
    base_url: str,
    model: str,
    task_description: str,
    examples: tuple[str, ...],
    timeout_seconds: int = 40,
) -> str:
    if not api_key.strip():
        raise ValueError("DeepSeek API Key 为空")
    if not examples:
        raise ValueError("没有可归纳的误判说明")

    payload = {
        "model": model.strip() or "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是专注检测应用的规则归纳器。"
                    "根据用户确认的误判样本，归纳一条短中文规则。"
                    "不要写完整 prompt，不要输出 Markdown，不要编号。"
                    "规则必须保守，不能把明显娱乐、购物、社交闲聊也放宽为专注。"
                    "长度控制在 80 个汉字以内。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": task_description,
                        "false_positive_examples": list(examples[:10]),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 120,
    }
    response = requests.post(
        f"{base_url.strip().rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return content.strip().strip("`").strip()


@dataclass(frozen=True)
class LlmRouter:
    config: AppConfig

    def judge(
        self,
        task: FocusTask,
        window: WindowSnapshot,
        ocr: OcrSnapshot,
        image_base64: str | None = None,
    ) -> ModelJudgment:
        ollama_result = self._judge_with_ollama(task, window, ocr)
        if self._should_use_vision(ollama_result, ocr, image_base64):
            vision_result = self._judge_with_ollama_vision(task, window, ocr, image_base64 or "")
            if vision_result.status is not FocusStatus.UNCERTAIN:
                return vision_result
            ollama_result = vision_result

        if ollama_result.status is not FocusStatus.UNCERTAIN:
            return ollama_result
        if self.config.deepseek_api_key:
            try:
                return self._judge_with_deepseek(task, window, ocr)
            except requests.RequestException as exc:
                return ModelJudgment(
                    status=FocusStatus.UNCERTAIN,
                    confidence=0.0,
                    reason=f"DeepSeek 兜底调用失败：{exc}",
                    provider="deepseek",
                    raw_response=str(exc),
                )
        return ollama_result

    def review_with_deepseek(
        self,
        task: FocusTask,
        window: WindowSnapshot,
        ocr: OcrSnapshot,
    ) -> ModelJudgment:
        if not self.config.deepseek_api_key:
            return ModelJudgment(
                status=FocusStatus.UNCERTAIN,
                confidence=0.0,
                reason="DeepSeek API Key 未配置，无法进行误判复核",
                provider="deepseek-review",
                raw_response="",
            )
        try:
            result = self._judge_with_deepseek(task, window, ocr)
        except requests.RequestException as exc:
            return ModelJudgment(
                status=FocusStatus.UNCERTAIN,
                confidence=0.0,
                reason=f"DeepSeek 误判复核失败：{exc}",
                provider="deepseek-review",
                raw_response=str(exc),
            )
        return ModelJudgment(
            status=result.status,
            confidence=result.confidence,
            reason=result.reason,
            provider="deepseek-review",
            raw_response=result.raw_response,
            used_vision=result.used_vision,
        )

    def _should_use_vision(
        self,
        judgment: ModelJudgment,
        ocr: OcrSnapshot,
        image_base64: str | None,
    ) -> bool:
        mode = self.config.vision_mode
        if mode in {"0", "false", "off", "disabled", "none"}:
            return False
        if not image_base64:
            return False
        if mode == "always":
            return True
        if mode != "uncertain":
            return False
        if judgment.status is FocusStatus.UNCERTAIN:
            return True
        return len(ocr.text.strip()) < self.config.vision_min_ocr_chars

    def _judge_with_ollama(
        self,
        task: FocusTask,
        window: WindowSnapshot,
        ocr: OcrSnapshot,
    ) -> ModelJudgment:
        payload = {
            "model": self.config.ollama_model,
            "prompt": self._build_ollama_prompt(task, window, ocr),
            "format": "json",
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            "options": {"temperature": 0, "num_predict": 120},
        }
        try:
            response = requests.post(
                f"{self.config.ollama_base_url.rstrip('/')}/api/generate",
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            return ModelJudgment(
                status=FocusStatus.UNCERTAIN,
                confidence=0.0,
                reason=f"Ollama 不可用：{exc}",
                provider="ollama",
                raw_response=str(exc),
            )

        raw = response.text
        content = response.json().get("response", "")
        return _parse_judgment(content, provider="ollama", raw_response=raw)

    def _judge_with_ollama_vision(
        self,
        task: FocusTask,
        window: WindowSnapshot,
        ocr: OcrSnapshot,
        image_base64: str,
    ) -> ModelJudgment:
        payload = {
            "model": self.config.ollama_model,
            "prompt": self._build_ollama_vision_prompt(task, window, ocr),
            "images": [image_base64],
            "format": "json",
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            "options": {"temperature": 0, "num_predict": 140},
        }
        try:
            response = requests.post(
                f"{self.config.ollama_base_url.rstrip('/')}/api/generate",
                json=payload,
                timeout=160,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            return ModelJudgment(
                status=FocusStatus.UNCERTAIN,
                confidence=0.0,
                reason=f"Ollama 视觉判断不可用：{exc}",
                provider="ollama-vision",
                raw_response=str(exc),
                used_vision=True,
            )

        raw = response.text
        content = response.json().get("response", "")
        return _parse_judgment(
            content,
            provider="ollama-vision",
            raw_response=raw,
            used_vision=True,
        )

    def _judge_with_deepseek(
        self,
        task: FocusTask,
        window: WindowSnapshot,
        ocr: OcrSnapshot,
    ) -> ModelJudgment:
        payload = {
            "model": self.config.deepseek_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._build_user_prompt(task, window, ocr)},
            ],
            "temperature": 0,
            "max_tokens": 180,
            "response_format": {"type": "json_object"},
        }
        response = requests.post(
            f"{self.config.deepseek_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        raw = response.text
        content = response.json()["choices"][0]["message"]["content"]
        return _parse_judgment(content, provider="deepseek", raw_response=raw)

    @staticmethod
    def _build_user_prompt(task: FocusTask, window: WindowSnapshot, ocr: OcrSnapshot) -> str:
        ocr_text = ocr.text[:4000]
        return json.dumps(
            {
                "task": task.description,
                "duration_minutes": task.duration_minutes,
                "active_process": window.process_name,
                "window_title": window.window_title,
                "ocr_engine": ocr.engine,
                "ocr_text": ocr_text,
                "allowed_processes": list(task.allowed_processes),
                "focus_keywords": list(task.focus_keywords),
                "task_correction_summary": task.correction_summary or "",
                "previous_false_positive_guidance": list(task.feedback_guidance[:6]),
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _build_ollama_prompt(task: FocusTask, window: WindowSnapshot, ocr: OcrSnapshot) -> str:
        return (
            f"{SYSTEM_PROMPT}\n\n"
            "用户输入如下 JSON：\n"
            f"{LlmRouter._build_user_prompt(task, window, ocr)}\n\n"
            "直接给出分类结果 JSON，不要复述输入，不要展示推理过程。"
        )

    @staticmethod
    def _build_ollama_vision_prompt(
        task: FocusTask,
        window: WindowSnapshot,
        ocr: OcrSnapshot,
    ) -> str:
        return (
            f"{SYSTEM_PROMPT}\n\n"
            "你还会收到一张当前前台窗口截图。截图只用于本次判断，不应假设它会被保存。\n"
            "请综合截图画面、窗口标题、进程名和 OCR 文本判断用户是否仍在执行指定任务。\n"
            "用户输入如下 JSON：\n"
            f"{LlmRouter._build_user_prompt(task, window, ocr)}\n\n"
            "直接给出分类结果 JSON，不要复述输入，不要展示推理过程。"
        )


def _parse_judgment(
    content: str,
    provider: str,
    raw_response: str,
    used_vision: bool = False,
) -> ModelJudgment:
    try:
        start = content.find("{")
        end = content.rfind("}") + 1
        data = json.loads(content[start:end])
        status = FocusStatus(data.get("status", "uncertain"))
        confidence = float(data.get("confidence", 0.0))
        reason = str(data.get("reason", "")).strip()
    except (ValueError, TypeError, json.JSONDecodeError):
        status = FocusStatus.UNCERTAIN
        confidence = 0.0
        reason = f"模型输出无法解析：{content[:200]}"

    return ModelJudgment(
        status=status,
        confidence=max(0.0, min(1.0, confidence)),
        reason=reason,
        provider=provider,
        raw_response=raw_response,
        used_vision=used_vision,
    )
