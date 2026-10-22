(function () {
  // Android Chrome keyboard fix: dvh doesn't reliably shrink for the
  // on-screen keyboard, so track visualViewport.height ourselves and
  // expose it as --vvh. Also nudge the focused input into view since
  // some keyboards report the resize a frame late.
  var vv = window.visualViewport;
  if (!vv) return;

  function setVvh() {
    document.documentElement.style.setProperty("--vvh", vv.height + "px");
  }
  setVvh();
  vv.addEventListener("resize", setVvh);
  vv.addEventListener("scroll", setVvh);

  document.addEventListener("focusin", function (e) {
    var t = e.target;
    if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT")) {
      setTimeout(function () {
        t.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }, 60);
    }
  });
})();
