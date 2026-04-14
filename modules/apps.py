"""T.M.O.S — App Launcher Module
Opens desktop apps, URLs, and system utilities.
Auto-discovers installed apps on Windows.
"""

import os
import subprocess
import webbrowser
import platform

# Full path candidates for apps that aren't in PATH
_CANDIDATES: dict[str, list[str]] = {
    'chrome': [
        r'%ProgramFiles%\Google\Chrome\Application\chrome.exe',
        r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe',
        r'%LocalAppData%\Google\Chrome\Application\chrome.exe',
    ],
    'firefox': [
        r'%ProgramFiles%\Mozilla Firefox\firefox.exe',
        r'%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe',
    ],
    'brave': [
        r'%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe',
        r'%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe',
    ],
    'vscode': [
        r'%LocalAppData%\Programs\Microsoft VS Code\Code.exe',
        r'%ProgramFiles%\Microsoft VS Code\Code.exe',
    ],
    'spotify': [
        r'%AppData%\Spotify\Spotify.exe',
        r'%LocalAppData%\Microsoft\WindowsApps\Spotify.exe',
    ],
    'discord': [
        r'%LocalAppData%\Discord\app-*\Discord.exe',
        r'%AppData%\discord\Discord.exe',
    ],
    'terminal': [
        r'%LocalAppData%\Microsoft\WindowsApps\wt.exe',
        r'%ProgramFiles%\WindowsApps\Microsoft.WindowsTerminal*\wt.exe',
    ],
    'steam': [
        r'%ProgramFiles(x86)%\Steam\Steam.exe',
        r'%ProgramFiles%\Steam\Steam.exe',
    ],
    'vlc': [
        r'%ProgramFiles%\VideoLAN\VLC\vlc.exe',
        r'%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe',
    ],
    'word': [
        r'%ProgramFiles%\Microsoft Office\root\Office16\WINWORD.EXE',
        r'%ProgramFiles(x86)%\Microsoft Office\root\Office16\WINWORD.EXE',
    ],
    'excel': [
        r'%ProgramFiles%\Microsoft Office\root\Office16\EXCEL.EXE',
        r'%ProgramFiles(x86)%\Microsoft Office\root\Office16\EXCEL.EXE',
    ],
    'obs': [
        r'%ProgramFiles%\obs-studio\bin\64bit\obs64.exe',
        r'%ProgramFiles(x86)%\obs-studio\bin\64bit\obs64.exe',
    ],
    'telegram': [
        r'%AppData%\Telegram Desktop\Telegram.exe',
    ],
    'whatsapp': [
        r'%LocalAppData%\WhatsApp\WhatsApp.exe',
    ],
    'fivem': [
        r'%LocalAppData%\FiveM\FiveM.exe',
    ],
}

# Simple shell commands (always work)
_SHELL_CMDS: dict[str, str] = {
    'notepad':       'notepad.exe',
    'calculator':    'calc.exe',
    'paint':         'mspaint.exe',
    'explorer':      'explorer.exe',
    'file explorer':  'explorer.exe',
    'task manager':  'taskmgr.exe',
    'settings':      'start ms-settings:',
    'powerpoint':    'POWERPNT.EXE',
    'cmd':           'cmd.exe',
    'powershell':    'powershell.exe',
    'snipping tool': 'SnippingTool.exe',
    'control panel': 'control',
    'device manager': 'devmgmt.msc',
}

# Aliases (map common names to canonical keys)
_ALIASES: dict[str, str] = {
    'code':          'vscode',
    'vs code':       'vscode',
    'visual studio code': 'vscode',
    'google chrome': 'chrome',
    'windows terminal': 'terminal',
    'wt':            'terminal',
    'calc':          'calculator',
    'files':         'explorer',
    'five m':        'fivem',
}


def _resolve(name: str) -> str | None:
    """Return the first existing path for a known app, or None."""
    import glob
    # Check aliases
    name = _ALIASES.get(name, name)
    for pattern in _CANDIDATES.get(name, []):
        expanded = os.path.expandvars(pattern)
        matches = glob.glob(expanded)
        if matches:
            return matches[0]
        if os.path.exists(expanded):
            return expanded
    return None


def open_app(name: str) -> dict:
    """Try to open an application by name."""
    key = name.lower().strip()

    # Resolve alias
    resolved_key = _ALIASES.get(key, key)

    # Try full-path resolution first
    path = _resolve(resolved_key)
    if path:
        try:
            subprocess.Popen([path])
            return {'success': True, 'message': f'Opening {name}...'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # Fall back to shell command
    cmd = _SHELL_CMDS.get(resolved_key, _SHELL_CMDS.get(key, key))
    try:
        if platform.system() == 'Windows':
            subprocess.Popen(cmd, shell=True)
        else:
            subprocess.Popen(cmd.split())
        return {'success': True, 'message': f'Opening {name}...'}
    except FileNotFoundError:
        return {'success': False, 'message': f'App not found: {name}. Try the full path or install it.'}
    except Exception as e:
        return {'success': False, 'message': f'Could not open {name}: {e}'}


def open_browser(url: str) -> dict:
    """Open a URL in the default browser."""
    url = url.strip()
    if not url:
        return {'success': False, 'message': 'No URL provided.'}
    if not url.startswith(('http://', 'https://')):
        url = f'https://{url}'
    try:
        webbrowser.open(url)
        return {'success': True, 'message': f'Opening {url}'}
    except Exception as e:
        return {'success': False, 'message': f'Failed to open browser: {e}'}


def get_available_apps() -> list[str]:
    """Return list of all known app names."""
    apps = set()
    apps.update(_CANDIDATES.keys())
    apps.update(_SHELL_CMDS.keys())
    apps.update(_ALIASES.keys())
    return sorted(apps)
