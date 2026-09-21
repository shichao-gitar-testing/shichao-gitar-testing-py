/* First-run flow: pick a location, name the view, save it as the default. */

(function () {
  "use strict";

  const detectButton = document.getElementById("detect");
  const detectStatus = document.getElementById("detect-status");
  const searchInput = document.getElementById("search");
  const resultsList = document.getElementById("results");

  const chosenPanel = document.getElementById("chosen");
  const chosenName = document.getElementById("chosen-name");
  const chosenCoords = document.getElementById("chosen-coords");
  const viewNameInput = document.getElementById("view-name");
  const confirmButton = document.getElementById("confirm");
  const confirmStatus = document.getElementById("confirm-status");

  let selected = null;

  function select(place) {
    selected = place;
    chosenName.textContent = place.name || "Selected location";
    chosenCoords.textContent =
      App.placeMeta(place) + (place.timezone ? " · " + place.timezone : "");
    chosenPanel.hidden = false;
    resultsList.innerHTML = "";
    App.setStatus(confirmStatus, "");
    chosenPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  detectButton.addEventListener("click", async () => {
    detectButton.disabled = true;
    try {
      const place = await App.detectLocation(detectStatus);
      App.setStatus(detectStatus, "Found you.", "ok");
      select(place);
    } catch (error) {
      App.setStatus(
        detectStatus,
        error.message + " You can search for your town below instead.",
        "error"
      );
    } finally {
      detectButton.disabled = false;
    }
  });

  App.attachPlaceSearch(searchInput, resultsList, detectStatus, select);

  confirmButton.addEventListener("click", async () => {
    if (!selected) return;
    confirmButton.disabled = true;
    App.setStatus(confirmStatus, "Saving…");
    try {
      const data = await App.api("/api/setup", {
        method: "POST",
        body: { location: selected, view_name: viewNameInput.value }
      });
      window.location.href = data.redirect;
    } catch (error) {
      App.setStatus(confirmStatus, error.message, "error");
      confirmButton.disabled = false;
    }
  });
})();
