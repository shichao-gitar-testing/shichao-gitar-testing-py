/* Dashboard: render location cards, manage views, locations and units. */

(function () {
  "use strict";

  const cards = document.getElementById("cards");
  const viewId = cards.dataset.viewId;
  const generatedAt = document.getElementById("generated-at");

  /* ------------------------------------------------------------ rendering */

  function detail(label, value, suffix) {
    if (value === null || value === undefined || value === "") return "";
    return (
      '<div class="detail"><div class="detail__label">' +
      App.esc(label) +
      '</div><div class="detail__value">' +
      App.esc(value) +
      App.esc(suffix || "") +
      "</div></div>"
    );
  }

  function renderCurrent(location) {
    const now = location.weather;
    if (!now) return "";
    const units = location.units || {};
    const feels =
      now.apparent_temperature !== null && now.apparent_temperature !== undefined
        ? "Feels like " + now.apparent_temperature + units.temperature
        : "";

    return (
      '<div class="now">' +
      '<span class="now__icon" aria-hidden="true">' +
      App.glyph(now.condition.icon) +
      "</span>" +
      '<div class="now__body">' +
      '<div class="now__temp">' +
      App.esc(now.temperature) +
      App.esc(units.temperature) +
      "</div>" +
      '<div class="now__condition">' +
      App.esc(now.condition.label) +
      "</div>" +
      '<div class="now__feels">' +
      App.esc(feels) +
      "</div>" +
      "</div></div>"
    );
  }

  function renderDetails(location) {
    const now = location.weather;
    const today = location.today;
    if (!now) return "";
    const units = location.units || {};

    const wind =
      now.wind_speed !== null && now.wind_speed !== undefined
        ? now.wind_speed + " " + units.wind_speed +
          (now.wind_compass ? " " + now.wind_compass : "")
        : null;

    let rain = null;
    if (today && today.precipitation_probability !== null &&
        today.precipitation_probability !== undefined) {
      rain = today.precipitation_probability + "%";
    }

    const parts =
      detail("Wind", wind) +
      detail("Humidity", now.humidity, "%") +
      detail("Cloud", now.cloud_cover, "%") +
      detail("Pressure", now.pressure, " hPa") +
      (today ? detail("UV index", today.uv_index_max) : "") +
      detail("Rain chance", rain) +
      (today ? detail("High / low",
        today.temp_max !== null && today.temp_min !== null
          ? today.temp_max + "° / " + today.temp_min + "°"
          : null) : "");

    return parts ? '<div class="details">' + parts + "</div>" : "";
  }

  function renderSun(location) {
    const today = location.today;
    if (!today) return "";
    const items = [];
    if (today.sunrise) {
      items.push(
        '<span class="sun__item"><span class="sun__glyph">🌅</span>' +
        '<span class="sun__time">' + App.esc(today.sunrise) + "</span>" +
        '<span class="sun__label">sunrise</span></span>'
      );
    }
    if (today.sunset) {
      items.push(
        '<span class="sun__item"><span class="sun__glyph">🌇</span>' +
        '<span class="sun__time">' + App.esc(today.sunset) + "</span>" +
        '<span class="sun__label">sunset</span></span>'
      );
    }
    if (today.day_length) {
      items.push(
        '<span class="sun__item"><span class="sun__label">daylight</span>' +
        '<span class="sun__time">' + App.esc(today.day_length) + "</span></span>"
      );
    }
    return items.length ? '<div class="sun">' + items.join("") + "</div>" : "";
  }

  function renderStrip(location) {
    const today = location.today;
    if (!today || !today.forecast || !today.forecast.length) return "";
    const days = today.forecast
      .map(
        (day) =>
          '<div class="strip__day">' +
          App.esc(day.label) +
          '<span class="strip__icon" aria-hidden="true">' +
          App.glyph(day.condition.icon) +
          "</span>" +
          '<span class="strip__temps">' +
          App.esc(day.temp_max) +
          '°<span class="strip__min"> / ' +
          App.esc(day.temp_min) +
          "°</span></span></div>"
      )
      .join("");
    return '<div class="strip">' + days + "</div>";
  }

  function eclipseTime(label, value, highlight) {
    if (!value) return "";
    return (
      '<div><div class="eclipse__timelabel">' +
      App.esc(label) +
      '</div><div class="eclipse__time' +
      (highlight ? " eclipse__central" : "") +
      '">' +
      App.esc(value) +
      "</div></div>"
    );
  }

  function countdown(eclipse) {
    const days = eclipse.days_away;
    if (days <= 0) return "today";
    if (days === 1) return "tomorrow";
    if (days < 60) return "in " + days + " days";
    if (days < 365) return "in " + Math.round(days / 30) + " months";
    return "in " + eclipse.years_away + " years";
  }

  function renderEclipse(location) {
    if (location.errors && location.errors.eclipse) {
      return '<p class="card__error">' + App.esc(location.errors.eclipse) + "</p>";
    }
    const eclipse = location.eclipse;
    if (!eclipse) {
      return (
        '<div class="eclipse"><div class="eclipse__kind">Next solar eclipse</div>' +
        '<p class="eclipse__note">No solar eclipse is visible from here in the ' +
        "next 25 years, which is unusual — please report this.</p></div>"
      );
    }

    let headline;
    if (eclipse.is_central && eclipse.central_duration) {
      headline =
        '<span class="eclipse__big">' +
        (eclipse.type === "total" ? "Totality" : "Annularity") +
        " for " +
        App.esc(eclipse.central_duration) +
        "</span>";
    } else {
      headline =
        'Up to <span class="eclipse__big">' +
        App.esc(eclipse.obscuration_percent) +
        "%</span> of the Sun covered";
    }
    headline +=
      ' <span class="eclipse__dim">· magnitude ' + App.esc(eclipse.magnitude) + "</span>";

    const times =
      eclipseTime("Starts", eclipse.times.first_contact) +
      (eclipse.is_central && eclipse.times.central_start
        ? eclipseTime(
            eclipse.type === "total" ? "Totality" : "Annularity",
            eclipse.times.central_start,
            true
          )
        : "") +
      eclipseTime("Maximum", eclipse.times.maximum) +
      (eclipse.is_central && eclipse.times.central_end
        ? eclipseTime("Ends central", eclipse.times.central_end, true)
        : "") +
      eclipseTime("Ends", eclipse.times.last_contact);

    const sun = eclipse.sun_events;
    const sunBits = [];
    if (sun.sunrise) sunBits.push("Sunrise " + sun.sunrise);
    if (sun.sunset) sunBits.push("Sunset " + sun.sunset);
    if (sun.polar_day) sunBits.push("Sun up all day");
    if (sun.polar_night) sunBits.push("Polar night");
    sunBits.push(
      "Sun " +
        Math.round(eclipse.sun_altitude) +
        "° above the horizon" +
        (eclipse.sun_compass ? " to the " + eclipse.sun_compass : "")
    );

    const lowSun =
      eclipse.sun_altitude < 10
        ? '<p class="eclipse__warn">The Sun will be low — you will need a clear ' +
          "view towards the " +
          App.esc(eclipse.sun_compass || "horizon") +
          ".</p>"
        : "";

    return (
      '<div class="eclipse eclipse--' + App.esc(eclipse.type) + '">' +
      '<div class="eclipse__head">' +
      '<span class="eclipse__kind">' + App.esc(eclipse.type_label) + "</span>" +
      '<span class="eclipse__countdown">' + App.esc(countdown(eclipse)) + "</span>" +
      "</div>" +
      '<div class="eclipse__date">' + App.esc(eclipse.date_label) + "</div>" +
      '<div class="eclipse__headline">' + headline + "</div>" +
      '<div class="eclipse__times">' + times + "</div>" +
      '<div class="eclipse__sun">' + App.esc(sunBits.join(" · ")) + "</div>" +
      lowSun +
      '<p class="eclipse__note">Local times, computed on this machine and ' +
      "accurate to roughly a minute. Never look at the Sun without proper " +
      "eclipse filters.</p>" +
      "</div>"
    );
  }

  function renderCard(location) {
    const article = document.createElement("article");
    article.className = "card";
    article.dataset.locationId = location.id;

    const place = [location.admin1, location.country]
      .filter(Boolean)
      .join(", ");

    let html =
      '<header class="card__head"><div>' +
      '<h2 class="card__title">' + App.esc(location.name) + "</h2>" +
      (place ? '<p class="card__place">' + App.esc(place) + "</p>" : "") +
      "</div>" +
      '<button class="card__remove" type="button" title="Remove from this view" ' +
      'aria-label="Remove ' + App.esc(location.name) + '">&times;</button>' +
      "</header>";

    if (location.errors && location.errors.weather) {
      html += '<p class="card__error">' + App.esc(location.errors.weather) + "</p>";
    } else {
      html +=
        renderCurrent(location) +
        renderDetails(location) +
        renderSun(location) +
        renderStrip(location);
    }

    html += renderEclipse(location);
    article.innerHTML = html;

    article
      .querySelector(".card__remove")
      .addEventListener("click", () => removeLocation(location));

    return article;
  }

  /* --------------------------------------------------------------- loading */

  async function load() {
    if (Number(cards.dataset.locationCount) === 0) return;
    try {
      const data = await App.api("/api/views/" + viewId + "/weather");
      cards.innerHTML = "";
      if (!data.locations.length) {
        cards.innerHTML =
          '<p class="empty">This view has no locations yet.</p>';
      } else {
        data.locations.forEach((location) => cards.appendChild(renderCard(location)));
      }
      generatedAt.textContent =
        "Updated " + new Date(data.generated_at).toLocaleTimeString();
    } catch (error) {
      cards.innerHTML = '<p class="empty">' + App.esc(error.message) + "</p>";
    }
  }

  document.getElementById("refresh").addEventListener("click", () => {
    cards.querySelectorAll(".card").forEach((card) => card.classList.add("card--loading"));
    generatedAt.textContent = "Refreshing…";
    load();
  });

  /* ------------------------------------------------------------- dialogs */

  document.querySelectorAll("[data-open]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      const dialog = document.getElementById(trigger.dataset.open);
      if (!dialog) return;
      // A trigger inside another dialog should close that one first.
      const parent = trigger.closest("dialog");
      if (parent && parent !== dialog) parent.close();
      dialog.showModal();
    });
  });

  /* -------------------------------------------------- adding a location */

  const addSearch = document.getElementById("add-search");
  const addResults = document.getElementById("add-results");
  const addStatus = document.getElementById("add-status");

  async function addLocation(place) {
    App.setStatus(addStatus, "Adding " + place.name + "…");
    try {
      await App.api("/api/views/" + viewId + "/locations", {
        method: "POST",
        body: { location: place }
      });
      App.setStatus(addStatus, place.name + " added.", "ok");
      addResults.innerHTML = "";
      addSearch.value = "";
      window.location.reload();
    } catch (error) {
      App.setStatus(addStatus, error.message, "error");
    }
  }

  App.attachPlaceSearch(addSearch, addResults, addStatus, addLocation);

  document.getElementById("add-use-current").addEventListener("click", async (event) => {
    event.target.disabled = true;
    try {
      const place = await App.detectLocation(addStatus);
      await addLocation(place);
    } catch (error) {
      App.setStatus(addStatus, error.message, "error");
    } finally {
      event.target.disabled = false;
    }
  });

  async function removeLocation(location) {
    if (!window.confirm("Remove " + location.name + " from this view?")) return;
    try {
      await App.api("/api/views/" + viewId + "/locations/" + location.id, {
        method: "DELETE"
      });
      window.location.reload();
    } catch (error) {
      window.alert(error.message);
    }
  }

  /* ---------------------------------------------------- managing views */

  const viewsStatus = document.getElementById("views-status");

  document.querySelectorAll("[data-remove-location]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = button.dataset.removeLocation;
      try {
        await App.api("/api/views/" + viewId + "/locations/" + id, { method: "DELETE" });
        window.location.reload();
      } catch (error) {
        App.setStatus(viewsStatus, error.message, "error");
      }
    });
  });

  const renameSave = document.getElementById("rename-save");
  if (renameSave) {
    renameSave.addEventListener("click", async () => {
      const name = document.getElementById("rename-input").value;
      try {
        await App.api("/api/views/" + viewId, {
          method: "PATCH",
          body: { name: name }
        });
        window.location.reload();
      } catch (error) {
        App.setStatus(viewsStatus, error.message, "error");
      }
    });
  }

  const makeDefault = document.getElementById("make-default");
  if (makeDefault) {
    makeDefault.addEventListener("click", async () => {
      try {
        await App.api("/api/views/" + viewId, {
          method: "PATCH",
          body: { make_default: true }
        });
        window.location.reload();
      } catch (error) {
        App.setStatus(viewsStatus, error.message, "error");
      }
    });
  }

  const deleteView = document.getElementById("delete-view");
  if (deleteView) {
    deleteView.addEventListener("click", async () => {
      if (!window.confirm("Delete this view? The locations in it will be forgotten.")) {
        return;
      }
      try {
        const data = await App.api("/api/views/" + viewId, { method: "DELETE" });
        window.location.href = data.redirect;
      } catch (error) {
        App.setStatus(viewsStatus, error.message, "error");
      }
    });
  }

  /* -------------------------------------------------- creating a view */

  const newViewStatus = document.getElementById("new-view-status");

  document.getElementById("new-view-create").addEventListener("click", async () => {
    const name = document.getElementById("new-view-name").value;
    const copy = document.getElementById("new-view-copy").checked;
    const makeItDefault = document.getElementById("new-view-default").checked;

    App.setStatus(newViewStatus, "Creating…");
    try {
      const data = await App.api("/api/views", {
        method: "POST",
        body: {
          name: name,
          copy_locations_from: copy ? viewId : null,
          make_default: makeItDefault
        }
      });
      window.location.href = data.redirect;
    } catch (error) {
      App.setStatus(newViewStatus, error.message, "error");
    }
  });

  /* ------------------------------------------------------------- units */

  const settingsStatus = document.getElementById("settings-status");

  document.getElementById("settings-save").addEventListener("click", async () => {
    App.setStatus(settingsStatus, "Saving…");
    try {
      await App.api("/api/preferences", {
        method: "PATCH",
        body: {
          temperature_unit: document.getElementById("pref-temperature").value,
          wind_speed_unit: document.getElementById("pref-wind").value,
          time_format: document.getElementById("pref-time").value
        }
      });
      window.location.reload();
    } catch (error) {
      App.setStatus(settingsStatus, error.message, "error");
    }
  });

  load();
})();
