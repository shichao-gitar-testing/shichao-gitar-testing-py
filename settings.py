"""Persistence for user settings: locations, views and preferences.

Everything lives in a single ``settings.json`` next to the application. That
file is deliberately git-ignored because it contains the user's home
coordinates; ``settings.json.sample`` documents the shape instead.

Writes are atomic (write to a temporary file, then rename) and guarded by a
lock, so an interrupted save or two concurrent requests cannot leave a
half-written file behind.
"""

import json
import os
import secrets
import tempfile
import threading
import uuid

SETTINGS_FILENAME = "settings.json"
SAMPLE_FILENAME = "settings.json.sample"

SCHEMA_VERSION = 1

TEMPERATURE_UNITS = ("celsius", "fahrenheit")
WIND_SPEED_UNITS = ("kmh", "mph", "ms", "kn")
TIME_FORMATS = ("24h", "12h")

_lock = threading.RLock()


class SettingsError(Exception):
    """Raised when a caller asks for something the settings cannot express."""


def _base_dir():
    return os.path.dirname(os.path.abspath(__file__))


def settings_path():
    """Absolute path of the settings file, overridable for tests."""
    return os.environ.get(
        "ECLIPSE_WEATHER_SETTINGS", os.path.join(_base_dir(), SETTINGS_FILENAME)
    )


def secret_key():
    """Stable key for signing the session cookie that carries the CSRF token.

    Generated on first use and stored alongside the other settings, so tokens
    survive a restart instead of invalidating every open tab.
    """
    override = os.environ.get("ECLIPSE_WEATHER_SECRET_KEY")
    if override:
        return override

    # _lock is reentrant, so the nested load/save below is fine.
    with _lock:
        data = load()
        if not data.get("secret_key"):
            data["secret_key"] = secrets.token_urlsafe(32)
            save(data)
        return data["secret_key"]


def default_settings():
    """A fresh, unconfigured settings document.

    ``secret_key`` stays ``None`` here on purpose: this function runs on every
    ``load()`` via ``_migrate()``, so generating a key would mint a new one on
    each read and silently throw it away. ``secret_key()`` fills it in once.
    """
    return {
        "version": SCHEMA_VERSION,
        "secret_key": None,
        "setup_complete": False,
        "default_view_id": None,
        "preferences": {
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
            "time_format": "24h",
        },
        "views": [],
    }


def _new_id():
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------
# Loading and saving
# --------------------------------------------------------------------------


def load():
    """Read settings from disk, falling back to defaults.

    A missing file means first run. A corrupt file is also treated as first
    run rather than crashing the app -- the user can always re-add locations,
    and refusing to start would be worse.
    """
    path = settings_path()
    with _lock:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (IOError, OSError):
            return default_settings()
        except ValueError:
            return default_settings()

    return _migrate(data)


def save(data):
    """Write settings to disk atomically."""
    path = settings_path()
    with _lock:
        directory = os.path.dirname(path) or "."
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=".settings-",
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise
    return data


def _migrate(data):
    """Fill in anything missing so older or hand-edited files still load."""
    if not isinstance(data, dict):
        return default_settings()

    base = default_settings()
    # "preferences" and "views" are rebuilt below, so they must not be
    # overwritten wholesale here -- a partial dict in the file would otherwise
    # replace the defaults and leave required keys missing.
    nested = ("preferences", "views")
    base.update(
        {k: v for k, v in data.items() if k in base and k not in nested}
    )

    preferences = dict(base["preferences"])
    supplied = data.get("preferences") or {}
    if isinstance(supplied, dict):
        preferences.update(
            {k: v for k, v in supplied.items() if k in preferences}
        )
    if preferences["temperature_unit"] not in TEMPERATURE_UNITS:
        preferences["temperature_unit"] = "celsius"
    if preferences["wind_speed_unit"] not in WIND_SPEED_UNITS:
        preferences["wind_speed_unit"] = "kmh"
    if preferences["time_format"] not in TIME_FORMATS:
        preferences["time_format"] = "24h"
    base["preferences"] = preferences

    views = []
    for raw in data.get("views") or []:
        view = _clean_view(raw)
        if view:
            views.append(view)
    base["views"] = views
    base["version"] = SCHEMA_VERSION

    ids = [view["id"] for view in views]
    if base.get("default_view_id") not in ids:
        base["default_view_id"] = ids[0] if ids else None
    base["setup_complete"] = bool(views) and bool(base["default_view_id"])

    return base


def _clean_view(raw):
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip() or "Untitled view"
    locations = []
    for item in raw.get("locations") or []:
        location = _clean_location(item)
        if location:
            locations.append(location)
    return {
        "id": str(raw.get("id") or _new_id()),
        "name": name[:60],
        "locations": locations,
    }


def _clean_location(raw):
    if not isinstance(raw, dict):
        return None
    try:
        latitude = float(raw["latitude"])
        longitude = float(raw["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None

    location = {
        "id": str(raw.get("id") or _new_id()),
        "name": (str(raw.get("name") or "").strip() or "Unnamed place")[:80],
        "latitude": round(latitude, 5),
        "longitude": round(longitude, 5),
    }
    for optional in ("country", "admin1", "timezone", "source"):
        value = raw.get(optional)
        if value:
            location[optional] = str(value)[:80]
    try:
        if raw.get("elevation") is not None:
            location["elevation"] = float(raw["elevation"])
    except (TypeError, ValueError):
        pass
    return location


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


def get_view(data, view_id):
    for view in data["views"]:
        if view["id"] == view_id:
            return view
    return None


def resolve_view(data, view_id=None):
    """Return the requested view, else the default view, else the first one."""
    if view_id:
        view = get_view(data, view_id)
        if view:
            return view
    if data.get("default_view_id"):
        view = get_view(data, data["default_view_id"])
        if view:
            return view
    return data["views"][0] if data["views"] else None


def create_view(data, name, locations=None):
    """Add a new view. The first view created also becomes the default."""
    name = (name or "").strip()
    if not name:
        raise SettingsError("A view needs a name.")

    view = {
        "id": _new_id(),
        "name": name[:60],
        "locations": [
            location
            for location in (_clean_location(item) for item in (locations or []))
            if location
        ],
    }
    # Give copied locations their own identifiers.
    for location in view["locations"]:
        location["id"] = _new_id()

    data["views"].append(view)
    if not data.get("default_view_id"):
        data["default_view_id"] = view["id"]
    data["setup_complete"] = True
    return view


def rename_view(data, view_id, name):
    view = get_view(data, view_id)
    if not view:
        raise SettingsError("That view no longer exists.")
    name = (name or "").strip()
    if not name:
        raise SettingsError("A view needs a name.")
    view["name"] = name[:60]
    return view


def delete_view(data, view_id):
    """Remove a view. The last remaining view cannot be deleted."""
    view = get_view(data, view_id)
    if not view:
        raise SettingsError("That view no longer exists.")
    if len(data["views"]) <= 1:
        raise SettingsError("You need at least one view. Rename this one instead.")

    data["views"] = [item for item in data["views"] if item["id"] != view_id]
    if data.get("default_view_id") == view_id:
        data["default_view_id"] = data["views"][0]["id"]
    return view


def set_default_view(data, view_id):
    view = get_view(data, view_id)
    if not view:
        raise SettingsError("That view no longer exists.")
    data["default_view_id"] = view_id
    return view


def reorder_views(data, view_ids):
    """Reorder views to match ``view_ids``; unlisted views keep their order."""
    by_id = {view["id"]: view for view in data["views"]}
    ordered = [by_id.pop(view_id) for view_id in view_ids if view_id in by_id]
    ordered.extend(view for view in data["views"] if view["id"] in by_id)
    data["views"] = ordered
    return data["views"]


# --------------------------------------------------------------------------
# Locations within a view
# --------------------------------------------------------------------------


def add_location(data, view_id, location):
    view = get_view(data, view_id)
    if not view:
        raise SettingsError("That view no longer exists.")

    cleaned = _clean_location(location)
    if not cleaned:
        raise SettingsError("That location is missing valid coordinates.")

    for existing in view["locations"]:
        if _same_place(existing, cleaned):
            raise SettingsError(
                "%s is already in this view." % existing["name"]
            )

    cleaned["id"] = _new_id()
    view["locations"].append(cleaned)
    return cleaned


def remove_location(data, view_id, location_id):
    view = get_view(data, view_id)
    if not view:
        raise SettingsError("That view no longer exists.")

    remaining = [item for item in view["locations"] if item["id"] != location_id]
    if len(remaining) == len(view["locations"]):
        raise SettingsError("That location is not in this view.")
    view["locations"] = remaining
    return view


def reorder_locations(data, view_id, location_ids):
    view = get_view(data, view_id)
    if not view:
        raise SettingsError("That view no longer exists.")

    by_id = {item["id"]: item for item in view["locations"]}
    ordered = [by_id.pop(loc_id) for loc_id in location_ids if loc_id in by_id]
    ordered.extend(item for item in view["locations"] if item["id"] in by_id)
    view["locations"] = ordered
    return view


def _same_place(a, b):
    """Treat locations within ~100 m of each other as the same place."""
    return (
        abs(a["latitude"] - b["latitude"]) < 0.001
        and abs(a["longitude"] - b["longitude"]) < 0.001
    )


def update_preferences(data, updates):
    preferences = data["preferences"]
    if "temperature_unit" in updates:
        value = updates["temperature_unit"]
        if value not in TEMPERATURE_UNITS:
            raise SettingsError("Unknown temperature unit.")
        preferences["temperature_unit"] = value
    if "wind_speed_unit" in updates:
        value = updates["wind_speed_unit"]
        if value not in WIND_SPEED_UNITS:
            raise SettingsError("Unknown wind speed unit.")
        preferences["wind_speed_unit"] = value
    if "time_format" in updates:
        value = updates["time_format"]
        if value not in TIME_FORMATS:
            raise SettingsError("Unknown time format.")
        preferences["time_format"] = value
    return preferences
