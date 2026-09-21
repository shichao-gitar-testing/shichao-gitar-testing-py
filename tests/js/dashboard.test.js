import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const FIXTURE = `
  <meta name="csrf-token" content="tok-123">
  <div id="cards" data-view-id="v1" data-location-count="1"></div>
  <span id="generated-at"></span>
  <button id="refresh">Refresh</button>

  <button data-open="add-dialog">Add</button>
  <button data-open="missing-dialog">Broken</button>
  <dialog id="add-dialog">
    <input id="add-search">
    <ul id="add-results"></ul>
    <p id="add-status"></p>
    <button id="add-use-current">Use current</button>
    <button data-open="views-dialog">Switch to views</button>
  </dialog>

  <dialog id="views-dialog">
    <p id="views-status"></p>
    <button data-remove-location="loc-9">Remove nine</button>
    <input id="rename-input" value="Renamed">
    <button id="rename-save">Rename</button>
    <button id="make-default">Make default</button>
    <button id="delete-view">Delete</button>
    <input id="new-view-name" value="Trip">
    <input id="new-view-copy" type="checkbox">
    <input id="new-view-default" type="checkbox">
    <button id="new-view-create">Create</button>
    <p id="new-view-status"></p>
  </dialog>

  <select id="pref-temperature"><option value="celsius" selected>C</option></select>
  <select id="pref-wind"><option value="kmh" selected>kmh</option></select>
  <select id="pref-time"><option value="24h" selected>24h</option></select>
  <button id="settings-save">Save</button>
  <p id="settings-status"></p>
`;

function location_(overrides = {}) {
  return {
    id: "loc-1",
    name: "Kraków",
    admin1: "Lesser Poland",
    country: "Poland",
    weather: {
      temperature: 24.3,
      apparent_temperature: 24.9,
      condition: { icon: "sun", label: "Clear" },
      humidity: 51,
      cloud_cover: 12,
      pressure: 1017,
      wind_speed: 9.4,
      wind_compass: "SSW"
    },
    units: { temperature: "°C", wind_speed: "km/h" },
    today: {
      sunrise: "05:32",
      sunset: "19:55",
      day_length: "14h 23m",
      uv_index_max: 5.8,
      precipitation_probability: 35,
      temp_max: 26.1,
      temp_min: 14.2,
      forecast: [
        { label: "Tue", condition: { icon: "rain" }, temp_max: 24, temp_min: 13 }
      ]
    },
    eclipse: eclipse_(),
    ...overrides
  };
}

function eclipse_(overrides = {}) {
  return {
    days_away: 30,
    years_away: 0,
    is_central: true,
    central_duration: "2m 14s",
    type: "total",
    type_label: "Total solar eclipse",
    date_label: "12 August 2026",
    obscuration_percent: 100,
    magnitude: 1.02,
    times: {
      first_contact: "18:32",
      central_start: "19:31",
      maximum: "19:32",
      central_end: "19:33",
      last_contact: "20:28"
    },
    sun_events: { sunrise: "05:32", sunset: "20:55" },
    sun_altitude: 25.4,
    sun_compass: "WNW",
    ...overrides
  };
}

let reloaded;
let navigated;

/** Build the page, load common.js, stub App.api, then run dashboard.js. */
async function loadDashboard(api) {
  vi.resetModules();
  document.body.innerHTML = FIXTURE;
  await import("../../static/js/common.js");
  window.App.api = api;
  await import("../../static/js/dashboard.js");
  // load() is kicked off during import; let its promise settle.
  await vi.waitFor(() => expect(document.getElementById("cards").innerHTML).not.toBe(""));
}

/** The common case: one successful weather payload. */
function servingLocations(locations) {
  return vi.fn(async () => ({
    locations,
    generated_at: "2026-08-17T12:00:00Z"
  }));
}

beforeEach(() => {
  reloaded = 0;
  navigated = [];
  // jsdom implements none of these.
  HTMLDialogElement.prototype.showModal = vi.fn(function () {
    this.open = true;
  });
  HTMLDialogElement.prototype.close = vi.fn(function () {
    this.open = false;
  });
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      get href() {
        return "http://localhost/";
      },
      set href(value) {
        navigated.push(value);
      },
      reload: () => {
        reloaded += 1;
      }
    }
  });
  vi.stubGlobal("confirm", vi.fn(() => true));
  vi.stubGlobal("alert", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function cardsHtml() {
  return document.getElementById("cards").innerHTML;
}

describe("rendering a card", () => {
  it("shows the current conditions, details, sun times and forecast strip", async () => {
    await loadDashboard(servingLocations([location_()]));

    expect(cardsHtml()).toContain("Kraków");
    expect(cardsHtml()).toContain("Lesser Poland, Poland");
    expect(cardsHtml()).toContain("24.3°C");
    expect(cardsHtml()).toContain("Feels like 24.9°C");
    expect(cardsHtml()).toContain("9.4 km/h SSW");
    expect(cardsHtml()).toContain("51%");
    expect(cardsHtml()).toContain("1017 hPa");
    expect(cardsHtml()).toContain("35%");
    expect(cardsHtml()).toContain("26.1° / 14.2°");
    expect(cardsHtml()).toContain("05:32");
    expect(cardsHtml()).toContain("14h 23m");
    expect(cardsHtml()).toContain("strip__day");
    expect(document.getElementById("generated-at").textContent).toContain("Updated");
  });

  it("omits the parts the payload does not carry", async () => {
    const bare = location_({
      admin1: null,
      country: null,
      weather: {
        temperature: 10,
        apparent_temperature: null,
        condition: { icon: "cloud", label: "Cloudy" },
        humidity: null,
        cloud_cover: null,
        pressure: null,
        wind_speed: null,
        wind_compass: null
      },
      today: null
    });
    await loadDashboard(servingLocations([bare]));

    expect(cardsHtml()).not.toContain("Feels like");
    expect(cardsHtml()).not.toContain("card__place");
    expect(cardsHtml()).not.toContain("sun__item");
    expect(cardsHtml()).not.toContain("strip__day");
  });

  it("escapes a hostile location name", async () => {
    await loadDashboard(
      servingLocations([location_({ name: '<img src=x onerror="boom">' })])
    );
    expect(document.querySelector("#cards img")).toBeNull();
  });

  it("reports a per-location weather error instead of the readings", async () => {
    await loadDashboard(
      servingLocations([location_({ errors: { weather: "Upstream is down." } })])
    );
    expect(cardsHtml()).toContain("Upstream is down.");
    expect(cardsHtml()).not.toContain("now__temp");
    // The eclipse is computed locally, so it still renders.
    expect(cardsHtml()).toContain("eclipse__kind");
  });

  it("reports a per-location eclipse error", async () => {
    await loadDashboard(
      servingLocations([location_({ errors: { eclipse: "Eclipse maths failed." } })])
    );
    expect(cardsHtml()).toContain("Eclipse maths failed.");
  });
});

describe("rendering the eclipse", () => {
  async function withEclipse(overrides) {
    await loadDashboard(servingLocations([location_({ eclipse: eclipse_(overrides) })]));
    return cardsHtml();
  }

  it("leads with the totality duration for a central eclipse", async () => {
    expect(await withEclipse({})).toContain("Totality for 2m 14s");
  });

  it("says annularity for a central annular eclipse", async () => {
    const html = await withEclipse({ type: "annular", type_label: "Annular eclipse" });
    expect(html).toContain("Annularity for 2m 14s");
  });

  it("leads with the obscuration for a partial eclipse", async () => {
    const html = await withEclipse({
      is_central: false,
      central_duration: null,
      obscuration_percent: 61
    });
    expect(html).toContain("Up to <span class=\"eclipse__big\">61%</span>");
  });

  it("warns when the Sun will be low", async () => {
    const html = await withEclipse({ sun_altitude: 4.2, sun_compass: "WNW" });
    expect(html).toContain("The Sun will be low");
    expect(html).toContain("towards the WNW");
  });

  it("does not warn when the Sun is high", async () => {
    expect(await withEclipse({ sun_altitude: 40 })).not.toContain("The Sun will be low");
  });

  it("mentions polar day and polar night", async () => {
    const html = await withEclipse({
      sun_events: { polar_day: true, polar_night: true }
    });
    expect(html).toContain("Sun up all day");
    expect(html).toContain("Polar night");
  });

  it.each([
    [{ days_away: 0 }, "today"],
    [{ days_away: 1 }, "tomorrow"],
    [{ days_away: 30 }, "in 30 days"],
    [{ days_away: 90 }, "in 3 months"],
    [{ days_away: 800, years_away: 2 }, "in 2 years"]
  ])("counts down as %o -> %s", async (overrides, expected) => {
    expect(await withEclipse(overrides)).toContain(expected);
  });

  it("explains when there is no eclipse at all", async () => {
    await loadDashboard(servingLocations([location_({ eclipse: null })]));
    expect(cardsHtml()).toContain("No solar eclipse is visible from here");
  });
});

describe("loading", () => {
  it("shows an empty-state when the view has no locations", async () => {
    await loadDashboard(servingLocations([]));
    expect(cardsHtml()).toContain("This view has no locations yet.");
  });

  it("shows the error when the request fails", async () => {
    await loadDashboard(
      vi.fn(async () => {
        throw new Error("The app is unreachable.");
      })
    );
    expect(cardsHtml()).toContain("The app is unreachable.");
  });

  it("does not call the API when the view is known to be empty", async () => {
    vi.resetModules();
    document.body.innerHTML = FIXTURE;
    document.getElementById("cards").dataset.locationCount = "0";
    await import("../../static/js/common.js");
    const api = vi.fn();
    window.App.api = api;
    await import("../../static/js/dashboard.js");
    expect(api).not.toHaveBeenCalled();
  });

  it("reloads on refresh", async () => {
    const api = servingLocations([location_()]);
    await loadDashboard(api);
    expect(api).toHaveBeenCalledTimes(1);

    document.getElementById("refresh").click();
    expect(document.getElementById("generated-at").textContent).toBe("Refreshing…");
    await vi.waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  });
});

describe("dialogs", () => {
  it("opens the dialog a trigger names", async () => {
    await loadDashboard(servingLocations([location_()]));
    document.querySelector('[data-open="add-dialog"]').click();
    expect(document.getElementById("add-dialog").showModal).toHaveBeenCalled();
  });

  it("ignores a trigger pointing at a dialog that is not there", async () => {
    await loadDashboard(servingLocations([location_()]));
    expect(() =>
      document.querySelector('[data-open="missing-dialog"]').click()
    ).not.toThrow();
  });

  it("closes the dialog a trigger sits inside before opening the next", async () => {
    await loadDashboard(servingLocations([location_()]));
    const add = document.getElementById("add-dialog");
    document.querySelector('[data-open="views-dialog"]').click();
    expect(add.close).toHaveBeenCalled();
    expect(document.getElementById("views-dialog").showModal).toHaveBeenCalled();
  });
});

describe("adding and removing locations", () => {
  it("adds the detected location and reloads", async () => {
    const api = servingLocations([location_()]);
    await loadDashboard(api);
    window.App.detectLocation = vi.fn(async () => ({ name: "Gdańsk" }));

    document.getElementById("add-use-current").click();

    await vi.waitFor(() => expect(reloaded).toBe(1));
    expect(api).toHaveBeenCalledWith("/api/views/v1/locations", {
      method: "POST",
      body: { location: { name: "Gdańsk" } }
    });
    expect(document.getElementById("add-status").textContent).toBe("Gdańsk added.");
  });

  it("reports a failed detection", async () => {
    await loadDashboard(servingLocations([location_()]));
    window.App.detectLocation = vi.fn(async () => {
      throw new Error("Location access was denied.");
    });

    document.getElementById("add-use-current").click();

    await vi.waitFor(() =>
      expect(document.getElementById("add-status").textContent).toBe(
        "Location access was denied."
      )
    );
    expect(reloaded).toBe(0);
  });

  it("removes a card's location after confirmation", async () => {
    const api = servingLocations([location_()]);
    await loadDashboard(api);

    document.querySelector(".card__remove").click();

    await vi.waitFor(() => expect(reloaded).toBe(1));
    expect(api).toHaveBeenCalledWith("/api/views/v1/locations/loc-1", {
      method: "DELETE"
    });
  });

  it("does nothing when the removal is cancelled", async () => {
    const api = servingLocations([location_()]);
    await loadDashboard(api);
    window.confirm.mockReturnValue(false);

    document.querySelector(".card__remove").click();
    expect(api).toHaveBeenCalledTimes(1); // only the initial load
    expect(reloaded).toBe(0);
  });

  it("alerts when the removal fails", async () => {
    let first = true;
    const api = vi.fn(async () => {
      if (first) {
        first = false;
        return { locations: [location_()], generated_at: "2026-08-17T12:00:00Z" };
      }
      throw new Error("Could not remove it.");
    });
    await loadDashboard(api);

    document.querySelector(".card__remove").click();
    await vi.waitFor(() => expect(window.alert).toHaveBeenCalledWith("Could not remove it."));
  });

  it("removes a location from the manage-views list", async () => {
    const api = servingLocations([location_()]);
    await loadDashboard(api);

    document.querySelector("[data-remove-location]").click();

    await vi.waitFor(() => expect(reloaded).toBe(1));
    expect(api).toHaveBeenCalledWith("/api/views/v1/locations/loc-9", {
      method: "DELETE"
    });
  });
});

describe("managing views", () => {
  it("renames the view", async () => {
    const api = servingLocations([location_()]);
    await loadDashboard(api);

    document.getElementById("rename-save").click();

    await vi.waitFor(() => expect(reloaded).toBe(1));
    expect(api).toHaveBeenCalledWith("/api/views/v1", {
      method: "PATCH",
      body: { name: "Renamed" }
    });
  });

  it("makes the view the default", async () => {
    const api = servingLocations([location_()]);
    await loadDashboard(api);

    document.getElementById("make-default").click();

    await vi.waitFor(() => expect(reloaded).toBe(1));
    expect(api).toHaveBeenCalledWith("/api/views/v1", {
      method: "PATCH",
      body: { make_default: true }
    });
  });

  it("deletes the view and follows the redirect", async () => {
    let first = true;
    const api = vi.fn(async () => {
      if (first) {
        first = false;
        return { locations: [location_()], generated_at: "2026-08-17T12:00:00Z" };
      }
      return { redirect: "/v/other" };
    });
    await loadDashboard(api);

    document.getElementById("delete-view").click();
    await vi.waitFor(() => expect(navigated).toEqual(["/v/other"]));
  });

  it("does not delete the view when the confirmation is declined", async () => {
    const api = servingLocations([location_()]);
    await loadDashboard(api);
    window.confirm.mockReturnValue(false);

    document.getElementById("delete-view").click();
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("reports a failure while managing views", async () => {
    let first = true;
    const api = vi.fn(async () => {
      if (first) {
        first = false;
        return { locations: [location_()], generated_at: "2026-08-17T12:00:00Z" };
      }
      throw new Error("That name is taken.");
    });
    await loadDashboard(api);

    document.getElementById("rename-save").click();
    await vi.waitFor(() =>
      expect(document.getElementById("views-status").textContent).toBe(
        "That name is taken."
      )
    );
  });

  it("creates a new view, copying locations when asked", async () => {
    let first = true;
    const api = vi.fn(async () => {
      if (first) {
        first = false;
        return { locations: [location_()], generated_at: "2026-08-17T12:00:00Z" };
      }
      return { redirect: "/v/new" };
    });
    await loadDashboard(api);

    document.getElementById("new-view-copy").checked = true;
    document.getElementById("new-view-default").checked = true;
    document.getElementById("new-view-create").click();

    await vi.waitFor(() => expect(navigated).toEqual(["/v/new"]));
    expect(api).toHaveBeenCalledWith("/api/views", {
      method: "POST",
      body: { name: "Trip", copy_locations_from: "v1", make_default: true }
    });
  });

  it("passes a null source when locations are not copied", async () => {
    let first = true;
    const api = vi.fn(async () => {
      if (first) {
        first = false;
        return { locations: [location_()], generated_at: "2026-08-17T12:00:00Z" };
      }
      return { redirect: "/v/new" };
    });
    await loadDashboard(api);

    document.getElementById("new-view-create").click();

    await vi.waitFor(() => expect(navigated).toEqual(["/v/new"]));
    expect(api.mock.calls[1][1].body.copy_locations_from).toBeNull();
  });

  it("reports a failure while creating a view", async () => {
    let first = true;
    const api = vi.fn(async () => {
      if (first) {
        first = false;
        return { locations: [location_()], generated_at: "2026-08-17T12:00:00Z" };
      }
      throw new Error("Name is required.");
    });
    await loadDashboard(api);

    document.getElementById("new-view-create").click();
    await vi.waitFor(() =>
      expect(document.getElementById("new-view-status").textContent).toBe(
        "Name is required."
      )
    );
  });
});

describe("preferences", () => {
  it("saves the units and reloads", async () => {
    const api = servingLocations([location_()]);
    await loadDashboard(api);

    document.getElementById("settings-save").click();

    await vi.waitFor(() => expect(reloaded).toBe(1));
    expect(api).toHaveBeenCalledWith("/api/preferences", {
      method: "PATCH",
      body: { temperature_unit: "celsius", wind_speed_unit: "kmh", time_format: "24h" }
    });
  });

  it("reports a failure while saving", async () => {
    let first = true;
    const api = vi.fn(async () => {
      if (first) {
        first = false;
        return { locations: [location_()], generated_at: "2026-08-17T12:00:00Z" };
      }
      throw new Error("Unknown unit.");
    });
    await loadDashboard(api);

    document.getElementById("settings-save").click();
    await vi.waitFor(() =>
      expect(document.getElementById("settings-status").textContent).toBe("Unknown unit.")
    );
  });
});
