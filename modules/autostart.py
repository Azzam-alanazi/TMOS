"""T.M.O.S — Start with Windows
Adds or removes a per-user "Run" registry entry that launches T.M.O.S
minimized to the tray when you sign in.
"""

import os
import platform
import sys

_RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
_NAME    = 'TMOS'


def command() -> str:
    """The command line Windows runs at sign-in (pythonw = no console window)."""
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    if os.path.exists(pyw):
        exe = pyw
    main = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'main.py'))
    return f'"{exe}" "{main}" --minimized'


def is_enabled() -> bool:
    if platform.system() != 'Windows':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> dict:
    if platform.system() != 'Windows':
        return {'success': False, 'message': 'Start with Windows is only supported on Windows.'}
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, _NAME)
                except FileNotFoundError:
                    pass
        return {'success': True,
                'message': 'T.M.O.S will start with Windows.' if enabled
                           else 'T.M.O.S will no longer start with Windows.'}
    except OSError as e:
        return {'success': False, 'message': f'Could not change startup setting: {e}'}
