// COP daily brief pages (site/brief/): times in the viewer's zone, with the zone named, and the Time settings button.
// The pages are written by tools/brief.py; CSP allows no inline script, so this file does the little they need.
(function () {
  "use strict";
  var T = window.TicTime;
  if (!T) return;                                   // without the shared module the UTC text stays
  var slot = document.getElementById("time-settings");
  if (slot) T.mountSettings(slot);
  T.renderAll(document);
  T.onChange(function () { T.renderAll(document); });
})();
