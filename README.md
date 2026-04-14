# T.M.O.S — Total Machine Operating System

A futuristic desktop AI assistant with always-on wake-word listening, an animated "web" orb, and three swappable AI backends (Groq, Gemini, Ollama). Built with PyQt6 + QWebEngine and a pure-SVG animated frontend.

> Say **"TMOS, what's the weather?"** — no buttons required.

---

## Features

- **Three AI backends, one toggle** — Groq (fast + free), Google Gemini (online), or Ollama (local & private). Swap at runtime; each keeps its own conversation history.
- **24/7 wake-word listening** — continuous mic listener that activates on "TMOS" or "Hey TMOS". No button presses.
- **Animated web orb** — SVG spider-web with 12 spokes and 5 concentric rings. Rotates idle, ripples outward when TMOS speaks, turns green while listening, orange while processing.
- **Streaming responses** — tokens arrive live in the chat bubble.
- **Voice output** — edge-tts online, pyttsx3 offline fallback, optional VoxCPM2 studio-quality neural TTS.
- **System control** — open apps, manage files, check stats (CPU / RAM / disk / battery), take screenshots, manage clipboard, launch Chrome/VS Code/Terminal.
- **Productivity** — reminders (APScheduler cron), notes (persistent JSON), timers, calc.
- **Custom frameless UI** — drag-handle title bar, system tray, keyboard shortcuts.

---

## Screenshots

*(add your own here after first run — the `web-wrap` orb, three-backend toggle, settings modal)*

---

## Quick Start

### 1. Requirements

- Python 3.11 or 3.12 (Python 3.13 needs C++ build tools for PyAudio on Windows)
- ~200 MB disk
- A free [Groq API key](https://console.groq.com/keys) (recommended) **or** [Gemini key](https://aistudio.google.com/apikey) **or** [Ollama](https://ollama.com) running locally

### 2. Install

```bash
git clone https://github.com/<your-username>/tmos.git
cd tmos
pip install -r requirements.txt
```

If `PyAudio` fails to build on Windows:

```bash
pip install pipwin
pipwin install pyaudio
```

### 3. Run

```bash
python main.py
```

### 4. Configure

1. Click the **⚙ Settings** icon (top-right)
2. Paste your **Groq API key** (or Gemini key)
3. Tick **Always-on wake-word listening**
4. Click **Save**

Now say **"TMOS, hello"** and it replies.

---

## Backend Comparison

| Backend | Cost | Speed | Quality | Offline | Best For |
|---|---|---|---|---|---|
| **Groq** (default) | Free | ~300 tok/s | Llama 3.3 70B | No | Everyday use |
| **Gemini** | Free tier | Fast | Gemini 2.0 Flash | No | Google ecosystem |
| **Ollama** | Free | Depends on GPU | Qwen 2.5, Llama, Mistral | Yes | Privacy / no internet |

---

## Voice Commands

TMOS understands free-form natural language (handled by the AI), plus a few built-in commands routed directly:

| Command | Action |
|---|---|
| `stats` | System stats (CPU, RAM, disk, battery, top processes) |
| `open chrome` / `open vscode` / `open terminal` | Launch apps |
| `screenshot` | Save + copy to clipboard |
| `clip <text>` | Copy text to clipboard |
| `note <text>` | Save a note |
| `remind me at HH:MM to <task>` | Set a reminder |
| `timer start/stop/check [name]` | Timers |
| `browse <url>` | Open URL in default browser |
| `use groq` / `use gemini` / `use ollama` | Switch AI backend |
| `clear` | Clear conversation history |

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl + M` | Toggle microphone |
| `Ctrl + L` | Focus input |
| `Ctrl + ,` | Open settings |
| `Esc` | Close modal / clear focus |

---

## Architecture

```
tmos/
├── main.py              # PyQt6 main window, WakeWordListener, AIStreamWorker
├── bridge.py            # Python ↔ JS QWebChannel bridge (all slots + signals)
├── server.py            # Optional REST server (flask) — lets other tools drive TMOS
├── requirements.txt
├── modules/
│   ├── ai.py            # Groq + Gemini + Ollama dispatch + streaming
│   ├── config.py        # Persistent JSON config at ~/.tmos/config.json
│   ├── tts.py           # VoxCPM2 → edge-tts → pyttsx3 fallback chain
│   ├── reminders.py     # APScheduler cron reminders
│   ├── notes.py         # Notes persistence
│   ├── tools.py         # Timers, calc, clipboard, screenshots
│   ├── files.py         # Path-safe file operations
│   ├── system_info.py   # psutil wrappers
│   └── apps.py          # Launch OS apps
└── ui/
    ├── index.html       # Single-file UI: CSS + JS + animated SVG web orb
    └── qwebchannel.js   # Qt WebChannel client
```

Python talks to the HTML UI via `QWebChannel`:

- **Signals** (Python → JS): `chat_pushed`, `ai_token`, `ai_stream_done`, `stats_pushed`, `reminder_fired`, `wake_detected`, `speaking_pushed`, `backend_pushed`, `config_pushed`, …
- **Slots** (JS → Python): `send_command`, `switch_backend`, `save_groq_key`, `save_gemini_key`, `toggle_always_listen`, `add_reminder`, `add_note`, …

The wake-word listener runs in its own `QThread`, uses `speech_recognition` with Google's free recognizer, and matches fuzzy variants of the wake word ("TMOS", "hey TMOS", "t mos", "temos", "team os") to survive imperfect recognition.

---

## Configuration

Config lives at `~/.tmos/config.json` and is created automatically. Keys in the file:

```json
{
  "ai_backend":     "groq",
  "groq_api_key":   "",
  "groq_model":     "llama-3.3-70b-versatile",
  "gemini_api_key": "",
  "gemini_model":   "gemini-2.0-flash",
  "ollama_model":   "qwen2.5",
  "wake_word":      "tmos",
  "always_listen":  true,
  "voice_enabled":  true
}
```

**Do not commit this file** — it contains your API keys. `.gitignore` already excludes `.tmos/` and `config.json`.

---

## Privacy

- Wake-word audio is sent to Google's free Speech-to-Text endpoint via the `speech_recognition` library. If you need full offline privacy, switch `speech_recognition` to Vosk (docs inside `modules/`).
- AI prompts go to whichever backend is active. Ollama is the only fully-offline option.
- All notes, reminders, and config are stored locally under `~/.tmos/`.

---

## Development

Contributions welcome. Adding a fourth backend is a self-contained task:

1. Add `_xxx_ask(prompt)` and `_xxx_stream(prompt)` to `modules/ai.py`
2. Add the key + model to `_DEFAULTS` in `modules/config.py`
3. Add a button + key field to `ui/index.html`
4. Add a `save_xxx_key` slot to `bridge.py`

Open a pull request.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Acknowledgements

- [Groq](https://groq.com) — fastest free LPU inference
- [Google AI Studio](https://aistudio.google.com) — free Gemini tier
- [Ollama](https://ollama.com) — local LLM runtime
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) — UI toolkit
- [APScheduler](https://apscheduler.readthedocs.io/) — reminder scheduling
- [edge-tts](https://github.com/rany2/edge-tts) — Microsoft Edge voices
