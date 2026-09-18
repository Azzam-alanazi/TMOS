"""T.M.O.S — Weather Module
Current conditions and a forecast of up to 7 days, free and without an API key,
from Open-Meteo:
  - a named city → its geocoder picks the right "Riyadh" (wttr.in's picks a
    town in South Africa); "Paris, Texas" narrows it down
  - no city     → the user's location (modules/location.py); wttr.in, which
    guesses from the IP address, only when that lookup fails
"""

from datetime import date

import requests

from modules import location

FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'
MAX_DAYS = 7

# WMO weather codes used by Open-Meteo
_WMO = {
    0: 'Clear sky', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast',
    45: 'Fog', 48: 'Freezing fog', 51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle',
    56: 'Freezing drizzle', 57: 'Freezing drizzle', 61: 'Light rain', 63: 'Rain',
    65: 'Heavy rain', 66: 'Freezing rain', 67: 'Freezing rain', 71: 'Light snow',
    73: 'Snow', 75: 'Heavy snow', 77: 'Snow grains', 80: 'Light showers', 81: 'Showers',
    82: 'Violent showers', 85: 'Snow showers', 86: 'Heavy snow showers',
    95: 'Thunderstorm', 96: 'Thunderstorm with hail', 99: 'Thunderstorm with heavy hail',
}


def get_weather(city: str = '', days: int = 1) -> dict:
    """Current weather for a city, or for the user's location when city is empty.
    days > 1 adds a daily 'forecast' list (today first)."""
    city = city.strip()
    try:
        days = max(1, min(int(days or 1), MAX_DAYS))
    except (TypeError, ValueError):
        days = 1
    try:
        if city:
            place = location.geocode(city)
            if not place:
                return {'success': False, 'message': f'Could not find a place called "{city}".'}
        else:
            place = location.get()
            if not place.get('success'):
                if place.get('off'):
                    return {'success': False, 'message': "Location is off in Settings, so I don't know "
                                                         'where you are. Name a city, e.g. `weather in Riyadh`.'}
                return _wttr_here()
        return _open_meteo(place, days)
    except requests.exceptions.ConnectionError:
        return {'success': False, 'message': 'No internet connection.'}
    except requests.exceptions.Timeout:
        return {'success': False, 'message': 'The weather service timed out.'}
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
        return {'success': False, 'message': f'Could not get the weather{" for " + city if city else ""}.'}


def _open_meteo(place: dict, days: int) -> dict:
    data = requests.get(FORECAST_URL, params={
        'latitude': place['lat'], 'longitude': place['lon'],
        'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,'
                   'wind_speed_10m,weather_code',
        'daily': 'weather_code,temperature_2m_max,temperature_2m_min,'
                 'precipitation_probability_max,sunrise,sunset',
        'timezone': 'auto', 'forecast_days': days,
    }, timeout=10).json()
    cur, daily = data['current'], data.get('daily') or {}
    forecast = [_day(daily, i) for i in range(len(daily.get('time') or []))]
    today = forecast[0] if forecast else {}
    w = {
        'success':  True,
        'place':    ', '.join(p for p in (place.get('area'), place.get('city')) if p)
                    or location.label(place),
        'country':  place.get('country', ''),
        'temp_c':   round(cur['temperature_2m']),
        'feels_c':  round(cur['apparent_temperature']),
        'desc':     _WMO.get(cur['weather_code'], 'Unknown conditions'),
        'humidity': round(cur['relative_humidity_2m']),
        'wind_kmh': round(cur['wind_speed_10m']),
        'high_c':   today.get('high_c'),
        'low_c':    today.get('low_c'),
        'rain_chance': today.get('rain_chance'),
        'sunrise':  today.get('sunrise'),
        'sunset':   today.get('sunset'),
    }
    if days > 1:
        w['forecast'] = forecast
    return w


def _day(daily: dict, i: int) -> dict:
    def val(key):
        vals = daily.get(key) or []
        return vals[i] if i < len(vals) else None

    def hhmm(iso):
        return iso[11:16] if isinstance(iso, str) and len(iso) >= 16 else None

    d = date.fromisoformat(daily['time'][i])
    hi, lo, rain = val('temperature_2m_max'), val('temperature_2m_min'), val('precipitation_probability_max')
    return {
        'date': d.isoformat(),
        'day': 'Today' if i == 0 else 'Tomorrow' if i == 1 else f'{d:%A}',
        'desc': _WMO.get(val('weather_code'), 'Unknown conditions'),
        'high_c': round(hi) if hi is not None else None,
        'low_c': round(lo) if lo is not None else None,
        'rain_chance': round(rain) if rain is not None else None,
        'sunrise': hhmm(val('sunrise')),
        'sunset': hhmm(val('sunset')),
    }


def _wttr_here() -> dict:
    resp = requests.get('https://wttr.in/', params={'format': 'j1'},
                        headers={'User-Agent': 'curl/8.0', 'Accept-Language': 'en'}, timeout=10)
    if resp.status_code != 200:
        return {'success': False, 'message': f'Weather service error (HTTP {resp.status_code}).'}
    data = resp.json()
    cur   = data['current_condition'][0]
    today = (data.get('weather') or [{}])[0]
    area  = (data.get('nearest_area') or [{}])[0]
    return {
        'success':  True,
        'place':    (area.get('areaName') or [{}])[0].get('value', 'your area'),
        'country':  (area.get('country') or [{}])[0].get('value', ''),
        'temp_c':   int(cur['temp_C']),
        'feels_c':  int(cur['FeelsLikeC']),
        'desc':     cur['weatherDesc'][0]['value'].strip(),
        'humidity': int(cur['humidity']),
        'wind_kmh': int(cur['windspeedKmph']),
        'high_c':   int(today['maxtempC']) if today.get('maxtempC') else None,
        'low_c':    int(today['mintempC']) if today.get('mintempC') else None,
    }


def describe(w: dict, day: int | None = None) -> str:
    """Markdown summary of a get_weather() result. day=1 describes tomorrow only;
    a result with a forecast lists every day."""
    if not w.get('success'):
        return w.get('message', 'Weather unavailable.')
    where = w['place'] + (f", {w['country']}" if w.get('country') else '')
    forecast = w.get('forecast') or []
    if day is not None and day < len(forecast):
        f = forecast[day]
        return f"**{where}, {f['day'].lower()}**: {_day_text(f)}."
    text = (f"**{where}**: {w['temp_c']}°C, {w['desc'].lower()} "
            f"(feels like {w['feels_c']}°C).")
    if w.get('high_c') is not None:
        text += f" High {w['high_c']}° / low {w['low_c']}°."
    if w.get('rain_chance'):
        text += f" {w['rain_chance']}% chance of rain."
    text += f" Humidity {w['humidity']}%, wind {w['wind_kmh']} km/h."
    if len(forecast) > 1:
        text += '\n' + '\n'.join(f"• **{f['day']}**: {_day_text(f)}" for f in forecast[1:])
    return text


def _day_text(f: dict) -> str:
    text = f"{f['desc'].lower()}, {f['high_c']}° / {f['low_c']}°"
    if f.get('rain_chance'):
        text += f", {f['rain_chance']}% chance of rain"
    return text
