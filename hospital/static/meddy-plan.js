// Meddy: the floor plan on the patient's screen, drawn in the look of the rest of the site (styles in
// meddy-site.css).
//
// It is the page's own plan, the same six rooms in the same order, made easier to read at a glance:
//   - the rooms are furnished: a picture of the hospital seen from straight above lies under everything
//     (media/plan.webp, drawn by an AI picture tool, see media/README.md). If the picture does not load, the
//     rooms are plain coloured boxes with doors and seats, and nothing else changes,
//   - a thin line joins the rooms through the doors: this is the way the visit goes. The part already walked
//     is blue. The short way from the nurse to the resuscitation bay is only shown once it is walked or pointed at,
//   - "YOU" is the small person in the blue ring, seen from above. When the visit moves on they walk along
//     the line, through the doors, to the next room; they do not jump and they do not cut through walls,
//   - every stop has its number on its room: done stops get a tick, the room the patient is in has a blue frame,
//   - people sit on the seats in the waiting room: as many as are really waiting (twelve seats),
//   - the staff are on the plan: the triage nurse at her desk, the resuscitation team in its bay, and in the
//     three doctor's rooms the doctors who called a ticket in the last minutes (the patient's own doctor among
//     them once they are called),
//   - pointing at a room or at a person, tapping them or reaching them with the keyboard opens a small note.
//     A room's note says what happens there and shows the way to it. A person's note says who they are: the
//     patient's own visit, a doctor's name, room and the ticket they called.
//
// A patient's phone is told nothing about OTHER patients: no name, no complaint, no level. Their note only says
// that they are waiting. The staff table is staff only, so this page does not ask for it either: it asks for
// the waiting room's public board (/api/board: tickets called and to which room), and the seats are counted
// by the page itself (/api/waiting-room).
//
// The nurse's screen has the same plan as a view of the whole department (an empty box with the id "staff-plan"
// turns it on). Nobody is "YOU" there. The people are the patients on the nurse's own lists: those waiting for
// her stand in her room, the line sits on the seats in its order, a doctor who has patients has one on the bed.
// Their shoulders have the colour of their level and their notes give the name, the complaint, the level and
// the wait: staff only, from the staff's own addresses (/api/staff, /api/line, /api/nurse/patients,
// /api/nurse/today). Tapping a patient opens their card on the nurse's screen. The plan never asks for a
// doctor's own list: that address tells the hospital that a person sits at that doctor's screen.
//
// Nobody on the plan is invented. The nurse is the nurse's station, which has no name. The page's own plan
// stays in the page, hidden: it is still what the page updates, and this file only reads it (which room is
// lit, where "YOU" stands, how many seats are taken, what the rooms are called). If this file does not load,
// that plan is simply shown as before.
// Anyone who asks for reduced motion gets no walking and no pulsing: "YOU" is just put where they belong.
(() => {
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
  const W = 640, H = 430;                                     // the plan's units: the picture is 1024 by 688, times 0.625
  const PICTURE = "/static/media/plan.webp?v=3";
  // the rooms, measured on the picture: place, size, and the colours used when the picture is missing
  const ROOM = { 1: { x: 15, y: 271.2, w: 145, h: 143.2, fill: "#eaf3ea", line: "#a8cba8" },
                 2: { x: 15.6, y: 15, w: 190.6, h: 135, fill: "#eceefb", line: "#c5cbf2" },
                 3: { x: 224.4, y: 15, w: 197.5, h: 134.7, fill: "#eef0fb", line: "#c3c9f0" },
                 4: { x: 177.5, y: 202.5, w: 244.4, h: 211.6, fill: "#fcf3df", line: "#e3c27c" },
                 5: { x: 439.4, y: 168.1, w: 185, h: 246.3, fill: "#e9f4f2", line: "#a9d2cb" },
                 6: { x: 439.4, y: 15.6, w: 185.6, h: 134.1, fill: "#fbeded", line: "#e6aeae" } };
  // where "YOU" stands: inside the door, at the counter, at the nurse's desk, in front of the seats, on the
  // patient's chair in a doctor's room, on a bed in the resuscitation bay. And which way they face there.
  const SPOT = { 1: [81, 342], 2: [81, 88], 3: [323, 88], 4: [323, 247.5], 5: [509, 302], 6: [486, 72] };
  const FACE = { 1: 90, 2: 0, 3: 0, 4: 180, 5: 0, 6: 0 };     // degrees, 0 is up the picture
  const SIGN = { 1: [87.5, 414.4], 2: [111, 15], 3: [323, 15], 4: [226, 202.5], 5: [532, 168.1], 6: [532, 15.6] };   // a room's name sits on its wall
  const AT = { "64,228": 1, "195,108": 2, "345,108": 3, "280,300": 4, "530,250": 5, "530,98": 6 };              // the page's own spots
  // The way: one line from room to room, in the order of the visit, through the doors.
  const WAY = [{ id: "12", from: 1, to: 2, d: "M81 342 V88" },
               { id: "23", from: 2, to: 3, d: "M81 88 H323" },
               { id: "34", from: 3, to: 4, d: "M323 88 V247.5" },
               { id: "45", from: 4, to: 5, d: "M323 247.5 H457 Q465 247.5 465 255.5 V294 Q465 302 473 302 H509" },
               { id: "36", from: 3, to: 6, d: "M323 88 H478 Q486 88 486 80 V72" }];
  // doors: where the way crosses a wall. [x, y, "v" for a wall that runs up and down, "h" for one that runs across]
  const DOORS = [[15, 342, "v"], [87.5, 271.2, "h"], [87.5, 150, "h"], [206.2, 83, "v"], [224.4, 83, "v"], [323, 149.7, "h"],
                 [323, 202.5, "h"], [421.9, 83, "v"], [439.4, 83, "v"], [421.9, 247.5, "v"], [439.4, 247.5, "v"]];
  // the twelve seats, where the picture has them, and the order people take them in (not shoulder to shoulder)
  const SEAT_X = [231.9, 258.8, 286.2, 313.1, 340.6, 367.5], SEAT_Y = [307, 351.5];
  const SIT = [1, 10, 4, 7, 0, 9, 3, 6, 5, 8, 2, 11];
  // where the staff are, on the chairs the picture gives them, and the beds their patients lie on
  const NURSE = [285.3, 40.3];
  const CUBICLE = [{ doctor: [508.4, 203.4], bed: [596, 193.4] }, { doctor: [508.4, 279.4], bed: [596, 268.8] },
                   { doctor: [508.4, 357.5], bed: [596, 346.3] }];
  const RESUS = { team: [451, 125], bed: [572.5, 70] };
  const TEAM = "DR-1000";                                     // the resuscitation team's line in the staff table
  const PRIVATE = "Their details are private: only the staff see them.";
  // The nurse's screen has no plan of its own to read the rooms' names from: they are the patient's page's names.
  const NAMES = { 1: ["Entrance", "registration"], 2: ["Check-in", "questionnaire"], 3: ["Nurse", "Meddy suggests a level"],
                  4: ["Waiting room", "most urgent first"], 5: ["Doctor rooms", "the right doctor for you"], 6: ["Resuscitation", "care right away"] };
  // Staff only: the colour and the name of each level, as on the nurse's screen. A patient's phone never gets a level.
  const LEVEL = { 1: ["#b4302f", "Resuscitation"], 2: ["#b4302f", "Emergency"], 3: ["#b06a00", "Urgent"], 4: ["#1d7a52", "Less urgent"], 5: ["#1d7a52", "Non-urgent"] };
  const UNMEASURED = "#9aa6b0";
  const QUEUE = [[283, 120], [309, 120], [335, 120], [361, 120]];    // staff only: where the patients stand who wait for the nurse
  // The people waiting wear quiet greys on purpose: on this page red, amber and green mean urgency only.
  // [shoulders, hair, skin], all soft so that nobody is darker than the rooms around them
  const LOOK = [["#a9b4bd", "#7a6658", "#f3cfae"], ["#b7c0c8", "#625a54", "#e6bb95"], ["#9eaab4", "#b39a80", "#f3cfae"],
                ["#b0bac3", "#c2bdb5", "#edc6a2"], ["#a3aeb8", "#6a5a50", "#cf9f7a"], ["#abb6bf", "#8c7a6c", "#f3cfae"]];
  // a person seen from straight above, facing up the picture: shoes, shoulders, head with hair
  const person = (top, hair, skin) => `<ellipse class="mp-foot" cx="-4.3" cy="-1.5" rx="2.5" ry="3.6"/><ellipse class="mp-foot" cx="4.3" cy="-1.5" rx="2.5" ry="3.6"/>
    <rect class="mp-top" x="-11.5" y="-7" width="23" height="14" rx="7"${top ? ` style="fill:${top}"` : ""}/>
    <circle class="mp-skin" r="5"${skin ? ` style="fill:${skin}"` : ""}/>
    <path class="mp-hair" d="M-4.98 .4 A5 5 0 1 0 4.98 .4 Q0 -2.1 -4.98 .4Z"${hair ? ` style="fill:${hair}"` : ""}/>`;

  // the rooms to pass from one to another, and the pieces of the way between them
  function route(from, to) {
    const back = { [from]: 0 }, queue = [from];
    while (queue.length) {
      const n = queue.shift();
      if (n === to) break;
      for (const w of WAY) {
        const m = w.from === n ? w.to : w.to === n ? w.from : 0;
        if (m && !(m in back)) { back[m] = n; queue.push(m); }
      }
    }
    const rooms = [];
    for (let n = to; n; n = back[n]) rooms.unshift(n);
    return rooms;
  }
  const pieces = (from, to) => { const rooms = route(from, to), out = [];
    for (let k = 0; k + 1 < rooms.length; k++) {
      const w = WAY.find((p) => (p.from === rooms[k] && p.to === rooms[k + 1]) || (p.to === rooms[k] && p.from === rooms[k + 1]));
      out.push({ way: w, back: w.from !== rooms[k] });
    }
    return out; };

  function build() {
    const you = document.getElementById("you"), seats = document.getElementById("seats");
    const desk = document.getElementById("staff-plan");       // the nurse's screen: an empty box to draw the plan in
    const old = you && seats ? you.closest("svg") : null;
    const ward = !old;                                        // the whole department, for the staff: nobody is "YOU"
    if (ward && !desk) return;
    const make = (tag, cls, text) => { const e = document.createElement(tag); e.className = cls; if (text) e.textContent = text; return e; };

    // ---- the picture
    const wrap = make("div", "mp-scene pic" + (ward ? " ward" : ""));
    wrap.setAttribute("role", "group");
    const title = old && old.querySelector("title");
    wrap.setAttribute("aria-label", (title ? title.textContent : "Floor plan of the department: entrance, check-in, nurse station, waiting room, doctor rooms and resuscitation bay") + ". Use the arrow keys to go from room to room, and from person to person.");
    const door = ([x, y, turn]) => turn === "v" ? `<path class="mp-door" d="M${x} ${y - 18} V${y + 18}"/>` : `<path class="mp-door" d="M${x - 18} ${y} H${x + 18}"/>`;
    const box = (cls, id) => { const r = ROOM[id];
      return `<rect class="${cls}" data-room="${id}" x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" rx="14" style="--room:${r.fill};--wall:${r.line}"/>`; };
    wrap.innerHTML = `<svg viewBox="0 0 ${W} ${H}" aria-hidden="true" focusable="false">
      <rect class="mp-floor" width="${W}" height="${H}"/>
      ${Object.keys(ROOM).map((id) => box("mp-room", id)).join("")}
      <image class="mp-pic" href="${PICTURE}" width="${W}" height="${H}" preserveAspectRatio="none"/>
      ${Object.keys(ROOM).map((id) => box("mp-frame", id)).join("")}
      ${DOORS.map(door).join("")}
      <path class="mp-way" d="M22 342 H81"/>
      ${WAY.map((w) => `<path class="mp-way${w.to === 6 ? " urgent" : ""}" data-way="${w.id}" d="${w.d}"/>`).join("")}
      ${WAY.map((w) => `<path class="mp-walked" data-way="${w.id}" d="${w.d}"/>`).join("")}
      ${WAY.map((w) => `<path class="mp-lead${w.to === 6 ? " urgent" : ""}" data-way="${w.id}" d="${w.d}"/>`).join("")}
      <g class="mp-seats">${Array.from({ length: 12 }, (_, i) =>
        `<g class="mp-seat" transform="translate(${SEAT_X[i % 6]} ${SEAT_Y[Math.floor(i / 6)]})"><rect class="mp-chair" x="-11" y="-11" width="22" height="22" rx="7"/>` +
        `<g class="mp-person" style="--turn:${180 + (i * 37) % 13 - 6}deg">${person(...LOOK[i % LOOK.length])}</g></g>`).join("")}</g>
      <g class="mp-staff">
        <g class="mp-spot mp-scrubs" data-who="nurse" transform="translate(${NURSE})"><g class="mp-person" style="--turn:90deg">${person("", "#6a5a50", "#f3cfae")}</g></g>
        ${CUBICLE.map((c, i) => `<g class="mp-spot" data-who="bed${i}" transform="translate(${c.bed})"><g class="mp-person" style="--turn:90deg">${person(...LOOK[(i + 2) % LOOK.length])}</g></g>
        <g class="mp-spot mp-coat" data-who="doc${i}" transform="translate(${c.doctor})"><g class="mp-person" style="--turn:180deg">${person("", LOOK[(i + 4) % LOOK.length][1], LOOK[(i + 4) % LOOK.length][2])}</g></g>`).join("")}
        ${QUEUE.map((at, i) => `<g class="mp-spot" data-who="queue${i}" transform="translate(${at})"><g class="mp-person" style="--turn:0deg">${person(...LOOK[(i + 3) % LOOK.length])}</g></g>`).join("")}
        <g class="mp-spot" data-who="resusbed1" transform="translate(${SPOT[6]})"><g class="mp-person" style="--turn:0deg">${person(...LOOK[4])}</g></g>
        <g class="mp-spot" data-who="resusbed" transform="translate(${RESUS.bed})"><g class="mp-person" style="--turn:0deg">${person(...LOOK[1])}</g></g>
        <g class="mp-spot mp-coat" data-who="team" transform="translate(${RESUS.team})"><g class="mp-person" style="--turn:90deg">${person("", "#8c7a6c", "#e6bb95")}</g></g>
      </g>
      <g class="mp-you"><circle class="mp-pulse" r="15"/><circle class="mp-ring" r="15"/><g class="mp-body"><g transform="scale(1.05)">${person()}</g></g></g>
    </svg>`;
    const svg = wrap.firstElementChild, dot = svg.querySelector(".mp-you"), body = dot.querySelector(".mp-body"), tag = make("span", "mp-tag", "YOU");
    const feet = dot.querySelectorAll(".mp-foot"), pic = svg.querySelector(".mp-pic");
    tag.setAttribute("aria-hidden", "true");
    wrap.appendChild(tag);
    // without the picture the plan is the plain one: coloured rooms, doors, seats
    const note = make("p", "mp-note", ward ? "Tap a person for their details, a patient to open their card." : "Tap a room or a person to see more."), drawn = make("span", "", " Picture made with an AI tool. Not a real hospital.");
    note.appendChild(drawn);
    pic.addEventListener("error", () => { wrap.classList.remove("pic"); pic.remove(); drawn.remove(); });

    // a plate on every room: its number (the stops 1 to 5) and its name. What happens there is in the room's note.
    const signs = {}, names = {}, subs = {}, counts = {}, hits = {};
    for (const id of Object.keys(ROOM)) {
      const room = old && document.getElementById("room-" + id), group = room && room.parentNode, sign = make("span", "mp-sign");
      sign.style.left = SIGN[id][0] / W * 100 + "%"; sign.style.top = SIGN[id][1] / H * 100 + "%";
      names[id] = group ? (group.querySelector(".room-label") || {}).textContent || NAMES[id][0] : NAMES[id][0];
      subs[id] = group ? (group.querySelector(".room-sub") || {}).textContent || "" : NAMES[id][1];
      const name = make("b", "", names[id]);
      if (+id <= 5) name.prepend(make("i", "mp-no", id));
      if (ward) name.appendChild(counts[id] = make("i", "mp-count"));                         // how many patients are in the room now
      sign.setAttribute("aria-hidden", "true");                                              // the room's button says the same
      sign.appendChild(name); wrap.appendChild(sign); signs[id] = sign;
    }
    const who = make("span", "mp-sign mp-who");                // "Dr. Novak, Room 19", once a doctor has called
    who.style.left = (ROOM[5].x + ROOM[5].w / 2) / W * 100 + "%"; who.style.top = (ROOM[5].y + ROOM[5].h) / H * 100 + "%";
    who.setAttribute("aria-hidden", "true"); who.hidden = true;
    wrap.appendChild(who);
    if (ward) desk.appendChild(wrap); else old.parentNode.insertBefore(wrap, old);     // in the page now, so lengths can be measured
    wrap.after(note);
    if (ward) {                                               // nobody is "YOU"; a line says what the colours mean
      dot.style.display = "none"; tag.hidden = true;
      const key = make("p", "mp-key");
      for (const [colour, text] of [[LEVEL[1][0], "Levels 1 and 2"], [LEVEL[3][0], "Level 3"], [LEVEL[4][0], "Levels 4 and 5"], [UNMEASURED, "No level yet"]]) {
        const item = make("span", "", text), blob = make("i", ""); blob.style.background = colour; item.prepend(blob); key.appendChild(item); }
      const ring = make("span", "", "Ring: feels worse, danger sign or re-check due"); ring.prepend(make("i", "ring")); key.appendChild(ring);
      note.after(key);
    }

    // ---- what the picture shows right now
    const S = { place: 1, at: 1, walk: null, hover: 0, note: "", turn: FACE[1], doctors: [], team: null };
    const folk = {};                                          // everyone who can be pointed at: id -> where they are, their button, what their note says
    let staff = null, board = null, loaded = 0;               // the staff table (staff only), the waiting room's public board, when they were asked for
    const D = { line: [], nurse: [], recheck: [], today: [], now: {} };   // staff only: the nurse's lists, and who is drawn where
    const lines = {}, walked = {}, leads = {}, long = {};
    for (const w of WAY) {
      lines[w.id] = svg.querySelector(`.mp-way[data-way="${w.id}"]`);
      walked[w.id] = svg.querySelector(`.mp-walked[data-way="${w.id}"]`); leads[w.id] = svg.querySelector(`.mp-lead[data-way="${w.id}"]`);
      long[w.id] = lines[w.id].getTotalLength();
      walked[w.id].style.strokeDasharray = long[w.id];
    }
    const put = (x, y) => {
      dot.setAttribute("transform", `translate(${x.toFixed(2)} ${y.toFixed(2)})`);
      tag.style.left = x / W * 100 + "%"; tag.style.top = (y + 18.5) / H * 100 + "%";
      if (folk.you) move("you", [x, y]);
    };
    // which way "YOU" faces: always turned the short way round
    const face = (deg) => { S.turn += ((deg - S.turn) % 360 + 540) % 360 - 180; body.style.transform = `rotate(${S.turn.toFixed(1)}deg)`; };
    // the shoes: one ahead, then the other, as far as the walk has come. Standing, they are under the shoulders.
    const step = (far) => { const swing = far === null ? 0 : Math.sin(far / 16 * Math.PI * 2);
      feet[0].setAttribute("cy", (-1.5 - 6.5 * Math.max(0, swing)).toFixed(2)); feet[1].setAttribute("cy", (-1.5 - 6.5 * Math.max(0, -swing)).toFixed(2)); };
    // the blue line: everything up to the piece "YOU" is on, and that piece as far as "YOU" has come
    const colour = (way, along) => {
      const behind = new Set(pieces(1, way.from).map((p) => p.way.id));
      for (const w of WAY) {
        const part = w.id === way.id ? along : behind.has(w.id) ? 1 : 0;
        walked[w.id].style.display = part > .001 ? "" : "none";
        walked[w.id].style.strokeDashoffset = long[w.id] * (1 - part);
      }
    };
    const stand = (id) => {
      put(SPOT[id][0], SPOT[id][1]); face(FACE[id]); step(null);
      const last = pieces(1, id).pop();
      if (last) colour(last.way, 1); else for (const w of WAY) walked[w.id].style.display = "none";
    };

    // Walking: along the line from the room "YOU" is in to the new one, slow at both ends, facing the way they
    // go. If the browser draws no frames (a tab in the background), "YOU" is put where they belong a moment
    // after the walk should have ended.
    let watch = 0;
    function goTo(id, atOnce) {
      if (S.walk) { S.at = S.walk.to; S.walk = null; }        // a walk still going: it ends where it was heading
      clearTimeout(watch);
      const legs = pieces(S.at, id), far = legs.reduce((sum, leg) => sum + long[leg.way.id], 0);
      if (atOnce || calm.matches || !legs.length) { S.at = id; stand(id); lead(); return; }
      const ms = Math.max(900, Math.min(3200, 500 + far * 4.4)), t0 = performance.now(), mine = S.walk = { to: id };
      const end = () => { if (S.walk !== mine) return; S.walk = null; S.at = id; stand(id); lead(); };
      const frame = (now) => {
        if (S.walk !== mine) return;
        const p = Math.min(1, (now - t0) / ms), eased = p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
        let d = eased * far, i = 0;
        while (i < legs.length - 1 && d > long[legs[i].way.id]) { d -= long[legs[i].way.id]; i++; }
        const leg = legs[i], size = long[leg.way.id], along = leg.back ? 1 - d / size : d / size;   // along: from the piece's own start
        const line = lines[leg.way.id], at = line.getPointAtLength(along * size);
        const near = Math.max(0, Math.min(size, along * size + (leg.back ? -1.5 : 1.5))), next = line.getPointAtLength(near);
        put(at.x, at.y); colour(leg.way, along);
        if (p < 1) {
          if (Math.abs(next.x - at.x) + Math.abs(next.y - at.y) > .01) face(Math.atan2(next.y - at.y, next.x - at.x) * 180 / Math.PI + 90);
          step(eased * far);
          requestAnimationFrame(frame);
        } else end();
      };
      requestAnimationFrame(frame);
      watch = setTimeout(end, ms + 1500);
    }

    // ---- the plates: done stops get a tick, the stop you are at is blue; in the resuscitation bay the waiting
    // room was skipped
    const onDuty = () => staff ? staff.doctors.filter((d) => d.on_duty && d.reg_no !== TEAM) : [];
    const duty = () => { const on = onDuty(), busy = on.filter((d) => d.waiting > 0).length;
      return staff ? `${on.length} of ${staff.total} doctors are on duty: ${busy} with patients, ${on.length - busy} free. The plan shows three of them` : ""; };
    const many = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
    const tally = (n) => !staff ? "" : n === 3 ? `${many(D.nurse.length, "patient")} waiting for the nurse` + (D.recheck.length ? `, ${D.recheck.length} to see again` : "")
      : n === 4 ? `${many(D.line.length, "patient")} waiting` + (D.line.length ? `, the longest for ${Math.max(...D.line.map((p) => p.waited))} min` : "")
      : n === 5 ? duty() : n === 6 && S.team ? `${many(S.team.waiting, "patient")} with the team` : "";
    const said = (id) => ward ? [names[id], tally(+id)] : [names[id], subs[id]].concat(+id === 5 ? [who.textContent] : []);
    const stage = (n) => ward ? "" : n === S.place ? "You are here" : n === 6 ? "" : (S.place === 6 ? n <= 3 : n < S.place) ? "Done"
      : S.place < 5 && n === S.place + 1 ? "Next" : "Later";
    const frameOf = (id) => svg.querySelector(`.mp-frame[data-room="${id}"]`);
    const light = () => {
      for (const id of Object.keys(signs)) {
        const n = +id, done = n <= 5 && (S.place === 6 ? n <= 3 : n < S.place), no = signs[id].querySelector(".mp-no");
        signs[id].classList.toggle("on", n === S.place); signs[id].classList.toggle("done", done);
        frameOf(id).classList.toggle("on", n === S.place);
        if (n === 5) who.classList.toggle("on", n === S.place);
        if (no) no.textContent = done ? "✓" : id;
        hits[id].setAttribute("aria-label", said(n).concat(stage(n)).filter(Boolean).join(". "));
      }
      if (S.hover) point(S.hover);                                                             // an open note follows the visit
    };
    const doctorLine = document.getElementById("doctor-line");
    const whoCalled = () => {                                 // "Dr. Novak, Room 19" goes on the doctor rooms' own plate
      const text = doctorLine ? doctorLine.textContent.trim() : "";
      who.textContent = text; who.hidden = !text;
      light(); people();
    };

    // ---- pointing at a room (or tapping it, or reaching it with the keyboard) lights it, shows the way there
    // from where "YOU" stands, and opens a small note: its name, what happens there, and where it is in the visit.
    // The six rooms are one stop for the Tab key; the arrow keys go from room to room, Escape closes the note.
    const tip = make("div", "mp-tip");
    tip.hidden = true; tip.setAttribute("aria-hidden", "true");
    let pinned = 0, kept = "";                                // the room, or the person, whose note was tapped open
    function lead() {                                         // the moving dots: the way to the room that is pointed at
      const legs = S.hover && !ward ? pieces(S.walk ? S.walk.to : S.at, S.hover) : [];
      for (const w of WAY) {
        const leg = legs.find((l) => l.way.id === w.id);
        leads[w.id].classList.toggle("show", Boolean(leg)); leads[w.id].classList.toggle("back", Boolean(leg && leg.back));
      }
    }
    function point(n) {                                       // n: the room, or 0 for none
      S.hover = n; S.note = ""; lead();
      for (const id of Object.keys(folk)) folk[id].hit.classList.remove("open");
      for (const id of Object.keys(hits)) {
        hits[id].classList.toggle("open", +id === n);
        frameOf(id).classList.toggle("lit", +id === n);
      }
      if (!n) { tip.hidden = true; return; }
      const r = ROOM[n], [name, ...more] = said(n), where = stage(n);
      tip.textContent = "";
      tip.append(make("b", "", (n <= 5 ? n + " · " : "") + name), make("span", "", more.filter(Boolean).join(". ")));
      if (where) tip.appendChild(make("em", "", where));
      tip.hidden = false;
      const mid = (r.x + r.w / 2) / W * wrap.clientWidth, half = tip.offsetWidth / 2 + 4;
      tip.style.left = Math.max(half, Math.min(wrap.clientWidth - half, mid)) + "px";
      // The note goes under a room in the top half and over a room in the bottom half; in a tall room it goes
      // into the room's lower part. It is never over "YOU" and never off the plan.
      const under = () => { tip.style.top = (r.y + r.h + 10) / H * 100 + "%"; tip.style.bottom = "auto"; };
      if (r.h >= 180) { tip.style.top = "auto"; tip.style.bottom = (H - r.y - r.h + 10) / H * 100 + "%"; }
      else if (r.y + r.h / 2 < H / 2) under();
      else {
        tip.style.top = "auto"; tip.style.bottom = (H - r.y + 10) / H * 100 + "%";
        if (tip.offsetTop < 2) under();
      }
      if (ward) return;
      // if the note would lie over "YOU", it slides sideways off them, to whichever side has the room
      const a = tip.getBoundingClientRect(), b = dot.getBoundingClientRect(), c = tag.getBoundingClientRect(), edge = wrap.getBoundingClientRect();
      const left = Math.min(b.left, c.left), right = Math.max(b.right, c.right);
      if (!(a.right < left - 3 || a.left > right + 3 || a.bottom < b.top - 3 || a.top > c.bottom + 3)) {
        if (left - edge.left - 8 >= a.width) tip.style.left = left - edge.left - 6 - a.width / 2 + "px";
        else if (edge.right - right - 8 >= a.width) tip.style.left = right - edge.left + 6 + a.width / 2 + "px";
      }
    }
    for (const id of Object.keys(ROOM)) {
      const n = +id, r = ROOM[id], hit = make("button", "mp-hit");
      hit.type = "button"; hit.tabIndex = n === 1 ? 0 : -1;
      Object.assign(hit.style, { left: r.x / W * 100 + "%", top: r.y / H * 100 + "%", width: r.w / W * 100 + "%", height: r.h / H * 100 + "%" });
      hit.addEventListener("pointerenter", (e) => { if (e.pointerType !== "touch") point(n); });
      hit.addEventListener("pointerleave", (e) => { if (e.pointerType !== "touch") rest(); });
      hit.addEventListener("focus", () => point(n));
      hit.addEventListener("blur", () => { if (pinned === n) pinned = 0; rest(); });
      hit.addEventListener("click", () => { kept = ""; pinned = pinned === n ? 0 : n; point(pinned); });
      hit.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { pinned = 0; kept = ""; point(0); return; }
        const move = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
        if (!move) return;
        e.preventDefault();
        const next = hits[(n - 1 + move + 6) % 6 + 1];
        hit.tabIndex = -1; next.tabIndex = 0; next.focus();
      });
      wrap.appendChild(hit); hits[id] = hit;
    }

    // ---- the people. Each of them has a round button over them: pointing at it, tapping it or reaching it with
    // the keyboard opens their note. They are one stop for the Tab key; the arrow keys go from person to person.
    const move = (id, at) => { folk[id].at = at; folk[id].hit.style.left = at[0] / W * 100 + "%"; folk[id].hit.style.top = at[1] / H * 100 + "%"; };
    const rest = () => { if (kept && !folk[kept].hit.hidden) tell(kept); else { kept = ""; point(pinned); } };   // back to the note that was tapped open
    function tell(id) {
      point(0);                                               // one note at a time
      S.note = id; folk[id].hit.classList.add("open");
      const { title, lines, last } = folk[id].says();
      tip.textContent = "";
      tip.appendChild(make("b", "", title));
      for (const line of lines.filter(Boolean)) tip.appendChild(make("span", "", line));
      if (last) tip.appendChild(make("em", "", last));
      tip.hidden = false;
      // over the person where there is room, under them where there is not; never off the plan
      const w = wrap.clientWidth, h = wrap.clientHeight, half = tip.offsetWidth / 2 + 4, gap = folk[id].hit.offsetHeight / 2 + 6;
      const x = folk[id].at[0] / W * w, y = folk[id].at[1] / H * h;
      tip.style.left = Math.max(half, Math.min(w - half, x)) + "px";
      if (y - gap - tip.offsetHeight > 2) { tip.style.top = "auto"; tip.style.bottom = h - y + gap + "px"; }
      else { tip.style.bottom = "auto"; tip.style.top = y + gap + (id === "you" ? tag.offsetHeight + 4 : 0) + "px"; }
      if (Date.now() - loaded > 8000) load();                 // the staff's numbers are never old for long
    }
    function person_(id, at, says, big, reg) {                // reg (staff only): the visit whose card a tap opens
      const hit = make("button", "mp-face" + (big ? " big" : ""));
      hit.type = "button"; hit.tabIndex = -1; hit.hidden = true;
      folk[id] = { at, hit, says };
      move(id, at);
      hit.addEventListener("pointerenter", (e) => { if (e.pointerType !== "touch") tell(id); });
      hit.addEventListener("pointerleave", (e) => { if (e.pointerType !== "touch") rest(); });
      hit.addEventListener("focus", () => tell(id));
      hit.addEventListener("blur", () => { if (kept === id) kept = ""; rest(); });
      hit.addEventListener("click", () => { pinned = 0; kept = kept === id ? "" : id; rest();
        const visit = reg && reg();
        if (visit) document.dispatchEvent(new CustomEvent("meddy-open", { detail: { reg: visit } })); });
      hit.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { pinned = 0; kept = ""; point(0); return; }
        const go = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
        if (!go) return;
        e.preventDefault();
        const seen = Object.keys(folk).filter((k) => !folk[k].hit.hidden), next = folk[seen[(seen.indexOf(id) + go + seen.length) % seen.length]].hit;
        hit.tabIndex = -1; next.tabIndex = 0; next.focus();
      });
      wrap.appendChild(hit);
    }
    const busy = (n) => n ? `With ${n} patient${n === 1 ? "" : "s"} now.` : "Free now.";
    if (!ward) {
      // "YOU": the patient's own visit, as the page keeps it (window.MeddyVisit in index.html)
      person_("you", SPOT[1], () => {
        const mine = typeof window.MeddyVisit === "function" ? window.MeddyVisit() : {};
        return { title: "You" + (mine.name ? " · " + mine.name : ""),
                 lines: [mine.reg && "Registration no. " + mine.reg, mine.ticket && "Ticket " + mine.ticket,
                         "Now at: " + said(S.place)[0] + (S.place === 5 && who.textContent ? ", " + who.textContent : "") + "."],
                 last: "Only you and the staff see your details." }; }, true);
      // the staff. A patient's phone gets no numbers about them: only who they are, and what the waiting room's
      // board shows everyone (which ticket a doctor called, and when)
      const ago = (n) => n < 1 ? "just now" : n === 1 ? "1 minute ago" : `${n} minutes ago`;
      person_("nurse", NURSE, () => ({ title: "Triage nurse", lines: ["Checks every patient before a doctor: blood pressure, pulse, temperature.",
        S.place === 3 ? "You are waiting for the nurse now." : ""], last: "" }));
      CUBICLE.forEach((c, i) => person_("doc" + i, c.doctor, () => { const d = S.doctors[i] || {};
        return { title: d.doctor || "Doctor", lines: [d.room, d.ticket ? `Called ticket ${d.ticket} ${ago(d.minutes_ago)}.` : ""], last: d.mine ? "Your doctor." : "" }; }));
      person_("team", RESUS.team, () => ({ title: "Resuscitation team", lines: ["Always on duty · Resus bay"], last: "" }));
      for (let i = 0; i < 12; i++)
        person_("seat" + i, [SEAT_X[i % 6], SEAT_Y[Math.floor(i / 6)]], () => ({ title: "Another patient", lines: ["Waiting to be called."], last: PRIVATE }));
    } else {
      // The staff's view. A patient's note: ticket or code, name, complaint, level, and what the nurse should know.
      const flags = (p) => [p.with_nurse && "With the nurse for a re-check.", p.worse && "Says they feel worse.", p.recheck_due && "Re-check due.",
                            p.danger && "Danger sign."].filter(Boolean).join(" ");
      const about = (id, more, open) => { const p = D.now[id];
        return !p ? { title: "Nobody here", lines: [], last: "" }
          : p.unknown ? { title: "A patient", lines: [...more(p), "Not one of this screen's patients: their card is on the doctor's screen."], last: "" }
          : { title: [p.ticket || p.code, p.patient || "No name given"].filter(Boolean).join(" · "),
              lines: [p.who || p.complaint, p.level ? `Level ${p.level}: ${LEVEL[p.level][1]}.` : "No level yet: not measured.", ...more(p), flags(p)],
              last: open ? "Tap to open their card." : "" }; };
      const visit = (id) => () => D.now[id] && D.now[id].reg_no;
      person_("nurse", NURSE, () => ({ title: "Triage nurse", lines: [`${many(D.nurse.length, "patient")} waiting from check-in.`,
        D.recheck.length ? `${many(D.recheck.length, "patient")} in the waiting room to see again.` : "Nobody to see again."], last: "" }));
      QUEUE.forEach((at, i) => person_("queue" + i, at, () => about("queue" + i, (p) => [
        [p.age !== null && p.age !== undefined && `${Math.round(p.age)} years`, p.sex].filter(Boolean).join(", "), `Checked in ${p.waited} min ago.`], true), false, visit("queue" + i)));
      CUBICLE.forEach((c, i) => {
        person_("doc" + i, c.doctor, () => { const d = S.doctors[i] || {}, list = d.list || [];
          return { title: d.name || "Doctor", lines: [[d.department, d.room].filter(Boolean).join(" · "), busy(d.waiting || 0),
                   d.queue ? `${many(d.queue, "patient")} in the line chosen for this doctor.` : "",
                   d.free_in_minutes !== null && d.free_in_minutes !== undefined ? `Free in about ${d.free_in_minutes} min.` : "",
                   list.length ? "From this screen: " + list.map((p) => p.ticket || p.code).join(", ") + "." : ""], last: "" }; });
        person_("bed" + i, c.bed, () => { const d = S.doctors[i] || {};
          return about("bed" + i, () => [`With ${d.name}, ${d.room}.`, d.waiting > 1 && `${many(d.waiting - 1, "more patient")} with this doctor.`], false); });
      });
      person_("team", RESUS.team, () => ({ title: "Resuscitation team", lines: ["Always on duty · Resus bay", busy(S.team ? S.team.waiting : 0)], last: "" }));
      for (const id of ["resusbed1", "resusbed"])
        person_(id, id === "resusbed" ? RESUS.bed : SPOT[6], () => about(id, () => ["With the resuscitation team."], false));
      for (let i = 0; i < 12; i++)
        person_("seat" + i, [SEAT_X[i % 6], SEAT_Y[Math.floor(i / 6)]], () => about("seat" + i, (p) => [`Place ${p.position} in line, waited ${p.waited} min.`], true),
                false, visit("seat" + i));
    }
    wrap.appendChild(tip);
    document.addEventListener("pointerdown", (e) => { if (!wrap.contains(e.target) && (pinned || kept)) { pinned = 0; kept = ""; point(0); } });

    // Who is on the plan right now, from the page's own plan (seats) and from the staff table.
    // Three doctor's rooms are drawn, so three of the doctors on duty are shown: the first by room number, and,
    // once the patient has been called, their own doctor in the middle room with them.
    const figure = (id) => id.startsWith("seat") ? chairs[+id.slice(4)] : svg.querySelector(`.mp-spot[data-who="${id}"]`);
    const show = (id, on) => { folk[id].hit.hidden = !on;
      const drawn = figure(id); if (drawn) drawn.classList.toggle(id.startsWith("seat") ? "taken" : "in", on); };
    const number = (d) => parseInt(d.room.replace(/\D+/g, ""), 10) || 0;
    // Staff only. The three doctor's rooms show the doctors on duty who have the most patients now.
    const busiest = () => onDuty().sort((a, b) => b.waiting - a.waiting || number(a) - number(b)).slice(0, 3);
    function wardNow() {
      // a patient takes a place: the colour of their level on their shoulders, a ring when the nurse should look
      const put_ = (id, p) => { D.now[id] = p || null; show(id, Boolean(p));
        if (!p) return;
        figure(id).querySelector(".mp-top").style.fill = p.level && LEVEL[p.level] ? LEVEL[p.level][0] : UNMEASURED;
        folk[id].hit.classList.toggle("alert", Boolean(p.worse || p.danger));
        folk[id].hit.classList.toggle("due", Boolean(p.recheck_due) && !(p.worse || p.danger)); };
      SIT.forEach((seat, i) => put_("seat" + seat, D.line[i]));
      QUEUE.forEach((at, i) => put_("queue" + i, D.nurse[i]));
      // Who is with a doctor: the count is the staff table's. The patients of this screen among them are known
      // by name (today's patients say "With Dr. Petrov, Room 5"); anyone else is drawn without a name.
      const withThem = (d) => D.today.filter((p) => p.where === `With ${d.name}, ${d.room}`);
      const onBed = (d, list, k) => list[k] || (d.waiting > k ? { unknown: true } : null);
      const shown = staff ? busiest() : [];
      CUBICLE.forEach((c, i) => { const d = shown[i], list = d ? withThem(d) : [];
        S.doctors[i] = d ? { ...d, list } : null;
        show("doc" + i, Boolean(d)); put_("bed" + i, d ? onBed(d, list, 0) : null); });
      S.team = staff ? staff.doctors.find((d) => d.reg_no === TEAM) || null : null;
      const critical = S.team ? withThem(S.team) : [];
      show("team", Boolean(S.team)); put_("resusbed1", S.team ? onBed(S.team, critical, 0) : null); put_("resusbed", S.team ? onBed(S.team, critical, 1) : null);
      show("nurse", Boolean(staff));
      if (staff) {
        counts[3].textContent = D.nurse.length || ""; counts[4].textContent = D.line.length || "";
        counts[5].textContent = Math.max(0, staff.visits["with doctor"] - (S.team ? S.team.waiting : 0)) || "";
        counts[6].textContent = (S.team && S.team.waiting) || "";
      }
      light();
    }
    function people() {
      if (ward) wardNow(); else here();
      for (const id of Object.keys(folk)) { const { title, lines, last } = folk[id].says();
        folk[id].hit.setAttribute("aria-label", [title, ...lines, last].filter(Boolean).map((text) => text.replace(/\.$/, "")).join(". ") + "."); }
      const seen = Object.keys(folk).filter((k) => !folk[k].hit.hidden), first = seen[0];
      for (const k of Object.keys(folk)) if (folk[k].hit.hidden) folk[k].hit.tabIndex = -1;
      if (first && !seen.some((k) => folk[k].hit.tabIndex === 0)) folk[first].hit.tabIndex = 0;
      if (S.note) { if (folk[S.note].hit.hidden) rest(); else tell(S.note); }      // an open note follows the numbers
      else if (S.hover) point(S.hover);
    }
    function here() {
      const taken = seats.querySelectorAll(".seat.taken").length;
      SIT.forEach((seat, i) => { chairs[seat].classList.toggle("taken", i < taken); show("seat" + seat, i < taken); });
      // Three doctor's rooms: the doctors who called a ticket in the last minutes, newest first, as the waiting
      // room's board has them; and, once the patient has been called, their own doctor in the middle room with them.
      const calls = [];
      for (const call of board ? board.called : []) if (!calls.some((k) => k.room === call.room)) calls.push(call);
      const [name, room] = who.textContent.split(", ");
      const mine = S.place === 5 && name && room ? { doctor: name, room, mine: true } : null;
      const others = calls.filter((call) => !mine || call.room !== room), shown = mine ? [others[0], mine, others[1]] : others.slice(0, 3);
      CUBICLE.forEach((c, i) => { S.doctors[i] = shown[i] || null; show("doc" + i, Boolean(shown[i])); });
      show("team", true); show("nurse", true); show("you", true);
    }
    // The patient's page asks for the waiting room's public board only. The nurse's screen asks for the staff
    // table and her own lists.
    async function load() {
      loaded = Date.now();
      const get = async (path) => { const answer = await fetch(path); if (!answer.ok) throw new Error(path); return answer.json(); };
      try {
        if (!ward) { board = await get("/api/board"); people(); return; }
        const [table, line, list, today] = await Promise.all([get("/api/staff"), get("/api/line"), get("/api/nurse/patients"),
                                                              get("/api/nurse/today").catch(() => ({ patients: [] }))]);
        staff = table; D.line = line.patients; D.nurse = list.patients; D.recheck = list.recheck || [];
        D.today = today.patients.filter((p) => p.status === "with doctor");
        people();
      } catch (e) {}
    }

    // ---- what the page's own plan says: which room is lit, where "YOU" stands, how many seats are taken
    const lit = () => { for (let i = 1; i <= 6; i++) if (document.getElementById("room-" + i).classList.contains("on")) return i; return 0; };
    const where = () => {
      const m = /translate\(\s*([-\d.]+)[\s,]+([-\d.]+)/.exec(you.getAttribute("transform") || "");
      return (m && AT[m[1] + "," + m[2]]) || lit() || 1;
    };
    const chairs = svg.querySelectorAll(".mp-seat");
    if (ward) {                                               // the staff's view follows the lists, not a visit
      S.place = 0; light(); load();
      setInterval(() => { if (!document.hidden) load(); }, 6000);
      return;
    }
    const born = performance.now();
    S.place = lit() || 1;
    goTo(where(), true); whoCalled(); load();
    setInterval(() => { if (!document.hidden) load(); }, 20000);
    new MutationObserver(() => goTo(where(), performance.now() - born < 1500))               // a reload does not replay the walk
      .observe(you, { attributes: true, attributeFilter: ["transform"] });
    const rooms = new MutationObserver(() => { const id = lit(); if (id && id !== S.place) { S.place = id; light(); people(); } });
    for (let i = 1; i <= 6; i++) rooms.observe(document.getElementById("room-" + i), { attributes: true, attributeFilter: ["class"] });
    new MutationObserver(people).observe(seats, { childList: true });
    if (doctorLine) new MutationObserver(whoCalled).observe(doctorLine, { childList: true, characterData: true, subtree: true });

    old.classList.add("mp-off");
    const main = old.closest("main");
    if (main) main.classList.add("mp-side");                  // the plan sits to one side (meddy-site.css)
  }

  if (document.getElementById("you") || document.readyState !== "loading") build();
  else document.addEventListener("DOMContentLoaded", build);
})();
