"""T.M.O.S — Location Module
Where the user is, so the AI, the weather and "near me" searches know without
asking. Chosen in ⚙ Settings:
  - auto   → Windows location services (Wi-Fi/GPS, street level) when they are
             switched on, otherwise the IP address (city level)
  - manual → a place the user typed in Settings
  - off    → nothing is looked up, and the AI isn't told
Also: geocoding place names, and the local time anywhere ("time in Tokyo").
"""

import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from modules import config

MODES = ('auto', 'manual', 'off')
TTL   = 30 * 60                     # look again after 30 minutes
RETRY_AFTER = 60                    # …or a minute after a failed lookup (offline)

GEOCODE_URL   = 'https://geocoding-api.open-meteo.com/v1/search'
NOMINATIM_URL = 'https://nominatim.openstreetmap.org/reverse'
UA = {'User-Agent': 'TMOS-Desktop-Assistant/1.0'}      # Nominatim asks apps to name themselves

SOURCES = {
    'windows': 'Windows location',
    'ip':      'your IP address (city level)',
    'manual':  'the place set in Settings',
}

_cache: dict | None = None          # last successful lookup, with 'key' and 'ts'
_last_error: dict = {}              # why the last lookup failed, for Settings
_resolve_lock = threading.Lock()    # one lookup at a time; others wait for its result


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def mode() -> str:
    m = config.get('location_mode')
    return m if m in MODES else 'auto'


def get(refresh: bool = False) -> dict:
    """The user's location: {'success', 'city', 'area', 'region', 'country',
    'country_code', 'lat', 'lon', 'timezone', 'source', 'accuracy_m'}.
    Uses the network when the cache is stale, so call it off the UI thread."""
    global _cache
    if mode() == 'off':
        return {'success': False, 'off': True,
                'message': 'Location is turned off in Settings.'}
    key = _key()
    with _resolve_lock:
        c = _cache if _cache and _cache['key'] == key else None
        if c and not refresh and time.time() - c['ts'] < TTL:
            return dict(c)
        loc = _resolve()
        if loc.get('success'):
            _cache = dict(loc, key=key, ts=time.time())
            _last_error.clear()
            return dict(_cache)
        _last_error.update(key=key, message=loc.get('message', ''), ts=time.time())
        return dict(c) if c else loc          # offline? the last known place beats nothing


def cached() -> dict | None:
    """The last known location without waiting. A missing or stale one is looked
    up in the background, so the next caller gets it."""
    if mode() == 'off':
        return None
    have = _cache if _cache and _cache['key'] == _key() else None
    if not have or time.time() - have['ts'] >= TTL:
        _refresh_in_background()
    return dict(have) if have else None


def label(loc: dict, detailed: bool = True) -> str:
    """'Al Wuroud, Riyadh, Riyadh Region, Saudi Arabia'."""
    parts = [loc.get('area') if detailed else '', loc.get('city'),
             loc.get('region') if detailed else '', loc.get('country')]
    out: list[str] = []
    for p in parts:
        p = (p or '').strip()
        if p and not any(p.lower() in o.lower() or o.lower() in p.lower() for o in out):
            out.append(p)
    if out:
        return ', '.join(out)
    if loc.get('lat') is not None:
        return f"{loc['lat']:.3f}, {loc['lon']:.3f}"
    return 'an unknown place'


def describe(loc: dict) -> str:
    """Markdown answer to "where am I?"."""
    if not loc.get('success'):
        return loc.get('message', 'Location unavailable.')
    text = f"📍 You're in **{label(loc)}**, according to {SOURCES.get(loc['source'], loc['source'])}"
    if loc.get('accuracy_m'):
        text += f" (within about {_distance(loc['accuracy_m'])})"
    text += '.'
    if loc['source'] == 'ip':
        text += ' For street-level accuracy, turn on Windows location in ⚙ **Settings › Location**.'
    return text


def prompt_line() -> str:
    """One sentence for the AI's system prompt ('' when unknown or off)."""
    loc = cached()
    if not loc:
        return ''
    how = 'precise' if loc['source'] == 'windows' else (
        'set by the user' if loc['source'] == 'manual' else 'approximate, from their IP address')
    line = f" The user's location: {label(loc)} ({how})."
    if loc.get('timezone'):
        line += f" Time zone: {loc['timezone']}."
    return line + (" Use it for anything local (weather, times, places, units, currency, "
                   "'near me') unless they name another place.")


def status() -> dict:
    """For the Settings dialog: what we know and how to make it better."""
    m = mode()
    loc = cached()
    out = {'mode': m, 'text': '', 'hint': ''}
    if m == 'off':
        out['text'] = 'Off: T.M.O.S does not look up or share your location.'
        return out
    if loc:
        out['text'] = f"Now: {label(loc)} · from {SOURCES.get(loc['source'], loc['source'])}"
        if loc.get('accuracy_m'):
            out['text'] += f", within about {_distance(loc['accuracy_m'])}"
    elif _last_error.get('key') == _key():
        out['text'] = _last_error['message']
    else:
        out['text'] = 'Finding your location…'
    if m == 'auto' and (not loc or loc['source'] == 'ip'):
        allowed, why = windows_location_allowed()
        if not allowed:
            out['hint'] = f'{why}, so your IP address is used (city level). Turn it on for street-level accuracy.'
    return out


def geocode(place: str) -> dict | None:
    """'Riyadh' or 'Paris, Texas' → a location dict (source 'search'), or None."""
    name, _, qualifier = place.partition(',')
    name = name.strip()
    if not name:
        return None
    data = requests.get(GEOCODE_URL, params={'name': name, 'count': 10, 'language': 'en'},
                        timeout=10).json()
    results = data.get('results') or []
    q = qualifier.strip().lower()
    if q:
        results = [r for r in results if q in ' '.join(
            (r.get('country', ''), r.get('admin1', ''), r.get('country_code', ''))).lower()] or results
    if not results:
        return None
    r = results[0]
    return {'success': True, 'city': r.get('name', name), 'area': '', 'region': r.get('admin1', ''),
            'country': r.get('country', ''), 'country_code': r.get('country_code', ''),
            'lat': r['latitude'], 'lon': r['longitude'], 'timezone': r.get('timezone', ''),
            'source': 'search'}


def local_time(place: str = '') -> dict:
    """Current time in a place (or here), and how far ahead/behind it is."""
    now_here = datetime.now().astimezone()
    place = place.strip()
    if not place:
        return {'success': True, 'place': 'here', 'time': f'{now_here:%H:%M}',
                'date': f'{now_here:%A %d %B %Y}'}
    try:
        loc = geocode(place)
    except (requests.RequestException, ValueError, KeyError):
        return {'success': False, 'message': 'Could not look that place up (offline?).'}
    if not loc or not loc.get('timezone'):
        return {'success': False, 'message': f'Could not find a place called "{place}".'}
    try:
        there = datetime.now(timezone.utc).astimezone(ZoneInfo(loc['timezone']))
    except (ZoneInfoNotFoundError, ValueError):
        return {'success': False, 'message': f"Don't know the time zone {loc['timezone']}. "
                                             "Run `pip install tzdata`."}
    diff_h = (there.utcoffset() - now_here.utcoffset()).total_seconds() / 3600
    return {'success': True, 'place': label(loc, detailed=False), 'timezone': loc['timezone'],
            'time': f'{there:%H:%M}', 'date': f'{there:%A %d %B %Y}',
            'difference': _hours_apart(diff_h)}


def windows_location_allowed() -> tuple[bool, str]:
    """Windows' own privacy switches, read from the registry: instant, never prompts."""
    try:
        import winreg
    except ImportError:
        return False, 'Windows location is not available'
    base = r'Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\location'
    checks = (
        (winreg.HKEY_LOCAL_MACHINE, base, 'Windows location services are off'),
        (winreg.HKEY_CURRENT_USER, base, 'Windows location is off for your account'),
        (winreg.HKEY_CURRENT_USER, base + r'\NonPackaged',
         '"Let desktop apps access your location" is off in Windows'),
    )
    for root, path, why in checks:
        try:
            with winreg.OpenKey(root, path) as k:
                if winreg.QueryValueEx(k, 'Value')[0] == 'Deny':
                    return False, why
        except OSError:
            pass
    return True, ''


# ══════════════════════════════════════════════════════════════════════════════
#  LOOKUPS
# ══════════════════════════════════════════════════════════════════════════════

def _key() -> tuple:
    return mode(), (config.get('location_place') or '').strip().lower()


def _refresh_in_background() -> None:
    failed_recently = (_last_error.get('key') == _key()
                       and time.time() - _last_error.get('ts', 0) < RETRY_AFTER)
    if not failed_recently and not _resolve_lock.locked():
        threading.Thread(target=get, daemon=True, name='tmos-location').start()


def _resolve() -> dict:
    try:
        if mode() == 'manual':
            place = (config.get('location_place') or '').strip()
            if not place:
                return {'success': False, 'message': 'No place set. Type one in ⚙ Settings › Location.'}
            loc = geocode(place)
            if not loc:
                return {'success': False, 'message': f'Could not find a place called "{place}".'}
            return dict(loc, source='manual')
        return _from_windows() or _from_ip()
    except requests.exceptions.ConnectionError:
        return {'success': False, 'message': 'No internet connection.'}
    except requests.exceptions.Timeout:
        return {'success': False, 'message': 'The location service timed out.'}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return {'success': False, 'message': 'Could not find your location.'}


# Windows.Devices.Geolocation through PowerShell: no extra Python packages needed.
_WINDOWS_PS = r'''
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Devices.Geolocation.Geolocator, Windows.Devices.Geolocation, ContentType = WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
try {
  $g = New-Object Windows.Devices.Geolocation.Geolocator
  $task = $asTask.MakeGenericMethod([Windows.Devices.Geolocation.Geoposition]).Invoke($null, @($g.GetGeopositionAsync()))
  if (-not $task.Wait(10000)) { throw 'timed out' }
  $c = $task.Result.Coordinate
  @{ lat = $c.Point.Position.Latitude; lon = $c.Point.Position.Longitude; accuracy = $c.Accuracy } | ConvertTo-Json -Compress
} catch {
  $e = $_.Exception; while ($e.InnerException) { $e = $e.InnerException }
  @{ error = $e.Message } | ConvertTo-Json -Compress
}
'''


def _from_windows() -> dict | None:
    """Street-level position from Windows location services, or None when they're off."""
    if not windows_location_allowed()[0]:
        return None
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', _WINDOWS_PS],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        ).stdout.decode('utf-8', errors='replace').strip()
        pos = json.loads(out.splitlines()[-1]) if out else {}
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None
    if 'lat' not in pos:
        if pos.get('error'):
            print(f"[Location] Windows location unavailable: {pos['error'].strip()[:120]}")
        return None
    lat, lon = float(pos['lat']), float(pos['lon'])
    place = _reverse(lat, lon)
    return dict(place, success=True, lat=round(lat, 5), lon=round(lon, 5), source='windows',
                accuracy_m=round(float(pos.get('accuracy') or 0)) or None)


def _reverse(lat: float, lon: float) -> dict:
    """Coordinates → neighbourhood, city and country (OpenStreetMap Nominatim)."""
    try:
        a = requests.get(NOMINATIM_URL, params={
            'lat': lat, 'lon': lon, 'format': 'jsonv2', 'zoom': 16, 'accept-language': 'en',
        }, headers=UA, timeout=8).json().get('address', {})
    except (requests.RequestException, ValueError):
        return {'city': '', 'area': '', 'region': '', 'country': '', 'country_code': ''}
    return {
        'area':    a.get('neighbourhood') or a.get('suburb') or a.get('quarter') or '',
        'city':    (a.get('city') or a.get('town') or a.get('village')
                    or a.get('municipality') or a.get('county') or ''),
        'region':  a.get('state') or a.get('province') or a.get('region') or '',
        'country': a.get('country', ''),
        'country_code': (a.get('country_code') or '').upper(),
    }


def _from_ip() -> dict:
    """City-level location from the public IP address. Tries three free services."""
    for fn in (_ipwhois, _ipinfo, _geojs):
        try:
            loc = fn()
        except (requests.RequestException, ValueError, KeyError, TypeError):
            continue
        if loc and loc.get('lat') is not None:
            return dict(loc, success=True, source='ip', area='')
    return {'success': False, 'message': 'Could not find your location (offline?).'}


def _ipwhois() -> dict | None:
    d = requests.get('https://ipwho.is/', headers=UA, timeout=6).json()
    if not d.get('success', True):
        return None
    tz = d.get('timezone')
    return {'city': d.get('city', ''), 'region': d.get('region', ''), 'country': d.get('country', ''),
            'country_code': d.get('country_code', ''),
            'lat': float(d['latitude']), 'lon': float(d['longitude']),
            'timezone': tz.get('id', '') if isinstance(tz, dict) else (tz or '')}


def _ipinfo() -> dict | None:
    d = requests.get('https://ipinfo.io/json', headers=UA, timeout=6).json()
    if not d.get('loc'):
        return None
    lat, lon = (float(x) for x in d['loc'].split(','))
    return {'city': d.get('city', ''), 'region': d.get('region', ''), 'country': d.get('country', ''),
            'country_code': d.get('country', ''), 'lat': lat, 'lon': lon,
            'timezone': d.get('timezone', '')}


def _geojs() -> dict | None:
    d = requests.get('https://get.geojs.io/v1/ip/geo.json', headers=UA, timeout=6).json()
    if not d.get('latitude'):
        return None
    return {'city': d.get('city', ''), 'region': d.get('region', ''), 'country': d.get('country', ''),
            'country_code': d.get('country_code', ''),
            'lat': float(d['latitude']), 'lon': float(d['longitude']),
            'timezone': d.get('timezone', '')}


# ── Formatting helpers ───────────────────────────────────────────────────────

def _distance(metres: float) -> str:
    return f'{round(metres)} m' if metres < 1000 else f'{metres / 1000:.1f} km'


def _hours_apart(h: float) -> str:
    if abs(h) < 0.01:
        return 'same time as you'
    n = abs(h)
    num = f'{n:g}' if n != int(n) else str(int(n))
    return f"{num} hour{'s' if n != 1 else ''} {'ahead of' if h > 0 else 'behind'} you"
