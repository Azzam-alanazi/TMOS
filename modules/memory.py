"""T.M.O.S — Long-term Memory
Facts the user asks T.M.O.S to keep ("my sister is called Sara", "I'm
vegetarian"). They are given to the AI with every question, survive restarts
and "clear memory" (which only resets the conversation), and are listed in
⚙ Settings, where each one can be deleted. Stored in ~/.tmos/memory.json.
"""

import json
import os
import re
import threading
import time
import uuid

DATA_DIR  = os.path.join(os.path.expanduser('~'), '.tmos')
DATA_FILE = os.path.join(DATA_DIR, 'memory.json')
os.makedirs(DATA_DIR, exist_ok=True)

MAX_FACTS = 60
MAX_LEN   = 300

_lock = threading.Lock()
_facts: list[dict] = []


def _load() -> None:
    global _facts
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        _facts = [x for x in loaded if isinstance(x, dict) and x.get('text')]
    except (FileNotFoundError, json.JSONDecodeError, IOError, TypeError):
        _facts = []


def _save() -> None:
    try:
        tmp = DATA_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_facts, f, indent=2, ensure_ascii=False)
        os.replace(tmp, DATA_FILE)
    except IOError:
        pass


_load()


def get_all() -> list[dict]:
    with _lock:
        return [dict(f) for f in _facts]


def add(text: str) -> dict:
    text = ' '.join((text or '').split())[:MAX_LEN].rstrip('.') + '.'
    if len(text) < 3:
        return {'success': False, 'message': 'Nothing to remember.'}
    with _lock:
        if any(f['text'].lower() == text.lower() for f in _facts):
            return {'success': True, 'message': 'I already remember that.', 'text': text}
        if len(_facts) >= MAX_FACTS:
            return {'success': False, 'message': f'My memory is full ({MAX_FACTS} facts). '
                                                 'Delete some in ⚙ Settings first.'}
        _facts.append({'id': uuid.uuid4().hex[:10], 'text': text,
                       'created': time.strftime('%Y-%m-%d %H:%M')})
        _save()
    return {'success': True, 'message': 'Got it, I will remember that.', 'text': text}


def remove(fid: str) -> dict:
    with _lock:
        before = len(_facts)
        _facts[:] = [f for f in _facts if f['id'] != fid]
        if len(_facts) == before:
            return {'success': False, 'message': 'No such fact.'}
        _save()
    return {'success': True}


def forget(query: str) -> dict:
    """Delete the facts that match query: every fact containing it, else the
    one sharing the most words with it."""
    q = ' '.join((query or '').lower().split()).rstrip('.')
    if not q:
        return {'success': False, 'message': 'Say what I should forget.'}
    with _lock:
        hits = [f for f in _facts if q in f['text'].lower()]
        if not hits:
            words = _words(q)
            scored = [(len(words & _words(f['text'])) / max(len(words), 1), f) for f in _facts]
            best = max(scored, key=lambda s: s[0], default=(0, None))
            hits = [best[1]] if best[0] >= 0.5 else []
        if not hits:
            return {'success': False, 'message': f'I have nothing remembered about "{query}".'}
        ids = {f['id'] for f in hits}
        _facts[:] = [f for f in _facts if f['id'] not in ids]
        _save()
    return {'success': True, 'forgotten': [f['text'] for f in hits]}


def clear_all() -> dict:
    with _lock:
        n = len(_facts)
        _facts.clear()
        _save()
    return {'success': True, 'cleared': n}


def prompt_block() -> str:
    """The facts, for the AI's system prompt ('' when there are none)."""
    facts = get_all()
    if not facts:
        return ''
    lines = '\n'.join(f'- {f["text"]}' for f in facts)
    return ('\n\nFacts the user asked you to remember ("I" and "my" mean the user). '
            f'Use them when relevant:\n{lines}')


_STOP = {'the', 'a', 'an', 'my', 'is', 'am', 'i', 'to', 'of', 'that', 'and', 'in', 'on', 'me'}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r'\w+', text.lower()) if w not in _STOP}
