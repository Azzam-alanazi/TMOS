"""T.M.O.S — Config Module
Persistent settings stored in ~/.tmos/config.json.
Handles API keys, backend preference, and other user settings.
"""

import json
import os
from typing import Any

DATA_DIR  = os.path.join(os.path.expanduser('~'), '.tmos')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
os.makedirs(DATA_DIR, exist_ok=True)

_DEFAULTS: dict[str, Any] = {
    'ai_backend':      'groq',          # 'groq' | 'gemini' | 'ollama'
    'gemini_api_key':  '',
    'gemini_model':    'gemini-flash-latest',
    'groq_api_key':    '',
    'groq_model':      'openai/gpt-oss-120b',
    'ollama_model':    'qwen2.5',
    'ai_tools':        True,            # let the AI open apps, set reminders, etc.
    'wake_word':       'tmos',          # e.g. "tmos" or "hey tmos"
    'always_listen':   True,            # continuous wake-word listening
    'stt_engine':      'auto',          # 'auto' | 'local' (Vosk) | 'groq' | 'gemini' | 'google'
    'mic_device':      '',              # microphone name; '' = automatic (skips virtual webcam mics)
    'voice_enabled':   True,
    'tts_voice':       'en-US-ChristopherNeural',
    'orb_style':       'nebula',        # nebula | plasma | pulse | reactor
    'hotkey':          'ctrl+shift+space',
    'start_with_windows': False,
    'server_token':    '',              # generated the first time server.py runs
}

_SECRET_KEYS = ('gemini_api_key', 'groq_api_key', 'server_token')


def _mask(k: str) -> str:
    return k[:6] + '...' + k[-4:] if len(k) > 10 else ('***' if k else '')

_config: dict[str, Any] = {}


def _load() -> None:
    global _config
    _config = dict(_DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8-sig') as f:   # tolerate Notepad's BOM
                _config.update(json.load(f))
        except (json.JSONDecodeError, IOError):
            pass
    # Google has shut down the Gemini 1.x models.
    if str(_config.get('gemini_model', '')).startswith('gemini-1.'):
        _config['gemini_model'] = _DEFAULTS['gemini_model']
    # 'google' used to be the only choice (and the default). 'auto' still falls back to it,
    # but first tries offline recognition and any engine you have a key for.
    if not _config.get('stt_auto_migrated'):
        if _config.get('stt_engine') == 'google':
            _config['stt_engine'] = 'auto'
        _config['stt_auto_migrated'] = True


def _save() -> None:
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(_config, f, indent=2)
    except IOError:
        pass


_load()


def get(key: str, default: Any = None) -> Any:
    return _config.get(key, default if default is not None else _DEFAULTS.get(key))


def set_value(key: str, value: Any) -> None:
    _config[key] = value
    _save()


def get_all() -> dict:
    # Don't expose secrets in full — return masked versions plus "is set" flags
    c = dict(_config)
    for k in _SECRET_KEYS:
        c[f'has_{k}'] = bool(c.get(k))
        c[k] = _mask(c.get(k, ''))
    return c


def update(values: dict) -> None:
    _config.update(values)
    _save()
