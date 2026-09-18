"""Text-to-speech — VoxCPM2 (local 2B neural model, studio-quality 48kHz).
Fallback chain: VoxCPM2 → edge-tts (online) → pyttsx3 (SAPI).

VoxCPM2 loads in the background at startup so the first few responses
use edge-tts, then seamlessly switch to VoxCPM2 once it's ready.

Speech can be interrupted with stop(), and set_state_callback() reports when
speech really starts and ends (drives the orb animation and mic gating).
"""

import os
import queue
import re
import tempfile
import threading
import time
from typing import Callable

from modules import config

# ── Voice design ──────────────────────────────────────────────────────────────
# VoxCPM2 accepts a natural-language voice description in parentheses.
VOICE_DESC = "(deep, calm, authoritative male voice, clear and precise, futuristic AI assistant) "
MODEL_ID   = "openbmb/VoxCPM2"
CFG_VALUE  = 2.0
TIMESTEPS  = 10          # 10 = fast; 20 = slightly better quality

# edge-tts voices (used while VoxCPM2 loads, or when it isn't installed)
FALLBACK_VOICE = "en-US-ChristopherNeural"
ARABIC_VOICE   = "ar-SA-HamedNeural"
VOICES = {
    'en-US-ChristopherNeural': 'Christopher (US, deep)',
    'en-US-GuyNeural':         'Guy (US)',
    'en-US-AriaNeural':        'Aria (US)',
    'en-US-JennyNeural':       'Jenny (US)',
    'en-GB-RyanNeural':        'Ryan (UK)',
    'en-GB-SoniaNeural':       'Sonia (UK)',
    'en-AU-WilliamNeural':     'William (AU)',
}

# ── State ─────────────────────────────────────────────────────────────────────
_q:           queue.Queue             = queue.Queue()
_worker_t:    threading.Thread | None = None
_muted:       bool                    = not config.get('voice_enabled')
_model                                = None
_model_ready: threading.Event         = threading.Event()
_gen:         int                     = 0      # bumped by stop(); older speech is dropped
_playing_gen: int                     = 0
_speaking:    bool                    = False
_last_active: float                   = 0.0
_on_state:    Callable[[bool], None] | None = None


# ══════════════════════════════════════════════════════════════════════════════
#  MODEL LOADING  (background thread at startup)
# ══════════════════════════════════════════════════════════════════════════════

def preload() -> None:
    """Kick off model loading in the background — call once at app start."""
    threading.Thread(target=_load_model, daemon=True, name='voxcpm-loader').start()


def _load_model():
    global _model
    try:
        from voxcpm import VoxCPM
        print('[TTS] Loading VoxCPM2 — this takes ~30–60 s on first run...')
        _model = VoxCPM.from_pretrained(MODEL_ID, load_denoiser=False)
        print('[TTS] VoxCPM2 ready ✓')
    except ImportError:
        _model = None       # optional extra, edge-tts is the normal voice
    except Exception as exc:
        print(f'[TTS] VoxCPM2 load failed ({exc}). Using edge-tts fallback.')
        _model = None
    finally:
        _model_ready.set()


# ══════════════════════════════════════════════════════════════════════════════
#  WORKER THREAD
# ══════════════════════════════════════════════════════════════════════════════

def _set_speaking(on: bool) -> None:
    global _speaking, _last_active
    _last_active = time.time()
    if on == _speaking:
        return
    _speaking = on
    if _on_state:
        try:
            _on_state(on)
        except Exception as exc:
            print(f'[TTS] state callback error: {exc}')


def _worker():
    # Pygame mixer at 48 kHz to match VoxCPM2 sample rate
    try:
        import pygame
        pygame.mixer.init(frequency=48000, size=-16, channels=1, buffer=2048)
        has_pygame = True
    except Exception:
        has_pygame = False

    global _playing_gen
    while True:
        item = _q.get()
        if item is None:        # poison pill
            break
        gen, text = item
        _playing_gen = gen
        if not _muted and text.strip() and not _interrupted():
            _say(text, has_pygame)
        _q.task_done()
        if _q.empty():
            _set_speaking(False)


def _interrupted() -> bool:
    return _playing_gen != _gen


def _say(text: str, has_pygame: bool):
    # If model already loaded → use VoxCPM2
    if _model_ready.is_set() and _model is not None:
        _say_voxcpm(text, has_pygame)
        return

    # Model still loading → use edge-tts immediately (don't wait)
    _say_edge(text, has_pygame)


def _tmp_path(suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix='tmos_tts_')
    os.close(fd)
    return path


def _say_voxcpm(text: str, has_pygame: bool):
    try:
        import soundfile as sf
        wav = _model.generate(
            text=VOICE_DESC + text,
            cfg_value=CFG_VALUE,
            inference_timesteps=TIMESTEPS,
        )
        tmp = _tmp_path('.wav')
        sf.write(tmp, wav, _model.tts_model.sample_rate)
        _play_file(tmp, has_pygame)
    except Exception as exc:
        print(f'[TTS] VoxCPM2 speak error: {exc}')
        _say_edge(text, has_pygame)


def _voice_for(text: str) -> str:
    arabic = len(re.findall(r'[؀-ۿ]', text))
    letters = len(re.findall(r'[A-Za-z؀-ۿ]', text)) or 1
    if arabic / letters > 0.3:
        return ARABIC_VOICE
    return config.get('tts_voice') or FALLBACK_VOICE


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
    tmp = _tmp_path('.mp3')
    try:
        comm = edge_tts.Communicate(text, _voice_for(text), rate='+0%', pitch='-5Hz')
        await comm.save(tmp)
    except Exception:
        _remove(tmp)
        raise
    _play_file(tmp, has_pygame)


def _say_sapi(text: str):
    if _interrupted():
        return
    try:
        import pyttsx3
        _set_speaking(True)
        eng = pyttsx3.init()
        eng.setProperty('rate', 165)
        eng.say(text)
        eng.runAndWait()
    except Exception as exc:
        print(f'[TTS] SAPI fallback error: {exc}')


def _remove(path: str):
    try:
        os.unlink(path)
    except OSError:
        pass


def _play_file(path: str, has_pygame: bool):
    try:
        if _interrupted():
            return
        _set_speaking(True)
        if has_pygame:
            import pygame
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                if _interrupted():
                    pygame.mixer.music.stop()
                    break
                time.sleep(0.03)
            pygame.mixer.music.unload()     # release the file so it can be deleted
        else:
            os.startfile(path)
            time.sleep(3)
    finally:
        _remove(path)


# ══════════════════════════════════════════════════════════════════════════════
#  TEXT CLEANUP
# ══════════════════════════════════════════════════════════════════════════════

def clean_for_speech(text: str) -> str:
    """Turn markdown into something that sounds natural: code blocks, URLs,
    tables and formatting symbols are not read aloud."""
    had_code = bool(re.search(r'```', text))
    text = re.sub(r'```.*?(```|$)', ' ', text, flags=re.DOTALL)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)          # [label](url) → label
    text = re.sub(r'https?://\S+', 'the link', text)
    text = re.sub(r'^\s*\|.*\|\s*$', ' ', text, flags=re.MULTILINE)  # tables
    text = re.sub(r'^\s{0,3}#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*(?:[-*•]|\d+[.)])\s+', '', text, flags=re.MULTILINE)
    text = (text.replace('**', '').replace('__', '').replace('*', '').replace('`', '')
                .replace('—', ', ').replace('|', ', ').replace('→', ' to ')
                .replace('↑', 'up').replace('↓', 'down').replace('°C', ' degrees')
                .replace('°', ' degrees').replace('%', ' percent'))
    text = re.sub(r'\s*\n+\s*', '. ', text)
    text = re.sub(r'([.!?:;,])(\s*\.)+', r'\1', text)
    text = re.sub(r'\s{2,}', ' ', text).strip(' .')
    if had_code:
        text = (text + '. ' if text else '') + "I've put the code on screen."
    return text


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
    if _muted:
        return
    clean = clean_for_speech(text)
    if not clean:
        return
    _ensure_worker()
    _q.put((_gen, clean))


def stop() -> bool:
    """Stop talking now and drop anything queued. Returns True if it was speaking."""
    global _gen
    was = _speaking or not _q.empty()
    _gen += 1
    while True:
        try:
            _q.get_nowait()
            _q.task_done()
        except queue.Empty:
            break
    if _worker_t is not None and _worker_t.is_alive():
        _q.put((_gen, ''))  # wakes the worker so it reports "not speaking"
    return was


def set_state_callback(fn: Callable[[bool], None] | None) -> None:
    """fn(True) when speech starts, fn(False) when it ends. Called from the TTS thread."""
    global _on_state
    _on_state = fn


def is_speaking() -> bool:
    return _speaking


def last_active() -> float:
    """time.time() of the last moment speech started or ended."""
    return _last_active


def toggle_mute() -> bool:
    set_muted(not _muted)
    return _muted


def set_muted(muted: bool) -> None:
    global _muted
    _muted = muted
    config.set_value('voice_enabled', not muted)
    if muted:
        stop()


def is_muted() -> bool:
    return _muted
