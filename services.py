"""Assembles the data a view needs: weather, sun times and eclipse details.

This is the layer between the Flask routes and the two sources of truth --
``providers`` for live weather and ``astro`` for anything the calendar cannot
supply. Locations are fetched concurrently, and a failure for one location is
reported inside that location's payload rather than failing the whole view.
"""

import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor

import astro
import providers

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

MAX_PARALLEL_LOCATIONS = 6

# Eclipse geometry is deterministic, so results only need recomputing when the
# date rolls over. Keyed by rounded coordinates and today's date.
_eclipse_cache = {}
_eclipse_cache_lock = threading.Lock()


# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------

WEATHER_CONDITIONS = {
    0: ("Clear sky", "sun", "moon"),
    1: ("Mainly clear", "sun-cloud", "moon-cloud"),
    2: ("Partly cloudy", "cloud-sun", "cloud-moon"),
    3: ("Overcast", "cloud", "cloud"),
    45: ("Fog", "fog", "fog"),
    48: ("Freezing fog", "fog", "fog"),
    51: ("Light drizzle", "drizzle", "drizzle"),
    53: ("Drizzle", "drizzle", "drizzle"),
    55: ("Heavy drizzle", "drizzle", "drizzle"),
    56: ("Freezing drizzle", "sleet", "sleet"),
    57: ("Freezing drizzle", "sleet", "sleet"),
    61: ("Light rain", "rain", "rain"),
    63: ("Rain", "rain", "rain"),
    65: ("Heavy rain", "rain-heavy", "rain-heavy"),
    66: ("Freezing rain", "sleet", "sleet"),
    67: ("Freezing rain", "sleet", "sleet"),
    71: ("Light snow", "snow", "snow"),
    73: ("Snow", "snow", "snow"),
    75: ("Heavy snow", "snow", "snow"),
    77: ("Snow grains", "snow", "snow"),
    80: ("Light showers", "showers", "showers"),
    81: ("Showers", "showers", "showers"),
    82: ("Violent showers", "rain-heavy", "rain-heavy"),
    85: ("Snow showers", "snow", "snow"),
    86: ("Heavy snow showers", "snow", "snow"),
    95: ("Thunderstorm", "storm", "storm"),
    96: ("Thunderstorm with hail", "storm", "storm"),
    99: ("Thunderstorm with hail", "storm", "storm"),
}

ECLIPSE_TYPE_LABELS = {
    "total": "Total solar eclipse",
    "annular": "Annular solar eclipse",
    "partial": "Partial solar eclipse",
}

_COMPASS_POINTS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)

UNIT_LABELS = {
    "celsius": "°C",
    "fahrenheit": "°F",
    "kmh": "km/h",
    "mph": "mph",
    "ms": "m/s",
    "kn": "kn",
}


def describe_weather_code(code, is_day=True):
    label, day_icon, night_icon = WEATHER_CONDITIONS.get(
        code, ("Unknown conditions", "cloud", "cloud")
    )
    return {"label": label, "icon": day_icon if is_day else night_icon}


def compass_direction(degrees):
    if degrees is None:
        return None
    index = int((float(degrees) % 360) / 22.5 + 0.5) % 16
    return _COMPASS_POINTS[index]


def _resolve_timezone(tz_name):
    if tz_name and ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return dt.timezone.utc


def format_clock(moment, time_format="24h", with_seconds=False):
    """Format a datetime as a wall clock reading."""
    if moment is None:
        return None
    if time_format == "12h":
        pattern = "%I:%M:%S %p" if with_seconds else "%I:%M %p"
        return moment.strftime(pattern).lstrip("0")
    pattern = "%H:%M:%S" if with_seconds else "%H:%M"
    return moment.strftime(pattern)


def format_duration(seconds):
    """Human-readable duration, e.g. '2h 14m' or '3m 41s'."""
    if seconds is None:
        return None
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    if minutes:
        return "%dm %02ds" % (minutes, secs)
    return "%ds" % secs


def location_label(location):
    """A one-line description like 'Krakow, Lesser Poland, Poland'."""
    parts = [location.get("name")]
    for key in ("admin1", "country"):
        value = location.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(part for part in parts if part)


# --------------------------------------------------------------------------
# Eclipse lookup
# --------------------------------------------------------------------------


def next_eclipse_for_location(location, today=None, time_format="24h"):
    """Next solar eclipse visible from a location, formatted for display."""
    latitude = float(location["latitude"])
    longitude = float(location["longitude"])
    elevation = float(location.get("elevation") or 0.0)
    tz_name = location.get("timezone")
    tz = _resolve_timezone(tz_name)

    now = dt.datetime.now(dt.timezone.utc)
    local_today = today or now.astimezone(tz).date()

    cache_key = (
        round(latitude, 2),
        round(longitude, 2),
        round(elevation, -1),
        local_today.isoformat(),
    )
    with _eclipse_cache_lock:
        cached = _eclipse_cache.get(cache_key)
    if cached is None:
        cached = astro.next_solar_eclipse(
            latitude, longitude, start=now, elevation_m=elevation
        )
        with _eclipse_cache_lock:
            # Keep the cache small; this is a personal app, not a service.
            if len(_eclipse_cache) > 256:
                _eclipse_cache.clear()
            _eclipse_cache[cache_key] = cached if cached is not None else False

    if not cached:
        return None

    return _format_eclipse(
        cached, latitude, longitude, elevation, tz_name, tz, local_today, time_format
    )


def _format_eclipse(
    circumstances, latitude, longitude, elevation, tz_name, tz, local_today, time_format
):
    def local(jd):
        if jd is None:
            return None
        return astro.jd_to_datetime(jd).astimezone(tz)

    maximum = local(circumstances["maximum_jd"])
    first_contact = local(circumstances.get("first_contact_jd"))
    last_contact = local(circumstances.get("last_contact_jd"))
    central_start = local(circumstances.get("central_start_jd"))
    central_end = local(circumstances.get("central_end_jd"))

    eclipse_date = maximum.date()

    # Sunrise and sunset on the eclipse date, computed rather than fetched:
    # the date is usually years beyond any forecast horizon.
    sun = astro.sun_events(eclipse_date, latitude, longitude, tz_name, elevation)

    partial_duration = None
    if first_contact and last_contact:
        partial_duration = (last_contact - first_contact).total_seconds()

    eclipse_type = circumstances["type"]
    obscuration_percent = circumstances["obscuration"] * 100.0

    return {
        "type": eclipse_type,
        "type_label": ECLIPSE_TYPE_LABELS.get(eclipse_type, "Solar eclipse"),
        "is_central": eclipse_type in ("total", "annular"),
        "date": eclipse_date.isoformat(),
        "date_label": _format_date(eclipse_date),
        "days_away": (eclipse_date - local_today).days,
        "years_away": round((eclipse_date - local_today).days / 365.25, 1),
        "magnitude": round(circumstances["magnitude"], 3),
        "obscuration_percent": (
            round(obscuration_percent, 1)
            if obscuration_percent < 99.95
            else round(obscuration_percent)
        ),
        "sun_altitude": circumstances["maximum_sun_altitude"],
        "sun_azimuth": circumstances["maximum_sun_azimuth"],
        "sun_compass": compass_direction(circumstances["maximum_sun_azimuth"]),
        "times": {
            "first_contact": format_clock(first_contact, time_format),
            "maximum": format_clock(maximum, time_format, with_seconds=True),
            "last_contact": format_clock(last_contact, time_format),
            "central_start": format_clock(central_start, time_format, with_seconds=True),
            "central_end": format_clock(central_end, time_format, with_seconds=True),
        },
        "partial_duration": format_duration(partial_duration),
        "central_duration": format_duration(
            circumstances.get("central_duration_seconds")
        ),
        "sun_events": {
            "sunrise": format_clock(sun["sunrise"], time_format),
            "sunset": format_clock(sun["sunset"], time_format),
            "day_length": format_duration(
                sun["day_length_hours"] * 3600.0
                if sun["day_length_hours"] is not None
                else None
            ),
            "polar_day": sun["polar_day"],
            "polar_night": sun["polar_night"],
        },
        "timezone": tz_name,
    }


def _format_date(date):
    # "%-d" is not portable, so strip the leading zero by hand.
    return "%d %s %d" % (date.day, date.strftime("%B"), date.year)


# --------------------------------------------------------------------------
# Per-location assembly
# --------------------------------------------------------------------------


def build_location_payload(location, preferences):
    """Everything the front end needs for one location card."""
    temperature_unit = preferences.get("temperature_unit", "celsius")
    wind_speed_unit = preferences.get("wind_speed_unit", "kmh")
    time_format = preferences.get("time_format", "24h")

    payload = {
        "id": location.get("id"),
        "name": location.get("name"),
        "label": location_label(location),
        "country": location.get("country"),
        "admin1": location.get("admin1"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "timezone": location.get("timezone"),
        "units": {
            "temperature": UNIT_LABELS.get(temperature_unit, ""),
            "wind_speed": UNIT_LABELS.get(wind_speed_unit, ""),
        },
        "weather": None,
        "today": None,
        "eclipse": None,
        "errors": {},
    }

    try:
        forecast = providers.fetch_weather(
            location["latitude"],
            location["longitude"],
            temperature_unit=temperature_unit,
            wind_speed_unit=wind_speed_unit,
        )
    except providers.ProviderError as error:
        payload["errors"]["weather"] = str(error)
        forecast = None

    tz_name = location.get("timezone")
    if forecast:
        tz_name = forecast.get("timezone") or tz_name
        payload["timezone"] = tz_name
        payload["weather"] = _format_current(forecast, time_format)
        payload["today"] = _format_today(forecast, time_format)
        if location.get("elevation") is None and forecast.get("elevation") is not None:
            location = dict(location, elevation=forecast["elevation"])

    # The eclipse search needs a timezone to report local times in; use the one
    # the forecast just told us if the stored location did not have it.
    eclipse_location = dict(location, timezone=tz_name)
    try:
        payload["eclipse"] = next_eclipse_for_location(
            eclipse_location, time_format=time_format
        )
    except Exception as error:  # pragma: no cover - defensive
        payload["errors"]["eclipse"] = "Could not compute eclipse data: %s" % error

    return payload


def _format_current(forecast, time_format):
    current = forecast.get("current") or {}
    is_day = bool(current.get("is_day", 1))
    code = current.get("weather_code")

    observed_at = None
    if current.get("time"):
        try:
            observed_at = format_clock(
                dt.datetime.fromisoformat(current["time"]), time_format
            )
        except ValueError:
            observed_at = None

    return {
        "temperature": _round_or_none(current.get("temperature_2m")),
        "apparent_temperature": _round_or_none(current.get("apparent_temperature")),
        "humidity": _round_or_none(current.get("relative_humidity_2m")),
        "precipitation": current.get("precipitation"),
        "cloud_cover": _round_or_none(current.get("cloud_cover")),
        "pressure": _round_or_none(
            current.get("pressure_msl") or current.get("surface_pressure")
        ),
        "wind_speed": _round_or_none(current.get("wind_speed_10m"), 1),
        "wind_gusts": _round_or_none(current.get("wind_gusts_10m"), 1),
        "wind_direction": current.get("wind_direction_10m"),
        "wind_compass": compass_direction(current.get("wind_direction_10m")),
        "is_day": is_day,
        "code": code,
        "condition": describe_weather_code(code, is_day),
        "observed_at": observed_at,
    }


def _format_today(forecast, time_format):
    daily = forecast.get("daily") or {}
    if not daily.get("time"):
        return None

    def first(key):
        values = daily.get(key) or []
        return values[0] if values else None

    sunrise = _parse_local(first("sunrise"))
    sunset = _parse_local(first("sunset"))

    days = []
    times = daily.get("time") or []
    for index in range(1, min(len(times), 4)):
        code = (daily.get("weather_code") or [None] * len(times))[index]
        days.append(
            {
                "date": times[index],
                "label": _weekday_label(times[index]),
                "condition": describe_weather_code(code, True),
                "temp_max": _round_or_none(
                    (daily.get("temperature_2m_max") or [None] * len(times))[index]
                ),
                "temp_min": _round_or_none(
                    (daily.get("temperature_2m_min") or [None] * len(times))[index]
                ),
                "precipitation_probability": (
                    daily.get("precipitation_probability_max") or [None] * len(times)
                )[index],
            }
        )

    return {
        "date": first("time"),
        "sunrise": format_clock(sunrise, time_format),
        "sunset": format_clock(sunset, time_format),
        "day_length": format_duration(first("daylight_duration")),
        "temp_max": _round_or_none(first("temperature_2m_max")),
        "temp_min": _round_or_none(first("temperature_2m_min")),
        "uv_index_max": _round_or_none(first("uv_index_max"), 1),
        "precipitation_sum": _round_or_none(first("precipitation_sum"), 1),
        "precipitation_probability": first("precipitation_probability_max"),
        "forecast": days,
    }


def _weekday_label(iso_date):
    try:
        return dt.date.fromisoformat(iso_date).strftime("%a")
    except (ValueError, TypeError):
        return iso_date


def _parse_local(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _round_or_none(value, digits=0):
    if value is None:
        return None
    try:
        rounded = round(float(value), digits)
    except (TypeError, ValueError):
        return None
    return int(rounded) if digits == 0 else rounded


def build_view_payload(view, preferences):
    """Build payloads for every location in a view, concurrently."""
    locations = view.get("locations") or []
    if not locations:
        return []

    if len(locations) == 1:
        return [build_location_payload(locations[0], preferences)]

    workers = min(MAX_PARALLEL_LOCATIONS, len(locations))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(build_location_payload, location, preferences)
            for location in locations
        ]
        results = []
        for location, future in zip(locations, futures):
            try:
                results.append(future.result())
            except Exception as error:  # pragma: no cover - defensive
                results.append(
                    {
                        "id": location.get("id"),
                        "name": location.get("name"),
                        "label": location_label(location),
                        "latitude": location.get("latitude"),
                        "longitude": location.get("longitude"),
                        "weather": None,
                        "today": None,
                        "eclipse": None,
                        "errors": {"weather": "Unexpected error: %s" % error},
                    }
                )
    return results
