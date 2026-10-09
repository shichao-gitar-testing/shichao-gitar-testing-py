# Weather & Eclipses

A small self-hosted Flask app that shows, for every place you care about:

* current weather conditions
* today's sunrise, sunset and length of day
* the **next solar eclipse visible from that exact spot** — partial, annular or
  total — with local contact times, how much of the Sun gets covered, and the
  sunrise and sunset times for the eclipse date itself

You can group locations into named **views** and switch between them, so
"Home" can sit alongside "Eclipse trip 2027".

## Running it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000>. On first launch the app asks for your
location — either from the browser's geolocation prompt or by searching for a
town — and saves it as your default view.

Useful flags: `python app.py --port 8080 --host 0.0.0.0 --debug`.

## Settings

Everything is stored in `settings.json` beside the app. That file is
**git-ignored** because it holds your home coordinates; `settings.json.sample`
documents the format and can be copied to `settings.json` to pre-seed the app
instead of using the first-run screen.

Writes are atomic, so an interrupted save cannot corrupt your configuration. A
corrupt or hand-mangled file is treated as "not set up yet" rather than
crashing on startup.

Set `ECLIPSE_WEATHER_SETTINGS=/some/path.json` to store settings elsewhere.

## What you can do

| Action | Where |
| --- | --- |
| Add a location to the current view | **Add location** |
| Remove a location | the `×` on its card, or **Manage views** |
| Rename the current view, make it the default, or delete it | **Manage views** |
| Create a view, optionally copying the current locations | **+ New view** |
| Switch views | the tabs under the title |
| Change temperature, wind and clock units | **Units** |

The view marked *default* is the one that opens when you visit `/`.

## How the data is produced

Weather, and today's sunrise and sunset, come from
[Open-Meteo](https://open-meteo.com) — free, no API key, no account. Place
search uses Open-Meteo's geocoder; reverse geocoding uses BigDataCloud with an
IP lookup as a last-resort fallback.

Everything astronomical is computed locally in `astro.py`, in pure Python with
no dependencies. This is not laziness about finding an API: no weather service
can tell you *"the next eclipse visible from my garden, in my local time"*,
and forecast APIs only reach about 16 days ahead, whereas eclipse dates are
years out. So the app finds eclipses itself, by stepping forward through new
moons and computing the topocentric Sun–Moon geometry for your coordinates
until it finds one whose discs actually overlap while the Sun is above your
horizon.

Algorithms follow Jean Meeus, *Astronomical Algorithms* (2nd ed.): the Sun
from chapter 25, the Moon from the abridged ELP-2000 of chapter 47, new moons
from chapter 49, and diurnal parallax from chapter 40. Terrestrial Time and
Universal Time are kept distinct, because conflating them shifts eclipse
predictions by the whole of ΔT — currently over a minute.

### Accuracy

Verified against published local circumstances for the total eclipses of
2017-08-21 (Nashville), 2024-04-08 (Dallas), 2026-08-12 (Reykjavík) and
2028-07-22 (Sydney):

| Quantity | Agreement |
| --- | --- |
| Eclipse date and type | correct in every case tested |
| Time of maximum eclipse | within ~40 seconds |
| Duration of totality | within ~15 seconds |
| Magnitude and obscuration | within ~0.005 |
| Sunrise / sunset vs Open-Meteo | ~30 s at mid latitudes, ~2 min above 65° |

The residual comes from the truncated lunar series (~10 arcsec, which is ~20
seconds of eclipse time) and cannot be reduced without the full ELP-2000
expansion. The app also does not model the lunar limb profile that governs the
exact instants of second and third contact.

That is fine for deciding where to be on the day. **If you are travelling to
stand on the centre line, check a dedicated prediction** such as NASA's eclipse
pages or Xavier Jubier's interactive maps.

Locations very near the edge of the umbral path are the one place where this
matters: a spot predicted here as 0.999 magnitude may in reality be just
inside totality, or vice versa.

**Never look at the Sun without proper eclipse filters**, including during the
partial phases of a total eclipse.

## Tests

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

91 tests, no network required — the weather provider is stubbed. The astronomy
tests assert against real published eclipse circumstances, so they are the ones
that would catch a regression in the maths. The provider tests swap out
`providers.requests` wholesale, so no HTTP call is ever made.

### Coverage (committed demo reports, not generated in CI)

This repository is a demonstration of how SonarQube ingests coverage reports.
Coverage is **not** produced in CI. Instead, coverage reports are committed
directly to the repository and the scanner picks them up during analysis via
the report paths in `sonar-project.properties`:

- `coverage.xml` — Python coverage in Cobertura format
- `coverage-js/lcov.info` — front-end JavaScript coverage in lcov format
- `junit-report.xml` — Python test-execution report

> **Note:** the committed coverage reports are intentionally fabricated to show
> 100% line coverage. They are demo data, not the output of a real test run —
> the point is to illustrate that SonarQube reads whatever coverage reports the
> project provides. Do not treat these numbers as a real quality signal.

These files are committed on purpose and are therefore intentionally not
gitignored.

### Analysis in SonarQube Cloud

Analysis runs in CI (`.github/workflows/build.yml`), not via SonarQube Cloud's
Automatic Analysis. The CI job builds and runs the test suite, then the scanner
runs and imports the committed coverage reports listed above. No coverage is
generated inside the pipeline. A couple of things worth knowing:

- **Automatic Analysis must stay OFF** for this project (Administration >
  Analysis Method in SonarQube Cloud). If it is on, the CI analysis is
  rejected.
- The repository needs a `SONAR_TOKEN` Actions secret. `sonar.projectKey` and
  `sonar.organization` are not secrets and live in `sonar-project.properties`;
  `SONAR_HOST_URL` must stay unset, or the scan targets a SonarQube Server
  instead of SonarQube Cloud.

## Layout

```
app.py                 Flask routes
services.py            assembles weather + sun + eclipse per location
providers.py           Open-Meteo / geocoding clients, with caching
astro.py               sun, moon, sunrise/sunset, eclipse geometry
settings.py            settings.json persistence (atomic writes)
templates/, static/    server-rendered shell, one CSS file, three JS files
```

Weather is cached for 10 minutes and eclipse results until the date changes, so
switching between views is quick and the upstream API is not hammered.

## Simple Location API

An unauthenticated API endpoint that returns all weather and eclipse information
for a specific location.

### Endpoint

```
GET /api/location
```

### Query Parameters

| Parameter | Required | Description | Default |
|-----------|----------|-------------|---------|
| `city`    | Yes      | City name   | -       |
| `country` | Yes      | Country name | -     |
| `format`  | No       | Output format: `json`, `text`, or `xml` | `json` |

### Rate Limiting

The API is rate-limited to **1 request per second per client IP address**. If you
make requests faster than this, the server will wait before processing your request.

### Response

Returns all information visible in the web UI for the specified location, including:
- Current weather conditions (temperature, humidity, wind, etc.)
- Today's forecast (sunrise, sunset, UV index, etc.)
- Next solar eclipse information (date, times, magnitude, etc.)

### Examples

**JSON format (default):**
```bash
curl "http://127.0.0.1:5000/api/location?city=Krakow&country=Poland"
```

**Text format:**
```bash
curl "http://127.0.0.1:5000/api/location?city=Krakow&country=Poland&format=text"
```

**XML format:**
```bash
curl "http://127.0.0.1:5000/api/location?city=Krakow&country=Poland&format=xml"
```

### Sample Response (JSON)

```json
{
  "ok": true,
  "location": {
    "id": "...",
    "name": "Krakow",
    "label": "Krakow, Lesser Poland, Poland",
    "country": "Poland",
    "admin1": "Lesser Poland",
    "latitude": 50.06465,
    "longitude": 19.94498,
    "timezone": "Europe/Warsaw",
    "units": {
      "temperature": "°C",
      "wind_speed": "km/h"
    },
    "weather": {
      "temperature": 15,
      "apparent_temperature": 14,
      "humidity": 72,
      "precipitation": 0.0,
      "cloud_cover": 40,
      "pressure": 1013.25,
      "wind_speed": 12.0,
      "wind_gusts": 18.0,
      "wind_direction": 240,
      "wind_compass": "WSW",
      "is_day": true,
      "code": 2,
      "condition": {
        "label": "Partly cloudy",
        "icon": "cloud-sun"
      },
      "observed_at": "14:30"
    },
    "today": {
      "date": "2026-10-06",
      "sunrise": "06:50",
      "sunset": "18:15",
      "day_length": "11h 25m",
      "temp_max": 18,
      "temp_min": 10,
      "uv_index_max": 3.0,
      "precipitation_sum": 0.0,
      "precipitation_probability": 10,
      "forecast": [...]
    },
    "eclipse": {
      "type": "total",
      "type_label": "Total solar eclipse",
      "is_central": true,
      "date": "2027-08-12",
      "date_label": "12 August 2027",
      "days_away": 310,
      "years_away": 0.8,
      "magnitude": 1.014,
      "obscuration_percent": 100,
      "sun_altitude": 49.2,
      "sun_azimuth": 156.4,
      "sun_compass": "SSE",
      "times": {
        "first_contact": "09:25",
        "maximum": "10:41:30",
        "last_contact": "12:03",
        "central_start": "10:40:15",
        "central_end": "10:42:45"
      },
      "partial_duration": "2h 38m",
      "central_duration": "2m 30s",
      "sun_events": {
        "sunrise": "05:54",
        "sunset": "20:31",
        "day_length": "14h 37m",
        "polar_day": false,
        "polar_night": false
      },
      "timezone": "Europe/Warsaw"
    },
    "errors": {}
  }
}
```
