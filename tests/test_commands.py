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
