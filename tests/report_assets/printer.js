/* Printer-regression behavior: chart cursor, touch-area overlay, recording seek.
 *
 * Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
 *
 * This file may be distributed under the terms of the GNU GPLv3 license
 */

(function () {
  "use strict";

  function clock(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    var h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
  }

  // Charts share one cursor so a temperature spike can be read against the
  // motion, buffer, and MCU charts at the same instant.
  var charts = Array.prototype.slice.call(document.querySelectorAll("svg.chart"));
  charts.forEach(function (svg) {
    svg.series = JSON.parse(svg.dataset.series);
    svg.plot = svg.dataset.plot.split(",").map(Number);
    svg.xmax = Number(svg.dataset.xmax);
    svg.cursor = svg.querySelector(".cursor");
    svg.readout = svg.closest("figure").querySelector(".chart-readout");
  });

  function nearest(points, time) {
    var low = 0, high = points.length - 1;
    while (low < high) {
      var mid = (low + high) >> 1;
      if (points[mid][0] < time) low = mid + 1; else high = mid;
    }
    if (low > 0 && Math.abs(points[low - 1][0] - time) < Math.abs(points[low][0] - time)) low -= 1;
    return points[low];
  }

  function show(time) {
    charts.forEach(function (svg) {
      var left = svg.plot[0], width = svg.plot[2];
      var x = left + (time / svg.xmax) * width;
      svg.cursor.setAttribute("x1", x);
      svg.cursor.setAttribute("x2", x);
      svg.cursor.removeAttribute("hidden");
      var text = clock(time) + "  " + svg.series.map(function (item) {
        var point = item[2].length ? nearest(item[2], time) : null;
        return point ? item[0] + " " + point[1] + " " + svg.dataset.unit : "";
      }).filter(Boolean).join(" · ");
      svg.readout.textContent = text;
    });
  }

  function hide() {
    charts.forEach(function (svg) {
      svg.cursor.setAttribute("hidden", "");
      svg.readout.textContent = "";
    });
  }

  charts.forEach(function (svg) {
    svg.addEventListener("mousemove", function (event) {
      var box = svg.getBoundingClientRect();
      var unit = svg.viewBox.baseVal.width / box.width;
      var time = ((event.clientX - box.left) * unit - svg.plot[0]) / svg.plot[2] * svg.xmax;
      if (time < 0 || time > svg.xmax) hide(); else show(time);
    });
    svg.addEventListener("mouseleave", hide);
  });

  // The screen modal is cloned from a template, so the overlay toggle is
  // delegated from the document.
  document.addEventListener("click", function (event) {
    var button = event.target.closest(".toggle-hitboxes");
    if (!button) return;
    var frame = button.closest(".frame");
    var shown = frame.classList.toggle("show-hitboxes");
    button.textContent = shown ? "Hide touch areas" : "Show touch areas";
  });

  // Screens: collapsible groups of similar screens, or one flat grid.
  function setGroup(group, open) {
    group.classList.toggle("open", open);
    group.classList.toggle("closed", !open);
    group.querySelector(".group-toggle").setAttribute("aria-expanded", String(open));
  }
  Array.prototype.forEach.call(document.querySelectorAll(".screen-group"), function (group) {
    group.querySelector(".group-toggle").addEventListener("click", function () {
      setGroup(group, !group.classList.contains("open"));
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll("button[data-groups]"), function (button) {
    button.addEventListener("click", function () {
      Array.prototype.forEach.call(document.querySelectorAll(".screen-group"), function (group) {
        setGroup(group, button.dataset.groups === "open");
      });
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll("button[data-screens-view]"), function (button) {
    button.addEventListener("click", function () {
      document.body.dataset.screens = button.dataset.screensView;
      Array.prototype.forEach.call(document.querySelectorAll("button[data-screens-view]"), function (other) {
        other.setAttribute("aria-pressed", String(other === button));
      });
    });
  });

  // Steps, failures, and logs link to report-run.html#t=SECONDS.
  var video = document.getElementById("recording");
  if (video) {
    var seek = function () {
      var match = /^#t=([\d.]+)$/.exec(location.hash);
      if (!match) return;
      var start = function () { video.currentTime = Number(match[1]); };
      if (video.readyState > 0) start(); else video.addEventListener("loadedmetadata", start, { once: true });
      video.scrollIntoView({ block: "center" });
    };
    seek();
    window.addEventListener("hashchange", seek);
  }
})();
