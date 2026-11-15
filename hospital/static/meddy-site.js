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
                 '<div class="curtain-bar"><i class="curtain-fill"></i></div><p></p></div>';
  document.body.appendChild(el);
  const caption = el.querySelector("p");
  let timers = [], swapLater = null, revealLater = null;

  // ---------- the bar: a 0-100% counter ----------
  // It climbs one percent at a time and shows the number under the bar. The curtain never lifts and the
  // page never leaves before it reads 100%: topUp() speeds up whatever is left, then resolves at 100.
  // A hidden tab draws no frames, so there it simply completes.
  const bar = el.querySelector(".curtain-fill");
  const pctLabel = document.createElement("span");
  pctLabel.className = "curtain-pct"; pctLabel.textContent = "0%";
  el.querySelector(".curtain-bar").after(pctLabel);
  let pct = 0, raf = 0, from = 0, startAt = 0, span = 0, done = null, doneWait = null;
  function draw(p) {
    pct = p;
    bar.style.transform = `scaleX(${p / 100})`;
    pctLabel.textContent = p + "%";
    if (p >= 100 && doneWait) { const d = doneWait; doneWait = null; d(); }
  }
  function step(now) {
    const t = span ? Math.min(1, Math.max(0, (now - startAt) / span)) : 1;
    const p = Math.min(100, from + Math.floor((100 - from) * t));
    if (p !== pct) draw(p);
    raf = p < 100 ? requestAnimationFrame(step) : 0;
  }
  function count(ms, delay) {           // from the current value to 100, one percent at a time
    cancelAnimationFrame(raf);
    from = pct; span = ms; startAt = performance.now() + (delay || 0);
    if (calm.matches || document.hidden) { draw(100); return; }
    raf = requestAnimationFrame(step);
  }
  function fillBar(ms, delay) { draw(0); count(ms, delay); }
  function topUp() {
    if (pct >= 100) return Promise.resolve();
    const p = new Promise((resolve) => { doneWait = resolve; });
    count(Math.round(120 + 3 * (100 - pct)), 0);       // what is left, quicker the less there is
    setTimeout(() => { if (pct < 100) draw(100); }, 1200); // never wait forever (a paused tab)
    return p;
  }
  function resetBar() { cancelAnimationFrame(raf); raf = 0; doneWait = null; draw(0); }

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
    draw(100);
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

  // Staff screens: locked with the staff PIN when the hospital has one. Any staff request refused with 401
  // (a first visit, or the 12 hours are over) puts the PIN screen in front of the page.
  const staffPage = /^\/(nurse|doctor|chief)\/?$/.test(location.pathname);
  const role = /^\/chief/.test(location.pathname) ? "chief" : "staff";      // the chief doctor's panel has its own PIN
  let lockShown = false;
  function showLock() {
    if (lockShown) return;
    lockShown = true;
    const lock = document.createElement("div");
    lock.id = "staff-lock";
    // Nothing of the page may show behind the lock: a tab opened before the PIN was set still holds names.
    for (const child of document.body.children) child.style.visibility = "hidden";
    lock.style.cssText = "position:fixed;inset:0;z-index:1200;display:grid;place-items:center;background:#f3f6f2;visibility:visible";
    lock.innerHTML = '<form style="width:min(360px,90vw);padding:28px;border-radius:20px;background:#fff;box-shadow:0 12px 40px rgba(0,0,0,.12);text-align:center">' +
      '<img src="/static/meddy-logo.webp" alt="Meddy" width="120" height="40" style="height:40px;width:auto">' +
      '<h2 style="margin:14px 0 6px">' + (role === "chief" ? "Chief doctor only" : "Staff only") + '</h2>' +
      '<p style="margin:0 0 16px;color:#45555b">' + (role === "chief" ? "Enter the chief doctor&#39;s PIN." : "Enter the staff PIN.") + '</p>' +
      '<input id="staff-pin" type="password" inputmode="numeric" autocomplete="off" maxlength="40" aria-label="Staff PIN" ' +
      'style="width:100%;min-height:56px;padding:12px 16px;border:2px solid #aab8b0;border-radius:16px;font:inherit;font-size:1.3rem;text-align:center">' +
      '<p id="staff-pin-error" role="alert" style="min-height:1.4em;margin:8px 0;color:#b4302f"></p>' +
      '<button class="go" type="submit" style="width:100%">Open</button>' +
      // For the hackathon judges, on purpose: the lock is part of what we built (patients' names stay behind it),
      // and the judges need the key to see it working.
      '<p style="margin:16px 0 0;padding:12px 14px;border-radius:14px;background:#f1eefc;color:#2f2466;font-size:.92rem;text-align:left">' +
      '<b>Judges:</b> the demo PINs are in <a href="https://github.com/potlatiy/emergency-triage-ai/blob/main/JUDGES.md" ' +
      'target="_blank" rel="noopener" style="color:#5a45b8;font-weight:700">JUDGES.md</a> in our GitHub repository. This lock is ' +
      'part of the build: patients&#39; names and the buttons that close visits sit behind it, so a passer-by with the link ' +
      'sees nothing. In a real hospital each staff member would sign in.</p></form>';
    document.body.appendChild(lock);
    const pin = lock.querySelector("#staff-pin");
    pin.focus();
    lock.querySelector("form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const response = await realFetch("/api/staff/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin: pin.value, role }) }).catch(() => null);
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
    realFetch("/api/staff/session?role=" + role, { cache: "no-store" }).then((r) => r.json()).then((s) => { if (s.locked) showLock(); }).catch(() => {});
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
      bar.style.cssText = "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:1150;display:flex;gap:12px;" +
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
