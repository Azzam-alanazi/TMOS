"""T.M.O.S — App Launcher Module
Opens desktop apps, URLs, and system utilities.
Auto-discovers installed apps on Windows.
"""

import os
import platform
import re
import shutil
import subprocess
import webbrowser

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


def _start_menu_shortcut(name: str) -> str | None:
    """Find an installed app's Start Menu shortcut whose name matches."""
    import glob
    roots = [
        os.path.expandvars(r'%AppData%\Microsoft\Windows\Start Menu\Programs'),
        os.path.expandvars(r'%ProgramData%\Microsoft\Windows\Start Menu\Programs'),
    ]
    want = re.sub(r'[^a-z0-9]', '', name.lower())
    if len(want) < 2:
        return None
    best: tuple[int, str] | None = None
    for root in roots:
        for lnk in glob.glob(os.path.join(root, '**', '*.lnk'), recursive=True):
            title = re.sub(r'[^a-z0-9]', '', os.path.splitext(os.path.basename(lnk))[0].lower())
            if 'uninstall' in title:
                continue
            if title == want:
                return lnk
            if title.startswith(want) or want in title:
                score = len(title)          # prefer the shortest (closest) match
                if best is None or score < best[0]:
                    best = (score, lnk)
    return best[1] if best else None


def open_app(name: str) -> dict:
    """Try to open an application by name."""
    key = re.sub(r'^(the|my)\s+', '', name.lower().strip()).removesuffix(' app').strip()

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

    # Known built-in commands (fixed strings, safe for the shell)
    cmd = _SHELL_CMDS.get(resolved_key)
    if cmd:
        try:
            if platform.system() == 'Windows':
                subprocess.Popen(cmd, shell=True)
            else:
                subprocess.Popen(cmd.split())
            return {'success': True, 'message': f'Opening {name}...'}
        except Exception as e:
            return {'success': False, 'message': f'Could not open {name}: {e}'}

    # Never hand an unknown name to the shell, since text like "x & del ..." would
    # run as a command. Only plain app names are allowed from here on.
    if not re.fullmatch(r'[\w .+\-]{1,60}', key):
        return {'success': False, 'message': f'App not found: {name}.'}

    if platform.system() == 'Windows':
        lnk = _start_menu_shortcut(resolved_key)
        target = lnk or shutil.which(resolved_key) or shutil.which(resolved_key.replace(' ', ''))
        if target:
            try:
                os.startfile(target)
                return {'success': True, 'message': f'Opening {name}...'}
            except OSError as e:
                return {'success': False, 'message': f'Could not open {name}: {e}'}
        return {'success': False, 'message': f'App not found: {name}. Is it installed?'}

    exe = shutil.which(resolved_key)
    if not exe:
        return {'success': False, 'message': f'App not found: {name}.'}
    subprocess.Popen([exe])
    return {'success': True, 'message': f'Opening {name}...'}


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
