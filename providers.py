"""Clients for the external services the app depends on.

All of these are free and need no API key:

* Open-Meteo forecast   -- current conditions, today's sunrise/sunset
* Open-Meteo geocoding  -- searching for a place by name
* BigDataCloud          -- reverse geocoding (coordinates to a place name)
* ipapi.co              -- coarse location from IP, used only as a fallback

Every call has a timeout and a small in-process cache. Failures are reported
back to the caller as ``ProviderError`` rather than raised into a 500, so one
unreachable location cannot take down the whole dashboard.
"""

import threading
import time

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
REVERSE_GEOCODE_URL = "https://api-bdc.net/data/reverse-geocode-client"
IP_LOOKUP_URL = "https://ipapi.co/json/"

REQUEST_TIMEOUT = 12
USER_AGENT = "eclipse-weather-python (https://github.com/, self-hosted)"

WEATHER_CACHE_SECONDS = 600
GEOCODE_CACHE_SECONDS = 86400


class ProviderError(Exception):
    """An upstream service failed or returned something unusable."""


class _TimedCache(object):
    """A tiny thread-safe TTL cache."""

    def __init__(self, ttl_seconds):
        self._ttl = ttl_seconds
        self._entries = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            entry = self._entries.get(key)
            if not entry:
                return None
            stored_at, value = entry
            if time.time() - stored_at > self._ttl:
                del self._entries[key]
                return None
            return value

    def set(self, key, value):
        with self._lock:
            self._entries[key] = (time.time(), value)

    def clear(self):
        with self._lock:
            self._entries.clear()


_weather_cache = _TimedCache(WEATHER_CACHE_SECONDS)
_geocode_cache = _TimedCache(GEOCODE_CACHE_SECONDS)


def clear_caches():
    _weather_cache.clear()
    _geocode_cache.clear()


def _get_json(url, params=None):
    try:
        response = requests.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
    except requests.exceptions.Timeout:
        raise ProviderError("The weather service did not respond in time.")
    except requests.exceptions.RequestException:
        raise ProviderError("Could not reach the weather service.")

    if response.status_code != 200:
        raise ProviderError(
            "The weather service returned an error (HTTP %d)." % response.status_code
        )

    try:
        return response.json()
    except ValueError:
        raise ProviderError("The weather service sent a malformed response.")


# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------

_CURRENT_FIELDS = (
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "is_day",
    "precipitation",
    "weather_code",
    "cloud_cover",
    "pressure_msl",
    "surface_pressure",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
)

_DAILY_FIELDS = (
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "sunrise",
    "sunset",
    "daylight_duration",
    "uv_index_max",
    "precipitation_sum",
    "precipitation_probability_max",
)


def fetch_weather(
    latitude,
    longitude,
    temperature_unit="celsius",
    wind_speed_unit="kmh",
    forecast_days=4,
):
    """Current conditions plus a short daily forecast for one location."""
    cache_key = (
        round(float(latitude), 4),
        round(float(longitude), 4),
        temperature_unit,
        wind_speed_unit,
        forecast_days,
    )
    cached = _weather_cache.get(cache_key)
    if cached is not None:
        return cached

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join(_CURRENT_FIELDS),
        "daily": ",".join(_DAILY_FIELDS),
        "timezone": "auto",
        "forecast_days": forecast_days,
        "temperature_unit": temperature_unit,
        "wind_speed_unit": wind_speed_unit,
    }
    payload = _get_json(FORECAST_URL, params)

    if "current" not in payload:
        raise ProviderError("The weather service did not return current conditions.")

    _weather_cache.set(cache_key, payload)
    return payload


# --------------------------------------------------------------------------
# Geocoding
# --------------------------------------------------------------------------


def search_places(query, limit=8, language="en"):
    """Look up candidate places by name."""
    query = (query or "").strip()
    if len(query) < 2:
        return []

    cache_key = ("search", query.lower(), limit, language)
    cached = _geocode_cache.get(cache_key)
    if cached is not None:
        return cached

    payload = _get_json(
        GEOCODE_URL,
        {"name": query, "count": limit, "language": language, "format": "json"},
    )

    results = []
    for item in payload.get("results") or []:
        if item.get("latitude") is None or item.get("longitude") is None:
            continue
        results.append(
            {
                "name": item.get("name"),
                "country": item.get("country"),
                "country_code": item.get("country_code"),
                "admin1": item.get("admin1"),
                "latitude": item["latitude"],
                "longitude": item["longitude"],
                "timezone": item.get("timezone"),
                "elevation": item.get("elevation"),
                "population": item.get("population"),
            }
        )

    _geocode_cache.set(cache_key, results)
    return results


def reverse_geocode(latitude, longitude):
    """Best-effort place name for a coordinate pair.

    Never raises: if every provider fails we fall back to formatted
    coordinates, because a nameless location is still perfectly usable.
    """
    cache_key = ("reverse", round(float(latitude), 3), round(float(longitude), 3))
    cached = _geocode_cache.get(cache_key)
    if cached is not None:
        return cached

    result = _reverse_via_bigdatacloud(latitude, longitude)
    if result is None:
        result = _reverse_via_open_meteo(latitude, longitude)
    if result is None:
        result = {
            "name": "%.3f, %.3f" % (float(latitude), float(longitude)),
            "country": None,
            "admin1": None,
        }

    result["latitude"] = round(float(latitude), 5)
    result["longitude"] = round(float(longitude), 5)
    _geocode_cache.set(cache_key, result)
    return result


def _reverse_via_bigdatacloud(latitude, longitude):
    try:
        payload = _get_json(
            REVERSE_GEOCODE_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "localityLanguage": "en",
            },
        )
    except ProviderError:
        return None

    name = (
        payload.get("city")
        or payload.get("locality")
        or payload.get("principalSubdivision")
    )
    if not name:
        return None
    return {
        "name": name,
        "country": payload.get("countryName"),
        "country_code": payload.get("countryCode"),
        "admin1": payload.get("principalSubdivision"),
    }


def _reverse_via_open_meteo(latitude, longitude):
    """Fallback: search Open-Meteo for the nearest named place.

    The geocoding API has no reverse endpoint, but the forecast API does
    return a timezone, and that is enough to build a usable label.
    """
    try:
        payload = _get_json(
            FORECAST_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "timezone": "auto",
                "forecast_days": 1,
            },
        )
    except ProviderError:
        return None

    timezone = payload.get("timezone")
    if not timezone:
        return None
    # "Europe/Warsaw" -> "Warsaw"
    label = timezone.rsplit("/", 1)[-1].replace("_", " ")
    return {"name": label, "country": None, "admin1": None, "timezone": timezone}


def locate_by_ip():
    """Coarse location from the caller's public IP address.

    Only used when the browser's geolocation is unavailable or declined. Note
    that when the app runs on a server this reports the *server's* location.
    """
    try:
        payload = _get_json(IP_LOOKUP_URL)
    except ProviderError:
        raise ProviderError("Could not determine your location from your network.")

    if payload.get("error") or payload.get("latitude") is None:
        raise ProviderError("Could not determine your location from your network.")

    return {
        "name": payload.get("city") or payload.get("region") or "Detected location",
        "country": payload.get("country_name"),
        "country_code": payload.get("country_code"),
        "admin1": payload.get("region"),
        "latitude": float(payload["latitude"]),
        "longitude": float(payload["longitude"]),
        "timezone": payload.get("timezone"),
        "source": "ip",
    }
