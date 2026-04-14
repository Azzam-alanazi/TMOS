"""T.M.O.S — Utility Tools Module
Calculator, timer, clipboard, screenshot, and system power commands.
"""

import math
import os
import platform
import subprocess
import time
from datetime import datetime, timedelta


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
    blocked = ('import', 'exec', 'eval', 'open', 'os.', 'sys.', '__', 'subprocess')
    if any(b in expr.lower() for b in blocked):
        return {'success': False, 'message': 'Expression contains blocked keywords.'}

    try:
        # Replace common symbols
        expr_clean = expr.replace('^', '**').replace('×', '*').replace('÷', '/')
        result = eval(expr_clean, {"__builtins__": {}}, _MATH_NS)
        return {'success': True, 'expression': expr, 'result': result}
    except ZeroDivisionError:
        return {'success': False, 'message': 'Division by zero.'}
    except SyntaxError:
        return {'success': False, 'message': f'Invalid expression: {expr}'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


# ── Timer / Stopwatch ────────────────────────────────────────────────────────

_timers: dict[str, dict] = {}


def start_timer(name: str = 'default', duration_sec: int | None = None) -> dict:
    """Start a stopwatch or countdown timer."""
    name = name.strip() or 'default'
    _timers[name] = {
        'start': time.time(),
        'duration': duration_sec,
        'name': name,
    }
    if duration_sec:
        return {'success': True, 'message': f'Timer "{name}" set for {duration_sec}s.'}
    return {'success': True, 'message': f'Stopwatch "{name}" started.'}


def check_timer(name: str = 'default') -> dict:
    """Check elapsed/remaining time on a timer."""
    t = _timers.get(name.strip() or 'default')
    if not t:
        return {'success': False, 'message': f'No timer named "{name}".'}
    elapsed = time.time() - t['start']
    if t['duration']:
        remaining = max(0, t['duration'] - elapsed)
        return {
            'success': True, 'name': t['name'],
            'elapsed': round(elapsed, 1),
            'remaining': round(remaining, 1),
            'done': remaining <= 0,
        }
    return {'success': True, 'name': t['name'], 'elapsed': round(elapsed, 1)}


def stop_timer(name: str = 'default') -> dict:
    """Stop and remove a timer."""
    t = _timers.pop(name.strip() or 'default', None)
    if not t:
        return {'success': False, 'message': f'No timer named "{name}".'}
    elapsed = time.time() - t['start']
    return {'success': True, 'name': t['name'], 'elapsed': round(elapsed, 1)}


# ── Clipboard ────────────────────────────────────────────────────────────────

def get_clipboard() -> dict:
    """Read clipboard text (Windows only for now)."""
    if platform.system() != 'Windows':
        return {'success': False, 'message': 'Clipboard only supported on Windows currently.'}
    try:
        result = subprocess.run(
            ['powershell', '-command', 'Get-Clipboard'],
            capture_output=True, text=True, timeout=5,
        )
        return {'success': True, 'text': result.stdout.strip()}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def set_clipboard(text: str) -> dict:
    """Write text to clipboard (Windows only for now)."""
    if platform.system() != 'Windows':
        return {'success': False, 'message': 'Clipboard only supported on Windows currently.'}
    try:
        subprocess.run(
            ['powershell', '-command', f'Set-Clipboard -Value "{text}"'],
            capture_output=True, timeout=5,
        )
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
            # Use PowerShell to take screenshot
            ps_script = f'''
Add-Type -AssemblyName System.Windows.Forms
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bitmap = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
$bitmap.Save("{save_path}")
$graphics.Dispose()
$bitmap.Dispose()
'''
            subprocess.run(
                ['powershell', '-command', ps_script],
                capture_output=True, timeout=10,
            )
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
            'shutdown': 'Shutting down in 30 seconds. Type "cancel shutdown" to abort.',
            'restart': 'Restarting in 30 seconds. Type "cancel shutdown" to abort.',
            'cancel': 'Shutdown/restart cancelled.',
        }
        return {'success': True, 'message': messages.get(action, f'{action} executed.')}
    except Exception as e:
        return {'success': False, 'message': str(e)}
