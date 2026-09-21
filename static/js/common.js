/* Shared helpers for the setup page and the dashboard. */

/* Assigned to window (rather than a bare const) so the module can also be
   imported directly by the front-end tests. Browsers see no difference:
   other scripts still reference it as a bare `App`. */
window.App = (function () {
  "use strict";

  /* Weather icon names come from the server; map them to glyphs here so the
     Python side stays free of presentation details. */
  const GLYPHS = {
    sun: "☀️",
    moon: "🌙",
    "sun-cloud": "🌤️",
    "moon-cloud": "☁️",
    "cloud-sun": "⛅",
    "cloud-moon": "☁️",
    cloud: "☁️",
    fog: "🌫️",
    drizzle: "🌦️",
    rain: "🌧️",
    "rain-heavy": "🌧️",
    showers: "🌦️",
    sleet: "🌨️",
    snow: "❄️",
    storm: "⛈️"
  };

  function glyph(name) {
    return GLYPHS[name] || GLYPHS.cloud;
  }

  /** Escape text for safe interpolation into HTML. */
  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /* Methods the server treats as read-only, and so does not CSRF-check. */
  const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

  /** The per-session CSRF token the server rendered into the page. */
  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  /** Fetch JSON and raise on transport or application-level failure. */
  async function api(path, options) {
    const settings = Object.assign({ headers: {} }, options || {});
    if (settings.body && typeof settings.body !== "string") {
      settings.body = JSON.stringify(settings.body);
      settings.headers["Content-Type"] = "application/json";
    }

    const method = (settings.method || "GET").toUpperCase();
    if (!SAFE_METHODS.has(method)) {
      settings.headers["X-CSRFToken"] = csrfToken();
    }

    let response;
    try {
      response = await fetch(path, settings);
    } catch (error) {
      throw new Error("Could not reach the app. Is it still running?");
    }

    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      throw new Error("The app sent an unreadable response.");
    }

    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || "Request failed (" + response.status + ").");
    }
    return payload;
  }

  function setStatus(element, message, kind) {
    if (!element) return;
    element.textContent = message || "";
    element.className = "status" + (kind ? " status--" + kind : "");
  }

  function debounce(fn, wait) {
    let timer = null;
    return function () {
      const args = arguments;
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(null, args), wait);
    };
  }

  /** A place's secondary line: region, country, coordinates. */
  function placeMeta(place) {
    const parts = [];
    if (place.admin1) parts.push(place.admin1);
    if (place.country) parts.push(place.country);
    parts.push(
      Number(place.latitude).toFixed(2) + ", " + Number(place.longitude).toFixed(2)
    );
    return parts.join(" · ");
  }

  /** Render search results into a list, calling onPick with the chosen place. */
  function renderPlaces(listElement, places, onPick) {
    listElement.innerHTML = "";
    if (!places.length) return;

    places.forEach((place) => {
      const item = document.createElement("li");
      item.className = "results__item";

      const button = document.createElement("button");
      button.type = "button";
      button.className = "results__button";
      button.innerHTML =
        '<span class="results__name">' + esc(place.name) + "</span>" +
        '<span class="results__meta">' + esc(placeMeta(place)) + "</span>";
      button.addEventListener("click", () => onPick(place));

      item.appendChild(button);
      listElement.appendChild(item);
    });
  }

  /** Wire a search box to the geocoder. */
  function attachPlaceSearch(input, listElement, statusElement, onPick) {
    const run = debounce(async () => {
      const query = input.value.trim();
      if (query.length < 2) {
        listElement.innerHTML = "";
        setStatus(statusElement, "");
        return;
      }
      setStatus(statusElement, "Searching…");
      try {
        const data = await api("/api/geocode?q=" + encodeURIComponent(query));
        renderPlaces(listElement, data.results, onPick);
        setStatus(
          statusElement,
          data.results.length ? "" : "No places matched “" + query + "”."
        );
      } catch (error) {
        listElement.innerHTML = "";
        setStatus(statusElement, error.message, "error");
      }
    }, 300);

    input.addEventListener("input", run);
  }

  /** Ask the browser for coordinates. */
  function browserPosition() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) {
        reject(new Error("This browser does not offer location access."));
        return;
      }
      navigator.geolocation.getCurrentPosition(
        (position) =>
          resolve({
            latitude: position.coords.latitude,
            longitude: position.coords.longitude
          }),
        (error) => {
          const messages = {
            1: "Location access was denied.",
            2: "Your position is unavailable right now.",
            3: "The location request timed out."
          };
          reject(new Error(messages[error.code] || "Could not get your location."));
        },
        { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 }
      );
    });
  }

  /**
   * Determine the user's location, preferring the browser's own geolocation
   * and falling back to an IP lookup when it is denied or unavailable.
   */
  async function detectLocation(statusElement) {
    setStatus(statusElement, "Asking your browser for your location…");
    try {
      const position = await browserPosition();
      setStatus(statusElement, "Looking up the place name…");
      const data = await api(
        "/api/reverse-geocode?lat=" + position.latitude + "&lon=" + position.longitude
      );
      return data.place;
    } catch (geoError) {
      setStatus(
        statusElement,
        geoError.message + " Trying your network location instead…"
      );
      try {
        const data = await api("/api/detect-location");
        return data.place;
      } catch (ipError) {
        throw new Error(geoError.message + " " + ipError.message);
      }
    }
  }

  return {
    api,
    esc,
    glyph,
    setStatus,
    debounce,
    placeMeta,
    renderPlaces,
    attachPlaceSearch,
    detectLocation
  };
})();
