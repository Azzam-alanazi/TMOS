"""T.M.O.S — Microphone selection
Lists the PC's microphones and picks the one to listen on. "Automatic" uses the
Windows default input, unless that's a virtual device such as a phone-as-webcam
app (Iriun, DroidCam, Camo…), which is often silent. Then it takes the first
real microphone instead.
"""

from modules import config

_VIRTUAL = ('iriun', 'droidcam', 'camo', 'epoccam', 'obs', 'virtual', 'sound mapper',
            'stereo mix', 'cable output', 'voicemeeter', 'what u hear', 'wave out')


def _is_virtual(name: str) -> bool:
    low = name.lower()
    return any(v in low for v in _VIRTUAL)


_cache: list[dict] | None = None


def list_inputs() -> list[dict]:
    """[{'index': PyAudio device index, 'name': str, 'default': bool, 'virtual': bool}].
    Read once per run: PortAudio isn't thread-safe, and the wake-word listener opens
    and closes it constantly on its own thread, so we must not scan it again later."""
    global _cache
    if _cache is None:
        _cache = _scan()
    return _cache


def _scan() -> list[dict]:
    try:
        import pyaudio
    except ImportError:
        return []
    pa = pyaudio.PyAudio()
    try:
        try:
            default_idx = pa.get_default_input_device_info()['index']
        except OSError:
            default_idx = -1
        mics = []
        for i in range(pa.get_device_count()):
            dev = pa.get_device_info_by_index(i)
            # MME lists every device once with a readable name; skip the other host APIs' duplicates
            if dev['maxInputChannels'] <= 0 or pa.get_host_api_info_by_index(dev['hostApi'])['name'] != 'MME':
                continue
            if 'sound mapper' in dev['name'].lower():
                continue
            mics.append({'index': i, 'name': dev['name'], 'default': i == default_idx,
                         'virtual': _is_virtual(dev['name'])})
        return mics
    finally:
        pa.terminate()


def resolve() -> tuple[int | None, str]:
    """(device index for sr.Microphone, human name) for the configured microphone."""
    mics = list_inputs()
    wanted = (config.get('mic_device') or '').strip()
    if wanted:
        for m in mics:
            if m['name'] == wanted:
                return m['index'], m['name']
    default = next((m for m in mics if m['default']), None)
    if default and not default['virtual']:
        return default['index'], default['name']
    real = next((m for m in mics if not m['virtual']), None)
    if real:
        return real['index'], real['name']
    if default:
        return default['index'], default['name']
    return None, 'system default'
