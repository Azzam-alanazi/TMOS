"""T.M.O.S — Weather Module
Current conditions, free and without an API key:
  - a named city → Open-Meteo (its geocoder picks the right "Riyadh"; wttr.in's
    picks a town in South Africa)
  - no city     → wttr.in, which finds your location from your IP address
"""

from urllib.parse import quote

import requests

GEOCODE_URL  = 'https://geocoding-api.open-meteo.com/v1/search'
FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'

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


def get_weather(city: str = '') -> dict:
    """Current weather for a city, or for your location (by IP) when city is empty."""
    city = city.strip()
    try:
        return _open_meteo(city) if city else _wttr_here()
    except requests.exceptions.ConnectionError:
        return {'success': False, 'message': 'No internet connection.'}
    except requests.exceptions.Timeout:
        return {'success': False, 'message': 'The weather service timed out.'}
    except (KeyError, IndexError, TypeError, ValueError):
        return {'success': False, 'message': f'Could not get the weather{" for " + city if city else ""}.'}


def _open_meteo(city: str) -> dict:
    name = city.split(',')[0].strip()
    geo = requests.get(GEOCODE_URL, params={'name': name, 'count': 1, 'language': 'en'},
                       timeout=10).json()
    if not geo.get('results'):
        return {'success': False, 'message': f'Could not find a place called "{city}".'}
    place = geo['results'][0]
    data = requests.get(FORECAST_URL, params={
        'latitude': place['latitude'], 'longitude': place['longitude'],
        'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,'
                   'wind_speed_10m,weather_code',
        'daily': 'temperature_2m_max,temperature_2m_min',
        'timezone': 'auto', 'forecast_days': 1,
    }, timeout=10).json()
    cur, daily = data['current'], data.get('daily', {})
    return {
        'success':  True,
        'place':    place['name'],
        'country':  place.get('country', ''),
        'temp_c':   round(cur['temperature_2m']),
        'feels_c':  round(cur['apparent_temperature']),
        'desc':     _WMO.get(cur['weather_code'], 'Unknown conditions'),
        'humidity': round(cur['relative_humidity_2m']),
        'wind_kmh': round(cur['wind_speed_10m']),
        'high_c':   round(daily['temperature_2m_max'][0]) if daily.get('temperature_2m_max') else None,
        'low_c':    round(daily['temperature_2m_min'][0]) if daily.get('temperature_2m_min') else None,
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


def describe(w: dict) -> str:
    """Markdown summary of a get_weather() result."""
    if not w.get('success'):
        return w.get('message', 'Weather unavailable.')
    where = w['place'] + (f", {w['country']}" if w.get('country') else '')
    text = (f"**{where}**: {w['temp_c']}°C, {w['desc'].lower()} "
            f"(feels like {w['feels_c']}°C).")
    if w.get('high_c') is not None:
        text += f" High {w['high_c']}° / low {w['low_c']}°."
    text += f" Humidity {w['humidity']}%, wind {w['wind_kmh']} km/h."
    return text
