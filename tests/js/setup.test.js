import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const FIXTURE = `
  <meta name="csrf-token" content="tok-123">
  <button id="detect">Detect</button>
  <p id="detect-status"></p>
  <input id="search">
  <ul id="results"></ul>
  <section id="chosen" hidden>
    <span id="chosen-name"></span>
    <span id="chosen-coords"></span>
  </section>
  <input id="view-name" value="Home">
  <button id="confirm">Confirm</button>
  <p id="confirm-status"></p>
`;

const PLACE = {
  name: "Kraków",
  admin1: "Lesser Poland",
  country: "Poland",
  latitude: 50.06143,
  longitude: 19.93658,
  timezone: "Europe/Warsaw"
};

let navigated;

/** Build the page, load common.js, stub App, then run setup.js against it. */
async function loadSetup() {
  vi.resetModules();
  document.body.innerHTML = FIXTURE;
  await import("../../static/js/common.js");
  await import("../../static/js/setup.js");
}

beforeEach(() => {
  navigated = [];
  // jsdom cannot navigate or scroll; both are called by setup.js.
  Element.prototype.scrollIntoView = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      get href() {
        return "http://localhost/";
      },
      set href(value) {
        navigated.push(value);
      },
      reload: vi.fn()
    }
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function el(id) {
  return document.getElementById(id);
}

describe("choosing a location", () => {
  it("shows the chosen place and reveals the panel", async () => {
    await loadSetup();
    window.App.detectLocation = vi.fn(async () => PLACE);

    el("detect").click();
    await vi.waitFor(() => expect(el("chosen").hidden).toBe(false));

    expect(el("chosen-name").textContent).toBe("Kraków");
    expect(el("chosen-coords").textContent).toBe(
      "Lesser Poland · Poland · 50.06, 19.94 · Europe/Warsaw"
    );
    expect(el("detect-status").textContent).toBe("Found you.");
  });

  it("omits the timezone suffix when the place has none", async () => {
    await loadSetup();
    window.App.detectLocation = vi.fn(async () => ({ ...PLACE, timezone: null }));

    el("detect").click();
    await vi.waitFor(() => expect(el("chosen").hidden).toBe(false));

    expect(el("chosen-coords").textContent).toBe(
      "Lesser Poland · Poland · 50.06, 19.94"
    );
  });

  it("falls back to a generic name", async () => {
    await loadSetup();
    window.App.detectLocation = vi.fn(async () => ({ ...PLACE, name: "" }));

    el("detect").click();
    await vi.waitFor(() =>
      expect(el("chosen-name").textContent).toBe("Selected location")
    );
  });

  it("explains a failed detection and re-enables the button", async () => {
    await loadSetup();
    window.App.detectLocation = vi.fn(async () => {
      throw new Error("Location access was denied.");
    });

    el("detect").click();
    await vi.waitFor(() =>
      expect(el("detect-status").textContent).toContain("Location access was denied.")
    );
    expect(el("detect-status").textContent).toContain("search for your town below");
    expect(el("detect").disabled).toBe(false);
  });
});

describe("confirming the view", () => {
  async function chooseAPlace() {
    await loadSetup();
    window.App.detectLocation = vi.fn(async () => PLACE);
    el("detect").click();
    await vi.waitFor(() => expect(el("chosen").hidden).toBe(false));
  }

  it("does nothing until a place has been picked", async () => {
    await loadSetup();
    const api = vi.fn();
    window.App.api = api;
    el("confirm").click();
    expect(api).not.toHaveBeenCalled();
  });

  it("posts the place and the view name, then follows the redirect", async () => {
    await chooseAPlace();
    const api = vi.fn(async () => ({ redirect: "/v/abc" }));
    window.App.api = api;

    el("view-name").value = "Home";
    el("confirm").click();

    await vi.waitFor(() => expect(navigated).toEqual(["/v/abc"]));
    expect(api).toHaveBeenCalledWith("/api/setup", {
      method: "POST",
      body: { location: PLACE, view_name: "Home" }
    });
  });

  it("shows the error and re-enables the button when saving fails", async () => {
    await chooseAPlace();
    window.App.api = vi.fn(async () => {
      throw new Error("That name is taken.");
    });

    el("confirm").click();

    await vi.waitFor(() =>
      expect(el("confirm-status").textContent).toBe("That name is taken.")
    );
    expect(el("confirm").disabled).toBe(false);
    expect(navigated).toEqual([]);
  });
});

describe("search wiring", () => {
  it("picks a place from the search results", async () => {
    vi.useFakeTimers();
    await loadSetup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ results: [PLACE] })
      }))
    );

    el("search").value = "Krakow";
    el("search").dispatchEvent(new Event("input"));
    await vi.advanceTimersByTimeAsync(300);

    el("results").querySelector("button").click();
    expect(el("chosen-name").textContent).toBe("Kraków");
    // Picking a place clears the result list.
    expect(el("results").innerHTML).toBe("");
  });
});
