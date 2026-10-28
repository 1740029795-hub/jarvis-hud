# J.A.R.V.I.S. — Voice-Controlled AI Assistant

A **self-built** Windows voice assistant: voice chat + computer control + long-term memory. Web-based holographic HUD, decoupled frontend/backend, fully modular and open source.

> Inspired by open-source projects like Aivy-OS and huashu-chrome, but built from scratch — you own all the code.

## Features

- 🎤 **Voice loop** — push-to-talk → local ASR (faster-whisper) → LLM (DeepSeek) → TTS (edge-tts)
- 🖱️ **4-layer computer control** — L0 structural APIs (PowerShell / WPS COM) → L1 UIA control tree → L2 coordinates → L3 screenshot + vision model
- 🌐 **Browser control** — powered by [huashu-chrome](https://github.com/alchaincyf/huashu-chrome), drives *your own logged-in* browser
- 📊 **WPS Office** — create spreadsheets & documents via COM
- 🧠 **Long-term memory** — SQLite + semantic search, auto-memory after each conversation
- ⏰ **Reminders** — one-shot + daily/weekly/monthly recurring
- 👁️ **Vision fallback** — DeepSeek multimodal model "sees" the screen
- 🔀 **Sub-agents** — parallel execution of independent tasks

## Architecture

```
Interaction   Web HUD frontend + edge-tts
      ↓
Cognition     DeepSeek (function calling) + vision + SQLite memory
      ↓
Orchestration Tool registry + sub-agent dispatch
      ↓
Execution     Desktop (UIA/coords) + Browser (huashu-chrome) + WPS (COM) + system commands
      ↓
Perception    ASR (faster-whisper) + screen (screenshot + vision)
```

## Requirements

- **Windows** (desktop control, WPS COM, etc. rely on Windows APIs)
- **Python 3.11** (avoids 3.14 wheel compatibility issues)
- **Edge browser** (for speech recognition + browser control)
- **ffmpeg** (audio transcoding for STT)
- **DeepSeek API Key** (LLM + vision, free signup at https://platform.deepseek.com)

## Installation

**One-click**: double-click `install.bat` (auto-creates venv, installs deps, prompts for key).

**Manual**:

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# configure API key (copy .env.example to .env, fill in your key)
copy .env.example .env

# place ffmpeg at ffmpeg\bin\ffmpeg.exe, or add to PATH
```

Models (whisper + sentence-transformers) download automatically on first run (via hf-mirror).

## Usage

Double-click `start.bat`, then open **http://localhost:8000** in Edge/Chrome.

Hold TALK and speak. Allow microphone permission on first use.

## Browser control (optional)

Requires the huashu-chrome extension:

```bash
npx -y huashu-chrome install
```

Then in Edge open `edge://extensions` → Developer mode → Load the directory printed by `npx -y huashu-chrome extension`.

## Project structure

```
├── server.py          # FastAPI backend (/stt /ask /tts)
├── index.html         # HUD frontend
├── js/                # frontend logic (voice loop, barge-in)
├── css/               # styles
├── core/              # brain, memory, config, tool registry
├── skills/            # tools: system/desktop/browser/WPS/reminder/vision/agent
├── stt/               # speech recognition (faster-whisper)
├── tts/               # speech synthesis (edge-tts)
├── data/              # runtime data (gitignored)
└── model/             # model files (gitignored, downloaded at runtime)
```

## Security

- Irreversible actions (delete/format/registry changes) are blocked and handed back to the user
- Every write operation reads back to verify (anti silent-failure)
- Dangerous command blacklist + screenshot evidence at each step

## License

[MIT](LICENSE)

Third-party libraries use permissive licenses (MIT/Apache).
