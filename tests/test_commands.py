"""Built-in command routing and the time/duration parsers."""

from datetime import datetime

import pytest

import commands
from modules import reminders, tools


class Ctx:
    """Stands in for the main window."""
    def __init__(self):
        self.replies, self.events = [], []
        ctx = self

        class Bridge:
            def switch_backend(self, b): ctx.events.append(('backend', b))
            def switch_model(self, m): ctx.events.append(('model', m))
            def clear_ai_history(self): ctx.events.append(('clear_memory',))
        self.bridge = Bridge()

    def reply(self, r): self.replies.append(r.text)
    def clear_chat(self): self.events.append(('clear_chat',))
    def refresh(self, *what): pass
    def log(self, *a): pass
    def run_async(self, fn, done): self.events.append(('async',))


@pytest.fixture
def ctx(monkeypatch):
    from modules import apps
    c = Ctx()
    monkeypatch.setattr(apps, 'open_app', lambda n: c.events.append(('app', n)) or {'success': True, 'message': 'ok'})
    monkeypatch.setattr(apps, 'open_browser', lambda u: c.events.append(('url', u)) or {'success': True, 'message': 'ok'})
    yield c
    tools.cancel_countdown(None)
    reminders.clear_all()


@pytest.mark.parametrize('text, event', [
    ('use groq', ('backend', 'groq')),
    ('Switch to local.', ('backend', 'ollama')),
    ('model 70b', ('model', '70b')),
    ('open spotify', ('app', 'spotify')),
    ('open github.com', ('url', 'github.com')),
    ('search lofi on youtube', ('url', 'https://www.youtube.com/results?search_query=lofi')),
    ('weather in Riyadh', ('async',)),
    ('clear', ('clear_chat',)),
    ('clear memory', ('clear_memory',)),
])
def test_routes(ctx, text, event):
    assert commands.dispatch(ctx, text)
    assert ctx.events[0] == event


@pytest.mark.parametrize('text', [
    'Open Spotify and remind me at 5 to stretch',   # compound → AI tool calling
    'tell me a joke',
    'weatherproof jackets',
    '2024',
    'remind me at 25:99 to x',
])
def test_goes_to_ai(ctx, text):
    assert not commands.dispatch(ctx, text)


def test_timer_and_reminder_commands(ctx):
    assert commands.dispatch(ctx, 'set a timer for 10 min called pasta')
    assert tools.list_countdowns()[0]['label'] == 'pasta'
    assert commands.dispatch(ctx, 'remind me to stretch at 17:30 every day')
    assert reminders.get_all()[0]['repeat'] == 'daily'
    assert commands.dispatch(ctx, 'remind me in 20 minutes to check the oven')
    assert any(r['text'] == 'check the oven' and r['repeat'] == 'once' for r in reminders.get_all())


def test_stop_phrases():
    assert commands.is_stop('Stop.')
    assert commands.is_stop('TMOS, stop talking')
    assert not commands.is_stop('stop timer')


def test_parse_clock():
    now = datetime(2026, 9, 18, 13, 35)
    assert commands.parse_clock('5', now) == '17:00'
    assert commands.parse_clock('9', now) == '21:00'
    assert commands.parse_clock('5:30 p.m.', now) == '17:30'
    assert commands.parse_clock('09:30', now) == '09:30'
    assert commands.parse_clock('12am', now) == '00:00'
    assert commands.parse_clock('13pm', now) is None


def test_parse_duration():
    assert tools.parse_duration('5 minutes') == 300
    assert tools.parse_duration('1h 30m') == 5400
    assert tools.parse_duration('half an hour') == 1800
    assert tools.parse_duration('90') == 5400
    assert tools.parse_duration('nothing') == 0


# ── New commands: location, time zones, weather phrasing, places, media, memory ──

@pytest.fixture
def sync_ctx(ctx):
    """run_async runs right away, so we can see what a command asked for."""
    ctx.run_async = lambda fn, done: done(fn())
    return ctx


@pytest.mark.parametrize('text', ['where am I', 'what time is it in Tokyo', 'time in New York',
                                  'find a coffee shop near me'])
def test_routes_async(ctx, text):
    assert commands.dispatch(ctx, text)
    assert ctx.events[0] == ('async',)


@pytest.mark.parametrize('text', [
    "What's the latest news on the Riyadh Metro?",
    'is there a pharmacy near me',
    'play some music',
    'forget it',
])
def test_new_phrases_go_to_ai(ctx, text):
    assert not commands.dispatch(ctx, text)


def test_time_at_the_moment_is_local_time(ctx):
    assert commands.dispatch(ctx, 'what time is it at the moment')
    assert ctx.replies[-1].startswith("It's **")


@pytest.mark.parametrize('text, args, day', [
    ('weather', ('', 1), None),
    ('weather tomorrow', ('', 2), 1),
    ('weather in Riyadh tomorrow', ('Riyadh', 2), 1),
    ('weather this week in Jeddah', ('Jeddah', 7), None),
    ('forecast', ('', 7), None),
    ('weather forecast for Paris, Texas', ('Paris, Texas', 7), None),
    ("what's the weather going to be like tomorrow", ('', 2), 1),
    ('will it rain tomorrow in London', ('London', 2), 1),
    ("what's the weather near me", ('', 1), None),
])
def test_weather_phrases(sync_ctx, monkeypatch, text, args, day):
    from modules import weather
    got = []
    monkeypatch.setattr(weather, 'get_weather', lambda city='', days=1: got.append((city, days)) or {})
    monkeypatch.setattr(weather, 'describe', lambda w, d=None: got.append(d) or 'ok')
    assert commands.dispatch(sync_ctx, text)
    assert got == [args, day]


def test_places(sync_ctx, monkeypatch):
    from modules import actions
    got = []
    monkeypatch.setattr(actions, 'run', lambda name, args: got.append((name, args)) or {'ok': True})
    for text in ['find a coffee shop near me', 'where is the nearest pharmacy?',
                 'directions to King Fahd Stadium', 'how do I get to the airport']:
        assert commands.dispatch(sync_ctx, text)
    assert got == [('open_map', {'query': 'coffee shop', 'directions': False}),
                   ('open_map', {'query': 'pharmacy', 'directions': False}),
                   ('open_map', {'query': 'King Fahd Stadium', 'directions': True}),
                   ('open_map', {'query': 'the airport', 'directions': True})]


@pytest.mark.parametrize('text, call', [
    ('pause', ('play_pause', {})),
    ('Resume the music.', ('play_pause', {})),
    ('stop the music', ('play_pause', {})),
    ('next song', ('next', {})),
    ('skip', ('next', {})),
    ('previous track', ('previous', {})),
    ('volume 30', ('set_volume', {'level': 30})),
    ('set the volume to 75%', ('set_volume', {'level': 75})),
    ('turn the volume up', ('volume_up', {'step': 10})),
    ('volume down by 20', ('volume_down', {'step': 20})),
    ('turn it down', ('volume_down', {'step': 10})),
    ('louder', ('volume_up', {'step': 10})),
    ('mute the sound', ('mute', {})),
    ('unmute the volume', ('unmute', {})),
])
def test_media_commands(ctx, monkeypatch, text, call):
    from modules import media
    calls = []
    monkeypatch.setattr(media, 'control', lambda a, **kw: calls.append((a, kw)) or {'success': True, 'message': 'ok'})
    assert commands.dispatch(ctx, text)
    assert calls == [call]


def test_youtube_still_wins_over_play(ctx):
    assert commands.dispatch(ctx, 'play lofi on youtube')
    assert ctx.events[0] == ('url', 'https://www.youtube.com/results?search_query=lofi')


def test_memory_commands(ctx):
    from modules import memory, notes
    memory.clear_all()
    notes.clear_all()
    assert commands.dispatch(ctx, 'remember that my sister is Sara')
    assert commands.dispatch(ctx, 'remember to buy milk')
    assert [f['text'] for f in memory.get_all()] == ['my sister is Sara.']
    assert notes.get_all()[-1]['text'] == 'buy milk'           # "remember to…" is a to-do
    assert commands.dispatch(ctx, 'what do you remember about me')
    assert 'my sister is Sara' in ctx.replies[-1]
    assert commands.dispatch(ctx, 'forget my sister')
    assert memory.get_all() == []
    notes.clear_all()
