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

  function run(fn) { if (fn) try { fn(); } catch (e) { console.error(e); } }
  function finish() {                                   // end any curtain that is still running, keeping its changes
    timers.forEach(clearTimeout); timers = [];
    const swap = swapLater, reveal = revealLater; swapLater = revealLater = null;
    run(swap); run(reveal);
    el.className = base();
  }
  function later(ms, fn) { timers.push(setTimeout(fn, ms)); }

  // until (optional): a promise for "the next step is ready". The curtain stays down until it settles, at most
  // 5 seconds, so a slow answer from the server never shows the old step again under a lifting curtain.
  let playing = 0;
  function play(text, swap, reveal, until) {
    finish();
    const { IN, HOLD, OUT } = timing();
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
        later(OUT, () => { if (mine === playing) el.className = base(); });
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
    play(text, leave);
  }

  // Safari and Firefox can bring a page back from memory (the Back button) with the curtain frozen half-way.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) { timers.forEach(clearTimeout); timers = []; swapLater = revealLater = null; el.className = base(); }
  });

  // A screen opened by go(): start covered, then roll the curtain away.
  let arriving = null;
  try { arriving = sessionStorage.getItem(KEY); sessionStorage.removeItem(KEY); } catch (e) {}
  if (arriving !== null) {
    const { OUT } = timing();
    caption.textContent = arriving;
    el.classList.add("cover");
    later(360, () => { el.classList.remove("cover"); el.classList.add("out"); });
    later(360 + OUT, () => { el.className = base(); });
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

  // Staff screens: locked with the staff PIN when the hospital has one. Any staff request refused with 401
  // (a first visit, or the 12 hours are over) puts the PIN screen in front of the page.
  const staffPage = /^\/(nurse|doctor)\/?$/.test(location.pathname);
  let lockShown = false;
  function showLock() {
    if (lockShown) return;
    lockShown = true;
    const lock = document.createElement("div");
    lock.id = "staff-lock";
    lock.style.cssText = "position:fixed;inset:0;z-index:60;display:grid;place-items:center;background:rgba(243,246,242,.97)";
    lock.innerHTML = '<form style="width:min(360px,90vw);padding:28px;border-radius:20px;background:#fff;box-shadow:0 12px 40px rgba(0,0,0,.12);text-align:center">' +
      '<img src="/static/meddy-logo.webp" alt="Meddy" width="120" height="40" style="height:40px;width:auto">' +
      '<h2 style="margin:14px 0 6px">Staff only</h2><p style="margin:0 0 16px;color:#45555b">Enter the staff PIN.</p>' +
      '<input id="staff-pin" type="password" inputmode="numeric" autocomplete="off" maxlength="40" aria-label="Staff PIN" ' +
      'style="width:100%;min-height:56px;padding:12px 16px;border:2px solid #aab8b0;border-radius:16px;font:inherit;font-size:1.3rem;text-align:center">' +
      '<p id="staff-pin-error" role="alert" style="min-height:1.4em;margin:8px 0;color:#b4302f"></p>' +
      '<button class="go" type="submit" style="width:100%">Open</button></form>';
    document.body.appendChild(lock);
    const pin = lock.querySelector("#staff-pin");
    pin.focus();
    lock.querySelector("form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const response = await realFetch("/api/staff/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin: pin.value }) }).catch(() => null);
      if (response && response.ok) { location.reload(); return; }
      lock.querySelector("#staff-pin-error").textContent = !response ? "The hospital server does not answer."
        : response.status === 429 ? "Too many wrong tries. Wait a minute." : "That is not the staff PIN.";
      pin.value = ""; pin.focus();
    });
  }
  const realFetch = window.fetch.bind(window);
  if (staffPage) {
    window.fetch = async (...args) => {
      const response = await realFetch(...args);
      if (response.status === 401 && String(args[0]).startsWith("/api/")) showLock();
      return response;
    };
    realFetch("/api/staff/session", { cache: "no-store" }).then((r) => r.json()).then((s) => { if (s.locked) showLock(); }).catch(() => {});
  }

  // A new version online: an open tab keeps its old screens until it is reloaded, so it says so.
  let first = null;
  const checkVersion = async () => {
    try {
      const { version } = await (await fetch("/api/version", { cache: "no-store" })).json();
      if (first === null) { first = version; return; }
      if (version === first || document.getElementById("new-version")) return;
      const bar = document.createElement("div");
      bar.id = "new-version"; bar.setAttribute("role", "status");
      bar.style.cssText = "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:50;display:flex;gap:12px;" +
        "align-items:center;padding:12px 18px;border-radius:999px;background:#173f9c;color:#fff;font-weight:700;" +
        "box-shadow:0 8px 24px rgba(0,0,0,.2)";
      bar.innerHTML = 'Meddy was updated. <button type="button" style="font:inherit;border:0;border-radius:999px;' +
        'padding:6px 14px;background:#fff;color:#173f9c;font-weight:800;cursor:pointer">Reload</button>';
      bar.querySelector("button").addEventListener("click", () => location.reload());
      document.body.appendChild(bar);
    } catch (e) {}
  };
  checkVersion();
  setInterval(checkVersion, 60000);
})();
