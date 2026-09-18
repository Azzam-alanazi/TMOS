"""T.M.O.S — AI Module (Groq + Gemini + Ollama)
Three swappable backends behind one API:
  - Groq   (online, fastest, free key) — default
  - Gemini (online, free tier)
  - Ollama (local, private)
All three share one conversation memory (kept across restarts), stream their
replies token by token, and can call T.M.O.S actions (open apps, set
reminders, timers, weather…) through tool calling — see modules/actions.py.
"""

import json
import re
import threading
import time
from datetime import datetime
from typing import Callable, Generator

import requests

from modules import actions, config, history

# ── Configuration ────────────────────────────────────────────────────────────
OLLAMA_URL = 'http://127.0.0.1:11434'
GROQ_URL   = 'https://api.groq.com/openai/v1'
GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta'

BACKENDS      = ('groq', 'gemini', 'ollama')
BACKEND_NAMES = {'groq': 'Groq', 'gemini': 'Gemini', 'ollama': 'Ollama'}

MAX_HISTORY_MESSAGES = 40      # 20 exchanges of memory
MAX_TOOL_ROUNDS      = 5       # model → tools → model … at most this many times

# Suggested models, shown first and used when the live list can't be fetched.
GROQ_MODELS = {
    'llama-3.3-70b-versatile': {'name': 'Llama 3.3 70B',  'desc': 'Best all-rounder'},
    'openai/gpt-oss-120b':     {'name': 'GPT-OSS 120B',   'desc': 'Strong at using tools'},
    'openai/gpt-oss-20b':      {'name': 'GPT-OSS 20B',    'desc': 'Fast, good tool use'},
    'llama-3.1-8b-instant':    {'name': 'Llama 3.1 8B',   'desc': 'Highest free volume'},
    'qwen/qwen3-32b':          {'name': 'Qwen 3 32B',     'desc': 'Alibaba reasoning'},
    'meta-llama/llama-4-scout-17b-16e-instruct': {'name': 'Llama 4 Scout', 'desc': 'Meta multimodal'},
}

GEMINI_MODELS = {
    # The "-latest" aliases always point at Google's current models, so they keep
    # working when versions are retired (2.5 Flash-Lite already is for new keys).
    'gemini-flash-latest':      {'name': 'Gemini Flash (latest)',      'desc': 'Fast & free (recommended)'},
    'gemini-flash-lite-latest': {'name': 'Gemini Flash-Lite (latest)', 'desc': 'Fastest, highest free limits'},
    'gemini-pro-latest':        {'name': 'Gemini Pro (latest)',        'desc': 'Most capable (low free limits)'},
}

OLLAMA_MODELS = {
    'qwen2.5':  {'name': 'Qwen 2.5',     'desc': 'Best all-rounder, uses tools', 'size': '~4.7 GB'},
    'qwen3':    {'name': 'Qwen 3',       'desc': 'Newer Qwen, uses tools',       'size': '~5.2 GB'},
    'llama3.1': {'name': 'Llama 3.1 8B', 'desc': 'Meta, uses tools',             'size': '~4.9 GB'},
    'llama3.2': {'name': 'Llama 3.2 3B', 'desc': 'Small & fast, uses tools',     'size': '~2.0 GB'},
    'mistral':  {'name': 'Mistral 7B',   'desc': 'Fast local',                   'size': '~4.1 GB'},
    'gemma3':   {'name': 'Gemma 3',      'desc': 'Google, no tool support',      'size': '~3.3 GB'},
}

_STATIC_MODELS = {'groq': GROQ_MODELS, 'gemini': GEMINI_MODELS, 'ollama': OLLAMA_MODELS}
_MODEL_KEYS    = {'groq': 'groq_model', 'gemini': 'gemini_model', 'ollama': 'ollama_model'}

SYSTEM_PROMPT = (
    "You are T.M.O.S (Total Machine Operating System), a personal AI desktop assistant. "
    "You help with daily tasks, answer questions, open apps, manage files, set reminders, "
    "and provide system information. Be concise, helpful, and slightly futuristic in tone — "
    "like an AI from a sci-fi movie. Keep responses short (2-4 sentences) unless asked for detail. "
    "Your replies are usually read aloud, so avoid tables and long lists. "
    "Use markdown for code blocks and emphasis. Be direct and efficient."
)

TOOLS_PROMPT = (
    " You can control the user's Windows PC with the provided tools. When the user asks you "
    "to do something (open an app or website, set a reminder or timer, save a note, check the "
    "weather or system stats…), call the tool instead of explaining how to do it. You may call "
    "several tools for one request. Afterwards, confirm in one short sentence what you did. "
    "Never claim you did something unless the tool call succeeded."
)

# ── State: one conversation shared by all backends ───────────────────────────
_history: list[dict] = [
    m for m in history.get_ai()
    if isinstance(m, dict) and m.get('role') in ('user', 'assistant') and m.get('content')
]
_lock = threading.Lock()
_model_cache: dict[str, tuple[float, list[dict]]] = {}


class BackendError(Exception):
    """A problem to show the user as-is (missing key, unknown model…)."""


# ══════════════════════════════════════════════════════════════════════════════
#  BACKEND / MODEL MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def get_backend() -> str:
    b = config.get('ai_backend')
    return b if b in BACKENDS else 'groq'


def set_backend(backend: str) -> dict:
    backend = backend.lower().strip()
    if backend == 'local':
        backend = 'ollama'
    if backend not in BACKENDS:
        return {'success': False, 'message': 'Backend must be groq, gemini, or ollama.'}
    config.set_value('ai_backend', backend)
    return {'success': True, 'backend': backend}


def get_model(backend: str | None = None) -> str:
    return config.get(_MODEL_KEYS[backend or get_backend()])


def get_available_models(backend: str | None = None) -> dict:
    """{model_id: {'name', 'desc'}} from the last fetched list (or the suggestions)."""
    return {m['id']: {'name': m['name'], 'desc': m['desc']}
            for m in _cached_models(backend or get_backend())}


def set_model(model: str) -> dict:
    """Pick a model by exact id, or by a unique part of its id or name ("70b", "flash-lite")."""
    model = model.strip()
    backend = get_backend()
    if not model:
        return {'success': False, 'message': 'No model name given.'}
    models = _cached_models(backend)
    by_id = {m['id'].lower(): m for m in models}
    chosen = by_id.get(model.lower())
    if chosen is None:
        q = model.lower()
        hits = [m for m in models if q in m['id'].lower() or q in m['name'].lower()]
        if len(hits) == 1:
            chosen = hits[0]
        elif len(hits) > 1:
            names = ', '.join(f"`{m['id']}`" for m in hits[:6])
            return {'success': False, 'message': f'"{model}" matches several models: {names}'}
        elif re.fullmatch(r'[\w./:-]+', model) and re.search(r'[-/:.\d]', model):
            chosen = {'id': model, 'name': model, 'desc': 'custom model'}   # a full id we don't list
        else:
            return {'success': False, 'message': f'Unknown {BACKEND_NAMES[backend]} model: {model}. '
                                                 f'Say `model list` to see them.'}
    config.set_value(_MODEL_KEYS[backend], chosen['id'])
    return {'success': True, 'model': chosen['id'],
            'info': {'name': chosen['name'], 'desc': chosen['desc']}}


def set_gemini_key(key: str) -> dict:
    key = key.strip()
    if not key: return {'success': False, 'message': 'Key empty.'}
    config.set_value('gemini_api_key', key)
    _model_cache.pop('gemini', None)
    return {'success': True, 'message': 'Gemini API key saved.'}


def set_groq_key(key: str) -> dict:
    key = key.strip()
    if not key: return {'success': False, 'message': 'Key empty.'}
    config.set_value('groq_api_key', key)
    _model_cache.pop('groq', None)
    return {'success': True, 'message': 'Groq API key saved.'}


def has_api_key(backend: str | None = None) -> bool:
    b = backend or get_backend()
    if b == 'gemini': return bool(config.get('gemini_api_key'))
    if b == 'groq':   return bool(config.get('groq_api_key'))
    return True  # Ollama never needs one


# ── Live model lists ─────────────────────────────────────────────────────────

def _static_list(backend: str) -> list[dict]:
    return [{'id': k, 'name': v['name'], 'desc': v['desc']} for k, v in _STATIC_MODELS[backend].items()]


def _cached_models(backend: str) -> list[dict]:
    cached = _model_cache.get(backend)
    return cached[1] if cached else _static_list(backend)


def fetch_models(backend: str | None = None, refresh: bool = False) -> tuple[list[dict], str]:
    """Ask the backend which models exist. Returns (models, error_message).
    Makes a network call, so run it off the UI thread. Cached for 10 minutes."""
    backend = backend or get_backend()
    cached = _model_cache.get(backend)
    if cached and not refresh and time.time() - cached[0] < 600:
        return cached[1], ''
    try:
        if backend == 'groq':
            models, err = _fetch_groq_models()
        elif backend == 'gemini':
            models, err = _fetch_gemini_models()
        else:
            models, err = _fetch_ollama_models()
    except requests.exceptions.RequestException:
        models, err = [], ('Ollama is not running.' if backend == 'ollama'
                           else f'Could not reach {BACKEND_NAMES[backend]} (offline?).')
    if not models:
        return _static_list(backend), err
    _model_cache[backend] = (time.time(), models)
    return models, err


def _order(models: list[dict], preferred: dict) -> list[dict]:
    rank = {k: i for i, k in enumerate(preferred)}
    return sorted(models, key=lambda m: (rank.get(m['id'], len(rank)), m['id']))


def _fetch_groq_models() -> tuple[list[dict], str]:
    key = config.get('groq_api_key')
    if not key:
        return [], ''
    resp = requests.get(f'{GROQ_URL}/models', headers={'Authorization': f'Bearer {key}'}, timeout=8)
    if resp.status_code != 200:
        return [], _friendly('Groq', '', resp.status_code, _err_text(resp))
    skip = ('whisper', 'guard', 'tts', 'orpheus', 'playai', 'distil', 'allam', 'compound')
    models = []
    for m in resp.json().get('data', []):
        mid = m.get('id', '')
        if not mid or m.get('active') is False or any(s in mid.lower() for s in skip):
            continue
        known = GROQ_MODELS.get(mid, {})
        models.append({'id': mid, 'name': known.get('name', mid),
                       'desc': known.get('desc', m.get('owned_by', ''))})
    return _order(models, GROQ_MODELS), ''


def _fetch_gemini_models() -> tuple[list[dict], str]:
    key = config.get('gemini_api_key')
    if not key:
        return [], ''
    resp = requests.get(f'{GEMINI_URL}/models', params={'pageSize': 1000},
                        headers={'x-goog-api-key': key}, timeout=8)
    if resp.status_code != 200:
        return [], _friendly('Gemini', '', resp.status_code, _err_text(resp))
    skip = ('embedding', 'tts', 'image', 'live', 'audio', 'computer-use', 'robotics', 'aqa', 'veo')
    models = []
    for m in resp.json().get('models', []):
        mid = m.get('name', '').removeprefix('models/')
        if (not mid.startswith('gemini') or any(s in mid for s in skip)
                or 'generateContent' not in m.get('supportedGenerationMethods', [])):
            continue
        known = GEMINI_MODELS.get(mid, {})
        models.append({'id': mid, 'name': m.get('displayName') or mid, 'desc': known.get('desc', '')})
    return _order(models, GEMINI_MODELS), ''


def _fetch_ollama_models() -> tuple[list[dict], str]:
    resp = requests.get(f'{OLLAMA_URL}/api/tags', timeout=3)
    models = []
    for m in resp.json().get('models', []):
        name = m.get('name', '')
        mid = name.removesuffix(':latest')
        size = m.get('size', 0) / 1024 ** 3
        params = (m.get('details') or {}).get('parameter_size', '')
        models.append({'id': mid, 'name': mid,
                       'desc': ' · '.join(x for x in (params, f'{size:.1f} GB' if size else '') if x)})
    if not models:
        return [], 'Ollama has no models yet. Run `ollama pull qwen2.5` in a terminal.'
    return _order(models, OLLAMA_MODELS), ''


def get_installed_ollama_models() -> list[str]:
    try:
        return [m['id'] for m in _fetch_ollama_models()[0]]
    except Exception:
        return []


def is_ollama_running() -> bool:
    try:
        resp = requests.get(f'{OLLAMA_URL}/api/tags', timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


# ── Conversation history ─────────────────────────────────────────────────────

def clear_history() -> None:
    with _lock:
        _history.clear()
        history.set_ai(_history)


# ══════════════════════════════════════════════════════════════════════════════
#  SHARED HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _system_prompt(tools_on: bool) -> str:
    now = datetime.now()
    prompt = SYSTEM_PROMPT + f" Current date and time: {now:%A %d %B %Y, %H:%M}."
    if tools_on:
        prompt += TOOLS_PROMPT
    return prompt


def _err_text(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300]
    err = data.get('error') if isinstance(data, dict) else None
    if isinstance(err, dict):
        return err.get('message') or json.dumps(err)[:300]
    if isinstance(err, str):
        return err
    return resp.text[:300]


def _friendly(backend: str, model: str, status: int, err: str) -> str:
    low = err.lower()
    if status in (401, 403) or 'api key' in low or 'api_key' in low:
        return f'{backend} rejected the API key. Open ⚙ **Settings** and paste a valid key.'
    if status == 429:
        return f'{backend} rate limit reached. Wait a minute, pick a smaller model, or switch backend.'
    if model and (status == 404 or 'not found' in low or 'does not exist' in low
                  or 'decommissioned' in low or 'not supported' in low):
        return f"{backend} can't use the model **{model}** ({err}). Pick another one from the model list."
    return f'{backend} error (HTTP {status}): {err}'


def _stream_lines(resp: requests.Response) -> Generator[dict, None, None]:
    """JSON objects from a server-sent-events (Groq, Gemini) or NDJSON (Ollama) stream."""
    for raw in resp.iter_lines():
        if not raw:
            continue
        line = raw.decode('utf-8', errors='replace').strip()
        if line.startswith('data:'):
            line = line[5:].strip()
        elif line.startswith(('event:', 'id:', ':')):
            continue
        if not line or line == '[DONE]':
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _run_tool(name: str, raw_args, on_tool) -> dict:
    args = actions.parse_args(raw_args)
    result = actions.run(name, args)
    print(f'[AI] tool {name}({json.dumps(args, ensure_ascii=False)}) → {json.dumps(result, ensure_ascii=False)[:200]}')
    if on_tool:
        try:
            on_tool(name, args, result)
        except Exception as e:
            print(f'[AI] on_tool callback error: {e}')
    return result


class _ThinkFilter:
    """Drops <think>…</think> reasoning that some models (Qwen 3, DeepSeek R1)
    put into their reply, even when a tag is split across stream chunks."""
    OPEN, CLOSE = '<think>', '</think>'

    def __init__(self):
        self.buf = ''
        self.inside = False

    def feed(self, token: str) -> str:
        self.buf += token
        out = ''
        while self.buf:
            if self.inside:
                i = self.buf.find(self.CLOSE)
                if i < 0:
                    self.buf = self.buf[-(len(self.CLOSE) - 1):]
                    return out
                self.buf = self.buf[i + len(self.CLOSE):]
                self.inside = False
            else:
                i = self.buf.find(self.OPEN)
                if i < 0:
                    keep = next((k for k in range(len(self.OPEN) - 1, 0, -1)
                                 if self.buf.endswith(self.OPEN[:k])), 0)
                    out += self.buf[:len(self.buf) - keep]
                    self.buf = self.buf[len(self.buf) - keep:]
                    return out
                out += self.buf[:i]
                self.buf = self.buf[i + len(self.OPEN):]
                self.inside = True
        return out

    def flush(self) -> str:
        out = '' if self.inside else self.buf
        self.buf = ''
        return out


# ══════════════════════════════════════════════════════════════════════════════
#  GROQ BACKEND (OpenAI-compatible, ultra-fast LPU)
# ══════════════════════════════════════════════════════════════════════════════

def _groq_stream(messages: list[dict], tools, on_tool, stop) -> Generator[str, None, str]:
    key = config.get('groq_api_key')
    if not key:
        raise BackendError('No Groq API key set yet. Open ⚙ **Settings** and paste a free key '
                           'from console.groq.com/keys.')
    model = config.get('groq_model')
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    msgs = [{'role': 'system', 'content': _system_prompt(bool(tools))}] + messages
    tool_defs = [{'type': 'function', 'function': s} for s in tools] if tools else None
    tool_retries = 1     # models occasionally write a malformed tool call; retry once, then go plain
    full = ''

    rounds = 0
    while rounds <= MAX_TOOL_ROUNDS:
        body = {'model': model, 'messages': msgs, 'temperature': 0.6,
                'max_tokens': 1024, 'stream': True}
        if tool_defs and rounds < MAX_TOOL_ROUNDS:
            body['tools'] = tool_defs
            body['tool_choice'] = 'auto'
        if re.search(r'qwen3|deepseek', model, re.IGNORECASE):
            body['reasoning_format'] = 'hidden'

        with requests.post(f'{GROQ_URL}/chat/completions', headers=headers, json=body,
                           timeout=(10, 60), stream=True) as resp:
            if resp.status_code != 200:
                err = _err_text(resp)
                if tool_defs and resp.status_code == 400 and 'tool' in err.lower():
                    if tool_retries:
                        tool_retries -= 1
                    else:
                        tool_defs = None
                    continue
                raise BackendError(_friendly('Groq', model, resp.status_code, err))

            text, calls, failed = '', {}, None
            think = _ThinkFilter()
            for chunk in _stream_lines(resp):
                if stop():
                    return full + text
                if 'error' in chunk:
                    failed = chunk['error']
                    break
                for ch in chunk.get('choices', []):
                    delta = ch.get('delta') or {}
                    tok = think.feed(delta.get('content') or '')
                    if tok:
                        text += tok
                        yield tok
                    for tc in delta.get('tool_calls') or []:
                        slot = calls.setdefault(tc.get('index', len(calls)),
                                                {'id': '', 'name': '', 'args': ''})
                        fn = tc.get('function') or {}
                        slot['id'] = tc.get('id') or slot['id']
                        slot['name'] += fn.get('name') or ''
                        slot['args'] += fn.get('arguments') or ''
            tail = think.flush()
            if tail:
                text += tail
                yield tail

        if failed:
            msg = failed.get('message', str(failed)) if isinstance(failed, dict) else str(failed)
            if tool_defs and 'tool' in msg.lower() and not text:
                if tool_retries:
                    tool_retries -= 1
                else:
                    tool_defs = None
                continue
            raise BackendError(f'Groq error: {msg}')

        full += text
        if not calls:
            return full
        msgs.append({'role': 'assistant', 'content': text or None, 'tool_calls': [
            {'id': c['id'] or f'call_{i}', 'type': 'function',
             'function': {'name': c['name'], 'arguments': c['args'] or '{}'}}
            for i, c in enumerate(calls.values())
        ]})
        for i, c in enumerate(calls.values()):
            if stop():
                return full
            result = _run_tool(c['name'], c['args'], on_tool)
            msgs.append({'role': 'tool', 'tool_call_id': c['id'] or f'call_{i}',
                         'content': json.dumps(result, ensure_ascii=False)})
        if text:
            full += '\n\n'
            yield '\n\n'
        rounds += 1
    return full


# ══════════════════════════════════════════════════════════════════════════════
#  GEMINI BACKEND
# ══════════════════════════════════════════════════════════════════════════════

def _upper_types(node):
    """Gemini's schema wants OBJECT/STRING/… type names."""
    if isinstance(node, dict):
        return {k: (v.upper() if k == 'type' and isinstance(v, str) else _upper_types(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_upper_types(x) for x in node]
    return node


def _gemini_decl(schema: dict) -> dict:
    decl = {'name': schema['name'], 'description': schema['description']}
    params = schema.get('parameters') or {}
    if params.get('properties'):          # Gemini rejects an empty OBJECT schema
        decl['parameters'] = _upper_types(params)
    return decl


def _gemini_stream(messages: list[dict], tools, on_tool, stop) -> Generator[str, None, str]:
    key = config.get('gemini_api_key')
    if not key:
        raise BackendError('No Gemini API key set yet. Open ⚙ **Settings** and paste a free key '
                           'from aistudio.google.com/apikey.')
    model = config.get('gemini_model')
    contents = [{'role': 'model' if m['role'] == 'assistant' else 'user',
                 'parts': [{'text': m['content']}]} for m in messages]
    gen_cfg = {'temperature': 0.7, 'maxOutputTokens': 2048}
    if 'flash' in model:
        # Flash models "think" by default: seconds of delay for a voice assistant, and the
        # thinking can use up the whole reply budget (an empty answer). Answer right away.
        gen_cfg['thinkingConfig'] = {'thinkingBudget': 0}
    decls = [_gemini_decl(s) for s in tools] if tools else None
    full = ''

    for rounds in range(MAX_TOOL_ROUNDS + 1):
        body = {
            'contents': contents,
            'systemInstruction': {'parts': [{'text': _system_prompt(bool(tools))}]},
            'generationConfig': gen_cfg,
        }
        if decls and rounds < MAX_TOOL_ROUNDS:
            body['tools'] = [{'functionDeclarations': decls}]

        with requests.post(f'{GEMINI_URL}/models/{model}:streamGenerateContent',
                           params={'alt': 'sse'}, headers={'x-goog-api-key': key},
                           json=body, timeout=(10, 90), stream=True) as resp:
            if resp.status_code != 200:
                err = _err_text(resp)
                if resp.status_code == 400 and gen_cfg.pop('thinkingConfig', None):
                    continue                  # this model can't switch thinking off — ask again without
                raise BackendError(_friendly('Gemini', model, resp.status_code, err))

            text, parts, calls = '', [], []
            for chunk in _stream_lines(resp):
                if stop():
                    return full + text
                if 'error' in chunk:
                    raise BackendError(f"Gemini error: {chunk['error'].get('message', chunk['error'])}")
                for cand in chunk.get('candidates', [])[:1]:
                    for p in (cand.get('content') or {}).get('parts', []):
                        if p.get('thought'):
                            continue
                        if 'functionCall' in p:
                            calls.append(p['functionCall'])
                            parts.append(p)          # keeps thoughtSignature, which Gemini 3 requires back
                            continue
                        tok = p.get('text', '')
                        if tok or p.get('thoughtSignature'):
                            parts.append(p)
                        if tok:
                            text += tok
                            yield tok

        full += text
        if not calls:
            return full
        contents.append({'role': 'model', 'parts': parts})
        responses = []
        for fc in calls:
            if stop():
                return full
            result = _run_tool(fc.get('name', ''), fc.get('args') or {}, on_tool)
            fr = {'name': fc.get('name', ''), 'response': result}
            if fc.get('id'):
                fr['id'] = fc['id']
            responses.append({'functionResponse': fr})
        contents.append({'role': 'user', 'parts': responses})
        if text:
            full += '\n\n'
            yield '\n\n'
    return full


# ══════════════════════════════════════════════════════════════════════════════
#  OLLAMA BACKEND
# ══════════════════════════════════════════════════════════════════════════════

def _ollama_stream(messages: list[dict], tools, on_tool, stop) -> Generator[str, None, str]:
    model = config.get('ollama_model')
    msgs = [{'role': 'system', 'content': _system_prompt(bool(tools))}] + messages
    tool_defs = [{'type': 'function', 'function': s} for s in tools] if tools else None
    full = ''

    rounds = 0
    while rounds <= MAX_TOOL_ROUNDS:
        body = {'model': model, 'messages': msgs, 'stream': True,
                'options': {'temperature': 0.7, 'num_predict': 1024}}
        if tool_defs and rounds < MAX_TOOL_ROUNDS:
            body['tools'] = tool_defs

        with requests.post(f'{OLLAMA_URL}/api/chat', json=body,
                           timeout=(5, 180), stream=True) as resp:
            if resp.status_code != 200:
                err = _err_text(resp)
                low = err.lower()
                if tool_defs and 'tool' in low and 'support' in low:
                    tool_defs = None          # e.g. gemma3 — answer without tools
                    continue
                if resp.status_code == 404 or 'not found' in low:
                    raise BackendError(
                        f"Ollama doesn't have **{model}** downloaded yet. Run this in a terminal, "
                        f"then try again:\n```\nollama pull {model}\n```")
                raise BackendError(f'Ollama error (HTTP {resp.status_code}): {err}')

            text, calls = '', []
            think = _ThinkFilter()
            for chunk in _stream_lines(resp):
                if stop():
                    return full + text
                if chunk.get('error'):
                    raise BackendError(f"Ollama error: {chunk['error']}")
                msg = chunk.get('message') or {}
                tok = think.feed(msg.get('content') or '')
                if tok:
                    text += tok
                    yield tok
                calls.extend(msg.get('tool_calls') or [])
                if chunk.get('done'):
                    break
            tail = think.flush()
            if tail:
                text += tail
                yield tail

        full += text
        if not calls:
            return full
        msgs.append({'role': 'assistant', 'content': text, 'tool_calls': calls})
        for c in calls:
            if stop():
                return full
            fn = c.get('function') or {}
            result = _run_tool(fn.get('name', ''), fn.get('arguments') or {}, on_tool)
            msgs.append({'role': 'tool', 'tool_name': fn.get('name', ''),
                         'content': json.dumps(result, ensure_ascii=False)})
        if text:
            full += '\n\n'
            yield '\n\n'
        rounds += 1
    return full


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API (auto-dispatches to active backend)
# ══════════════════════════════════════════════════════════════════════════════

_IMPLS = {'groq': _groq_stream, 'gemini': _gemini_stream, 'ollama': _ollama_stream}


def _lstrip_stream(gen: Generator[str, None, str]) -> Generator[str, None, str]:
    """Pass tokens through, dropping leading whitespace; returns gen's return value."""
    started = False
    try:
        while True:
            try:
                tok = next(gen)
            except StopIteration as e:
                return e.value or ''
            if not started:
                tok = tok.lstrip()
                if not tok:
                    continue
                started = True
            yield tok
    finally:
        gen.close()


def ask_stream(prompt: str,
               on_tool: Callable[[str, dict, dict], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> Generator[str, None, None]:
    """Stream the reply to prompt. on_tool(name, args, result) fires after each
    action the AI takes; should_stop() lets the caller cancel mid-answer."""
    prompt = (prompt or '').strip()
    if not prompt:
        yield 'Please provide a prompt.'
        return
    stop = should_stop or (lambda: False)
    backend = get_backend()
    name = BACKEND_NAMES[backend]
    with _lock:
        messages = list(_history)
    messages.append({'role': 'user', 'content': prompt})
    tools = actions.SCHEMAS if config.get('ai_tools') else None
    used_tools: list[str] = []

    def _on_tool(tool, args, result):
        used_tools.append(tool)
        if on_tool:
            on_tool(tool, args, result)

    try:
        reply = yield from _lstrip_stream(_IMPLS[backend](messages, tools, _on_tool, stop))
    except BackendError as e:
        yield str(e)
        return
    except requests.exceptions.ConnectionError:
        if backend == 'ollama':
            yield (f"Ollama isn't running. Install it from ollama.com (or start it), then run "
                   f"`ollama pull {get_model('ollama')}`. Or switch to Groq.")
        else:
            yield 'No internet connection. Check your network, or switch to Ollama for offline use.'
        return
    except requests.exceptions.Timeout:
        yield f'{name} took too long to answer. Try again.'
        return
    except requests.exceptions.RequestException as e:
        yield f'{name} request failed: {e}'
        return

    if stop():
        return          # cancelled — don't remember a half answer
    reply = (reply or '').strip()
    if not reply:
        if not used_tools:
            yield f'No response from {name}. Try again or pick another model.'
            return
        reply = 'Done.'
        yield reply
    with _lock:
        _history.extend([{'role': 'user', 'content': prompt},
                         {'role': 'assistant', 'content': reply}])
        del _history[:-MAX_HISTORY_MESSAGES]
        history.set_ai(_history)


def ask(prompt: str) -> str:
    """Blocking version of ask_stream() — returns the whole reply."""
    return ''.join(ask_stream(prompt)).strip()
