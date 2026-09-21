"""Flask application: weather, sun times and the next visible solar eclipse.

Run it with::

    python app.py

On first launch the app asks for your current location and stores it in
``settings.json`` as the default view. After that you can add more locations to
a view, create additional views, and switch between them.
"""

import datetime as dt

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
