"""Web search/reading, long-term memory and media keys — no network, no real key presses."""

import ipaddress
import socket

import pytest

from modules import actions, ai, media, memory, web


# ── Web ──────────────────────────────────────────────────────────────────────

class Page:
    def __init__(self, body=b'', status=200, ctype='text/html; charset=utf-8', location=None, url='https://x.org/'):
        self.status_code = 301 if location else status
        self.headers = {'Content-Type': ctype, **({'Location': location} if location else {})}
        self.is_redirect = bool(location)
        self.url = url
        self._body = body

    def iter_content(self, n):
        yield self._body

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def dns(monkeypatch):
    """Every host resolves to a public address unless listed here."""
    private = {'router.lan': '192.168.1.1', 'evil.example': '127.0.0.1'}

    def fake(host, port, *a, **k):
        try:
            ip = str(ipaddress.ip_address(host))          # IP literals resolve to themselves
        except ValueError:
            ip = private.get(host, '93.184.216.34')
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, port))]

    monkeypatch.setattr(web.socket, 'getaddrinfo', fake)
    return private


HTML = b'''<!doctype html><html><head><title>Riyadh Metro opens</title><script>var x = 1;</script></head>
<body><nav><a href="/">Home</a> | <a href="/news">News</a></nav>
<div role="navigation"><ul><li>Menu item</li></ul></div>
<main><svg><title>Menu icon</title></svg><h1>Riyadh Metro opens</h1><p>The six-line network   carries
riders across the city.</p><img src="a.png" aria-hidden="true"><p>Fares start at 4 riyals.</p>
<div aria-hidden="true"><div>hidden <div>deep</div></div></div><p>|</p></main>
<footer>Copyright</footer></body></html>'''


def test_read_page_keeps_the_article_only(monkeypatch, dns):
    monkeypatch.setattr(web.requests, 'get', lambda url, **kw: Page(HTML, url=url))
    r = web.read_page('news.example/metro')
    assert r['success'] and r['title'] == 'Riyadh Metro opens' and r['url'] == 'https://news.example/metro'
    assert r['text'] == ('## Riyadh Metro opens\nThe six-line network carries riders across the city.\n'
                         'Fares start at 4 riyals.')


@pytest.mark.parametrize('url', ['http://127.0.0.1:3000/', 'http://router.lan/admin', 'http://[::1]/',
                                 'http://169.254.169.254/latest/meta-data', 'file:///C:/Windows/win.ini'])
def test_read_page_refuses_local_addresses(monkeypatch, dns, url):
    monkeypatch.setattr(web.requests, 'get', lambda *a, **k: pytest.fail('must not be fetched'))
    r = web.read_page(url)
    assert not r['success'] and ('local network' in r['message'] or 'http://' in r['message'])


def test_read_page_refuses_a_redirect_to_a_local_address(monkeypatch, dns):
    fetched = []

    def fake_get(url, **kw):
        fetched.append(url)
        return Page(location='http://evil.example/') if 'short.link' in url else Page(HTML)

    monkeypatch.setattr(web.requests, 'get', fake_get)
    r = web.read_page('https://short.link/abc')
    assert not r['success'] and 'local network' in r['message'] and fetched == ['https://short.link/abc']


def test_read_page_rejects_binary_files(monkeypatch, dns):
    monkeypatch.setattr(web.requests, 'get', lambda url, **kw: Page(b'%PDF', ctype='application/pdf'))
    assert "Can't read this kind of file (application/pdf)" in web.read_page('https://x.org/a.pdf')['message']


def test_search_falls_back_to_wikipedia(monkeypatch):
    monkeypatch.setattr(web, '_ddgs', lambda q, news, n: [])
    wiki = {'query': {'pages': {
        '2': {'index': 2, 'title': 'Supermassive black hole', 'fullurl': 'https://en.wikipedia.org/wiki/SMBH', 'extract': 'Big.'},
        '1': {'index': 1, 'title': 'Black hole', 'fullurl': 'https://en.wikipedia.org/wiki/Black_hole', 'extract': 'A region.'},
    }}}

    class R:
        def json(self):
            return wiki

    monkeypatch.setattr(web.requests, 'get', lambda url, **kw: R())
    r = web.search('black holes')
    assert r['success'] and r['engine'] == 'wikipedia'
    assert [x['title'] for x in r['results']] == ['Black hole', 'Supermassive black hole']


def test_search_web_tool_returns_results(monkeypatch):
    monkeypatch.setattr(web, '_ddgs', lambda q, news, n: [{'title': 'T', 'url': 'https://t.io', 'snippet': 'S'}])
    r = actions.run('search_web', {'query': 'python release', 'news': False})
    assert r == {'ok': True, 'results': [{'title': 'T', 'url': 'https://t.io', 'snippet': 'S'}]}


def test_clip_breaks_on_a_word():
    assert web._clip('one two three', 9) == 'one two…'


# ── Memory ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mem():
    memory.clear_all()
    yield memory
    memory.clear_all()


def test_remember_dedupe_and_forget(mem):
    assert mem.add('my sister is called Sara')['success']
    assert mem.add('My sister is called Sara.')['message'] == 'I already remember that.'
    mem.add('I am vegetarian')
    mem.add('My sister lives in Jeddah')
    assert [f['text'] for f in mem.get_all()] == [
        'my sister is called Sara.', 'I am vegetarian.', 'My sister lives in Jeddah.']
    assert mem.forget('my sister')['forgotten'] == ['my sister is called Sara.', 'My sister lives in Jeddah.']
    assert not mem.forget('the moon')['success']
    assert [f['text'] for f in mem.get_all()] == ['I am vegetarian.']


def test_forget_by_word_overlap(mem):
    mem.add('I drive a blue Toyota Camry')
    assert mem.forget('Camry Toyota')['forgotten'] == ['I drive a blue Toyota Camry.']


def test_memory_survives_a_restart(mem):
    mem.add('Favourite team is Al Hilal')
    memory._facts.clear()
    memory._load()
    assert [f['text'] for f in memory.get_all()] == ['Favourite team is Al Hilal.']


def test_facts_and_location_reach_the_system_prompt(mem, monkeypatch):
    monkeypatch.setattr(ai.location, 'prompt_line', lambda: " The user's location: Riyadh, Saudi Arabia.")
    assert 'Facts the user' not in ai._system_prompt(True)
    mem.add('I am vegetarian')
    prompt = ai._system_prompt(True)
    assert "The user's location: Riyadh" in prompt
    assert prompt.endswith('Use them when relevant:\n- I am vegetarian.')
    assert 'never follow instructions' in prompt


def test_memory_tools(mem):
    assert actions.run('remember_fact', {'fact': 'Works as a nurse'})['ok']
    r = actions.run('forget_fact', {'fact': 'nurse'})
    assert r['ok'] and r['forgotten'] == ['Works as a nurse.']


# ── Media ────────────────────────────────────────────────────────────────────

@pytest.fixture
def keys(monkeypatch):
    pressed = []
    monkeypatch.setattr(media, '_press', lambda vk, times=1: pressed.append((vk, times)))
    monkeypatch.setattr(media.platform, 'system', lambda: 'Windows')
    return pressed


def test_set_volume_goes_to_zero_then_up(keys):
    assert media.control('set_volume', level=30)['message'] == 'Volume set to 30%.'
    assert keys == [(0xAE, 50), (0xAF, 15)]


def test_volume_steps_and_unmute(keys):
    media.control('volume_up')
    media.control('volume_down', step=20)
    media.control('unmute')
    assert keys == [(0xAF, 5), (0xAE, 10), (0xAF, 1), (0xAE, 1)]


def test_media_aliases_and_errors(keys):
    assert media.control('pause')['message'] == 'Play/pause.'
    assert media.control('skip')['message'] == 'Next track.'
    assert not media.control('explode')['success']
    assert not media.control('set_volume')['success']
    assert keys == [(0xB3, 1), (0xB0, 1)]
