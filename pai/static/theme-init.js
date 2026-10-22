  // Same theme key and default as the owner UI; applied before first paint.
  (function () {
    var saved = null;
    try { saved = localStorage.getItem("pai.theme"); } catch (_) {}
    document.documentElement.setAttribute("data-theme", saved === "dark" ? "dark" : "light");
  })();
