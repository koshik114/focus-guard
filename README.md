# Focus Guard

Focus Guard is a Windows desktop GUI application that checks whether the user is
still focused on a user-defined task. It uses local screenshot OCR and an LLM
classifier. The first version prioritizes local privacy and low recurring cost.

## Confirmed MVP Scope

- GUI style: light Notion / Linear-inspired productivity interface.
- Platform: Windows desktop.
- Runtime: source-based Python application; no `.exe` packaging in phase 1.
- Check cadence: default 60 seconds.
- Capture scope: foreground window region on one monitor, with primary monitor
  fallback.
- OCR: RapidOCR first, with an interface that allows PaddleOCR later.
- Screenshot storage: screenshots are not saved.
- Stored evidence: active process, window title, OCR text, model judgment,
  confidence, provider, whether vision was used, raw response, reminder state,
  user feedback, and false-positive notes.
- Local model: Ollama `qwen3.5:2b-q4_K_M`.
- Vision input: optional local Ollama image judgment. Default mode uses vision
  only when OCR/text evidence is too weak or the text model returns `uncertain`.
- Cloud fallback: DeepSeek only when Ollama is unavailable or returns
  `uncertain`.
- Reminder: modal, always-on-top dialog requiring explicit feedback.
- Tray: closing the main window minimizes the app to the system tray.

## Setup

Use Python 3.11 or newer.

```powershell
cd C:\Users\koshi\Desktop\focus-guard
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Prepare the local model:

```powershell
ollama pull qwen3.5:2b-q4_K_M
ollama serve
```

Create local configuration:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` if needed. `.env` is ignored by Git.

## Run

```powershell
python -m focus_guard.main
```

Or, after editable installation:

```powershell
focus-guard
```

## Environment Variables

```text
FOCUS_GUARD_OLLAMA_BASE_URL=http://localhost:11434
FOCUS_GUARD_OLLAMA_MODEL=qwen3.5:2b-q4_K_M
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
FOCUS_GUARD_CHECK_INTERVAL_SECONDS=60
FOCUS_GUARD_OCR_ENGINE=rapidocr
FOCUS_GUARD_VISION_MODE=uncertain
FOCUS_GUARD_VISION_MIN_OCR_CHARS=80
FOCUS_GUARD_VISION_MAX_IMAGE_SIDE=1280
FOCUS_GUARD_VISION_JPEG_QUALITY=72
```

DeepSeek is optional. If `DEEPSEEK_API_KEY` is empty, the app will keep the
decision local and return `uncertain` when Ollama is unavailable or inconclusive.

`FOCUS_GUARD_VISION_MODE` supports:

- `off`: never send screenshots to the model.
- `uncertain`: send an in-memory screenshot only when text evidence is weak.
- `always`: send an in-memory screenshot for every model-based judgment.

## Data Policy

The application does not save screenshots. When vision is enabled, the active
window screenshot is resized, JPEG-compressed, encoded in memory, sent to local
Ollama, and then discarded. The database stores only OCR text and judgment
metadata under `data/`, which is ignored by Git.

False-positive notes are saved because they are useful for later evaluation and
possible local-model fine-tuning dataset construction.

## Architecture

```text
src/focus_guard/
├── config.py              # .env and runtime config
├── models.py              # task, OCR, judgment, event data models
├── storage.py             # SQLite event store
├── services/
│   ├── window.py          # active Windows foreground window
│   ├── screenshot.py      # active window capture and in-memory image encoding
│   ├── ocr.py             # OCR engine abstraction, RapidOCR implementation
│   ├── llm.py             # Ollama-first, DeepSeek fallback classifier
│   └── detector.py        # one detection cycle
└── ui/
    ├── main_window.py     # GUI, timer, tray, log table
    ├── reminder_dialog.py # forced manual confirmation dialog
    └── theme.py           # light modern QSS theme
```

## Next Implementation Steps

1. Add task templates and historical task reuse.
2. Add settings page for model, interval, and fallback behavior.
3. Add export for fine-tuning/evaluation JSONL.
4. Add optional PaddleOCR backend when RapidOCR accuracy is insufficient.
5. Add evaluation reports for false positives and false negatives.
