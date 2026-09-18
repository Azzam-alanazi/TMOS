"""T.M.O.S — Global Hotkey (Windows)
Summon T.M.O.S from any app with a key combo such as Ctrl+Shift+Space.
Uses the Win32 RegisterHotKey API on a background thread, so there's no extra
dependency and no keyboard hook.
"""

import ctypes
import platform
import threading
from ctypes import wintypes
from typing import Callable

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

_MODS = {'ctrl': MOD_CONTROL, 'control': MOD_CONTROL, 'alt': MOD_ALT,
         'shift': MOD_SHIFT, 'win': MOD_WIN, 'super': MOD_WIN}
_KEYS = {'space': 0x20, 'enter': 0x0D, 'tab': 0x09, 'esc': 0x1B, 'escape': 0x1B,
         'backspace': 0x08, 'insert': 0x2D, 'delete': 0x2E, 'home': 0x24, 'end': 0x23,
         'pageup': 0x21, 'pagedown': 0x22, 'up': 0x26, 'down': 0x28, 'left': 0x25,
         'right': 0x27, '`': 0xC0, ';': 0xBA, ',': 0xBC, '.': 0xBE, '/': 0xBF}


def parse(combo: str) -> tuple[int, int] | None:
    """'ctrl+shift+space' → (modifiers, virtual-key code), or None if invalid."""
    parts = [p.strip().lower() for p in combo.split('+') if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in _MODS:
            return None
        mods |= _MODS[p]
    key = parts[-1]
    if key in _KEYS:
        vk = _KEYS[key]
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith('f') and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x6F + int(key[1:])
    else:
        return None
    return mods, vk


def pretty(combo: str) -> str:
    return '+'.join(p.strip().capitalize() for p in combo.split('+'))


class GlobalHotkey:
    """Calls callback() (on a background thread) whenever the combo is pressed."""

    def __init__(self, combo: str, callback: Callable[[], None]):
        self.combo = combo
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.error = ''

    def start(self) -> bool:
        if platform.system() != 'Windows':
            self.error = 'Global hotkeys are only supported on Windows.'
            return False
        parsed = parse(self.combo)
        if not parsed:
            self.error = f'Invalid hotkey: {self.combo}'
            return False
        self._thread = threading.Thread(target=self._run, args=parsed, daemon=True, name='tmos-hotkey')
        self._thread.start()
        self._ready.wait(2)
        return not self.error

    def stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(1)

    def _run(self, mods: int, vk: int) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
            self.error = f'{pretty(self.combo)} is already used by another app.'
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.callback()
                    except Exception as e:
                        print(f'[Hotkey] callback error: {e}')
        finally:
            user32.UnregisterHotKey(None, 1)
