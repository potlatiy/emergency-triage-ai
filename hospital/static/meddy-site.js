// Meddy: the white curtain with the logo, shared by every screen (styles in meddy-site.css).
//
//   MeddyCurtain.play(caption, swap, reveal, until)  rolls the curtain up over the screen, calls swap() once the
//                                    screen is covered, holds the logo while the bar fills, then calls reveal() and
//                                    rolls the curtain away. until (optional) is a promise for "the next step is ready".
//   MeddyCurtain.go(url, caption)    covers the screen, fills the bar, then opens another screen; it opens covered
//                                    and rolls the curtain away, so moving between screens feels like one website.
//   MeddyCurtain.hold(caption) / release()   a screen restoring itself after a reload starts covered, then lifts.
//
// Plain timers drive it (nothing waits on an animation event), and a second call finishes the first one at once.
// The bar is driven here, not by CSS, so the curtain always knows how full it is: before the curtain lifts or the
// page leaves, the bar glides the rest of the way. It never jumps from half to full.
// Reduced motion: the curtain fades instead of rolling, and the bar is shown full.
(function () {
  const KEY = "meddy-curtain";
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
  // rolling: 460 ms up (sheet curve), the logo holds, 380 ms away. Reduced motion: a 200 ms fade each way.
  const timing = () => calm.matches ? { IN: 200, HOLD: 520, OUT: 200 } : { IN: 460, HOLD: 600, OUT: 380 };
  const base = () => "curtain" + (calm.matches ? " calm" : "");
  const el = document.createElement("div");
  el.className = base();
  el.setAttribute("aria-hidden", "true");
  el.innerHTML = '<div class="stage"><img src="/static/meddy-logo.webp" alt="" width="876" height="288">' +
                 '<div class="bar"><i></i></div><p></p></div>';
  document.body.appendChild(el);
  const caption = el.querySelector("p");
  let timers = [], swapLater = null, revealLater = null;

  // ---------- the bar ----------
  // A soft start and a soft landing (easeInOutQuad, easings.net). topUp() finishes whatever is left, quicker the
  // less there is, on the strong ease-out, so leaving early still shows the bar completing.
  const bar = el.querySelector(".bar i");
  const FILL = "cubic-bezier(0.45, 0, 0.55, 1)", LAND = "cubic-bezier(0.23, 1, 0.32, 1)";
  let barRun = null;
  function barAt() {
    const m = getComputedStyle(bar).transform;
    if (!m || m === "none") return 1;
    const v = parseFloat(m.slice(m.indexOf("(") + 1));
    return isNaN(v) ? 0 : Math.min(1, Math.max(0, v));
  }
  function fillBar(ms, delay) {
    if (barRun) barRun.cancel();
    if (calm.matches) { barRun = null; return; }
    barRun = bar.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }],
                         { duration: ms, delay: delay || 0, easing: FILL, fill: "both" });
  }
  function topUp() {
    if (!barRun || calm.matches) return Promise.resolve();
    const at = barAt();
    if (at >= 0.995) return Promise.resolve();
    barRun.cancel();
    barRun = bar.animate([{ transform: `scaleX(${at})` }, { transform: "scaleX(1)" }],
                         { duration: Math.round(140 + 260 * (1 - at)), easing: LAND, fill: "both" });
    return barRun.finished.catch(() => {});
  }
  function resetBar() { if (barRun) { barRun.cancel(); barRun = null; } }

  function run(fn) { if (fn) try { fn(); } catch (e) { console.error(e); } }
  function finish() {                                   // end any curtain that is still running, keeping its changes
    timers.forEach(clearTimeout); timers = [];
    const swap = swapLater, reveal = revealLater; swapLater = revealLater = null;
    run(swap); run(reveal);
    el.className = base(); resetBar();
  }
  function later(ms, fn) { timers.push(setTimeout(fn, ms)); }
  function rollAway(mine, before) {                     // the bar completes, then the curtain lifts
    const { OUT } = timing();
    topUp().then(() => {
      if (mine !== playing) return;
      run(before);
      el.classList.remove("in", "cover", "loading"); el.classList.add("out");
      later(OUT, () => { if (mine === playing) { el.className = base(); resetBar(); } });
    });
  }

  // The curtain stays down until `until` settles, at most 5 seconds, so a slow answer from the server never
  // shows the old step again under a lifting curtain.
  let playing = 0;
  function play(text, swap, reveal, until) {
    finish();
    const { IN, HOLD } = timing();
    const mine = ++playing;
    caption.textContent = text || "";
    swapLater = swap; revealLater = reveal;
    void el.offsetWidth;                                // start from below the screen every time
    el.classList.add("in");
    fillBar(700, 160);                                  // full at about 860 ms; the curtain lifts at 1060
    later(IN, () => { const s = swapLater; swapLater = null; run(s); });
    later(IN + HOLD, () => {
      const wait = new Promise((done) => setTimeout(done, 5000));
      Promise.race([until || Promise.resolve(), wait]).then(() => {
        if (mine !== playing || !el.classList.contains("in")) return;   // a newer change already took over
        rollAway(mine, () => { const r = revealLater; revealLater = null; run(r); });
      });
    });
  }

  // hold(text): a screen that has just loaded starts covered (no roll-in) while it restores itself, so the
  // visitor never sees a blank half-built page. release(): the bar completes, then the curtain rolls away.
  let heldSince = 0;
  function hold(text) {
    finish();
    caption.textContent = text || "";
    el.className = base() + " cover loading";
    fillBar(560, 80);
    heldSince = performance.now();
  }
  function release() {
    if (!el.classList.contains("cover")) return;
    const mine = ++playing;
    later(Math.max(0, 640 - (performance.now() - heldSince)), () => rollAway(mine));
  }

  // A short note at the bottom of the screen, for when a screen cannot be opened.
  let note = null;
  function notice(message) {
    if (!note) {
      note = document.createElement("div");
      note.className = "meddy-notice"; note.setAttribute("role", "status");
      document.body.appendChild(note);
    }
    note.textContent = message;
    note.classList.add("on");
    clearTimeout(note.hide); note.hide = setTimeout(() => note.classList.remove("on"), 7000);
  }

  function go(url, text) {
    // Check the next screen answers before leaving, so a server that does not have it yet never leaves
    // the visitor on an error page: the curtain rolls back and the note says what to do.
    const ready = fetch(url, { cache: "no-store" }).then((r) => r.ok).catch(() => false);
    finish();
    const mine = ++playing;
    caption.textContent = text || "";
    void el.offsetWidth;
    el.classList.add("in");
    fillBar(700, 160);
    // Leave only once the bar is full on this screen; the next one opens covered, with the bar full.
    later(calm.matches ? 260 : 860, () => ready.then((ok) => {
      if (mine !== playing) return;
      if (!ok) {
        notice((text || "That screen") + " isn't available on this server yet. Restart the server, then try again.");
        rollAway(mine);
        return;
      }
      topUp().then(() => {
        if (mine !== playing) return;
        try { sessionStorage.setItem(KEY, text || ""); } catch (e) {}
        window.location.href = url;
      });
    }));
  }

  // Safari and Firefox can bring a page back from memory (the Back button) with the curtain frozen half-way.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) { timers.forEach(clearTimeout); timers = []; swapLater = revealLater = null; el.className = base(); resetBar(); }
  });

  // A screen opened by go(): start covered with the bar full, then roll the curtain away.
  let arriving = null;
  try { arriving = sessionStorage.getItem(KEY); sessionStorage.removeItem(KEY); } catch (e) {}
  if (arriving !== null) {
    const mine = ++playing;
    caption.textContent = arriving;
    el.classList.add("cover");
    later(360, () => rollAway(mine));
  }

  // The switcher between the three screens uses the curtain; a click with a modifier key still opens a new tab.
  document.addEventListener("click", (event) => {
    const link = event.target.closest(".roles a");
    if (!link || link.getAttribute("aria-current") === "page") return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    go(link.href, link.dataset.caption || "");
  });

  const covering = () => el.classList.contains("cover");     // held over a screen that is restoring itself
  window.MeddyCurtain = { play, go, hold, release, covering };
})();
