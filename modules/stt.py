"""T.M.O.S — Speech-to-Text Module
Turns captured microphone audio into text.

Engines:
  vosk   — offline (free, private, no rate limits). Used to spot the wake word
           24/7 without sending anything off the PC.
  groq   — Groq Whisper, very accurate (needs a Groq key)
  gemini — Gemini audio understanding (needs a Gemini key)
  google — Google's free web recognizer (no key, but unreliable on some networks)

"auto" (default) spots the wake word with Vosk, then transcribes the actual
command with the most accurate engine you have a key for. If one engine fails
(timeout, rate limit…), the next one is tried.
"""

import base64
import json
import os
import re
import threading
import zipfile

import requests

from modules import config

GROQ_STT_URL   = 'https://api.groq.com/openai/v1/audio/transcriptions'
GROQ_STT_MODEL = 'whisper-large-v3-turbo'
GEMINI_URL     = 'https://generativelanguage.googleapis.com/v1beta'
# Tried in order until one works on your key: a dedicated transcription model, then fast Flash-Lites.
GEMINI_STT_MODELS = ('gemini-3.5-transcribe', 'gemini-flash-lite-latest', 'gemini-3.5-flash-lite',
                     'gemini-2.5-flash-lite')

VOSK_MODEL = 'vosk-model-small-en-us-0.15'
VOSK_URL   = f'https://alphacephei.com/vosk/models/{VOSK_MODEL}.zip'
MODELS_DIR = os.path.join(os.path.expanduser('~'), '.tmos', 'models')

ENGINES = ('auto', 'local', 'groq', 'gemini', 'google')
NAMES = {'vosk': 'offline (Vosk)', 'groq': 'Groq Whisper', 'gemini': 'Gemini', 'google': 'Google'}

# Whisper sometimes "hears" these in silence or background noise.
_NOISE = {'thank you.', 'thank you', 'thanks for watching!', 'you', 'bye.', '.', ''}

_vosk_model = None
_vosk_lock = threading.Lock()
_gemini_stt_model: str | None = None


# ── Which engines to use ─────────────────────────────────────────────────────

def setting() -> str:
    s = config.get('stt_engine')
    return s if s in ENGINES else 'auto'


def vosk_installed() -> bool:
    try:
        import vosk  # noqa: F401
        return True
    except ImportError:
        return False


def _vosk_path() -> str:
    return os.path.join(MODELS_DIR, VOSK_MODEL)


def vosk_ready() -> bool:
    return vosk_installed() and os.path.isdir(_vosk_path())


def _cloud_chain() -> list[str]:
    chain = []
    if config.get('groq_api_key'):
        chain.append('groq')
    if config.get('gemini_api_key'):
        chain.append('gemini')
    chain.append('google')
    return chain


def chain(purpose: str = 'command') -> list[str]:
    """Engines to try, in order. purpose='wake' is the always-on listening loop."""
    s = setting()
    if s == 'local':
        return ['vosk'] if vosk_ready() else _cloud_chain()
    if s == 'auto':
        if purpose == 'wake' and vosk_ready():
            return ['vosk']
        engines = _cloud_chain()
        return engines + (['vosk'] if vosk_ready() else [])
    # an explicit cloud engine first, the rest as fallbacks
    rest = [e for e in _cloud_chain() if e != s]
    return [s] + rest + (['vosk'] if vosk_ready() else [])


def wake_is_local() -> bool:
    return chain('wake') == ['vosk']


def describe() -> str:
    wake, cmd = chain('wake'), chain('command')
    if wake == ['vosk'] and cmd and cmd[0] != 'vosk':
        return f'offline wake word + {NAMES[cmd[0]]} for commands'
    return NAMES[cmd[0]] if cmd else 'none'


# ── Recognize ────────────────────────────────────────────────────────────────

def recognize(recognizer, audio, purpose: str = 'command') -> str:
    """Return the transcript. Raises speech_recognition.UnknownValueError when no
    speech was understood, or RequestError when every engine failed."""
    import speech_recognition as sr

    errors = []
    for engine in chain(purpose):
        try:
            text = _ENGINES[engine](recognizer, audio).strip()
        except sr.UnknownValueError:
            raise
        except Exception as e:           # network error, timeout, rate limit, bad key…
            errors.append(f'{NAMES[engine]}: {_short(e)}')
            print(f'[STT] {NAMES[engine]} failed: {e}')
            continue
        if text.lower() in _NOISE:
            raise sr.UnknownValueError()
        return text
    raise sr.RequestError('Speech recognition failed — ' + '; '.join(errors))


def _short(e: Exception) -> str:
    msg = str(e)
    if '10060' in msg or 'timed out' in msg.lower() or isinstance(e, (TimeoutError, requests.Timeout)):
        return 'timed out'
    if isinstance(e, requests.ConnectionError):
        return 'no connection'
    return msg[:120]


def _google(recognizer, audio) -> str:
    recognizer.operation_timeout = 8          # the default is no timeout (then WinError 10060 after ~21 s)
    return recognizer.recognize_google(audio)


def _groq(recognizer, audio) -> str:
    resp = requests.post(
        GROQ_STT_URL,
        headers={'Authorization': f'Bearer {config.get("groq_api_key")}'},
        files={'file': ('speech.wav', audio.get_wav_data(), 'audio/wav')},
        data={'model': GROQ_STT_MODEL, 'response_format': 'json', 'temperature': '0'},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f'HTTP {resp.status_code}: {resp.text[:150]}')
    return resp.json().get('text') or ''


def _gemini(recognizer, audio) -> str:
    global _gemini_stt_model
    wav = audio.get_wav_data(convert_rate=16000, convert_width=2)
    prompt = ('Transcribe this voice command exactly as spoken, in the language it was spoken in. '
              'Reply with only the transcript and nothing else. If there is no clear speech, '
              'reply with an empty message.')
    candidates = [_gemini_stt_model] if _gemini_stt_model else list(GEMINI_STT_MODELS)
    if config.get('gemini_model') not in candidates:
        candidates.append(config.get('gemini_model'))
    last = ''
    for model in candidates:
        gen = {'temperature': 0, 'maxOutputTokens': 256,
               'thinkingConfig': {'thinkingBudget': 0}}     # thinking only adds seconds here
        for _ in range(2):
            body = {'contents': [{'role': 'user', 'parts': [
                        {'inline_data': {'mime_type': 'audio/wav',
                                         'data': base64.b64encode(wav).decode()}},
                        {'text': prompt}]}],
                    'generationConfig': gen}
            resp = requests.post(f'{GEMINI_URL}/models/{model}:generateContent',
                                 headers={'x-goog-api-key': config.get('gemini_api_key')},
                                 json=body, timeout=10)
            if resp.status_code == 400 and 'thinkingConfig' in gen:
                gen.pop('thinkingConfig')                  # model can't switch thinking off
                continue
            break
        if resp.status_code in (400, 404, 429):
            # retired, not on this key, or out of free quota (the transcribe model's is tiny)
            last = f'HTTP {resp.status_code} for {model}'
            if _gemini_stt_model == model:
                _gemini_stt_model = None
            continue
        if resp.status_code != 200:
            raise RuntimeError(f'HTTP {resp.status_code}: {resp.text[:150]}')
        _gemini_stt_model = model
        parts = (resp.json().get('candidates') or [{}])[0].get('content', {}).get('parts', [])
        # transcription models answer with {"audioTranscription": {"text": …}} parts
        text = ''.join(p.get('text') or (p.get('audioTranscription') or {}).get('text', '')
                       for p in parts if not p.get('thought')).strip()
        text = re.sub(r'^[>\s*_"“]+|[\s*_"”]+$', '', text)   # models sometimes add markdown/quotes
        if text.startswith(('(', '[')) and text.endswith((')', ']')):
            text = ''                    # "(no speech)", "[silence]"
        return text
    raise RuntimeError(last or 'no Gemini model available')


def _vosk(recognizer, audio) -> str:
    import speech_recognition as sr
    from vosk import KaldiRecognizer
    model = _load_vosk()
    if model is None:
        raise RuntimeError('offline model not downloaded')
    kr = KaldiRecognizer(model, 16000)
    kr.AcceptWaveform(audio.get_raw_data(convert_rate=16000, convert_width=2))
    text = json.loads(kr.FinalResult()).get('text', '')
    if not text:
        raise sr.UnknownValueError()
    return text


_ENGINES = {'vosk': _vosk, 'groq': _groq, 'gemini': _gemini, 'google': _google}


# ── Offline model ────────────────────────────────────────────────────────────

def _load_vosk():
    global _vosk_model
    with _vosk_lock:
        if _vosk_model is None and vosk_ready():
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)
            _vosk_model = Model(_vosk_path())
        return _vosk_model


def download_vosk_model(log=print) -> bool:
    """Fetch the small English offline model (~40 MB) into ~/.tmos/models once."""
    if not vosk_installed():
        return False
    if os.path.isdir(_vosk_path()):
        return True
    os.makedirs(MODELS_DIR, exist_ok=True)
    tmp = os.path.join(MODELS_DIR, VOSK_MODEL + '.zip.part')
    try:
        log('Downloading the offline speech model (40 MB, one time only)...')
        with requests.get(VOSK_URL, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
        with zipfile.ZipFile(tmp) as z:
            z.extractall(MODELS_DIR)
        log('Offline speech model ready.')
        return True
    except Exception as e:
        log(f'Could not download the offline speech model: {e}')
        return False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# Kept for callers that only need the engine for commands.
def engine() -> str:
    c = chain('command')
    return c[0] if c else 'google'
