// Meddy: the white curtain with the logo, shared by every screen (styles in meddy-site.css).
//
//   MeddyCurtain.play(caption, swap, reveal)  rolls the curtain up over the screen, calls swap() once the screen is
//                                             covered, holds the logo, then calls reveal() and rolls the curtain away.
//   MeddyCurtain.go(url, caption)              covers the screen and opens another one; it opens covered and rolls
//                                              the curtain away, so moving between screens feels like one website.
//
// Nothing waits on an animation event: plain timers drive it, and a second call finishes the first one at once.
// Anyone who asks for reduced motion gets the change straight away, without the curtain.
(function () {
  const IN = 420, HOLD = 640, OUT = 380, KEY = "meddy-curtain";
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
  const el = document.createElement("div");
  el.className = "curtain";
  el.setAttribute("aria-hidden", "true");
  el.innerHTML = '<div class="stage"><img src="/static/meddy-logo.webp" alt="" width="876" height="288">' +
                 '<div class="bar"><i></i></div><p></p></div>';
  document.body.appendChild(el);
  const caption = el.querySelector("p");
  let timers = [], swapLater = null, revealLater = null;

  function run(fn) { if (fn) try { fn(); } catch (e) { console.error(e); } }
  function finish() {                                   // end any curtain that is still running, keeping its changes
    timers.forEach(clearTimeout); timers = [];
    const swap = swapLater, reveal = revealLater; swapLater = revealLater = null;
    run(swap); run(reveal);
    el.className = "curtain";
  }
  function later(ms, fn) { timers.push(setTimeout(fn, ms)); }

  // until (optional): a promise for "the next step is ready". The curtain stays down until it settles, at most
  // 5 seconds, so a slow answer from the server never shows the old step again under a lifting curtain.
  let playing = 0;
  function play(text, swap, reveal, until) {
    finish();
    if (calm.matches) { run(swap); run(reveal); return; }
    const mine = ++playing;
    caption.textContent = text || "";
    swapLater = swap; revealLater = reveal;
    void el.offsetWidth;                                // start from below the screen every time
    el.classList.add("in");
    later(IN, () => { const s = swapLater; swapLater = null; run(s); });
    later(IN + HOLD, () => {
      const wait = new Promise((done) => setTimeout(done, 5000));
      Promise.race([until || Promise.resolve(), wait]).then(() => {
        if (mine !== playing || !el.classList.contains("in")) return;   // a newer change already took over
        const r = revealLater; revealLater = null; run(r);
        el.classList.remove("in"); el.classList.add("out");
        later(OUT, () => { if (mine === playing) el.className = "curtain"; });
      });
    });
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
    const leave = () => ready.then((ok) => {
      if (!ok) {
        notice((text || "That screen") + " isn't available on this server yet. Restart the server, then try again.");
        return;
      }
      try { sessionStorage.setItem(KEY, text || ""); } catch (e) {}
      window.location.href = url;
    });
    if (calm.matches) { leave(); return; }
    play(text, leave);
  }

  // Safari and Firefox can bring a page back from memory (the Back button) with the curtain frozen half-way.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) { timers.forEach(clearTimeout); timers = []; swapLater = revealLater = null; el.className = "curtain"; }
  });

  // A screen opened by go(): start covered, then roll the curtain away.
  let arriving = null;
  try { arriving = sessionStorage.getItem(KEY); sessionStorage.removeItem(KEY); } catch (e) {}
  if (arriving !== null && !calm.matches) {
    caption.textContent = arriving;
    el.classList.add("cover");
    later(420, () => { el.classList.remove("cover"); el.classList.add("out"); });
    later(420 + OUT, () => { el.className = "curtain"; });
  }

  // The switcher between the three screens uses the curtain; a click with a modifier key still opens a new tab.
  document.addEventListener("click", (event) => {
    const link = event.target.closest(".roles a");
    if (!link || link.getAttribute("aria-current") === "page") return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    go(link.href, link.dataset.caption || "");
  });

  window.MeddyCurtain = { play, go };
})();
