"""T.M.O.S — Utility Tools Module
Calculator, timers, clipboard, screenshot, and system power commands.
"""

import math
import os
import platform
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime
from typing import Callable


# ── Calculator ───────────────────────────────────────────────────────────────

# Safe math namespace (no builtins, no exec/eval exploits)
_MATH_NS = {
    k: getattr(math, k) for k in dir(math) if not k.startswith('_')
}
_MATH_NS.update({
    'abs': abs, 'round': round, 'min': min, 'max': max,
    'int': int, 'float': float, 'sum': sum,
    'pi': math.pi, 'e': math.e, 'tau': math.tau,
    'inf': math.inf,
})


def calculate(expr: str) -> dict:
    """Safely evaluate a math expression."""
    expr = expr.strip()
    if not expr:
        return {'success': False, 'message': 'No expression provided.'}

    # Block dangerous patterns
    blocked = ('import', 'exec', 'eval', 'open', 'os.', 'sys.', '__', 'subprocess', 'lambda')
    if any(b in expr.lower() for b in blocked):
        return {'success': False, 'message': 'Expression contains blocked keywords.'}

    try:
        # Replace common symbols
        expr_clean = expr.replace('^', '**').replace('×', '*').replace('÷', '/')
        result = eval(expr_clean, {"__builtins__": {}}, _MATH_NS)
        if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
            result = int(result)
        return {'success': True, 'expression': expr, 'result': result}
    except ZeroDivisionError:
        return {'success': False, 'message': 'Division by zero.'}
    except SyntaxError:
        return {'success': False, 'message': f'Invalid expression: {expr}'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


# ── Durations ────────────────────────────────────────────────────────────────

_UNIT_SECONDS = {
    'h': 3600, 'hr': 3600, 'hrs': 3600, 'hour': 3600, 'hours': 3600,
    'm': 60, 'min': 60, 'mins': 60, 'minute': 60, 'minutes': 60,
    's': 1, 'sec': 1, 'secs': 1, 'second': 1, 'seconds': 1,
}
_DURATION_RE = re.compile(
    r'(\d+(?:\.\d+)?|an?|one|half an?)\s*'
    r'(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b', re.IGNORECASE)
_WORD_NUMBERS = {'a': 1, 'an': 1, 'one': 1, 'half a': 0.5, 'half an': 0.5}


def parse_duration(text: str) -> int:
    """'5 minutes' → 300, '1h 30m' → 5400, 'an hour' → 3600, '90' → 5400 (bare = minutes)."""
    text = text.strip().lower()
    if re.fullmatch(r'\d+(?:\.\d+)?', text):
        return int(float(text) * 60)
    total = 0.0
    for num, unit in _DURATION_RE.findall(text):
        n = _WORD_NUMBERS.get(num.lower()) or float(num)
        total += n * _UNIT_SECONDS[unit.lower()]
    return int(total)


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h: parts.append(f'{h} hour{"s" if h != 1 else ""}')
    if m: parts.append(f'{m} minute{"s" if m != 1 else ""}')
    if s or not parts: parts.append(f'{s} second{"s" if s != 1 else ""}')
    return ' '.join(parts)


# ── Stopwatches ──────────────────────────────────────────────────────────────

_timers: dict[str, dict] = {}


def start_timer(name: str = 'default') -> dict:
    """Start a stopwatch."""
    name = name.strip() or 'default'
    _timers[name] = {'start': time.time(), 'name': name}
    return {'success': True, 'message': f'Stopwatch "{name}" started.'}


def check_timer(name: str = 'default') -> dict:
    """Check elapsed time on a stopwatch."""
    t = _timers.get(name.strip() or 'default')
    if not t:
        return {'success': False, 'message': f'No stopwatch named "{name}".'}
    return {'success': True, 'name': t['name'], 'elapsed': round(time.time() - t['start'], 1)}


def stop_timer(name: str = 'default') -> dict:
    """Stop and remove a stopwatch."""
    t = _timers.pop(name.strip() or 'default', None)
    if not t:
        return {'success': False, 'message': f'No stopwatch named "{name}".'}
    return {'success': True, 'name': t['name'], 'elapsed': round(time.time() - t['start'], 1)}


# ── Countdown timers ─────────────────────────────────────────────────────────

_countdowns: dict[str, dict] = {}
_countdown_lock = threading.Lock()
_on_countdown_done: Callable[[dict], None] | None = None


def set_countdown_callback(fn: Callable[[dict], None] | None) -> None:
    """fn(timer) is called from a background thread when a countdown finishes."""
    global _on_countdown_done
    _on_countdown_done = fn


def _countdown_fired(tid: str) -> None:
    with _countdown_lock:
        t = _countdowns.pop(tid, None)
    if t and _on_countdown_done:
        _on_countdown_done(_public(t))


def _public(t: dict) -> dict:
    return {k: t[k] for k in ('id', 'label', 'seconds', 'ends_at')}


def start_countdown(seconds: int, label: str = '') -> dict:
    seconds = int(seconds)
    if seconds <= 0:
        return {'success': False, 'message': 'Timer length must be more than 0 seconds.'}
    if seconds > 24 * 3600:
        return {'success': False, 'message': 'Timers can be at most 24 hours.'}
    tid = uuid.uuid4().hex[:12]
    label = label.strip() or format_duration(seconds)
    t = {'id': tid, 'label': label, 'seconds': seconds, 'ends_at': time.time() + seconds}
    t['_timer'] = threading.Timer(seconds, _countdown_fired, args=[tid])
    t['_timer'].daemon = True
    with _countdown_lock:
        _countdowns[tid] = t
    t['_timer'].start()
    return {'success': True, 'timer': _public(t),
            'message': f'Timer set for {format_duration(seconds)}.'}


def cancel_countdown(tid: str | None = None) -> dict:
    """Cancel one countdown by id, or all of them when tid is None."""
    with _countdown_lock:
        ids = [tid] if tid else list(_countdowns)
        cancelled = [_countdowns.pop(i) for i in ids if i in _countdowns]
    for t in cancelled:
        t['_timer'].cancel()
    if not cancelled:
        return {'success': False, 'message': 'No timer to cancel.'}
    return {'success': True, 'cancelled': len(cancelled),
            'message': 'Timer cancelled.' if len(cancelled) == 1 else f'{len(cancelled)} timers cancelled.'}


def list_countdowns() -> list[dict]:
    with _countdown_lock:
        return sorted((_public(t) for t in _countdowns.values()), key=lambda t: t['ends_at'])


# ── Clipboard ────────────────────────────────────────────────────────────────

def _powershell(script: str, env: dict | None = None, timeout: int = 5) -> subprocess.CompletedProcess:
    return subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
        capture_output=True, timeout=timeout, env=env,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )


def get_clipboard() -> dict:
    """Read clipboard text (Windows only for now)."""
    if platform.system() != 'Windows':
        return {'success': False, 'message': 'Clipboard only supported on Windows currently.'}
    try:
        result = _powershell('[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw')
        return {'success': True, 'text': result.stdout.decode('utf-8', errors='replace').strip()}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def set_clipboard(text: str) -> dict:
    """Write text to clipboard (Windows only for now)."""
    if platform.system() != 'Windows':
        return {'success': False, 'message': 'Clipboard only supported on Windows currently.'}
    try:
        # Pass the text through an environment variable so quotes, $ and
        # non-English characters can never be interpreted as PowerShell code.
        env = dict(os.environ, TMOS_CLIP=text)
        result = _powershell('Set-Clipboard -Value $env:TMOS_CLIP', env=env)
        if result.returncode != 0:
            err = result.stderr.decode('utf-8', errors='replace').strip()
            return {'success': False, 'message': f'Copy failed: {err[:200]}'}
        return {'success': True, 'message': 'Copied to clipboard.'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


# ── Screenshot ───────────────────────────────────────────────────────────────

def take_screenshot(save_path: str | None = None) -> dict:
    """Take a screenshot and save it."""
    if save_path is None:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        save_path = os.path.join(os.path.expanduser('~'), 'Desktop', f'tmos_screenshot_{ts}.png')

    if platform.system() == 'Windows':
        try:
            ps_script = '''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screen = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bitmap = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($screen.Left, $screen.Top, 0, 0, $bitmap.Size)
$bitmap.Save($env:TMOS_SHOT)
$graphics.Dispose()
$bitmap.Dispose()
'''
            _powershell(ps_script, env=dict(os.environ, TMOS_SHOT=save_path), timeout=10)
            if os.path.exists(save_path):
                return {'success': True, 'path': save_path, 'message': f'Screenshot saved to {save_path}'}
            return {'success': False, 'message': 'Screenshot failed — file not created.'}
        except Exception as e:
            return {'success': False, 'message': str(e)}
    else:
        return {'success': False, 'message': 'Screenshots only supported on Windows currently.'}


# ── System Power ─────────────────────────────────────────────────────────────

def system_command(action: str) -> dict:
    """Execute system power commands (lock, sleep, shutdown, restart)."""
    action = action.lower().strip()

    if platform.system() != 'Windows':
        return {'success': False, 'message': 'System commands only supported on Windows currently.'}

    commands = {
        'lock':     'rundll32.exe user32.dll,LockWorkStation',
        'sleep':    'rundll32.exe powrprof.dll,SetSuspendState 0,1,0',
        'shutdown': 'shutdown /s /t 30 /c "T.M.O.S: Shutting down in 30 seconds..."',
        'restart':  'shutdown /r /t 30 /c "T.M.O.S: Restarting in 30 seconds..."',
        'cancel':   'shutdown /a',
    }

    cmd = commands.get(action)
    if not cmd:
        return {'success': False, 'message': f'Unknown action: {action}. Use: lock, sleep, shutdown, restart, cancel'}

    try:
        subprocess.Popen(cmd, shell=True)
        messages = {
            'lock': 'Locking workstation...',
            'sleep': 'Putting system to sleep...',
            'shutdown': 'Shutting down in 30 seconds. Say "cancel shutdown" to abort.',
            'restart': 'Restarting in 30 seconds. Say "cancel shutdown" to abort.',
            'cancel': 'Shutdown/restart cancelled.',
        }
        return {'success': True, 'message': messages.get(action, f'{action} executed.')}
    except Exception as e:
        return {'success': False, 'message': str(e)}
