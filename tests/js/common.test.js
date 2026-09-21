import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** (Re)load common.js and hand back the fresh App namespace. */
async function loadApp() {
  vi.resetModules();
  document.head.innerHTML = '<meta name="csrf-token" content="tok-123">';
  document.body.innerHTML = "";
  await import("../../static/js/common.js");
  return window.App;
}

/** A stand-in for the Response that fetch resolves to. */
function jsonResponse(payload, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => payload };
}

let App;

beforeEach(async () => {
  App = await loadApp();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("esc", () => {
  it("returns an empty string for null and undefined", () => {
    expect(App.esc(null)).toBe("");
    expect(App.esc(undefined)).toBe("");
  });

  it("escapes every character that could break out of HTML", () => {
    expect(App.esc(`<a href="x">&'`)).toBe(
      "&lt;a href=&quot;x&quot;&gt;&amp;&#39;"
    );
  });

  it("escapes the ampersand first, so entities are not double-escaped oddly", () => {
    expect(App.esc("&lt;")).toBe("&amp;lt;");
  });

  it("stringifies non-strings", () => {
    expect(App.esc(0)).toBe("0");
    expect(App.esc(false)).toBe("false");
  });
});

describe("glyph", () => {
  it("maps a known condition", () => {
    expect(App.glyph("sun")).toBe("☀️");
    expect(App.glyph("storm")).toBe("⛈️");
  });

  it("falls back to the cloud for anything unknown", () => {
    expect(App.glyph("nonsense")).toBe(App.glyph("cloud"));
    expect(App.glyph(undefined)).toBe(App.glyph("cloud"));
  });
});

describe("placeMeta", () => {
  it("joins region, country and coordinates", () => {
    expect(
      App.placeMeta({
        admin1: "Lesser Poland",
        country: "Poland",
        latitude: 50.06143,
        longitude: 19.93658
      })
    ).toBe("Lesser Poland · Poland · 50.06, 19.94");
  });

  it("omits the parts that are missing", () => {
    expect(App.placeMeta({ latitude: 1, longitude: 2 })).toBe("1.00, 2.00");
  });
});

describe("setStatus", () => {
  it("does nothing when there is no element", () => {
    expect(() => App.setStatus(null, "hi")).not.toThrow();
  });

  it("sets the message and a modifier class", () => {
    const el = document.createElement("p");
    App.setStatus(el, "Saving…", "ok");
    expect(el.textContent).toBe("Saving…");
    expect(el.className).toBe("status status--ok");
  });

  it("clears the message and the modifier when given neither", () => {
    const el = document.createElement("p");
    App.setStatus(el, "x", "error");
    App.setStatus(el);
    expect(el.textContent).toBe("");
    expect(el.className).toBe("status");
  });
});

describe("debounce", () => {
  it("runs once, after the wait, with the last arguments", async () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const debounced = App.debounce(fn, 300);
    debounced("a");
    debounced("b");
    expect(fn).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(300);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith("b");
  });
});

describe("api", () => {
  it("does not attach a CSRF token to a read", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await App.api("/api/thing");
    expect(fetchMock.mock.calls[0][1].headers["X-CSRFToken"]).toBeUndefined();
  });

  it("attaches a CSRF token to a write", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await App.api("/api/thing", { method: "post", body: { a: 1 } });
    const sent = fetchMock.mock.calls[0][1];
    expect(sent.headers["X-CSRFToken"]).toBe("tok-123");
    expect(sent.headers["Content-Type"]).toBe("application/json");
    expect(sent.body).toBe('{"a":1}');
  });

  it("leaves a body that is already a string alone", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await App.api("/x", { method: "POST", body: "raw" });
    const sent = fetchMock.mock.calls[0][1];
    expect(sent.body).toBe("raw");
    expect(sent.headers["Content-Type"]).toBeUndefined();
  });

  it("sends an empty token when the page has no CSRF meta tag", async () => {
    document.head.innerHTML = "";
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await App.api("/x", { method: "DELETE" });
    expect(fetchMock.mock.calls[0][1].headers["X-CSRFToken"]).toBe("");
  });

  it("reports an unreachable app", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("network down");
    }));
    await expect(App.api("/x")).rejects.toThrow("Could not reach the app");
  });

  it("reports a body it cannot parse", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("nope");
      }
    })));
    await expect(App.api("/x")).rejects.toThrow("unreadable response");
  });

  it("prefers the error the server supplied", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      jsonResponse({ error: "That view is gone." }, { ok: false, status: 404 })
    ));
    await expect(App.api("/x")).rejects.toThrow("That view is gone.");
  });

  it("falls back to the status code when there is no error message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      jsonResponse({}, { ok: false, status: 500 })
    ));
    await expect(App.api("/x")).rejects.toThrow("Request failed (500).");
  });

  it("treats ok:false in the body as a failure even on a 200", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      jsonResponse({ ok: false, error: "Name is required." })
    ));
    await expect(App.api("/x")).rejects.toThrow("Name is required.");
  });

  it("returns the payload on success", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ ok: true, n: 1 })));
    await expect(App.api("/x")).resolves.toEqual({ ok: true, n: 1 });
  });
});

describe("renderPlaces", () => {
  const place = {
    name: "Kraków",
    admin1: "Lesser Poland",
    country: "Poland",
    latitude: 50.06143,
    longitude: 19.93658
  };

  it("empties the list when there is nothing to show", () => {
    const list = document.createElement("ul");
    list.innerHTML = "<li>stale</li>";
    App.renderPlaces(list, [], () => {});
    expect(list.innerHTML).toBe("");
  });

  it("renders one button per place", () => {
    const list = document.createElement("ul");
    App.renderPlaces(list, [place, { ...place, name: "Kazimierz" }], () => {});
    expect(list.querySelectorAll("button.results__button")).toHaveLength(2);
    expect(list.querySelector(".results__name").textContent).toBe("Kraków");
    expect(list.querySelector(".results__meta").textContent).toContain("50.06, 19.94");
  });

  it("calls onPick with the place that was clicked", () => {
    const list = document.createElement("ul");
    const onPick = vi.fn();
    App.renderPlaces(list, [place], onPick);
    list.querySelector("button").click();
    expect(onPick).toHaveBeenCalledWith(place);
  });

  it("escapes the place name", () => {
    const list = document.createElement("ul");
    App.renderPlaces(list, [{ ...place, name: "<script>x</script>" }], () => {});
    expect(list.querySelector("script")).toBeNull();
    expect(list.querySelector(".results__name").textContent).toBe("<script>x</script>");
  });
});

describe("attachPlaceSearch", () => {
  let input;
  let list;
  let status;
  let onPick;

  beforeEach(() => {
    vi.useFakeTimers();
    input = document.createElement("input");
    list = document.createElement("ul");
    status = document.createElement("p");
    onPick = vi.fn();
    App.attachPlaceSearch(input, list, status, onPick);
  });

  async function type(value) {
    input.value = value;
    input.dispatchEvent(new Event("input"));
    await vi.advanceTimersByTimeAsync(300);
  }

  it("does not search for a query shorter than two characters", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    list.innerHTML = "<li>stale</li>";
    await type("a");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(list.innerHTML).toBe("");
    expect(status.textContent).toBe("");
  });

  it("renders what the geocoder returns", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      jsonResponse({ results: [{ name: "Kraków", latitude: 50, longitude: 19 }] })
    ));
    await type("Krakow");
    expect(list.querySelectorAll("button")).toHaveLength(1);
    expect(status.textContent).toBe("");
  });

  it("says so when nothing matched", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ results: [] })));
    await type("Nowhere");
    expect(status.textContent).toContain("No places matched");
  });

  it("shows the error and clears the list when the lookup fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      jsonResponse({ error: "Geocoder is down." }, { ok: false, status: 502 })
    ));
    list.innerHTML = "<li>stale</li>";
    await type("Krakow");
    expect(list.innerHTML).toBe("");
    expect(status.textContent).toBe("Geocoder is down.");
    expect(status.className).toContain("status--error");
  });

  it("url-encodes the query", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ results: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await type("São Paulo");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/geocode?q=S%C3%A3o%20Paulo");
  });
});

describe("detectLocation", () => {
  let status;

  beforeEach(() => {
    status = document.createElement("p");
  });

  function withGeolocation(value) {
    Object.defineProperty(navigator, "geolocation", {
      value,
      configurable: true,
      writable: true
    });
  }

  it("reverse-geocodes the browser position", async () => {
    withGeolocation({
      getCurrentPosition: (ok) => ok({ coords: { latitude: 50.1, longitude: 19.9 } })
    });
    const fetchMock = vi.fn(async () => jsonResponse({ place: { name: "Kraków" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(App.detectLocation(status)).resolves.toEqual({ name: "Kraków" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/reverse-geocode?lat=50.1&lon=19.9");
  });

  it("falls back to the IP lookup when the browser has no geolocation", async () => {
    withGeolocation(undefined);
    const fetchMock = vi.fn(async () => jsonResponse({ place: { name: "Warsaw" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(App.detectLocation(status)).resolves.toEqual({ name: "Warsaw" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/detect-location");
  });

  it.each([
    [1, "Location access was denied."],
    [2, "Your position is unavailable right now."],
    [3, "The location request timed out."],
    [99, "Could not get your location."]
  ])("explains geolocation error code %i", async (code, message) => {
    withGeolocation({
      getCurrentPosition: (_ok, fail) => fail({ code })
    });
    vi.stubGlobal("fetch", vi.fn(async () =>
      jsonResponse({ error: "IP lookup failed." }, { ok: false, status: 500 })
    ));

    await expect(App.detectLocation(status)).rejects.toThrow(
      message + " IP lookup failed."
    );
  });

  it("falls back to the IP lookup when the reverse geocode fails", async () => {
    withGeolocation({
      getCurrentPosition: (ok) => ok({ coords: { latitude: 1, longitude: 2 } })
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ error: "no" }, { ok: false, status: 500 }))
      .mockResolvedValueOnce(jsonResponse({ place: { name: "Somewhere" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(App.detectLocation(status)).resolves.toEqual({ name: "Somewhere" });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/detect-location");
  });
});
