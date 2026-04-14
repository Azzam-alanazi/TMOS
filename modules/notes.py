"""T.M.O.S — Notes Module
Simple persistent note-taking stored as JSON.
"""

import json
import os
import time

DATA_DIR  = os.path.join(os.path.expanduser('~'), '.tmos')
DATA_FILE = os.path.join(DATA_DIR, 'notes.json')
os.makedirs(DATA_DIR, exist_ok=True)

_notes: list[dict] = []


def _load() -> None:
    global _notes
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                _notes = json.load(f)
        except (json.JSONDecodeError, IOError):
            _notes = []
    else:
        _notes = []


def _save() -> None:
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(_notes, f, indent=2, ensure_ascii=False)
    except IOError:
        pass


# Initialize on import
_load()


def get_all() -> list[dict]:
    return _notes.copy()


def add(text: str, title: str = '') -> dict:
    text = text.strip()
    if not text:
        return {'success': False, 'message': 'Note text is required.'}
    note = {
        'id': str(int(time.time() * 1000)),
        'title': title.strip() or text[:40],
        'text': text,
        'created': time.strftime('%Y-%m-%d %H:%M'),
    }
    _notes.append(note)
    _save()
    return {'success': True, 'note': note}


def remove(nid: str) -> dict:
    global _notes
    before = len(_notes)
    _notes = [n for n in _notes if n['id'] != nid]
    if len(_notes) < before:
        _save()
        return {'success': True}
    return {'success': False, 'message': 'Note not found.'}


def clear_all() -> dict:
    global _notes
    count = len(_notes)
    _notes.clear()
    _save()
    return {'success': True, 'cleared': count}


def search(query: str) -> list[dict]:
    q = query.lower()
    return [n for n in _notes if q in n['text'].lower() or q in n.get('title', '').lower()]
