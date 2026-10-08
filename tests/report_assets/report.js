/* Shared behavior for the offline FF5M UI-regression report pages.
 *
 * Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
 *
 * This file may be distributed under the terms of the GNU GPLv3 license
 */

(function () {
  "use strict";

  var params = new URLSearchParams(location.search);
  var items = Array.prototype.slice.call(document.querySelectorAll("[data-item]"));
  var sections = Array.prototype.slice.call(document.querySelectorAll("[data-section]"));
  var counter = document.querySelector("[data-count]");
  var search = document.querySelector("input[data-search]");
  var state = {};
  var defaults = {};
  var afterFilter = [];
  var afterStart = [];

  function renderRegions(root) {
    Array.prototype.forEach.call(root.querySelectorAll("canvas.issue-crop"), function (canvas) {
      if (canvas.dataset.started) return;
      canvas.dataset.started = "true";
      var status = canvas.parentNode.querySelector(".crop-status");
      var image = new Image();
      image.onload = function () {
        var region = canvas.dataset.region.split(" ").map(Number);
        var x = Math.floor(region[0] * image.naturalWidth);
        var y = Math.floor(region[1] * image.naturalHeight);
        var width = Math.min(image.naturalWidth - x,
          Math.ceil((region[0] + region[2]) * image.naturalWidth) - x);
        var height = Math.min(image.naturalHeight - y,
          Math.ceil((region[1] + region[3]) * image.naturalHeight) - y);
        if (width <= 0 || height <= 0) {
          status.textContent = "Detail unavailable; open the full image.";
          return;
        }
        canvas.width = width * 2;
        canvas.height = height * 2;
        var context = canvas.getContext("2d");
        context.imageSmoothingEnabled = false;
        context.drawImage(image, x, y, width, height, 0, 0, canvas.width, canvas.height);
        canvas.hidden = false;
        status.textContent = "2× detail (scaled to fit on narrow screens)";
      };
      image.onerror = function () {
        status.textContent = "Detail unavailable; open the full image.";
      };
      image.src = canvas.dataset.src;
    });
  }

  renderRegions(document);

  function updateUrl() {
    var query = new URLSearchParams();
    Object.keys(state).forEach(function (name) {
      if (state[name] !== defaults[name]) query.set(name, state[name]);
    });
    if (search && search.value.trim()) query.set("q", search.value.trim());
    var text = query.toString();
    try {
      history.replaceState(
        null, "", location.pathname + (text ? "?" + text : "") + location.hash);
    } catch (error) { /* file:// pages may refuse URL updates */ }
  }

  // Items carry "|value|" token lists in data-<filter>, so values with spaces
  // stay exact and one item can belong to several values (checks, evidence).
  function matches(item, query) {
    for (var name in state) {
      if (state[name] === "all") continue;
      if ((item.dataset[name] || "").indexOf("|" + state[name] + "|") < 0) return false;
    }
    return !query || (item.dataset.search || "").indexOf(query) >= 0;
  }

  function apply() {
    var query = search ? search.value.trim().toLowerCase() : "";
    var visible = 0;
    items.forEach(function (item) {
      item.hidden = !matches(item, query);
      if (!item.hidden) visible += 1;
    });
    sections.forEach(function (section) {
      var shown = section.querySelectorAll("[data-item]:not([hidden])").length;
      var label = section.querySelector("[data-section-count]");
      section.hidden = !shown;
      if (label) label.textContent = shown;
    });
    if (counter) counter.textContent = visible + " of " + items.length;
    var filtered = query !== "" || Object.keys(state).some(function (name) {
      return state[name] !== defaults[name];
    });
    document.body.dataset.filtered = String(filtered);
    updateUrl();
    afterFilter.forEach(function (hook) { hook(); });
  }

  function pressButtons(control) {
    Array.prototype.forEach.call(control.querySelectorAll("button[data-value]"), function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.value === state[control.dataset.filter]));
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-filter]"), function (control) {
    var name = control.dataset.filter;
    defaults[name] = control.dataset.default || "all";
    state[name] = params.get(name) || defaults[name];
    if (control.tagName === "SELECT") {
      control.value = state[name];
      if (control.value !== state[name]) { state[name] = "all"; control.value = "all"; }
      control.addEventListener("change", function () { state[name] = control.value; apply(); });
      return;
    }
    pressButtons(control);
    control.addEventListener("click", function (event) {
      var button = event.target.closest("button[data-value]");
      if (!button) return;
      state[name] = button.dataset.value;
      pressButtons(control);
      apply();
    });
  });

  if (search) {
    search.value = params.get("q") || "";
    search.addEventListener("input", apply);
  }

  var tileSize = document.querySelector("input[data-tile-size]");
  if (tileSize) {
    tileSize.addEventListener("input", function () {
      document.documentElement.style.setProperty("--tile", tileSize.value + "px");
    });
  }

  function visibleItems(selector) {
    return Array.prototype.filter.call(document.querySelectorAll(selector), function (item) {
      return !item.hidden;
    });
  }

  function typing(event) {
    return /^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName);
  }

  function setHash(value) {
    try {
      history.replaceState(null, "", location.pathname + location.search + value);
    } catch (error) { /* see updateUrl */ }
  }

  // Gallery: a modal that steps through the frames the filters leave visible.
  var dialog = document.getElementById("frame-dialog");
  if (dialog) {
    var content = dialog.querySelector(".modal-content");
    var position = dialog.querySelector(".modal-position");
    var previous = dialog.querySelector("[data-step='-1']");
    var next = dialog.querySelector("[data-step='1']");
    var current = null;

    var open = function (number) {
      var template = document.getElementById("detail-" + number);
      if (!template) return;
      current = number;
      content.replaceChildren(template.content.cloneNode(true));
      renderRegions(content);
      var tiles = visibleItems(".shot-tile");
      var index = tiles.findIndex(function (tile) { return tile.dataset.frame === String(number); });
      position.textContent = index < 0 ? "" : (index + 1) + " / " + tiles.length;
      previous.disabled = index <= 0;
      next.disabled = index < 0 || index >= tiles.length - 1;
      if (!dialog.open) dialog.showModal();
      dialog.scrollTop = 0;
      setHash("#frame-" + number);
    };

    var step = function (delta) {
      var tiles = visibleItems(".shot-tile");
      var index = tiles.findIndex(function (tile) { return tile.dataset.frame === String(current); });
      var target = tiles[index + delta];
      if (target) open(target.dataset.frame);
    };

    Array.prototype.forEach.call(document.querySelectorAll(".shot-tile"), function (tile) {
      tile.addEventListener("click", function () { open(tile.dataset.frame); });
    });
    previous.addEventListener("click", function () { step(-1); });
    next.addEventListener("click", function () { step(1); });
    dialog.querySelector(".modal-close").addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener("close", function () { setHash(""); });
    document.addEventListener("keydown", function (event) {
      if (!dialog.open) return;
      if (event.key === "ArrowLeft") step(-1);
      if (event.key === "ArrowRight") step(1);
    });
    var linked = /^#frame-(\d+)$/.exec(location.hash);
    if (linked) afterStart.push(function () { open(linked[1]); });
  }

  // Compare: one designer/printer pair at a time in side-by-side, swipe or
  // difference view.
  var pairs = Array.prototype.slice.call(document.querySelectorAll(".pair"));
  if (pairs.length) {
    var selected = null;
    var entries = Array.prototype.slice.call(document.querySelectorAll(".pair-item"));

    var choose = function (number) {
      selected = number;
      pairs.forEach(function (pair) { pair.hidden = pair.id !== "pair-" + number; });
      entries.forEach(function (entry) {
        entry.setAttribute("aria-pressed", String(entry.dataset.pair === String(number)));
      });
      setHash(number === null ? "" : "#pair-" + number);
    };

    var move = function (delta) {
      var list = visibleItems(".pair-item");
      var index = list.findIndex(function (entry) { return entry.dataset.pair === String(selected); });
      var target = list[Math.max(0, Math.min(list.length - 1, index + delta))];
      if (target) {
        choose(target.dataset.pair);
        target.scrollIntoView({ block: "nearest" });
      }
    };

    entries.forEach(function (entry) {
      entry.addEventListener("click", function () { choose(entry.dataset.pair); });
    });
    afterFilter.push(function () {
      var list = visibleItems(".pair-item");
      var stillVisible = list.some(function (entry) { return entry.dataset.pair === String(selected); });
      if (!stillVisible) choose(list.length ? list[0].dataset.pair : null);
    });

    var setMode = function (mode) {
      document.body.dataset.view = mode;
      Array.prototype.forEach.call(document.querySelectorAll(".viewer"), function (viewer) {
        viewer.dataset.mode = mode;
      });
      Array.prototype.forEach.call(document.querySelectorAll("button[data-view]"), function (button) {
        button.setAttribute("aria-pressed", String(button.dataset.view === mode));
      });
    };
    Array.prototype.forEach.call(document.querySelectorAll("button[data-view]"), function (button) {
      button.addEventListener("click", function () { setMode(button.dataset.view); });
    });
    var cut = document.querySelector("input[data-cut]");
    if (cut) {
      cut.addEventListener("input", function () {
        document.documentElement.style.setProperty("--cut", cut.value + "%");
      });
    }
    document.addEventListener("keydown", function (event) {
      if (typing(event)) return;
      if (event.key === "ArrowDown" || event.key === "ArrowRight") { event.preventDefault(); move(1); }
      if (event.key === "ArrowUp" || event.key === "ArrowLeft") { event.preventDefault(); move(-1); }
    });

    setMode("side");
    var linkedPair = /^#pair-(\d+)$/.exec(location.hash);
    if (linkedPair && document.getElementById("pair-" + linkedPair[1])) {
      afterStart.push(function () { choose(linkedPair[1]); });
    }
  }

  apply();
  afterStart.forEach(function (start) { start(); });
})();
