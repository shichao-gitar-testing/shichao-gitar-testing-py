"""Flask application: weather, sun times and the next visible solar eclipse.

Run it with::

    python app.py

On first launch the app asks for your current location and stores it in
``settings.json`` as the default view. After that you can add more locations to
a view, create additional views, and switch between them.
"""

import datetime as dt
import threading
import time

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_wtf.csrf import CSRFError, CSRFProtect

import providers
import services
import settings as settings_store


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------

_rate_limit_lock = threading.Lock()
_rate_limit_cache = {}


def _get_client_ip():
    """Get the client IP address, accounting for proxies."""
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def _check_rate_limit(max_per_second=1):
    """Rate limit: max requests per second per client."""
    client_ip = _get_client_ip()
    current_time = time.time()

    with _rate_limit_lock:
        last_request_time = _rate_limit_cache.get(client_ip, 0)
        time_since_last = current_time - last_request_time

        if time_since_last < 1.0 / max_per_second:
            sleep_time = (1.0 / max_per_second) - time_since_last
            time.sleep(sleep_time)
            current_time = time.time()

        _rate_limit_cache[client_ip] = current_time

    return True

app = Flask(__name__)
app.secret_key = settings_store.secret_key()
app.config["JSON_SORT_KEYS"] = False
# The dashboard is meant to be left open; expiring the token after the default
# hour would turn an idle tab into a confusing failure on the next edit.
app.config["WTF_CSRF_TIME_LIMIT"] = None
csrf = CSRFProtect(app)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _json_body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _error(message, status=400):
    return jsonify({"ok": False, "error": message}), status


def _view_summary(data):
    """Lightweight description of every view, for the switcher."""
    return [
        {
            "id": view["id"],
            "name": view["name"],
            "location_count": len(view.get("locations") or []),
            "is_default": view["id"] == data.get("default_view_id"),
        }
        for view in data["views"]
    ]


def _view_detail(view, data):
    return {
        "id": view["id"],
        "name": view["name"],
        "is_default": view["id"] == data.get("default_view_id"),
        "locations": view.get("locations") or [],
    }


def _mutate(handler):
    """Load settings, apply ``handler``, save, and report the outcome.

    Keeps every write route down to its actual business logic.
    """
    data = settings_store.load()
    try:
        result = handler(data)
    except settings_store.SettingsError as error:
        return _error(str(error))
    settings_store.save(data)

    payload = {
        "ok": True,
        "views": _view_summary(data),
        "default_view_id": data.get("default_view_id"),
    }
    if isinstance(result, dict):
        payload.update(result)
    return jsonify(payload)


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


@app.route("/")
def index():
    data = settings_store.load()
    if not data["setup_complete"] or not data["views"]:
        return redirect(url_for("setup"))

    view = settings_store.resolve_view(data)
    return redirect(url_for("show_view", view_id=view["id"]))


@app.route("/setup")
def setup():
    data = settings_store.load()
    return render_template(
        "setup.html",
        already_configured=bool(data["views"]),
        year=dt.date.today().year,
    )


@app.route("/view/<view_id>")
def show_view(view_id):
    data = settings_store.load()
    if not data["setup_complete"] or not data["views"]:
        return redirect(url_for("setup"))

    view = settings_store.get_view(data, view_id)
    if not view:
        abort(404)

    return render_template(
        "dashboard.html",
        view=view,
        views=_view_summary(data),
        default_view_id=data.get("default_view_id"),
        preferences=data["preferences"],
        year=dt.date.today().year,
    )


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404,
                           message="That page does not exist."), 404


@app.errorhandler(CSRFError)
def csrf_failed(_reason):
    # Every caller of the write APIs is the page's own JavaScript, so answer in
    # the JSON shape it expects rather than Flask-WTF's HTML error page.
    return _error("Security check failed. Reload the page and try again.", 400)


# --------------------------------------------------------------------------
# Read APIs
# --------------------------------------------------------------------------


@app.route("/api/views")
def api_views():
    data = settings_store.load()
    return jsonify(
        {
            "ok": True,
            "views": _view_summary(data),
            "default_view_id": data.get("default_view_id"),
            "preferences": data["preferences"],
        }
    )


@app.route("/api/views/<view_id>")
def api_view_detail(view_id):
    data = settings_store.load()
    view = settings_store.get_view(data, view_id)
    if not view:
        return _error("That view no longer exists.", 404)
    return jsonify({"ok": True, "view": _view_detail(view, data)})


@app.route("/api/views/<view_id>/weather")
def api_view_weather(view_id):
    """The heavy call: weather plus eclipse data for every location in a view."""
    data = settings_store.load()
    view = settings_store.get_view(data, view_id)
    if not view:
        return _error("That view no longer exists.", 404)

    locations = services.build_view_payload(view, data["preferences"])
    return jsonify(
        {
            "ok": True,
            "view": {"id": view["id"], "name": view["name"]},
            "locations": locations,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(
                timespec="seconds"
            ),
        }
    )


@app.route("/api/geocode")
def api_geocode():
    query = request.args.get("q", "")
    try:
        results = providers.search_places(query)
    except providers.ProviderError as error:
        return _error(str(error), 502)
    return jsonify({"ok": True, "results": results})


@app.route("/api/reverse-geocode")
def api_reverse_geocode():
    try:
        latitude = float(request.args["lat"])
        longitude = float(request.args["lon"])
    except (KeyError, TypeError, ValueError):
        return _error("Provide numeric lat and lon parameters.")

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return _error("Those coordinates are out of range.")

    place = providers.reverse_geocode(latitude, longitude)
    place["source"] = "geolocation"
    return jsonify({"ok": True, "place": place})


@app.route("/api/detect-location")
def api_detect_location():
    """Fallback location detection, used when the browser will not say."""
    try:
        place = providers.locate_by_ip()
    except providers.ProviderError as error:
        return _error(str(error), 502)
    return jsonify({"ok": True, "place": place})


@app.route("/api/location", methods=["GET"])
def api_location():
    """Get all weather and eclipse information for a location by city and country.
    
    Query parameters:
        city: City name (required)
        country: Country name (required)
        format: Output format - json, text, or xml (default: json)
    
    Returns all information visible in the web UI for the specified location.
    """
    _check_rate_limit(max_per_second=1)
    
    city = request.args.get("city", "").strip()
    country = request.args.get("country", "").strip()
    output_format = request.args.get("format", "json").lower()
    
    if not city:
        return _error("Provide a 'city' parameter.")
    if not country:
        return _error("Provide a 'country' parameter.")
    
    if output_format not in ("json", "text", "xml"):
        return _error("Invalid format. Use 'json', 'text', or 'xml'.")
    
    # Search for the location
    query = f"{city}, {country}"
    try:
        results = providers.search_places(query)
    except providers.ProviderError as error:
        return _error(str(error), 502)
    
    if not results:
        return _error(f"Location not found: {city}, {country}", 404)
    
    # Use the first result
    location = results[0]
    preferences = {"temperature_unit": "celsius", "wind_speed_unit": "kmh", "time_format": "24h"}
    
    # Build the payload using the same service as the dashboard
    payload = services.build_location_payload(location, preferences)
    
    if output_format == "text":
        return _format_as_text(payload), 200, {"Content-Type": "text/plain; charset=utf-8"}
    elif output_format == "xml":
        return _format_as_xml(payload), 200, {"Content-Type": "application/xml; charset=utf-8"}
    
    return jsonify({"ok": True, "location": payload})


def _format_as_text(payload):
    """Format location data as plain text."""
    lines = []
    lines.append("=" * 50)
    lines.append(f"Location: {payload.get('name')}, {payload.get('country')}")
    lines.append("=" * 50)
    lines.append(f"Coordinates: {payload.get('latitude')}, {payload.get('longitude')}")
    lines.append(f"Timezone: {payload.get('timezone')}")
    lines.append("")
    
    # Weather
    weather = payload.get("weather")
    if weather:
        lines.append("CURRENT WEATHER")
        lines.append("-" * 30)
        lines.append(f"Temperature: {weather.get('temperature')}{payload.get('units', {}).get('temperature', '')}")
        lines.append(f"Feels like: {weather.get('apparent_temperature')}{payload.get('units', {}).get('temperature', '')}")
        lines.append(f"Condition: {weather.get('condition', {}).get('label', 'N/A')}")
        lines.append(f"Humidity: {weather.get('humidity')}%")
        lines.append(f"Wind: {weather.get('wind_speed')} {payload.get('units', {}).get('wind_speed', '')} {weather.get('wind_compass', '')}")
        lines.append(f"Wind gusts: {weather.get('wind_gusts')} {payload.get('units', {}).get('wind_speed', '')}")
        lines.append(f"Cloud cover: {weather.get('cloud_cover')}%")
        lines.append(f"Pressure: {weather.get('pressure')} hPa")
        lines.append(f"Precipitation: {weather.get('precipitation')} mm")
        lines.append(f"Observed at: {weather.get('observed_at')}")
        lines.append("")
    
    # Today's forecast
    today = payload.get("today")
    if today:
        lines.append("TODAY'S FORECAST")
        lines.append("-" * 30)
        lines.append(f"Sunrise: {today.get('sunrise')}")
        lines.append(f"Sunset: {today.get('sunset')}")
        lines.append(f"Day length: {today.get('day_length')}")
        lines.append(f"UV Index: {today.get('uv_index_max')}")
        lines.append(f"Temp range: {today.get('temp_min')} - {today.get('temp_max')}{payload.get('units', {}).get('temperature', '')}")
        lines.append(f"Precipitation: {today.get('precipitation_sum')} mm")
        lines.append("")
    
    # Eclipse
    eclipse = payload.get("eclipse")
    if eclipse:
        lines.append("NEXT SOLAR ECLIPSE")
        lines.append("-" * 30)
        lines.append(f"Type: {eclipse.get('type_label')}")
        lines.append(f"Date: {eclipse.get('date_label')} ({eclipse.get('date')})")
        lines.append(f"Days away: {eclipse.get('days_away')}")
        lines.append(f"Years away: {eclipse.get('years_away')}")
        lines.append(f"Magnitude: {eclipse.get('magnitude')}")
        lines.append(f"Obscuration: {eclipse.get('obscuration_percent')}%")
        lines.append(f"Sun position: {eclipse.get('sun_compass')} at {eclipse.get('sun_altitude')}°")
        times = eclipse.get("times", {})
        lines.append("Contact times:")
        lines.append(f"  First contact: {times.get('first_contact')}")
        lines.append(f"  Maximum: {times.get('maximum')}")
        lines.append(f"  Last contact: {times.get('last_contact')}")
        if times.get("central_start"):
            lines.append(f"  Central start: {times.get('central_start')}")
            lines.append(f"  Central end: {times.get('central_end')}")
        lines.append(f"Partial duration: {eclipse.get('partial_duration')}")
        if eclipse.get("central_duration"):
            lines.append(f"Central duration: {eclipse.get('central_duration')}")
        lines.append(f"Timezone: {eclipse.get('timezone')}")
        sun_events = eclipse.get("sun_events", {})
        lines.append(f"Sunrise on eclipse day: {sun_events.get('sunrise')}")
        lines.append(f"Sunset on eclipse day: {sun_events.get('sunset')}")
        lines.append(f"Day length on eclipse day: {sun_events.get('day_length')}")
    else:
        lines.append("No upcoming solar eclipse visible from this location.")
    
    return "\n".join(lines)


def _format_as_xml(payload):
    """Format location data as XML."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<location>"]

    # Add all sections conditionally
    sections = [
        _xml_basic_info(payload),
        _xml_weather(payload.get("weather")) if payload.get("weather") else [],
        _xml_today(payload.get("today")) if payload.get("today") else [],
        _xml_eclipse(payload.get("eclipse")) if payload.get("eclipse") else [],
        _xml_errors(payload.get("errors")) if payload.get("errors") else [],
    ]

    for section in sections:
        lines.extend(section)

    lines.append("</location>")
    return "\n".join(lines)


def _xml_basic_info(payload):
    """Generate XML for basic location info."""
    return [
        f"  <name>{_escape_xml(payload.get('name', ''))}</name>",
        f"  <label>{_escape_xml(payload.get('label', ''))}</label>",
        f"  <country>{_escape_xml(payload.get('country', ''))}</country>",
        f"  <admin1>{_escape_xml(payload.get('admin1', '') or '')}</admin1>",
        f"  <latitude>{payload.get('latitude')}</latitude>",
        f"  <longitude>{payload.get('longitude')}</longitude>",
        f"  <timezone>{_escape_xml(payload.get('timezone', ''))}</timezone>",
    ]


def _xml_weather(weather):
    """Generate XML for weather data."""
    cond = weather.get("condition", {})
    return [
        "  <weather>",
        f"    <temperature>{weather.get('temperature')}</temperature>",
        f"    <apparent_temperature>{weather.get('apparent_temperature')}</apparent_temperature>",
        f"    <humidity>{weather.get('humidity')}</humidity>",
        f"    <precipitation>{weather.get('precipitation')}</precipitation>",
        f"    <cloud_cover>{weather.get('cloud_cover')}</cloud_cover>",
        f"    <pressure>{weather.get('pressure')}</pressure>",
        f"    <wind_speed>{weather.get('wind_speed')}</wind_speed>",
        f"    <wind_gusts>{weather.get('wind_gusts')}</wind_gusts>",
        f"    <wind_direction>{weather.get('wind_direction')}</wind_direction>",
        f"    <wind_compass>{_escape_xml(weather.get('wind_compass') or '')}</wind_compass>",
        f"    <is_day>{weather.get('is_day')}</is_day>",
        f"    <weather_code>{weather.get('code')}</weather_code>",
        f"    <condition_label>{_escape_xml(cond.get('label') or '')}</condition_label>",
        f"    <condition_icon>{_escape_xml(cond.get('icon') or '')}</condition_icon>",
        f"    <observed_at>{_escape_xml(weather.get('observed_at') or '')}</observed_at>",
        "  </weather>",
    ]


def _xml_today(today):
    """Generate XML for today's forecast."""
    lines = [
        "  <today>",
        f"    <date>{_escape_xml(today.get('date') or '')}</date>",
        f"    <sunrise>{_escape_xml(today.get('sunrise') or '')}</sunrise>",
        f"    <sunset>{_escape_xml(today.get('sunset') or '')}</sunset>",
        f"    <day_length>{_escape_xml(today.get('day_length') or '')}</day_length>",
        f"    <temp_max>{today.get('temp_max')}</temp_max>",
        f"    <temp_min>{today.get('temp_min')}</temp_min>",
        f"    <uv_index_max>{today.get('uv_index_max')}</uv_index_max>",
        f"    <precipitation_sum>{today.get('precipitation_sum')}</precipitation_sum>",
        f"    <precipitation_probability>{today.get('precipitation_probability')}</precipitation_probability>",
    ]

    lines.extend(_xml_forecast_section(today.get("forecast", [])))
    lines.append("  </today>")
    return lines


def _xml_forecast_section(forecast):
    """Generate XML for forecast section."""
    if not forecast:
        return []
    lines = ["    <forecast>"]
    lines.extend(_xml_forecast_items(forecast))
    lines.append("    </forecast>")
    return lines


def _xml_forecast_items(forecast):
    """Generate XML for forecast items."""
    items = []
    for day in forecast:
        cond = day.get("condition", {})
        items.extend([
            "      <day>",
            f"        <date>{_escape_xml(day.get('date') or '')}</date>",
            f"        <label>{_escape_xml(day.get('label') or '')}</label>",
            f"        <temp_max>{day.get('temp_max')}</temp_max>",
            f"        <temp_min>{day.get('temp_min')}</temp_min>",
            f"        <precipitation_probability>{day.get('precipitation_probability')}</precipitation_probability>",
            f"        <condition_label>{_escape_xml(cond.get('label') or '')}</condition_label>",
            f"        <condition_icon>{_escape_xml(cond.get('icon') or '')}</condition_icon>",
            "      </day>",
        ])
    return items


def _xml_eclipse(eclipse):
    """Generate XML for eclipse data."""
    times = eclipse.get("times", {})
    sun_events = eclipse.get("sun_events", {})

    return [
        "  <eclipse>",
        f"    <type>{_escape_xml(eclipse.get('type') or '')}</type>",
        f"    <type_label>{_escape_xml(eclipse.get('type_label') or '')}</type_label>",
        f"    <is_central>{eclipse.get('is_central')}</is_central>",
        f"    <date>{_escape_xml(eclipse.get('date') or '')}</date>",
        f"    <date_label>{_escape_xml(eclipse.get('date_label') or '')}</date_label>",
        f"    <days_away>{eclipse.get('days_away')}</days_away>",
        f"    <years_away>{eclipse.get('years_away')}</years_away>",
        f"    <magnitude>{eclipse.get('magnitude')}</magnitude>",
        f"    <obscuration_percent>{eclipse.get('obscuration_percent')}</obscuration_percent>",
        f"    <sun_altitude>{eclipse.get('sun_altitude')}</sun_altitude>",
        f"    <sun_azimuth>{eclipse.get('sun_azimuth')}</sun_azimuth>",
        f"    <sun_compass>{_escape_xml(eclipse.get('sun_compass') or '')}</sun_compass>",
    ] + _xml_eclipse_times(times) + [
        f"    <partial_duration>{_escape_xml(eclipse.get('partial_duration') or '')}</partial_duration>",
        f"    <central_duration>{_escape_xml(eclipse.get('central_duration') or '')}</central_duration>",
    ] + _xml_eclipse_sun_events(sun_events) + [
        f"    <timezone>{_escape_xml(eclipse.get('timezone') or '')}</timezone>",
        "  </eclipse>",
    ]


def _xml_eclipse_times(times):
    """Generate XML for eclipse times."""
    return [
        "    <times>",
        f"      <first_contact>{_escape_xml(times.get('first_contact') or '')}</first_contact>",
        f"      <maximum>{_escape_xml(times.get('maximum') or '')}</maximum>",
        f"      <last_contact>{_escape_xml(times.get('last_contact') or '')}</last_contact>",
        f"      <central_start>{_escape_xml(times.get('central_start') or '')}</central_start>",
        f"      <central_end>{_escape_xml(times.get('central_end') or '')}</central_end>",
        "    </times>",
    ]


def _xml_eclipse_sun_events(sun_events):
    """Generate XML for sun events on eclipse day."""
    return [
        "    <sun_events>",
        f"      <sunrise>{_escape_xml(sun_events.get('sunrise') or '')}</sunrise>",
        f"      <sunset>{_escape_xml(sun_events.get('sunset') or '')}</sunset>",
        f"      <day_length>{_escape_xml(sun_events.get('day_length') or '')}</day_length>",
        f"      <polar_day>{sun_events.get('polar_day')}</polar_day>",
        f"      <polar_night>{sun_events.get('polar_night')}</polar_night>",
        "    </sun_events>",
    ]


def _xml_errors(errors):
    """Generate XML for errors."""
    lines = ["  <errors>"]
    for key, value in errors.items():
        lines.append(f'    <error key="{_escape_xml(key)}">{_escape_xml(value)}</error>')
    lines.append("  </errors>")
    return lines


def _escape_xml(text):
    """Escape special XML characters."""
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------


@app.route("/api/setup", methods=["POST"])
def api_setup():
    """Store the first location and create the default view."""
    body = _json_body()
    location = body.get("location")
    if not isinstance(location, dict):
        return _error("Choose a location first.")

    view_name = (body.get("view_name") or "").strip() or "Home"

    data = settings_store.load()
    try:
        view = settings_store.create_view(data, view_name)
        settings_store.add_location(data, view["id"], location)
        settings_store.set_default_view(data, view["id"])
    except settings_store.SettingsError as error:
        return _error(str(error))

    settings_store.save(data)
    return jsonify(
        {"ok": True, "view_id": view["id"], "redirect": url_for("show_view", view_id=view["id"])}
    )


# --------------------------------------------------------------------------
# Write APIs: views
# --------------------------------------------------------------------------


@app.route("/api/views", methods=["POST"])
def api_create_view():
    body = _json_body()
    name = body.get("name")
    copy_from = body.get("copy_locations_from")

    def handler(data):
        locations = None
        if copy_from:
            source = settings_store.get_view(data, copy_from)
            if source:
                locations = [dict(item) for item in source["locations"]]
        view = settings_store.create_view(data, name, locations)
        if body.get("make_default"):
            settings_store.set_default_view(data, view["id"])
        return {
            "view": _view_detail(view, data),
            "redirect": url_for("show_view", view_id=view["id"]),
        }

    return _mutate(handler)


@app.route("/api/views/<view_id>", methods=["PATCH"])
def api_update_view(view_id):
    body = _json_body()

    def handler(data):
        if "name" in body:
            settings_store.rename_view(data, view_id, body["name"])
        if body.get("make_default"):
            settings_store.set_default_view(data, view_id)
        if isinstance(body.get("location_order"), list):
            settings_store.reorder_locations(data, view_id, body["location_order"])
        view = settings_store.get_view(data, view_id)
        if not view:
            raise settings_store.SettingsError("That view no longer exists.")
        return {"view": _view_detail(view, data)}

    return _mutate(handler)


@app.route("/api/views/<view_id>", methods=["DELETE"])
def api_delete_view(view_id):
    def handler(data):
        settings_store.delete_view(data, view_id)
        target = settings_store.resolve_view(data)
        return {
            "redirect": url_for("show_view", view_id=target["id"]) if target else url_for("setup")
        }

    return _mutate(handler)


@app.route("/api/views/order", methods=["POST"])
def api_reorder_views():
    body = _json_body()
    order = body.get("order")
    if not isinstance(order, list):
        return _error("Provide an 'order' list of view ids.")

    def handler(data):
        settings_store.reorder_views(data, order)
        return {}

    return _mutate(handler)


# --------------------------------------------------------------------------
# Write APIs: locations
# --------------------------------------------------------------------------


@app.route("/api/views/<view_id>/locations", methods=["POST"])
def api_add_location(view_id):
    body = _json_body()
    location = body.get("location") if isinstance(body.get("location"), dict) else body

    def handler(data):
        added = settings_store.add_location(data, view_id, location)
        view = settings_store.get_view(data, view_id)
        return {"location": added, "view": _view_detail(view, data)}

    return _mutate(handler)


@app.route("/api/views/<view_id>/locations/<location_id>", methods=["DELETE"])
def api_remove_location(view_id, location_id):
    def handler(data):
        settings_store.remove_location(data, view_id, location_id)
        view = settings_store.get_view(data, view_id)
        return {"view": _view_detail(view, data)}

    return _mutate(handler)


# --------------------------------------------------------------------------
# Write APIs: preferences
# --------------------------------------------------------------------------


@app.route("/api/preferences", methods=["PATCH"])
def api_update_preferences():
    body = _json_body()

    def handler(data):
        preferences = settings_store.update_preferences(data, body)
        # Cached weather is unit-specific, so drop it when units change.
        providers.clear_caches()
        return {"preferences": preferences}

    return _mutate(handler)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    options = parser.parse_args()

    app.run(host=options.host, port=options.port, debug=options.debug)
