"""T.M.O.S — AI Actions (tool calling)
The functions the AI is allowed to call, described as JSON schemas, plus the
dispatcher that runs them. This is what lets "open Spotify and remind me at 5
to stretch" or "what's the news in Riyadh?" work without matching an exact command.

Deliberately NOT exposed to the AI: deleting files, shutdown/restart and
arbitrary shell commands. Those stay behind explicit typed/spoken commands.
"""

import json
from urllib.parse import quote, quote_plus


def _schema(name: str, description: str, props: dict | None = None,
            required: list[str] | None = None) -> dict:
    return {
        'name': name,
        'description': description,
        'parameters': {
            'type': 'object',
            'properties': props or {},
            'required': required or [],
        },
    }


SCHEMAS: list[dict] = [
    _schema('open_app', "Open a desktop application on the user's Windows PC "
            '(e.g. chrome, spotify, discord, vscode, notepad, steam, word).',
            {'name': {'type': 'string', 'description': 'Application name'}}, ['name']),
    _schema('open_website', 'Open a URL in the default web browser.',
            {'url': {'type': 'string', 'description': 'Full URL or domain, e.g. github.com'}}, ['url']),
    _schema('open_search', "Open a Google or YouTube search in the user's browser, for when they "
            'want to see the results or watch videos. To answer a question yourself, use search_web.',
            {'query': {'type': 'string'},
             'site': {'type': 'string', 'enum': ['google', 'youtube'],
                      'description': 'Where to search (default google)'}}, ['query']),
    _schema('search_web', 'Search the internet and get the top results back. Use it for news, prices, '
            'scores, recent events, opening hours or any fact you are unsure of, then answer from them.',
            {'query': {'type': 'string'},
             'news': {'type': 'boolean', 'description': 'Search recent news articles instead'}}, ['query']),
    _schema('read_webpage', 'Read the text of a web page (e.g. a search result) to answer from it or summarize it.',
            {'url': {'type': 'string'}}, ['url']),
    _schema('set_reminder', 'Set a reminder that pops up and is spoken aloud. Give EITHER '
            'time (24-hour HH:MM clock time) OR in_minutes (relative).',
            {'text': {'type': 'string', 'description': 'What to remind the user about'},
             'time': {'type': 'string', 'description': '24-hour time HH:MM, e.g. 17:00'},
             'in_minutes': {'type': 'number', 'description': 'Minutes from now'},
             'daily': {'type': 'boolean', 'description': 'Repeat every day (default false)'}},
            ['text']),
    _schema('list_reminders', 'List the reminders that are currently set.'),
    _schema('add_note', 'Save a note for the user.',
            {'text': {'type': 'string'}}, ['text']),
    _schema('list_notes', "Read the user's saved notes."),
    _schema('start_timer', 'Start a countdown timer that alerts the user when it ends.',
            {'seconds': {'type': 'integer', 'description': 'Length in seconds'},
             'label': {'type': 'string', 'description': 'Optional name, e.g. "pasta"'}},
            ['seconds']),
    _schema('get_system_stats', 'Get CPU, RAM, disk, battery and top processes.'),
    _schema('get_weather', "Current weather and a daily forecast. Leave city empty for the user's location.",
            {'city': {'type': 'string'},
             'days': {'type': 'integer', 'description': 'Days to forecast including today, 1-7 '
                                                        '(default 1; 2 covers tomorrow)'}}),
    _schema('get_location', "Get the user's current location: area, city, country, coordinates, time zone."),
    _schema('get_time', "Current local time in a city or country (time zones). Empty place = the user's own time.",
            {'place': {'type': 'string'}}),
    _schema('open_map', "Show places or directions on Google Maps, around the user's location "
            '(e.g. "pharmacy", "coffee shops", "King Fahd Stadium").',
            {'query': {'type': 'string'},
             'directions': {'type': 'boolean', 'description': 'Directions from the user to the place'}},
            ['query']),
    _schema('media_control', 'Control music/video playback and the system volume.',
            {'action': {'type': 'string', 'enum': ['play_pause', 'next', 'previous', 'stop', 'volume_up',
                                                   'volume_down', 'mute', 'unmute', 'set_volume']},
             'level': {'type': 'integer', 'description': 'Volume 0-100, for set_volume'}}, ['action']),
    _schema('remember_fact', 'Save a lasting fact about the user (name, family, likes, work, plans…) to your '
            'long-term memory. Use it when they tell you something worth keeping or say "remember…".',
            {'fact': {'type': 'string', 'description': 'e.g. "Sister is called Sara"'}}, ['fact']),
    _schema('forget_fact', 'Delete a fact from your long-term memory when the user asks you to forget it.',
            {'fact': {'type': 'string', 'description': 'The fact, or a distinctive part of it'}}, ['fact']),
    _schema('take_screenshot', 'Take a screenshot and save it to the Desktop.'),
    _schema('copy_to_clipboard', 'Copy text to the clipboard.',
            {'text': {'type': 'string'}}, ['text']),
    _schema('read_clipboard', 'Read the text currently on the clipboard.'),
    _schema('calculate', 'Evaluate a math expression exactly (use for any arithmetic).',
            {'expression': {'type': 'string', 'description': 'Python-style math, e.g. 2**10/3'}},
            ['expression']),
    _schema('list_files', 'List files in a folder (default: the home folder).',
            {'path': {'type': 'string', 'description': 'Folder path, e.g. ~/Desktop'}}),
    _schema('lock_computer', 'Lock the Windows session.'),
]

TOOL_NAMES = {s['name'] for s in SCHEMAS}

# Short human labels for the chat UI
LABELS = {
    'open_app': 'Open app', 'open_website': 'Open site', 'open_search': 'Search',
    'search_web': 'Web search', 'read_webpage': 'Read page', 'get_location': 'Location',
    'get_time': 'World clock', 'open_map': 'Map', 'media_control': 'Media',
    'remember_fact': 'Remembered', 'forget_fact': 'Forgot',
    'set_reminder': 'Reminder', 'list_reminders': 'Reminders', 'add_note': 'Note',
    'list_notes': 'Notes', 'start_timer': 'Timer', 'get_system_stats': 'System stats',
    'get_weather': 'Weather', 'take_screenshot': 'Screenshot',
    'copy_to_clipboard': 'Copy', 'read_clipboard': 'Clipboard', 'calculate': 'Calculate',
    'list_files': 'Files', 'lock_computer': 'Lock',
}


def parse_args(raw) -> dict:
    """Tool arguments arrive as a JSON string (Groq) or a dict (Gemini/Ollama)."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        return {}


def run(name: str, args: dict) -> dict:
    """Execute one tool call. Always returns a JSON-serialisable dict."""
    fn = _HANDLERS.get(name)
    if fn is None:
        return {'ok': False, 'error': f'Unknown tool: {name}'}
    try:
        return fn(**{k: v for k, v in (args or {}).items() if v is not None})
    except TypeError as e:
        return {'ok': False, 'error': f'Bad arguments for {name}: {e}'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


# ── Handlers ─────────────────────────────────────────────────────────────────

def _result(r: dict, **extra) -> dict:
    out = {'ok': bool(r.get('success', True))}
    if r.get('message'):
        out['message'] = r['message']
    out.update(extra)
    return out


def _open_app(name: str) -> dict:
    from modules import apps
    return _result(apps.open_app(name))


def _open_website(url: str) -> dict:
    from modules import apps
    return _result(apps.open_browser(url))


def _open_search(query: str, site: str = 'google') -> dict:
    from modules import apps
    if site == 'youtube':
        url = f'https://www.youtube.com/results?search_query={quote_plus(query)}'
    else:
        url = f'https://www.google.com/search?q={quote_plus(query)}'
    return _result(apps.open_browser(url))


def _search_web(query: str, news: bool = False) -> dict:
    from modules import web
    r = web.search(query, news=bool(news))
    return _result(r, results=r.get('results', []))


def _read_webpage(url: str) -> dict:
    from modules import web
    r = web.read_page(url)
    if not r['success']:
        return _result(r)
    return {'ok': True, 'url': r['url'], 'title': r['title'], 'text': r['text'],
            'truncated': r['truncated']}


def _set_reminder(text: str, time: str = '', in_minutes: float | None = None,
                  daily: bool = False) -> dict:
    from modules import reminders
    req = {'text': text, 'repeat': 'daily' if daily else 'once'}
    if in_minutes:
        req['in_seconds'] = int(float(in_minutes) * 60)
    else:
        req['time'] = time
    r = reminders.add(req)
    if not r['success']:
        return _result(r)
    rem = r['reminder']
    return {'ok': True, 'time': rem['time'], 'repeat': rem['repeat'], 'text': rem['text']}


def _list_reminders() -> dict:
    from modules import reminders
    return {'ok': True, 'reminders': [
        {'time': r['time'], 'text': r['text'], 'repeat': r.get('repeat', 'once')}
        for r in reminders.get_all()
    ]}


def _add_note(text: str) -> dict:
    from modules import notes
    return _result(notes.add(text))


def _list_notes() -> dict:
    from modules import notes
    return {'ok': True, 'notes': [
        {'text': n['text'], 'created': n['created']} for n in notes.get_all()[-20:]
    ]}


def _start_timer(seconds: int, label: str = '') -> dict:
    from modules import tools
    return _result(tools.start_countdown(int(seconds), label))


def _get_system_stats() -> dict:
    from modules import system_info
    s = system_info.get_stats()
    return {'ok': True, 'cpu_percent': s['cpu']['percent'], 'ram': s['ram'], 'disk': s['disk'],
            'battery': s['battery'], 'top_processes': s['processes'],
            'uptime_hours': s['os']['uptime']}


def _get_weather(city: str = '', days: int = 1) -> dict:
    from modules import weather
    w = weather.get_weather(city, days)
    w['ok'] = w.pop('success', False)
    return w


def _get_location() -> dict:
    from modules import location
    loc = location.get()
    if not loc.get('success'):
        return {'ok': False, 'message': loc.get('message', 'Location unavailable.')}
    out = {'ok': True, 'place': location.label(loc),
           'source': location.SOURCES.get(loc['source'], loc['source'])}
    for k in ('area', 'city', 'region', 'country', 'timezone', 'accuracy_m'):
        if loc.get(k):
            out[k] = loc[k]
    out['lat'], out['lon'] = round(loc['lat'], 3), round(loc['lon'], 3)
    return out


def _get_time(place: str = '') -> dict:
    from modules import location
    r = location.local_time(place)
    r['ok'] = r.pop('success', False)
    return r


def _open_map(query: str, directions: bool = False) -> dict:
    from modules import apps, location
    loc = location.get() if location.mode() != 'off' else {}
    if directions:
        url = f'https://www.google.com/maps/dir/?api=1&destination={quote_plus(query)}'
        if loc.get('source') == 'windows':          # precise; otherwise Maps asks the browser
            url += f"&origin={loc['lat']},{loc['lon']}"
    elif loc.get('success'):
        zoom = 15 if loc['source'] == 'windows' else 12
        url = f"https://www.google.com/maps/search/{quote(query)}/@{loc['lat']},{loc['lon']},{zoom}z"
    else:
        url = f'https://www.google.com/maps/search/?api=1&query={quote_plus(query)}'
    return _result(apps.open_browser(url))


def _media_control(action: str, level: int | None = None) -> dict:
    from modules import media
    return _result(media.control(action, level=level))


def _remember_fact(fact: str) -> dict:
    from modules import memory
    return _result(memory.add(fact))


def _forget_fact(fact: str) -> dict:
    from modules import memory
    r = memory.forget(fact)
    return _result(r, forgotten=r.get('forgotten', []))


def _take_screenshot() -> dict:
    from modules import tools
    return _result(tools.take_screenshot())


def _copy_to_clipboard(text: str) -> dict:
    from modules import tools
    return _result(tools.set_clipboard(text))


def _read_clipboard() -> dict:
    from modules import tools
    r = tools.get_clipboard()
    return _result(r, text=r.get('text', '')[:4000])


def _calculate(expression: str) -> dict:
    from modules import tools
    r = tools.calculate(expression)
    return _result(r, result=r.get('result'))


def _list_files(path: str = '') -> dict:
    from modules import files
    r = files.list_dir(path or None)
    items = [{'name': i['name'], 'folder': i['is_folder'], 'size': i['size_human']}
             for i in r.get('items', [])[:60]]
    return _result(r, path=r.get('path', path), items=items, total=len(r.get('items', [])))


def _lock_computer() -> dict:
    from modules import tools
    return _result(tools.system_command('lock'))


_HANDLERS = {
    'open_app': _open_app, 'open_website': _open_website, 'open_search': _open_search,
    'search_web': _search_web, 'read_webpage': _read_webpage,
    'set_reminder': _set_reminder, 'list_reminders': _list_reminders,
    'add_note': _add_note, 'list_notes': _list_notes, 'start_timer': _start_timer,
    'get_system_stats': _get_system_stats, 'get_weather': _get_weather,
    'take_screenshot': _take_screenshot, 'copy_to_clipboard': _copy_to_clipboard,
    'read_clipboard': _read_clipboard, 'calculate': _calculate,
    'list_files': _list_files, 'lock_computer': _lock_computer,
    'get_location': _get_location, 'get_time': _get_time, 'open_map': _open_map,
    'media_control': _media_control, 'remember_fact': _remember_fact, 'forget_fact': _forget_fact,
}
