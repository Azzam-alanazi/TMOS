"""T.M.O.S — Media & Volume
Play/pause, next/previous track and the system volume, by sending the same
media keys a keyboard has. That works with whatever is playing (Spotify,
YouTube in a browser, VLC…) and shows Windows' own volume pop-up.
"""

import platform

# Virtual-key codes
_VK = {
    'play_pause': 0xB3, 'next': 0xB0, 'previous': 0xB1, 'stop': 0xB2,
    'volume_up': 0xAF, 'volume_down': 0xAE, 'mute': 0xAD,
}
_KEYEVENTF_EXTENDEDKEY = 0x1
_KEYEVENTF_KEYUP       = 0x2
_VOLUME_STEP = 2            # each volume key moves Windows' volume by 2%

ACTIONS = ('play_pause', 'next', 'previous', 'stop', 'volume_up', 'volume_down',
           'mute', 'unmute', 'set_volume')

_MESSAGES = {
    'play_pause': 'Play/pause.', 'next': 'Next track.', 'previous': 'Previous track.',
    'stop': 'Playback stopped.', 'mute': 'Sound muted.', 'unmute': 'Sound on.',
}


def _press(vk: int, times: int = 1) -> None:
    import ctypes
    user32 = ctypes.windll.user32
    for _ in range(times):
        user32.keybd_event(vk, 0, _KEYEVENTF_EXTENDEDKEY, 0)
        user32.keybd_event(vk, 0, _KEYEVENTF_EXTENDEDKEY | _KEYEVENTF_KEYUP, 0)


def control(action: str, level: float | None = None, step: float | None = None) -> dict:
    """action: one of ACTIONS. level: 0-100 for set_volume. step: % for volume_up/down."""
    action = (action or '').strip().lower().replace(' ', '_').replace('-', '_')
    action = {'play': 'play_pause', 'pause': 'play_pause', 'resume': 'play_pause',
              'skip': 'next', 'prev': 'previous', 'back': 'previous',
              'louder': 'volume_up', 'quieter': 'volume_down', 'volume': 'set_volume'}.get(action, action)
    if action not in ACTIONS:
        return {'success': False, 'message': f'Unknown media action "{action}". '
                                             f'Use one of: {", ".join(ACTIONS)}.'}
    if platform.system() != 'Windows':
        return {'success': False, 'message': 'Media control only works on Windows.'}

    if action == 'set_volume':
        if level is None:
            return {'success': False, 'message': 'Say which volume, from 0 to 100.'}
        level = max(0, min(100, int(round(float(level)))))
        _press(_VK['volume_down'], 100 // _VOLUME_STEP)        # to 0 (this also unmutes)…
        _press(_VK['volume_up'], round(level / _VOLUME_STEP))  # …then up to the level
        return {'success': True, 'message': f'Volume set to {level}%.', 'volume': level}
    if action in ('volume_up', 'volume_down'):
        pct = max(_VOLUME_STEP, min(100, int(round(float(step or 10)))))
        _press(_VK[action], max(1, round(pct / _VOLUME_STEP)))
        return {'success': True, 'message': f'Volume {"up" if action == "volume_up" else "down"} {pct}%.'}
    if action == 'unmute':
        _press(_VK['volume_up'])            # any volume key unmutes; undo its +2%
        _press(_VK['volume_down'])
    else:
        _press(_VK[action])
    return {'success': True, 'message': _MESSAGES[action]}
