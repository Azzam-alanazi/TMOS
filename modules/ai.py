"""T.M.O.S — AI Module (Gemini + Ollama)
Dual backend support:
  - Gemini (online, fast) — default
  - Ollama (local, private)
Toggle between them at runtime. Conversation history is preserved per-backend.
"""

import json
import requests
from typing import Generator

from modules import config

# ── Configuration ────────────────────────────────────────────────────────────
OLLAMA_URL = 'http://127.0.0.1:11434'
GROQ_URL   = 'https://api.groq.com/openai/v1/chat/completions'

# Free Groq-hosted models (fast LPU inference, OpenAI-compatible API)
GROQ_MODELS = {
    'llama-3.3-70b-versatile': {'name': 'Llama 3.3 70B',     'desc': 'Best quality (1k/day)'},
    'llama-3.1-8b-instant':    {'name': 'Llama 3.1 8B',      'desc': 'Highest volume (14.4k/day)'},
    'qwen/qwen3-32b':          {'name': 'Qwen 3 32B',        'desc': 'Alibaba reasoning'},
    'moonshotai/kimi-k2-instruct': {'name': 'Kimi K2',       'desc': 'Moonshot agent model'},
    'meta-llama/llama-4-scout-17b-16e-instruct': {'name': 'Llama 4 Scout', 'desc': 'Meta multimodal'},
}

# Free Google Gemini models (as of 2026) — fast and free tier available
GEMINI_MODELS = {
    'gemini-2.0-flash':      {'name': 'Gemini 2.0 Flash',      'desc': 'Fast & free (recommended)'},
    'gemini-2.0-flash-lite': {'name': 'Gemini 2.0 Flash-Lite', 'desc': 'Fastest, lowest cost'},
    'gemini-1.5-flash':      {'name': 'Gemini 1.5 Flash',      'desc': 'Stable flash model'},
    'gemini-1.5-pro':        {'name': 'Gemini 1.5 Pro',        'desc': 'Most capable (rate-limited)'},
}

OLLAMA_MODELS = {
    'qwen2.5':  {'name': 'Qwen 2.5',  'desc': 'Best all-rounder',       'size': '~4.7 GB'},
    'llama3.3': {'name': 'Llama 3.3', 'desc': 'Meta general-purpose',   'size': '~4.9 GB'},
    'mistral':  {'name': 'Mistral 7B','desc': 'Fastest local',          'size': '~4.1 GB'},
    'phi4':     {'name': 'Phi-4',     'desc': 'Efficient small model',   'size': '~2.2 GB'},
    'gemma3':   {'name': 'Gemma 3',   'desc': 'Google edge-optimized',   'size': '~5.0 GB'},
    'llama3':   {'name': 'Llama 3',   'desc': 'Meta 8B (legacy)',        'size': '~4.7 GB'},
}

SYSTEM_PROMPT = (
    "You are T.M.O.S (Total Machine Operating System), a personal AI desktop assistant. "
    "You help with daily tasks, answer questions, open apps, manage files, set reminders, "
    "and provide system information. Be concise, helpful, and slightly futuristic in tone — "
    "like an AI from a sci-fi movie. Keep responses short (2-4 sentences) unless asked for detail. "
    "Use markdown formatting for code blocks and emphasis. Be direct and efficient."
)

# ── State: separate histories for each backend ───────────────────────────────
_history_gemini: list[dict] = []   # [{role: 'user'|'model', parts: [{text}]}]
_history_ollama: list[dict] = []   # [{role: 'user'|'assistant', content: ''}]
_history_groq:   list[dict] = []   # [{role: 'user'|'assistant', content: ''}]
_max_history: int = 20


# ══════════════════════════════════════════════════════════════════════════════
#  BACKEND / MODEL MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def get_backend() -> str:
    return config.get('ai_backend')


def set_backend(backend: str) -> dict:
    backend = backend.lower().strip()
    if backend not in ('groq', 'gemini', 'ollama'):
        return {'success': False, 'message': 'Backend must be groq, gemini, or ollama.'}
    config.set_value('ai_backend', backend)
    return {'success': True, 'backend': backend}


def get_model() -> str:
    b = get_backend()
    if b == 'gemini': return config.get('gemini_model')
    if b == 'groq':   return config.get('groq_model')
    return config.get('ollama_model')


def set_model(model: str) -> dict:
    model = model.strip()
    backend = get_backend()
    if backend == 'gemini':
        key, table = 'gemini_model', GEMINI_MODELS
    elif backend == 'groq':
        key, table = 'groq_model', GROQ_MODELS
    else:
        key, table = 'ollama_model', OLLAMA_MODELS
        model = model.lower()
    if model in table:
        config.set_value(key, model)
        return {'success': True, 'model': model, 'info': table[model]}
    return {'success': False, 'message': f'Unknown {backend} model: {model}'}


def get_available_models() -> dict:
    b = get_backend()
    if b == 'gemini': return GEMINI_MODELS
    if b == 'groq':   return GROQ_MODELS
    return OLLAMA_MODELS


def set_api_key(key: str) -> dict:
    """Saves the API key for the currently-active backend."""
    key = key.strip()
    if not key:
        return {'success': False, 'message': 'API key cannot be empty.'}
    b = get_backend()
    if b == 'gemini':
        config.set_value('gemini_api_key', key)
        return {'success': True, 'message': 'Gemini API key saved.'}
    if b == 'groq':
        config.set_value('groq_api_key', key)
        return {'success': True, 'message': 'Groq API key saved.'}
    return {'success': False, 'message': 'Ollama does not use an API key.'}


def set_gemini_key(key: str) -> dict:
    key = key.strip()
    if not key: return {'success': False, 'message': 'Key empty.'}
    config.set_value('gemini_api_key', key)
    return {'success': True, 'message': 'Gemini API key saved.'}


def set_groq_key(key: str) -> dict:
    key = key.strip()
    if not key: return {'success': False, 'message': 'Key empty.'}
    config.set_value('groq_api_key', key)
    return {'success': True, 'message': 'Groq API key saved.'}


def has_api_key() -> bool:
    b = get_backend()
    if b == 'gemini': return bool(config.get('gemini_api_key'))
    if b == 'groq':   return bool(config.get('groq_api_key'))
    return True  # Ollama never needs one


# ── Conversation history ─────────────────────────────────────────────────────

def clear_history() -> None:
    _history_gemini.clear()
    _history_ollama.clear()
    _history_groq.clear()


def _trim(hist: list) -> None:
    while len(hist) > _max_history * 2:
        hist.pop(0)


# ══════════════════════════════════════════════════════════════════════════════
#  GEMINI BACKEND
# ══════════════════════════════════════════════════════════════════════════════

def _gemini_url(model: str, stream: bool = False) -> str:
    method = 'streamGenerateContent' if stream else 'generateContent'
    return f'https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}'


def _gemini_ask(prompt: str) -> str:
    api_key = config.get('gemini_api_key')
    if not api_key:
        return 'No Gemini API key set. Click the ⚙ settings icon to add one, or switch to Ollama.'

    model = config.get('gemini_model')
    # Gemini expects "contents" with role 'user' or 'model'
    _history_gemini.append({'role': 'user', 'parts': [{'text': prompt}]})

    payload = {
        'contents': _history_gemini,
        'systemInstruction': {'parts': [{'text': SYSTEM_PROMPT}]},
        'generationConfig': {
            'temperature': 0.7,
            'topP': 0.9,
            'maxOutputTokens': 512,
        },
    }

    try:
        resp = requests.post(
            _gemini_url(model),
            params={'key': api_key},
            json=payload,
            timeout=30,
        )
        data = resp.json()

        if 'error' in data:
            _history_gemini.pop()  # remove user message on failure
            return f"Gemini error: {data['error'].get('message', 'Unknown error')}"

        candidates = data.get('candidates', [])
        if not candidates:
            _history_gemini.pop()
            return 'No response from Gemini.'

        reply = ''.join(
            p.get('text', '')
            for p in candidates[0].get('content', {}).get('parts', [])
        ).strip()

        if not reply:
            _history_gemini.pop()
            return 'Empty response from Gemini.'

        _history_gemini.append({'role': 'model', 'parts': [{'text': reply}]})
        _trim(_history_gemini)
        return reply

    except requests.exceptions.ConnectionError:
        _history_gemini.pop()
        return 'No internet connection. Check your network or switch to Ollama.'
    except Exception as e:
        if _history_gemini and _history_gemini[-1].get('role') == 'user':
            _history_gemini.pop()
        return f'Gemini error: {e}'


def _gemini_stream(prompt: str) -> Generator[str, None, None]:
    api_key = config.get('gemini_api_key')
    if not api_key:
        yield 'No Gemini API key set. Click the ⚙ settings icon to add one.'
        return

    model = config.get('gemini_model')
    _history_gemini.append({'role': 'user', 'parts': [{'text': prompt}]})

    payload = {
        'contents': _history_gemini,
        'systemInstruction': {'parts': [{'text': SYSTEM_PROMPT}]},
        'generationConfig': {
            'temperature': 0.7,
            'topP': 0.9,
            'maxOutputTokens': 512,
        },
    }

    full_reply = ''
    try:
        resp = requests.post(
            _gemini_url(model, stream=True),
            params={'key': api_key, 'alt': 'sse'},
            json=payload,
            timeout=60,
            stream=True,
        )

        # Non-200 → read body, emit error, stop.
        if resp.status_code != 200:
            body = resp.text
            print(f'[Gemini] HTTP {resp.status_code}: {body}')
            try:
                err = resp.json().get('error', {}).get('message', body)
            except Exception:
                err = body
            _history_gemini.pop()
            yield f'Gemini error (HTTP {resp.status_code}): {err}'
            return

        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode('utf-8', errors='replace').strip()
            if line.startswith('data:'):
                line = line[5:].strip()
            if not line or line == '[DONE]':
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                print(f'[Gemini] non-JSON line: {line[:200]}')
                continue
            if 'error' in chunk:
                _history_gemini.pop()
                yield f"Gemini error: {chunk['error'].get('message')}"
                return
            for cand in chunk.get('candidates', []):
                for p in cand.get('content', {}).get('parts', []):
                    token = p.get('text', '')
                    if token:
                        full_reply += token
                        yield token

        if full_reply.strip():
            _history_gemini.append({'role': 'model', 'parts': [{'text': full_reply}]})
            _trim(_history_gemini)
        else:
            # Streaming yielded nothing — fall back to non-streaming call.
            if _history_gemini and _history_gemini[-1].get('role') == 'user':
                _history_gemini.pop()
            print('[Gemini] stream empty — falling back to non-streaming.')
            fallback = _gemini_ask(prompt)
            yield fallback

    except requests.exceptions.ConnectionError:
        if _history_gemini and _history_gemini[-1].get('role') == 'user':
            _history_gemini.pop()
        yield 'No internet connection. Check your network or switch to Ollama.'
    except Exception as e:
        if _history_gemini and _history_gemini[-1].get('role') == 'user':
            _history_gemini.pop()
        yield f'Gemini error: {e}'


# ══════════════════════════════════════════════════════════════════════════════
#  GROQ BACKEND (OpenAI-compatible, ultra-fast LPU)
# ══════════════════════════════════════════════════════════════════════════════

def _groq_messages(prompt: str) -> list[dict]:
    msgs = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    msgs.extend(_history_groq)
    msgs.append({'role': 'user', 'content': prompt})
    return msgs


def _groq_headers() -> dict | None:
    key = config.get('groq_api_key')
    if not key:
        return None
    return {
        'Authorization': f'Bearer {key}',
        'Content-Type':  'application/json',
    }


def _groq_ask(prompt: str) -> str:
    headers = _groq_headers()
    if headers is None:
        return 'No Groq API key set. Open ⚙ Settings and paste your key from console.groq.com/keys.'

    model = config.get('groq_model')
    try:
        resp = requests.post(
            GROQ_URL,
            headers=headers,
            json={
                'model': model,
                'messages': _groq_messages(prompt),
                'temperature': 0.7,
                'max_tokens': 512,
                'stream': False,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            print(f'[Groq] HTTP {resp.status_code}: {resp.text}')
            try:
                err = resp.json().get('error', {}).get('message', resp.text)
            except Exception:
                err = resp.text
            return f'Groq error (HTTP {resp.status_code}): {err}'

        data = resp.json()
        reply = data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
        if not reply:
            return 'Empty response from Groq.'
        _history_groq.append({'role': 'user',      'content': prompt})
        _history_groq.append({'role': 'assistant', 'content': reply})
        _trim(_history_groq)
        return reply
    except requests.exceptions.ConnectionError:
        return 'No internet connection. Check your network or switch to Ollama.'
    except Exception as e:
        return f'Groq error: {e}'


def _groq_stream(prompt: str) -> Generator[str, None, None]:
    headers = _groq_headers()
    if headers is None:
        yield 'No Groq API key set. Open ⚙ Settings and paste your key from console.groq.com/keys.'
        return

    model = config.get('groq_model')
    full_reply = ''
    try:
        resp = requests.post(
            GROQ_URL,
            headers=headers,
            json={
                'model': model,
                'messages': _groq_messages(prompt),
                'temperature': 0.7,
                'max_tokens': 512,
                'stream': True,
            },
            timeout=60,
            stream=True,
        )
        if resp.status_code != 200:
            body = resp.text
            print(f'[Groq] HTTP {resp.status_code}: {body}')
            try:
                err = resp.json().get('error', {}).get('message', body)
            except Exception:
                err = body
            yield f'Groq error (HTTP {resp.status_code}): {err}'
            return

        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode('utf-8', errors='replace').strip()
            if line.startswith('data:'):
                line = line[5:].strip()
            if not line or line == '[DONE]':
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            for ch in chunk.get('choices', []):
                delta = ch.get('delta', {}) or ch.get('message', {})
                token = delta.get('content', '') or ''
                if token:
                    full_reply += token
                    yield token

        if full_reply.strip():
            _history_groq.append({'role': 'user',      'content': prompt})
            _history_groq.append({'role': 'assistant', 'content': full_reply})
            _trim(_history_groq)
        else:
            print('[Groq] stream empty — falling back to non-streaming.')
            yield _groq_ask(prompt)

    except requests.exceptions.ConnectionError:
        yield 'No internet connection. Check your network or switch to Ollama.'
    except Exception as e:
        yield f'Groq error: {e}'


# ══════════════════════════════════════════════════════════════════════════════
#  OLLAMA BACKEND
# ══════════════════════════════════════════════════════════════════════════════

def _ollama_messages(prompt: str) -> list[dict]:
    msgs = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    msgs.extend(_history_ollama)
    msgs.append({'role': 'user', 'content': prompt})
    return msgs


def _ollama_ask(prompt: str) -> str:
    model = config.get('ollama_model')
    try:
        resp = requests.post(
            f'{OLLAMA_URL}/api/chat',
            json={
                'model': model,
                'messages': _ollama_messages(prompt),
                'stream': False,
                'options': {'temperature': 0.7, 'num_predict': 512},
            },
            timeout=60,
        )
        data = resp.json()
        reply = data.get('message', {}).get('content', '').strip()
        if not reply:
            return 'No response from Ollama.'
        _history_ollama.append({'role': 'user', 'content': prompt})
        _history_ollama.append({'role': 'assistant', 'content': reply})
        _trim(_history_ollama)
        return reply
    except requests.exceptions.ConnectionError:
        return (f'Ollama not running. Start it with:\n```\nollama serve\n```\n'
                f'Then: `ollama pull {model}`')
    except requests.exceptions.Timeout:
        return 'Ollama timed out. Try again in a moment.'
    except Exception as e:
        return f'Ollama error: {e}'


def _ollama_stream(prompt: str) -> Generator[str, None, None]:
    model = config.get('ollama_model')
    full_reply = ''
    try:
        resp = requests.post(
            f'{OLLAMA_URL}/api/chat',
            json={
                'model': model,
                'messages': _ollama_messages(prompt),
                'stream': True,
                'options': {'temperature': 0.7, 'num_predict': 512},
            },
            timeout=60, stream=True,
        )
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
                token = chunk.get('message', {}).get('content', '')
                if token:
                    full_reply += token
                    yield token
                if chunk.get('done', False):
                    break
            except json.JSONDecodeError:
                continue
        if full_reply.strip():
            _history_ollama.append({'role': 'user', 'content': prompt})
            _history_ollama.append({'role': 'assistant', 'content': full_reply})
            _trim(_history_ollama)
    except requests.exceptions.ConnectionError:
        yield (f'Ollama not running. Start it with:\n```\nollama serve\n```\n'
               f'Then: `ollama pull {model}`')
    except Exception as e:
        yield f'Ollama error: {e}'


def get_installed_ollama_models() -> list[str]:
    try:
        resp = requests.get(f'{OLLAMA_URL}/api/tags', timeout=3)
        data = resp.json()
        return [m['name'].split(':')[0] for m in data.get('models', [])]
    except Exception:
        return []


def is_ollama_running() -> bool:
    try:
        resp = requests.get(f'{OLLAMA_URL}/api/tags', timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API (auto-dispatches to active backend)
# ══════════════════════════════════════════════════════════════════════════════

def ask(prompt: str) -> str:
    if not prompt.strip():
        return 'Please provide a prompt.'
    b = get_backend()
    if b == 'gemini': return _gemini_ask(prompt)
    if b == 'groq':   return _groq_ask(prompt)
    return _ollama_ask(prompt)


def ask_stream(prompt: str) -> Generator[str, None, None]:
    if not prompt.strip():
        yield 'Please provide a prompt.'
        return
    b = get_backend()
    if b == 'gemini':
        yield from _gemini_stream(prompt)
    elif b == 'groq':
        yield from _groq_stream(prompt)
    else:
        yield from _ollama_stream(prompt)
