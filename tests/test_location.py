"""Location (Windows → IP → manual → off), the world clock and the weather that uses them.
Every HTTP call is faked."""

import json

import pytest
import requests

from modules import actions, config, location, weather


class Resp:
    def __init__(self, body):
        self._body = body
        self.status_code = 200

    def json(self):
        return self._body


IPWHOIS = {'success': True, 'city': 'Riyadh', 'region': 'Riyadh Region', 'country': 'Saudi Arabia',
           'country_code': 'SA', 'latitude': 24.69, 'longitude': 46.72,
           'timezone': {'id': 'Asia/Riyadh'}}
GEO_PARIS = {'results': [
    {'name': 'Paris', 'latitude': 48.85, 'longitude': 2.35, 'country': 'France',
     'country_code': 'FR', 'admin1': 'Île-de-France', 'timezone': 'Europe/Paris'},
    {'name': 'Paris', 'latitude': 33.66, 'longitude': -95.55, 'country': 'United States',
     'country_code': 'US', 'admin1': 'Texas', 'timezone': 'America/Chicago'},
]}


@pytest.fixture
def net(monkeypatch):
    """Fake requests.get: routes[url-substring] → body, or an exception to raise."""
    routes, calls = {}, []

    def fake_get(url, params=None, **kw):
        calls.append((url, params))
        for part, body in routes.items():
            if part in url:
                if isinstance(body, Exception):
                    raise body
                return Resp(body(params) if callable(body) else body)
        raise requests.exceptions.ConnectionError(url)

    monkeypatch.setattr(location.requests, 'get', fake_get)
    monkeypatch.setattr(config, '_save', lambda: None)
    monkeypatch.setattr(location, '_cache', None)
    monkeypatch.setattr(location, '_last_error', {})
    monkeypatch.setattr(location, 'windows_location_allowed', lambda: (False, 'Windows location services are off'))
    monkeypatch.setattr(location, '_refresh_in_background', lambda: None)
    monkeypatch.setitem(config._config, 'location_mode', 'auto')
    monkeypatch.setitem(config._config, 'location_place', '')
    return routes, calls


def test_ip_location_falls_through_providers(net):
    routes, _ = net
    routes['ipwho.is'] = requests.exceptions.Timeout()
    routes['ipinfo.io'] = {'city': 'Riyadh', 'region': 'Riyadh Region', 'country': 'SA',
                           'loc': '24.69,46.72', 'timezone': 'Asia/Riyadh'}
    loc = location.get()
    assert loc['success'] and loc['source'] == 'ip'
    assert (loc['city'], loc['lat'], loc['timezone']) == ('Riyadh', 24.69, 'Asia/Riyadh')
    assert location.label(loc) == 'Riyadh, SA'          # region skipped: it repeats the city


def test_location_is_cached_and_reused_when_offline(net, monkeypatch):
    routes, calls = net
    routes['ipwho.is'] = IPWHOIS
    assert location.get()['city'] == 'Riyadh'
    n = len(calls)
    assert location.get()['city'] == 'Riyadh'
    assert len(calls) == n                              # served from the cache
    routes.clear()                                      # now offline
    assert location.get(refresh=True)['city'] == 'Riyadh'   # last known place beats nothing


def test_windows_location_is_preferred_and_reverse_geocoded(net, monkeypatch):
    routes, _ = net
    monkeypatch.setattr(location, 'windows_location_allowed', lambda: (True, ''))

    class Done:
        stdout = json.dumps({'lat': 24.7412, 'lon': 46.6573, 'accuracy': 35.4}).encode()

    monkeypatch.setattr(location.subprocess, 'run', lambda *a, **k: Done())
    routes['nominatim'] = {'address': {'neighbourhood': 'Al Wuroud', 'city': 'Riyadh',
                                       'state': 'Riyadh Region', 'country': 'Saudi Arabia',
                                       'country_code': 'sa'}}
    loc = location.get()
    assert loc['source'] == 'windows' and loc['accuracy_m'] == 35
    assert location.label(loc) == 'Al Wuroud, Riyadh, Saudi Arabia'
    assert '(precise)' in location.prompt_line()


def test_windows_error_falls_back_to_ip(net, monkeypatch):
    routes, _ = net
    monkeypatch.setattr(location, 'windows_location_allowed', lambda: (True, ''))

    class Failed:
        stdout = b'{"error": "The service cannot be started"}'

    monkeypatch.setattr(location.subprocess, 'run', lambda *a, **k: Failed())
    routes['ipwho.is'] = IPWHOIS
    assert location.get()['source'] == 'ip'


def test_manual_place_uses_the_qualifier(net):
    routes, _ = net
    routes['geocoding-api'] = GEO_PARIS
    config._config.update(location_mode='manual', location_place='Paris, Texas')
    loc = location.get()
    assert loc['source'] == 'manual' and loc['region'] == 'Texas'
    config._config['location_place'] = 'Paris'         # a new place invalidates the cache
    assert location.get()['country'] == 'France'


def test_off_means_no_lookups(net):
    routes, calls = net
    config._config['location_mode'] = 'off'
    assert location.get() == {'success': False, 'off': True, 'message': 'Location is turned off in Settings.'}
    assert location.prompt_line() == ''
    w = weather.get_weather('')
    assert not w['success'] and 'Location is off' in w['message']
    assert calls == []


def test_prompt_line_and_status(net, monkeypatch):
    routes, _ = net
    routes['ipwho.is'] = IPWHOIS
    location.get()
    line = location.prompt_line()
    assert 'Riyadh, Saudi Arabia (approximate' in line and 'Asia/Riyadh' in line
    st = location.status()
    assert st['text'].startswith('Now: Riyadh') and 'Windows location services are off' in st['hint']


def test_status_explains_a_failed_lookup(net):
    config._config.update(location_mode='manual', location_place='')
    location.get()
    assert 'No place set' in location.status()['text']


def test_local_time_in_another_city(net):
    routes, _ = net
    routes['geocoding-api'] = {'results': [
        {'name': 'Tokyo', 'latitude': 35.69, 'longitude': 139.69, 'country': 'Japan', 'timezone': 'Asia/Tokyo'}]}
    r = location.local_time('Tokyo')
    assert r['success'] and r['place'] == 'Tokyo, Japan' and len(r['time']) == 5
    assert r['difference'].endswith('you')
    assert location._hours_apart(6) == '6 hours ahead of you'
    assert location._hours_apart(-5.5) == '5.5 hours behind you'
    assert location._hours_apart(0) == 'same time as you'


FORECAST = {
    'current': {'temperature_2m': 38.6, 'apparent_temperature': 35.2, 'relative_humidity_2m': 8,
                'wind_speed_10m': 10.4, 'weather_code': 0},
    'daily': {'time': ['2026-09-18', '2026-09-19', '2026-09-20'], 'weather_code': [0, 61, 3],
              'temperature_2m_max': [42.2, 30.1, 35], 'temperature_2m_min': [28, 21.5, 25],
              'precipitation_probability_max': [0, 80, 10],
              'sunrise': ['2026-09-18T05:40', '2026-09-19T05:40', '2026-09-20T05:41'],
              'sunset': ['2026-09-18T18:01', '2026-09-19T18:00', '2026-09-20T17:59']},
}


def test_weather_uses_location_and_forecasts(net):
    routes, calls = net
    routes['ipwho.is'] = IPWHOIS
    routes['api.open-meteo.com'] = FORECAST
    w = weather.get_weather('', days=3)
    assert w['success'] and w['place'] == 'Riyadh' and w['temp_c'] == 39 and w['sunset'] == '18:01'
    assert [d['day'] for d in w['forecast']] == ['Today', 'Tomorrow', 'Sunday']
    assert calls[-1][1]['latitude'] == 24.69 and calls[-1][1]['forecast_days'] == 3
    assert weather.describe(w, day=1) == '**Riyadh, Saudi Arabia, tomorrow**: light rain, 30° / 22°, 80% chance of rain.'
    assert '• **Sunday**: overcast, 35° / 25°, 10% chance of rain' in weather.describe(w)


def test_weather_for_a_named_city(net):
    routes, _ = net
    routes['geocoding-api'] = GEO_PARIS
    routes['api.open-meteo.com'] = FORECAST
    w = weather.get_weather('Paris, Texas')
    assert w['place'] == 'Paris' and w['country'] == 'United States' and 'forecast' not in w


def test_get_location_tool_rounds_coordinates(net):
    routes, _ = net
    routes['ipwho.is'] = dict(IPWHOIS, latitude=24.691234, longitude=46.721987)
    r = actions.run('get_location', {})
    assert r['ok'] and (r['lat'], r['lon']) == (24.691, 46.722) and r['city'] == 'Riyadh'


@pytest.mark.parametrize('source, directions, expect', [
    ('windows', False, 'https://www.google.com/maps/search/coffee%20shop/@24.7,46.6,15z'),
    ('ip', False, 'https://www.google.com/maps/search/coffee%20shop/@24.7,46.6,12z'),
    ('windows', True, 'https://www.google.com/maps/dir/?api=1&destination=coffee+shop&origin=24.7,46.6'),
    ('ip', True, 'https://www.google.com/maps/dir/?api=1&destination=coffee+shop'),
    (None, False, 'https://www.google.com/maps/search/?api=1&query=coffee+shop'),
])
def test_open_map_urls(monkeypatch, source, directions, expect):
    from modules import apps
    opened = []
    monkeypatch.setattr(apps, 'open_browser', lambda u: opened.append(u) or {'success': True})
    monkeypatch.setattr(location, 'mode', lambda: 'auto')
    loc = {'success': True, 'lat': 24.7, 'lon': 46.6, 'source': source} if source else {'success': False}
    monkeypatch.setattr(location, 'get', lambda refresh=False: loc)
    assert actions.run('open_map', {'query': 'coffee shop', 'directions': directions})['ok']
    assert opened == [expect]
