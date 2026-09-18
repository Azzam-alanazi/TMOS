"""T.M.O.S — History Module
Keeps the chat transcript and the AI's conversation memory across restarts,
stored in ~/.tmos/history.json.
"""

import json
import os
import threading
import time

DATA_DIR  = os.path.join(os.path.expanduser('~'), '.tmos')
DATA_FILE = os.path.join(DATA_DIR, 'history.json')
os.makedirs(DATA_DIR, exist_ok=True)

MAX_CHAT = 200          # chat bubbles kept on disk

_lock = threading.Lock()
_data: dict = {'chat': [], 'ai': []}


def _load() -> None:
    global _data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            _data = {'chat': list(loaded.get('chat', [])), 'ai': list(loaded.get('ai', []))}
        except (json.JSONDecodeError, IOError, AttributeError):
            _data = {'chat': [], 'ai': []}


def _save() -> None:
    try:
        tmp = DATA_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_data, f, ensure_ascii=False)
        os.replace(tmp, DATA_FILE)
    except IOError:
        pass


_load()


# ── Chat transcript (what the UI shows) ──────────────────────────────────────

def add_chat(text: str, is_user: bool) -> None:
    if not text or not text.strip():
        return
    with _lock:
        _data['chat'].append({'text': text, 'user': is_user, 'ts': int(time.time())})
        del _data['chat'][:-MAX_CHAT]
        _save()


def get_chat(limit: int = 60) -> list[dict]:
    with _lock:
        return list(_data['chat'][-limit:])


def clear_chat() -> None:
    with _lock:
        _data['chat'].clear()
        _save()


# ── AI memory (what the model remembers) ─────────────────────────────────────

def get_ai() -> list[dict]:
    with _lock:
        return list(_data['ai'])


def set_ai(messages: list[dict]) -> None:
    with _lock:
        _data['ai'] = list(messages)
        _save()
