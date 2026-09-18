"""T.M.O.S — Speech-to-Text Module
Turns captured microphone audio into text with either Google's free recognizer
or Groq's Whisper (more accurate, uses your Groq key). Falls back to Google if
Whisper fails.
"""

import requests

from modules import config

GROQ_STT_URL   = 'https://api.groq.com/openai/v1/audio/transcriptions'
GROQ_STT_MODEL = 'whisper-large-v3-turbo'

# Whisper sometimes "hears" these in silence or background noise.
_WHISPER_NOISE = {'thank you.', 'thank you', 'thanks for watching!', 'you', 'bye.', '.', ''}


def engine() -> str:
    if config.get('stt_engine') == 'groq' and config.get('groq_api_key'):
        return 'groq'
    return 'google'


def recognize(recognizer, audio) -> str:
    """Return the transcript, or raise speech_recognition.UnknownValueError /
    RequestError like recognize_google() does."""
    import speech_recognition as sr

    if engine() == 'groq':
        try:
            resp = requests.post(
                GROQ_STT_URL,
                headers={'Authorization': f'Bearer {config.get("groq_api_key")}'},
                files={'file': ('speech.wav', audio.get_wav_data(), 'audio/wav')},
                data={'model': GROQ_STT_MODEL, 'response_format': 'json', 'temperature': '0'},
                timeout=15,
            )
            if resp.status_code == 200:
                text = (resp.json().get('text') or '').strip()
                if text.lower() in _WHISPER_NOISE:
                    raise sr.UnknownValueError()
                return text
            print(f'[STT] Groq Whisper HTTP {resp.status_code}: {resp.text[:200]} — using Google.')
        except requests.exceptions.RequestException as e:
            print(f'[STT] Groq Whisper failed ({e}) — using Google.')

    return recognizer.recognize_google(audio)
