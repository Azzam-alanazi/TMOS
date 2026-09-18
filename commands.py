"""T.M.O.S — Built-in commands
Instant, free, offline shortcuts that run without asking the AI. Each command
is a regex plus a handler, registered with @command. Anything that doesn't
match goes to the AI, which can do the same things (and combinations of them)
through tool calling — see modules/actions.py.

A handler gets (ctx, match) where ctx is the main window, and returns:
  Reply(...)  — show (and speak) this answer
  None        — handled; the handler replies by itself (or needs no reply)
  False       — not actually for me; keep looking / ask the AI
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from urllib.parse import quote_plus

from modules import tools


@dataclass
class Reply:
    text: str
    speak: str | None = None      # None → speak the text; '' → stay silent
    level: str = 'ok'             # activity-log level: ok | warn | err | sys


_COMMANDS: list[tuple[re.Pattern, Callable]] = []


def command(*patterns: str):
    def deco(fn):
        for p in patterns:
            _COMMANDS.append((re.compile(p, re.IGNORECASE), fn))
        return fn
    return deco


STOP_RE = re.compile(
    r"(?:(?:hey\s+)?t\.?\s?m\.?\s?o\.?\s?s[,\s]*)?"
    r"(?:stop(?: talking| speaking| it)?|shut up|be quiet|quiet|silence|enough|never ?mind|cancel that)",
    re.IGNORECASE)


def is_stop(text: str) -> bool:
    return bool(STOP_RE.fullmatch(text.strip().strip('.!?, ')))


def dispatch(ctx, text: str) -> bool:
    """Run the first matching command. Returns False if nothing handled it."""
    raw = text.strip()
    cleaned = raw.rstrip('.!?').strip()          # voice input adds punctuation
    for pattern, fn in _COMMANDS:
        m = pattern.fullmatch(raw) or pattern.fullmatch(cleaned)
        if not m:
            continue
        result = fn(ctx, m)
        if result is False:
            continue
        if isinstance(result, Reply):
            ctx.reply(result)
        return True
    return False


# ── Time parsing helpers ─────────────────────────────────────────────────────

TIME = r'\d{1,2}(?::\d{2})?\s*(?:[ap]\.?\s?m\.?)?'
DAILY = r'(?:\s+(every\s*day|daily|each\s+day|every\s+morning|every\s+evening))?'


def parse_clock(text: str, now: datetime | None = None) -> str | None:
    """'5pm' → '17:00', '17:30' → '17:30', 'at 5' → whichever of 05:00/17:00 comes next."""
    s = text.lower().replace('.', '').replace(' ', '')
    m = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?([ap]m)?', s)
    if not m:
        return None
    h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if mins > 59:
        return None
    if ampm:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ampm == 'pm' else 0)
    elif 1 <= h <= 12 and not m.group(1).startswith('0'):
        # "at 5" — pick the next 5 o'clock (05:00 or 17:00)
        now = now or datetime.now()
        options = []
        for hh in (h % 12, h % 12 + 12):
            cand = now.replace(hour=hh, minute=mins, second=0, microsecond=0)
            if cand <= now:
                cand += timedelta(days=1)
            options.append(cand)
        h = min(options).hour
    if h > 23:
        return None
    return f'{h:02d}:{mins:02d}'


def _in_words(due: datetime) -> str:
    return tools.format_duration((due - datetime.now()).total_seconds() // 60 * 60 + 60) \
        if due > datetime.now() else 'now'


# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS (checked in this order)
# ══════════════════════════════════════════════════════════════════════════════

# ── Help / chat / memory ─────────────────────────────────────────────────────

@command(r'help|commands|what can you do')
def _help(ctx, m):
    return Reply(
        '**Try saying or typing:**\n'
        '• `open spotify` · `search black holes` · `youtube lofi` · `browse github.com`\n'
        '• `weather in Riyadh` · `stats` · `what time is it`\n'
        '• `timer 10 minutes` · `timer 25 min called focus` · `cancel timer`\n'
        '• `remind me at 5pm to call mom` · `remind me in 20 minutes to stretch` '
        '· add `every day` to repeat\n'
        '• `note buy milk` · `notes` · `copy [text]` · `clipboard` · `screenshot`\n'
        '• `calc 2^10` · `use groq / gemini / ollama` · `model list` · `model 120b`\n'
        '• `clear` (chat) · `clear memory` (AI) · `lock` · `sleep` · `shutdown`\n'
        '• `stop` or **Esc** — stop talking · `mute` / `unmute` — voice off/on\n\n'
        'Anything else goes to the AI, which can also combine these: '
        '*"open Spotify and remind me at 5 to stretch"*.',
        speak='Here are some things you can ask me.')


@command(r'clear|clear chat|clear screen|cls')
def _clear_chat(ctx, m):
    ctx.clear_chat()


@command(r'clear memory|clear history|forget(?: everything)?|reset(?: memory)?|new chat')
def _clear_memory(ctx, m):
    ctx.bridge.clear_ai_history()


@command(r'mute|be silent|voice off|(?:turn )?off (?:the )?voice')
def _mute(ctx, m):
    from modules import tts
    tts.set_muted(True)
    ctx.sync_voice()
    return Reply('🔇 Voice off. I\'ll reply in text only. Say `unmute` to hear me again.', speak='')


@command(r'unmute|voice on|speak aloud|(?:turn )?on (?:the )?voice')
def _unmute(ctx, m):
    from modules import tts
    tts.set_muted(False)
    ctx.sync_voice()
    return Reply('🔊 Voice on.')


# ── Backend / model ──────────────────────────────────────────────────────────

@command(r'(?:use|switch to|change to)\s+(groq|gemini|ollama|local)(?:\s+backend)?')
def _backend(ctx, m):
    ctx.bridge.switch_backend(m.group(1).lower().replace('local', 'ollama'))


@command(r'model list|models|list models|model')
def _model_list(ctx, m):
    from modules import ai
    current = ai.get_model()
    lines = [f"• `{k}` — {v['name']}{' (' + v['desc'] + ')' if v['desc'] else ''}"
             f"{'  ◄' if k == current else ''}"
             for k, v in ai.get_available_models().items()]
    return Reply(f'**{ai.BACKEND_NAMES[ai.get_backend()]} models:**\n' + '\n'.join(lines[:25])
                 + '\n\nSwitch with `model <name>`, e.g. `model 120b`.',
                 speak='Here are the available models.')


@command(r'model\s+(.+)')
def _model(ctx, m):
    ctx.bridge.switch_model(m.group(1).strip())


# ── Timers (before "open/start …" so "start timer" lands here) ──────────────

@command(r'(?:(?:set|start)\s+)?(?:a\s+)?timer\s+(start|stop|end|check|status)(?:\s+(\w+))?',
         r'(?:start\s+)?stopwatch(?:\s+(start|stop|end|check|status))?(?:\s+(\w+))?')
def _stopwatch(ctx, m):
    action = (m.group(1) or 'start').lower()
    name = m.group(2) or 'default'
    if action == 'start':
        r = tools.start_timer(name)
    elif action in ('stop', 'end'):
        r = tools.stop_timer(name)
        if r['success']:
            r['message'] = f'Stopwatch "{name}" stopped at {tools.format_duration(r["elapsed"])}.'
    else:
        r = tools.check_timer(name)
        if r['success']:
            r['message'] = f'Stopwatch "{name}": {tools.format_duration(r["elapsed"])} elapsed.'
    return Reply(r['message'], level='ok' if r['success'] else 'warn')


@command(r'(?:cancel|stop|clear|delete)\s+(?:the\s+|all\s+|my\s+)?(?:timers?|countdowns?)(?:\s+(.+))?')
def _cancel_timer(ctx, m):
    target = (m.group(1) or '').strip().lower()
    running = tools.list_countdowns()
    if target and target != 'all':
        running = [t for t in running if target in t['label'].lower()]
        if not running:
            return Reply(f'No timer called "{target}".', level='warn')
        for t in running:
            tools.cancel_countdown(t['id'])
        r = {'success': True, 'message': f'Cancelled {len(running)} timer(s).'}
    else:
        r = tools.cancel_countdown(None)
    ctx.refresh('timers')
    return Reply(r['message'], level='ok' if r['success'] else 'warn')


@command(r'timers|list timers|show timers|my timers')
def _list_timers(ctx, m):
    running = tools.list_countdowns()
    if not running:
        return Reply('No timers running.')
    import time as _t
    lines = [f"• **{t['label']}** — {tools.format_duration(max(0, t['ends_at'] - _t.time()))} left"
             for t in running]
    return Reply('**Timers:**\n' + '\n'.join(lines), speak=f'{len(running)} timers running.')


@command(r'(?:(?:set|start)\s+)?(?:a\s+)?(?:countdown|timer)\s+(?:for\s+)?(.+)',
         r'(?:set\s+|start\s+)?(?:a\s+)?(\d+(?:\.\d+)?\s*[a-z]+(?:\s+\d+\s*[a-z]+)?)\s+timer(?:\s+(?:for|called|named)\s+(.+))?')
def _countdown(ctx, m):
    spec = m.group(1).strip()
    label = m.group(2).strip() if m.re.groups > 1 and m.group(2) else ''
    if not label:
        lm = re.fullmatch(r'(.+?)\s+(?:called|named|labeled|labelled|for)\s+(.+)', spec, re.IGNORECASE)
        if lm and tools.parse_duration(lm.group(1)):
            spec, label = lm.group(1), lm.group(2)
    seconds = tools.parse_duration(spec)
    if seconds <= 0:
        return False
    r = tools.start_countdown(seconds, label)
    ctx.refresh('timers')
    if not r['success']:
        return Reply(r['message'], level='warn')
    t = r['timer']
    name = f' called **{label}**' if label else ''
    return Reply(f'⏱ Timer{name} set for **{tools.format_duration(seconds)}**.',
                 speak=f'Timer set for {tools.format_duration(seconds)}.')


# ── Reminders ────────────────────────────────────────────────────────────────

def _set_reminder(ctx, text: str, time_str: str | None = None, in_seconds: int = 0, daily=None):
    from modules import reminders as rem
    req = {'text': text.strip(), 'repeat': 'daily' if daily else 'once'}
    if in_seconds:
        req['in_seconds'] = in_seconds
    else:
        req['time'] = time_str
    r = rem.add(req)
    if not r['success']:
        return Reply(r['message'], level='warn')
    ctx.refresh('reminders')
    rm = r['reminder']
    if rm['repeat'] == 'daily':
        when = f"every day at **{rm['time']}**"
        spoken = f"every day at {rm['time']}"
    else:
        due = datetime.fromisoformat(rm['due'])
        day = '' if due.date() == datetime.now().date() else ' tomorrow'
        when = f"for **{rm['time']}**{day} (in {_in_words(due)})"
        spoken = f"for {rm['time']}{day}"
    return Reply(f"⏰ Reminder set {when}: {rm['text']}", speak=f'Reminder set {spoken}.')


@command(rf'remind me\s+(?:at|@)\s+({TIME})\s+(?:to\s+|that\s+|about\s+)?(.+?){DAILY}',
         rf'remind me\s+(?:to\s+|that\s+|about\s+)?(.+?)\s+at\s+({TIME}){DAILY}')
def _remind_at(ctx, m):
    a, b = m.group(1), m.group(2)
    time_part, text = (a, b) if re.fullmatch(TIME, a.strip(), re.IGNORECASE) else (b, a)
    clock = parse_clock(time_part)
    if not clock:
        return False
    return _set_reminder(ctx, text, time_str=clock, daily=m.group(3))


@command(r'remind me\s+in\s+(.+?)\s+(?:to|that|about)\s+(.+)',
         r'remind me\s+(?:to\s+|that\s+|about\s+)?(.+?)\s+in\s+(.+)')
def _remind_in(ctx, m):
    a, b = m.group(1), m.group(2)
    seconds = tools.parse_duration(a)
    text = b
    if not seconds:
        seconds, text = tools.parse_duration(b), a
    if not seconds:
        return False
    return _set_reminder(ctx, text, in_seconds=seconds)


@command(r'reminders|list reminders|show reminders|my reminders')
def _list_reminders(ctx, m):
    from modules import reminders as rem
    items = rem.get_all()
    if not items:
        return Reply('No reminders set. Try `remind me at 5pm to call mom`.')
    lines = [f"• **{r['time']}**{' daily' if r.get('repeat') == 'daily' else ''} — {r['text']}"
             for r in items]
    return Reply('**Reminders:**\n' + '\n'.join(lines), speak=f'You have {len(items)} reminders.')


# ── Web ──────────────────────────────────────────────────────────────────────

def _open_url(url: str, reply: str) -> Reply:
    from modules import apps
    r = apps.open_browser(url)
    return Reply(reply if r['success'] else r['message'], level='ok' if r['success'] else 'err')


@command(r'(?:youtube|yt)(?:\s+search)?(?:\s+for)?\s+(.+)',
         r'(?:play|search(?:\s+for)?|find)\s+(.+?)\s+on\s+youtube')
def _youtube(ctx, m):
    q = m.group(1).strip()
    return _open_url(f'https://www.youtube.com/results?search_query={quote_plus(q)}',
                     f'Searching YouTube for: {q}')


@command(r'open\s+\w+\s+and\s+search(?:\s+for)?\s+(.+)',
         r'(?:search|google)(?:\s+for)?\s+(.+?)(?:\s+on\s+google)?')
def _search(ctx, m):
    q = m.group(1).strip()
    return _open_url(f'https://www.google.com/search?q={quote_plus(q)}', f'Searching Google for: {q}')


@command(r'(?:browse|goto|go to|open)\s+((?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?)')
def _browse(ctx, m):
    url = m.group(1)
    if re.search(r'\.(?:exe|msc|lnk|bat|cmd|txt|docx?|xlsx?|pdf|png|jpe?g)$', url, re.IGNORECASE):
        return False
    return _open_url(url, f'Opening {url}...')


# ── Apps ─────────────────────────────────────────────────────────────────────

@command(r'(?:open|launch)\s+(.+)')
def _open_app(ctx, m):
    name = m.group(1).strip()
    # "open spotify and remind me…" is a job for the AI's tool calling
    if re.search(r'\b(and|then|also)\b|[,;]', name) or len(name.split()) > 4:
        return False
    from modules import apps
    r = apps.open_app(name)
    return Reply(r['message'], level='ok' if r['success'] else 'err')


# ── Info ─────────────────────────────────────────────────────────────────────

@command(r"(?:what(?:'s| is) the )?time|what time is it|(?:what(?:'s| is) )?(?:the |today's )?date(?: today)?|what day is (?:it|today)")
def _time(ctx, m):
    now = datetime.now()
    return Reply(f"It's **{now:%H:%M}** on {now:%A, %d %B %Y}.",
                 speak=f"It's {now:%H:%M}, {now:%A %d %B}.")


@command(r"(?:the\s+)?weather(?:\s+forecast)?(?:\s+(?:in|for|at))?(?:\s+(.+?))?(?:\s+today|\s+now)?",
         r"(?:what(?:'s| is)|how(?:'s| is)) the weather(?: like)?(?:\s+(?:in|for|at))?(?:\s+(.+?))?(?:\s+today|\s+now)?")
def _weather(ctx, m):
    city = (m.group(1) or '').strip()
    if city and not re.fullmatch(r"[\w .,'-]{1,60}", city):
        return False
    from modules import weather
    ctx.log(f'Fetching weather{" for " + city if city else ""}...', 'sys')
    ctx.run_async(lambda: weather.get_weather(city),
                  lambda w: ctx.reply(Reply(weather.describe(w), level='ok' if w.get('success') else 'warn')))


@command(r'stats|system stats|system status|status')
def _stats(ctx, m):
    from modules import system_info
    s = system_info.get_stats()
    c, r, d = s['cpu'], s['ram'], s['disk']
    batt = s.get('battery')
    freq = c.get('freq_mhz')
    freq_str = f' @ {freq}MHz' if freq else ''
    display = (
        f"**CPU** {c['percent']}% ({c['cores']}C/{c['threads']}T{freq_str})\n"
        f"**RAM** {r['used']}/{r['total']} GB ({r['percent']}%)\n"
        f"**Disk** {d['used']}/{d['total']} GB ({d['percent']}%)"
    )
    if batt:
        plug = '⚡' if batt['plugged'] else '🔋'
        display += f"\n{plug} **Battery** {batt['percent']}%"
    return Reply(display, speak=f"CPU {c['percent']} percent. RAM {r['used']} of {r['total']} gigabytes.")


@command(r'files|list files|home files')
def _files(ctx, m):
    from modules import files
    r = files.list_dir()
    items = r.get('items', [])
    names = ', '.join(('📁 ' if i['is_folder'] else '') + i['name'] for i in items[:12])
    suffix = f' … ({len(items)} total)' if len(items) > 12 else ''
    return Reply(f'**Home:** {names}{suffix}', speak=f'Your home folder has {len(items)} items.')


# ── Calculator ───────────────────────────────────────────────────────────────

@command(r'(?:calc|calculate|compute)\s+(.+)',
         r"(?:what(?:'s| is)\s+)?([\d\s.+\-*/^()×÷%]+)(?:\s*=)?")
def _calc(ctx, m):
    expr = m.group(1).strip()
    if not re.search(r'\d', expr) or (m.re.pattern.startswith('(?:what') and
                                      not re.search(r'\d\s*[+\-*/^×÷%]\s*[\d(]', expr)):
        return False
    r = tools.calculate(expr)
    if not r['success']:
        return Reply(r['message'], level='warn')
    return Reply(f'**{expr}** = `{r["result"]}`', speak=f'{expr} equals {r["result"]}')


# ── Clipboard / screenshot ───────────────────────────────────────────────────

@command(r'screenshot|ss|screen capture|take a screenshot|capture (?:the )?screen')
def _screenshot(ctx, m):
    r = tools.take_screenshot()
    return Reply(r.get('message', 'Screenshot failed.'),
                 speak='Screenshot taken.' if r['success'] else 'Screenshot failed.',
                 level='ok' if r['success'] else 'err')


@command(r'clipboard|paste|get clipboard|read clipboard|what(?:\'s| is) on (?:my|the) clipboard')
def _clipboard(ctx, m):
    r = tools.get_clipboard()
    if not r['success']:
        return Reply(r['message'], level='err')
    text = r['text']
    if not text:
        return Reply('Clipboard is empty.')
    short = text[:300] + ('…' if len(text) > 300 else '')
    return Reply(f'**Clipboard:**\n```\n{short}\n```', speak='Here is your clipboard.')


@command(r'copy\s+(.+)')
def _copy(ctx, m):
    r = tools.set_clipboard(m.group(1).strip())
    return Reply(r['message'], level='ok' if r['success'] else 'err')


# ── Notes ────────────────────────────────────────────────────────────────────

@command(r'notes|list notes|show notes|my notes|show my notes')
def _list_notes(ctx, m):
    from modules import notes
    all_notes = notes.get_all()
    if not all_notes:
        return Reply('No notes yet. Use `note [text]` to save one.')
    lines = [f'• **{n["title"]}** ({n["created"]})' for n in all_notes[-8:]]
    return Reply('**Recent notes:**\n' + '\n'.join(lines), speak=f'You have {len(all_notes)} notes.')


@command(r'(?:add\s+)?note(?::|\s)\s*(.+)',
         r'(?:take|make)\s+a\s+note(?:\s+that)?\s*:?\s*(.+)',
         r'remember(?:\s+that)?\s+(.+)')
def _note(ctx, m):
    from modules import notes
    text = m.group(1).strip()
    r = notes.add(text)
    ctx.refresh('notes')
    if not r['success']:
        return Reply(r['message'], level='warn')
    return Reply(f'📝 Note saved: {text[:80]}', speak='Note saved.')


# ── Power ────────────────────────────────────────────────────────────────────

def _power(action: str) -> Reply:
    r = tools.system_command(action)
    return Reply(r['message'], level='ok' if r['success'] else 'err')


@command(r'lock|lock (?:the )?(?:pc|computer|screen)')
def _lock(ctx, m):
    return _power('lock')


@command(r'sleep|sleep (?:pc|computer)|go to sleep')
def _sleep(ctx, m):
    return _power('sleep')


@command(r'cancel\s+(?:the\s+)?(?:shutdown|restart|reboot)')
def _cancel_power(ctx, m):
    return _power('cancel')


@command(r'(?:shutdown|shut down)(?:\s+(?:the\s+)?(?:pc|computer))?')
def _shutdown(ctx, m):
    return _power('shutdown')


@command(r'(?:restart|reboot)(?:\s+(?:the\s+)?(?:pc|computer))?')
def _restart(ctx, m):
    return _power('restart')
