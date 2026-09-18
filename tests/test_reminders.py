"""One-time reminders fire once and disappear; daily ones stay."""

from modules import reminders


def test_once_reminder_removed_after_firing():
    fired = []
    reminders._callback = fired.append
    rid = reminders.add({'time': '17:00', 'text': 'call mom'})['reminder']['id']
    reminders._fire(rid)
    assert fired[0]['text'] == 'call mom'
    assert all(r['id'] != rid for r in reminders.get_all())


def test_daily_reminder_stays():
    fired = []
    reminders._callback = fired.append
    rid = reminders.add({'time': '08:00', 'text': 'stretch', 'repeat': 'daily'})['reminder']['id']
    reminders._fire(rid)
    reminders._fire(rid)
    assert len(fired) == 2
    assert any(r['id'] == rid for r in reminders.get_all())
    reminders.remove(rid)


def test_relative_reminder_and_bad_time():
    r = reminders.add({'text': 'oven', 'in_seconds': 600})
    assert r['success'] and r['reminder']['due']
    assert not reminders.add({'time': '24:61', 'text': 'x'})['success']
    reminders.clear_all()
