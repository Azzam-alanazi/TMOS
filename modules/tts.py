"""Text-to-speech — VoxCPM2 (local 2B neural model, studio-quality 48kHz).
Fallback chain: VoxCPM2 → edge-tts (online) → pyttsx3 (SAPI).

VoxCPM2 loads in the background at startup so the first few responses
use edge-tts, then seamlessly switch to VoxCPM2 once it's ready.
"""

import os
import queue
import tempfile
import threading
import time

# ── Voice design ──────────────────────────────────────────────────────────────
# VoxCPM2 accepts a natural-language voice description in parentheses.
VOICE_DESC = "(deep, calm, authoritative male voice, clear and precise, futuristic AI assistant) "
MODEL_ID   = "openbmb/VoxCPM2"
CFG_VALUE  = 2.0
TIMESTEPS  = 10          # 10 = fast; 20 = slightly better quality

# edge-tts fallback voice (used while VoxCPM2 loads)
FALLBACK_VOICE = "en-US-ChristopherNeural"

# ── State ─────────────────────────────────────────────────────────────────────
_q:           queue.Queue             = queue.Queue()
_worker_t:    threading.Thread | None = None
_muted:       bool                    = False
_model                                = None
_model_ready: threading.Event         = threading.Event()


# ══════════════════════════════════════════════════════════════════════════════
#  MODEL LOADING  (background thread at startup)
# ══════════════════════════════════════════════════════════════════════════════

def preload() -> None:
    """Kick off model loading in the background — call once at app start."""
    threading.Thread(target=_load_model, daemon=True, name='voxcpm-loader').start()


def _load_model():
    global _model
    try:
        print('[TTS] Loading VoxCPM2 — this takes ~30–60 s on first run...')
        from voxcpm import VoxCPM
        _model = VoxCPM.from_pretrained(MODEL_ID, load_denoiser=False)
        print('[TTS] VoxCPM2 ready ✓')
    except Exception as exc:
        print(f'[TTS] VoxCPM2 load failed ({exc}). Using edge-tts fallback.')
        _model = None
    finally:
        _model_ready.set()


# ══════════════════════════════════════════════════════════════════════════════
#  WORKER THREAD
# ══════════════════════════════════════════════════════════════════════════════

def _worker():
    # Pygame mixer at 48 kHz to match VoxCPM2 sample rate
    try:
        import pygame
        pygame.mixer.init(frequency=48000, size=-16, channels=1, buffer=2048)
        has_pygame = True
    except Exception:
        has_pygame = False

    while True:
        text = _q.get()
        if text is None:        # poison pill
            break
        if not _muted and text.strip():
            _say(text, has_pygame)
        _q.task_done()


def _say(text: str, has_pygame: bool):
    # If model already loaded → use VoxCPM2
    if _model_ready.is_set() and _model is not None:
        _say_voxcpm(text, has_pygame)
        return

    # Model still loading → use edge-tts immediately (don't wait)
    _say_edge(text, has_pygame)

    # If model finished loading while we were speaking, great — next call uses it


def _say_voxcpm(text: str, has_pygame: bool):
    try:
        import soundfile as sf
        wav = _model.generate(
            text=VOICE_DESC + text,
            cfg_value=CFG_VALUE,
            inference_timesteps=TIMESTEPS,
        )
        tmp = tempfile.mktemp(suffix='.wav')
        sf.write(tmp, wav, _model.tts_model.sample_rate)
        _play_file(tmp, has_pygame)
    except Exception as exc:
        print(f'[TTS] VoxCPM2 speak error: {exc}')
        _say_edge(text, has_pygame)


def _say_edge(text: str, has_pygame: bool):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_edge_async(text, has_pygame))
    except Exception as exc:
        print(f'[TTS] edge-tts error: {exc}')
        _say_sapi(text)
    finally:
        loop.close()


async def _edge_async(text: str, has_pygame: bool):
    import edge_tts
    tmp = tempfile.mktemp(suffix='.mp3')
    comm = edge_tts.Communicate(text, FALLBACK_VOICE, rate='+0%', pitch='-5Hz')
    await comm.save(tmp)
    _play_file(tmp, has_pygame)


def _say_sapi(text: str):
    try:
        import pyttsx3
        eng = pyttsx3.init()
        eng.setProperty('rate', 165)
        eng.say(text)
        eng.runAndWait()
    except Exception as exc:
        print(f'[TTS] SAPI fallback error: {exc}')


def _play_file(path: str, has_pygame: bool):
    try:
        if has_pygame:
            import pygame
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.05)
        else:
            os.startfile(path)
            time.sleep(max(2, len(path) / 14))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_worker():
    global _worker_t
    if _worker_t is None or not _worker_t.is_alive():
        _worker_t = threading.Thread(target=_worker, daemon=True, name='tmos-tts')
        _worker_t.start()


def speak(text: str) -> None:
    """Queue text for speech — non-blocking."""
    _ensure_worker()
    clean = (text
             .replace('**', '').replace('*', '').replace('`', '')
             .replace('#',  '').replace('—',  ', ').replace('|',  ', ')
             .replace('→', 'to').replace('↑', 'up').replace('↓', 'down'))
    _q.put(clean)


def toggle_mute() -> bool:
    global _muted
    _muted = not _muted
    return _muted


def set_muted(muted: bool) -> None:
    global _muted
    _muted = muted


def is_muted() -> bool:
    return _muted
