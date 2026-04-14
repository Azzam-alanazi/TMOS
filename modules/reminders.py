"""T.M.O.S — Reminders Module
Persistent reminders with cron-style scheduling via APScheduler.
Supports one-time and recurring reminders with labels.
"""

import json
import os
import time
import logging
from typing import Callable

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    _HAS_APS = True
except ImportError:
    _HAS_APS = False

logger = logging.getLogger('tmos.reminders')

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_DIR  = os.path.join(os.path.expanduser('~'), '.tmos')
DATA_FILE = os.path.join(DATA_DIR, 'reminders.json')
os.makedirs(DATA_DIR, exist_ok=True)

# ── State ────────────────────────────────────────────────────────────────────
_reminders: list[dict] = []
_callback: Callable | None = None
_scheduler = None


# ── Persistence ──────────────────────────────────────────────────────────────

def _load() -> None:
    global _reminders
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                _reminders = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f'Failed to load reminders: {e}')
            _reminders = []
    else:
        _reminders = []


def _save() -> None:
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(_reminders, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.error(f'Failed to save reminders: {e}')


# ── Scheduling ───────────────────────────────────────────────────────────────

def _fire(reminder: dict) -> None:
    if _callback:
        try:
            _callback(reminder)
        except Exception as e:
            logger.error(f'Reminder callback error: {e}')


def _schedule(reminder: dict) -> None:
    if not _HAS_APS or _scheduler is None:
        return
    try:
        time_str = reminder.get('time', '')
        if ':' not in time_str:
            return
        parts = time_str.split(':')
        h, m = int(parts[0]), int(parts[1])
        _scheduler.add_job(
            _fire, 'cron', hour=h, minute=m,
            args=[reminder], id=reminder['id'], replace_existing=True,
        )
    except (ValueError, KeyError) as e:
        logger.warning(f'Failed to schedule reminder: {e}')


# ── Public API ───────────────────────────────────────────────────────────────

def init(callback: Callable | None = None) -> None:
    """Initialize the reminders system."""
    global _callback, _scheduler
    _callback = callback
    _load()
    if _HAS_APS:
        _scheduler = BackgroundScheduler()
        _scheduler.start()
        for r in _reminders:
            _schedule(r)
    else:
        logger.warning('APScheduler not installed — reminders will not fire.')


def get_all() -> list[dict]:
    """Return all saved reminders."""
    return _reminders.copy()


def add(reminder: dict) -> dict:
    """Add a new reminder. Expects {'time': 'HH:MM', 'text': '...'}."""
    time_str = reminder.get('time', '').strip()
    text = reminder.get('text', '').strip()

    if not time_str or not text:
        return {'success': False, 'message': 'Time and text are required.'}

    # Validate time format
    try:
        parts = time_str.split(':')
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except (ValueError, IndexError):
        return {'success': False, 'message': 'Invalid time format. Use HH:MM (24h).'}

    reminder = {
        'id': str(int(time.time() * 1000)),
        'time': f'{h:02d}:{m:02d}',
        'text': text,
        'label': reminder.get('label', ''),
        'created': time.strftime('%Y-%m-%d %H:%M'),
    }
    _reminders.append(reminder)
    _save()
    _schedule(reminder)
    return {'success': True, 'reminder': reminder}


def remove(rid: str) -> dict:
    """Remove a reminder by ID."""
    global _reminders
    before = len(_reminders)
    _reminders = [r for r in _reminders if r['id'] != rid]

    if len(_reminders) == before:
        return {'success': False, 'message': f'Reminder not found: {rid}'}

    if _HAS_APS and _scheduler:
        try:
            _scheduler.remove_job(rid)
        except Exception:
            pass
    _save()
    return {'success': True}


def clear_all() -> dict:
    """Remove all reminders."""
    global _reminders
    count = len(_reminders)
    if _HAS_APS and _scheduler:
        for r in _reminders:
            try:
                _scheduler.remove_job(r['id'])
            except Exception:
                pass
    _reminders.clear()
    _save()
    return {'success': True, 'cleared': count}
