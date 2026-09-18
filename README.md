# T.M.O.S — Desktop AI Assistant

A futuristic desktop AI assistant with always-on wake-word listening, an animated orb (four styles), and three swappable AI backends (Groq, Gemini, Ollama) that can act on your PC, search the web, know where you are and remember what you tell them. Built with PyQt6 + QWebEngine.

> Say **"TMOS, what's the weather?"** — no buttons required.

---

## Features

- **Three AI backends, one toggle**: Groq (fast + free), Google Gemini (online), or Ollama (local & private). Swap at runtime. They share one conversation memory, which survives restarts.
- **The AI can act on your PC (tool calling)**: *"open Spotify and remind me at 5 to stretch"* just works. The AI can open apps and sites, set reminders and timers, save and read notes, check weather and system stats, control music and volume, use the clipboard, take screenshots and lock the PC. It can never delete files or shut down; those stay behind explicit commands.
- **Knows where you are**: Windows location (street level) when it's switched on, otherwise your IP address (city level), or a place you pick in Settings. The AI gets it with every question, so *"weather tomorrow"*, *"what time is it in Tokyo?"* and *"find a pharmacy near me"* need no city. Turn it off in Settings and nothing is looked up.
- **Looks things up on the web**: for news, prices, scores or anything recent, the AI searches the internet (no API key needed) and can read a page to answer from it.
- **Remembers you**: *"remember that my sister is Sara"*. Facts you tell it are given to the AI with every question, survive restarts, and are listed in Settings, where you can delete each one.
- **24/7 wake-word listening**: continuous mic listener that activates on "TMOS" or "Hey TMOS". It ignores T.M.O.S's own voice, but "stop" still cuts it off mid-sentence.
- **Animated orb, four styles**: Nebula (particle sphere), Plasma (fluid orb), Pulse (sound-wave ring) and Reactor (HUD rings). Click the orb to switch. It reacts to the real speech and turns green while listening and orange while thinking.
- **Streaming responses** with markdown, code blocks with copy buttons, and chips showing which actions the AI took.
- **Voice output**: edge-tts online (7 voices, automatic Arabic voice for Arabic text), pyttsx3 offline fallback, optional VoxCPM2. Code blocks and links aren't read aloud.
- **Speech input**: Google (free) or Groq Whisper (more accurate).
- **Productivity**: one-time or daily reminders, countdown timers with alarms, notes, weather with a 7-day forecast, world clock, calculator.
- **Music & volume**: play/pause, next/previous track and the system volume, for whatever is playing (Spotify, YouTube, VLC…).
- **Everywhere**: global hotkey (Ctrl+Shift+Space) to summon it, start with Windows, lives in the tray.

---

## Screenshots

<img width="1279" height="792" alt="image" src="https://github.com/user-attachments/assets/5bb87b02-5794-464f-8e94-9afa5d3aa54c" />

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
4. Optional: pick a voice, speech recognition, the global hotkey and *Start with Windows*
5. Click **Save**

Now say **"TMOS, hello"** and it replies.

---

## Backend Comparison

| Backend | Cost | Speed | Quality | Offline | Best For |
|---|---|---|---|---|---|
| **Groq** (default) | Free | Very fast | GPT-OSS 120B | No | Everyday use |
| **Gemini** | Free tier | Fast | Gemini 2.5 Flash | No | Google ecosystem |
| **Ollama** | Free | Depends on GPU | Qwen 2.5, Llama, Mistral | Yes | Privacy / no internet |

---

## Voice Commands

These built-in commands run instantly without the AI. Everything else goes to the AI, which can also combine them.

| Command | Action |
|---|---|
| `open spotify` / `open github.com` | Launch any installed app (found through the Start Menu) or website |
| `search X` / `youtube X` | Google / YouTube search |
| `weather` / `weather in Riyadh` / `weather tomorrow` / `forecast` / `will it rain` | Current weather or the forecast, for your location or any city |
| `what time is it` / `time in Tokyo` | Time and date, here or anywhere |
| `where am I` | Your location and where it came from |
| `pharmacy near me` / `nearest gas station` / `directions to the airport` | Places and directions in Google Maps, around you |
| `pause` / `next song` / `previous track` / `volume up` / `volume 30` / `mute the sound` | Music and volume |
| `remember that …` / `what do you remember about me` / `forget …` | Long-term memory (`remember to …` saves a note) |
| `timer 10 minutes` / `timer 25 min called focus` / `cancel timer` | Countdown timers with an alarm |
| `remind me at 5pm to call mom` / `remind me in 20 minutes to stretch` | One-time reminder (add `every day` to repeat) |
| `note buy milk` / `notes` | Save / list notes |
| `stats` / `files` | System stats / home folder |
| `calc 2^10` / `what is 12 * 12` | Calculator |
| `copy <text>` / `clipboard` / `screenshot` | Clipboard and screenshots |
| `use groq` / `use gemini` / `use ollama` | Switch AI backend |
| `model list` / `model 120b` | List models / switch by a unique part of the name |
| `stop` | Stop talking and cancel the current answer |
| `mute` / `unmute` | Turn spoken replies off / on |
| `clear` / `clear memory` | Clear the chat / reset the AI's memory |
| `lock` / `sleep` / `shutdown` / `restart` / `cancel shutdown` | Power |

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl + Shift + Space` | Summon T.M.O.S from any app (change it in Settings) |
| `Esc` | Stop talking / cancel the answer, close Settings |
| `↑` / `↓` | Previous / next command |
| `Ctrl + M` | Speak a command |
| `Ctrl + L` | Focus input |
| `Ctrl + ,` | Open settings |

---

## Architecture

```
tmos/
├── main.py              # PyQt6 window, tray, hotkey, wake-word listener, AI stream worker
├── commands.py          # Built-in commands: a regex + handler each (@command)
├── bridge.py            # Python ↔ JS QWebChannel bridge (all slots + signals)
├── server.py            # Optional REST server (flask), token-protected
├── requirements.txt
├── modules/
│   ├── ai.py            # Groq + Gemini + Ollama: streaming, tool calling, live model lists
│   ├── actions.py       # The tools the AI may call (JSON schemas + dispatcher)
│   ├── location.py      # Where you are: Windows location → IP → manual; world clock
│   ├── web.py           # Web search (ddgs, Wikipedia fallback) + reading a page
│   ├── memory.py        # Facts the AI remembers about you
│   ├── media.py         # Play/pause, tracks, volume (media keys)
│   ├── config.py        # Persistent JSON config at ~/.tmos/config.json
│   ├── history.py       # Chat transcript + AI memory across restarts
│   ├── tts.py           # VoxCPM2 → edge-tts → pyttsx3, interruptible
│   ├── stt.py           # Google or Groq Whisper speech recognition
│   ├── reminders.py     # APScheduler: one-time and daily reminders
│   ├── tools.py         # Timers, calc, clipboard, screenshots, power
│   ├── weather.py       # Open-Meteo current weather + 7-day forecast
│   ├── hotkey.py        # Global hotkey (Win32 RegisterHotKey)
│   ├── autostart.py     # Start with Windows
│   └── notes.py, files.py, system_info.py, apps.py
├── tests/               # pytest: commands, reminders, AI streaming + tool loop, location, web, memory, media
└── ui/
    ├── index.html       # Single-file UI: CSS + JS + canvas orb (4 styles)
    └── qwebchannel.js   # Qt WebChannel client
```

Python talks to the HTML UI via `QWebChannel`:

- **Signals** (Python → JS): `chat_pushed`, `ai_token`, `ai_stream_done`, `tool_pushed`, `busy_pushed`, `speaking_pushed`, `timers_pushed`, `alert_pushed`, `models_pushed`, `config_pushed`, …
- **Slots** (JS → Python): `ui_ready`, `send_command`, `stop`, `save_settings`, `switch_backend`, `switch_model`, `add_reminder`, `cancel_timer`, …

A typed or spoken command first goes through `commands.dispatch()`. If no built-in command matches, it streams to the AI along with the tools from `modules/actions.py`. The AI can call tools for several rounds before it answers.

---

## Configuration

Config lives at `~/.tmos/config.json`, is created automatically and is edited from the ⚙ Settings dialog:

```json
{
  "ai_backend":     "groq",
  "groq_api_key":   "",
  "groq_model":     "openai/gpt-oss-120b",
  "gemini_api_key": "",
  "gemini_model":   "gemini-flash-latest",
  "ollama_model":   "qwen2.5",
  "ai_tools":       true,
  "location_mode":  "auto",
  "location_place": "",
  "wake_word":      "tmos",
  "always_listen":  true,
  "stt_engine":     "auto",
  "voice_enabled":  true,
  "tts_voice":      "en-US-ChristopherNeural",
  "hotkey":         "ctrl+shift+space",
  "start_with_windows": false
}
```

### REST server

`python server.py` starts a REST API on `http://127.0.0.1:3000`. It only accepts connections from this PC unless you add `--lan`. Every request except `GET /` needs the access token printed at startup, sent as `Authorization: Bearer <token>`.

---

## Privacy

- While 24/7 listening is on, everything the mic picks up is sent for recognition: to Google's free endpoint, or to Groq if you choose Groq Whisper in Settings. Turn 24/7 listening off and use the mic button (Ctrl+M) to send audio only on demand.
- AI prompts go to whichever backend is active. Ollama is the only fully-offline option.
- **Location**: in *Automatic* mode T.M.O.S asks Windows for your position (if you allowed that in Windows' privacy settings) and turns it into a place name with OpenStreetMap; otherwise it looks up your IP address with ipwho.is (or ipinfo.io / geojs.io). The place name goes to the AI with every question. Choose *A place I choose* to share only a city you type, or *Off* to share and look up nothing.
- **Web search**: when the AI searches, the query goes to public search engines through the `ddgs` package (or to Wikipedia). Pages it reads are fetched from this PC; it refuses addresses on your local network.
- All notes, reminders, remembered facts and config are stored locally under `~/.tmos/`.

---

## Development

Run the tests with:

```bash
pip install pytest
python -m pytest
```

They use a temporary home folder, so your real `~/.tmos` data is never touched.

Adding a fourth backend is a self-contained task:

1. Add a `_xxx_stream(messages, tools, on_tool, stop)` generator to `modules/ai.py` and register it in `_IMPLS`
2. Add the key + model to `_DEFAULTS` in `modules/config.py`
3. Add a button + key field to `ui/index.html`

A new built-in command is one decorated function in `commands.py`. A new AI tool is one schema plus one handler in `modules/actions.py`.

Free Groq keys allow about 8,000 tokens a minute, and every request carries the tool list, so keep tool descriptions short. When Groq asks to wait a few seconds, T.M.O.S waits and retries instead of failing.

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
