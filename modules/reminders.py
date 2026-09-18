"""T.M.O.S — Reminders Module
Persistent reminders scheduled with APScheduler.
One-time reminders fire once and remove themselves; daily ones repeat.
"""

import json
import os
import threading
import time
import uuid
import logging
from datetime import datetime, timedelta
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
_lock = threading.RLock()
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
    # Reminders saved by older versions have no repeat/due fields.
    for r in _reminders:
        r.setdefault('repeat', 'once')
        if r['repeat'] == 'once' and not r.get('due'):
            r['due'] = _next_occurrence(r['time']).isoformat(timespec='seconds')


def _save() -> None:
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(_reminders, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.error(f'Failed to save reminders: {e}')


# ── Time helpers ─────────────────────────────────────────────────────────────

def _parse_hhmm(time_str: str) -> tuple[int, int]:
    parts = time_str.strip().split(':')
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError
    return h, m


def _next_occurrence(time_str: str, now: datetime | None = None) -> datetime:
    """The next time the clock shows HH:MM (today if still ahead, else tomorrow)."""
    now = now or datetime.now()
    h, m = _parse_hhmm(time_str)
    due = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    return due


# ── Scheduling ───────────────────────────────────────────────────────────────

def _fire(rid: str) -> None:
    with _lock:
        reminder = next((r for r in _reminders if r['id'] == rid), None)
        if reminder is None:
            return
        fired = dict(reminder)
        if reminder.get('repeat') != 'daily':
            _reminders.remove(reminder)
            _save()
    due = fired.get('due')
    if due and (datetime.now() - datetime.fromisoformat(due)).total_seconds() > 120:
        fired['missed'] = True     # it came due while T.M.O.S was closed
    if _callback:
        try:
            _callback(fired)
        except Exception as e:
            logger.error(f'Reminder callback error: {e}')


def _schedule(reminder: dict) -> None:
    if not _HAS_APS or _scheduler is None:
        return
    try:
        if reminder.get('repeat') == 'daily':
            h, m = _parse_hhmm(reminder['time'])
            _scheduler.add_job(
                _fire, 'cron', hour=h, minute=m,
                args=[reminder['id']], id=reminder['id'], replace_existing=True,
            )
        else:
            _scheduler.add_job(
                _fire, 'date', run_date=datetime.fromisoformat(reminder['due']),
                args=[reminder['id']], id=reminder['id'], replace_existing=True,
                misfire_grace_time=None,     # still fire if it came due while we were closed
            )
    except (ValueError, KeyError) as e:
        logger.warning(f'Failed to schedule reminder: {e}')


# ── Public API ───────────────────────────────────────────────────────────────

def init(callback: Callable | None = None) -> None:
    """Initialize the reminders system. Reminders are only scheduled when a
    callback is given (the desktop app), so the REST server doesn't fire them too."""
    global _callback, _scheduler
    _callback = callback
    with _lock:
        _load()
    if callback is None:
        return
    if _HAS_APS:
        _scheduler = BackgroundScheduler()
        _scheduler.start()
        with _lock:
            for r in _reminders:
                _schedule(r)
    else:
        logger.warning('APScheduler not installed — reminders will not fire.')


def get_all() -> list[dict]:
    """Return all saved reminders, soonest first."""
    with _lock:
        items = [dict(r) for r in _reminders]
    for r in items:
        if r.get('repeat') == 'daily':
            r['next'] = _next_occurrence(r['time']).isoformat(timespec='seconds')
        else:
            r['next'] = r.get('due', '')
    return sorted(items, key=lambda r: r['next'])


def add(reminder: dict) -> dict:
    """Add a reminder.
    {'text': '...', 'time': 'HH:MM'} or {'text': '...', 'in_seconds': 600},
    plus optional 'repeat': 'once' (default) | 'daily'."""
    text = str(reminder.get('text', '')).strip()
    repeat = 'daily' if reminder.get('repeat') == 'daily' else 'once'
    if not text:
        return {'success': False, 'message': 'Reminder text is required.'}

    if reminder.get('in_seconds'):
        seconds = int(reminder['in_seconds'])
        if seconds <= 0:
            return {'success': False, 'message': 'Reminder must be in the future.'}
        due = datetime.now().replace(microsecond=0) + timedelta(seconds=seconds)
        time_str = due.strftime('%H:%M')
    else:
        time_str = str(reminder.get('time', '')).strip()
        try:
            h, m = _parse_hhmm(time_str)
        except (ValueError, IndexError):
            return {'success': False, 'message': 'Invalid time format. Use HH:MM (24h).'}
        time_str = f'{h:02d}:{m:02d}'
        due = _next_occurrence(time_str)

    new = {
        'id': uuid.uuid4().hex[:12],
        'time': time_str,
        'text': text,
        'repeat': repeat,
        'label': reminder.get('label', ''),
        'created': time.strftime('%Y-%m-%d %H:%M'),
    }
    if repeat == 'once':
        new['due'] = due.isoformat(timespec='seconds')
    with _lock:
        _reminders.append(new)
        _save()
    _schedule(new)
    return {'success': True, 'reminder': new}


def remove(rid: str) -> dict:
    """Remove a reminder by ID."""
    global _reminders
    with _lock:
        before = len(_reminders)
        _reminders = [r for r in _reminders if r['id'] != rid]
        if len(_reminders) == before:
            return {'success': False, 'message': f'Reminder not found: {rid}'}
        _save()

    if _HAS_APS and _scheduler:
        try:
            _scheduler.remove_job(rid)
        except Exception:
            pass
    return {'success': True}


def clear_all() -> dict:
    """Remove all reminders."""
    with _lock:
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
