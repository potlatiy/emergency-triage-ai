"""Attack a fresh copy of the hospital server and grade what holds.

    python security_audit.py            prints the report
    python security_audit.py --write    also saves it as SECURITY_AUDIT.md

It starts its own server on a spare port with a throwaway database, a test staff PIN and no language model, then
plays the attacker over real HTTP: floods, guessing, other patients' visits, oversized and malformed requests,
and changes made straight in the database file. Nothing outside this computer is contacted.

Every check is a sentence that should be true of a safe site. A check passes or fails; nothing is skipped.
The checks marked "known gap" are things we already know Meddy does not do (they are listed in SECURITY.md).
They stay in the audit and count against the grade, so the grade is not a list of only what works.

Weights are our own judgement:  3 = a patient could be harmed or their data exposed,
                                2 = the service could be disturbed or the staff misled,
                                1 = extra hardening.
Grade = weight of the passed checks / weight of all checks.  A from 90%, B from 80%, C from 70%, D from 60%, else F.
"""

import hashlib
import hmac
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "hospital"))
import guard  # noqa: E402  (no database behind it: the limits and the content policy)

STAFF_PIN, CHIEF_PIN = "4821", "9173"                   # test PINs for the audit's own server only
BUILT_IN_LOG_KEY = b"meddy: the key every copy of this code has"       # the same constant as in hospital/db.py
CHECKS = []                                             # (area, weight, title, known gap?, run last?, function)
BASE = ""
DATABASE = ""
_address = 0


def check(area, weight, title, known_gap=False, last=False):
    """last: the check uses up the limit for everybody, so it runs after all the others (the report keeps the
    order they are written in)."""
    def keep(function):
        CHECKS.append((area, weight, title, known_gap, last, function))
        return function
    return keep


def new_address():
    """A public-looking address nobody has used yet in this audit. The audit's server stands behind a pretend
    proxy (MEDDY_PROXY=1), so the last address in X-Forwarded-For is taken as the caller's, as it is online."""
    global _address
    _address += 1
    return f"93.184.{_address // 250}.{_address % 250 + 1}"


def call(method, path, body=None, address=None, headers=None, cookie=None, raw=None):
    """One request. Returns (status, headers, body as JSON when it is JSON, else as text)."""
    send = dict(headers or {})
    if address:
        send["X-Forwarded-For"] = address
    if cookie:
        send["Cookie"] = cookie
    data = raw
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        send.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(BASE + path, data=data, method=method, headers=send)
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            status, got, text = answer.status, answer.headers, answer.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        status, got, text = error.code, error.headers, error.read().decode("utf-8", "replace")
    try:
        return status, got, json.loads(text)
    except ValueError:
        return status, got, text


def sign_in(pin=STAFF_PIN, role="staff", address=None):
    """The cookie a staff screen gets for the right PIN, or None."""
    status, headers, _ = call("POST", "/api/staff/login", {"pin": pin, "role": role}, address or new_address())
    cookies = headers.get_all("Set-Cookie") or []
    return "; ".join(c.split(";")[0] for c in cookies) if status == 200 and cookies else None


def register(address=None, complaint="sore throat since yesterday", **more):
    body = {"complaint": complaint, "age": 30, "sex": "Female", "use_model": False, **more}
    return call("POST", "/api/register", body, address or new_address())


def waiting_patient(staff):
    """A patient registered on a phone of their own and brought into the waiting room by the nurse.
    Returns (registration number, the phone's key, the phone's address)."""
    phone = new_address()
    _, _, arrival = register(phone)
    reg, key = arrival["reg_no"], arrival["key"]
    call("POST", "/api/checkin", {"reg_no": reg, "arrival": "Walked in", "pain": 3, "answers": []}, phone,
         {"X-Meddy-Visit": key})
    call("POST", "/api/triage", {"reg_no": reg}, cookie=staff)
    call("POST", "/api/line/join", {"reg_no": reg, "level": 4, "review": "checked"}, cookie=staff)
    return reg, key, phone


# ================================================================ 1. spam and flooding

@check("Spam and flooding", 3, "One connection cannot fill the nurse's list: the 11th registration in a minute is refused")
def _():
    spammer = new_address()
    codes = [register(spammer)[0] for _ in range(13)]
    return codes == [200] * 10 + [429] * 3, f"13 registrations: {codes.count(200)} accepted, {codes.count(429)} refused"


@check("Spam and flooding", 3, "Writing a false address in front of the request does not make the caller somebody new")
def _():
    spammer = new_address()
    for _ in range(10):
        register(spammer)
    status, _, _ = register(f"8.8.8.8, 1.2.3.4, {spammer}")
    return status == 429, f"with two made-up addresses in front: {status}"


@check("Spam and flooding", 2, "A second address line in the request does not make the caller somebody new either")
def _():
    import http.client
    spammer, codes = new_address(), []
    host, port = BASE.replace("http://", "").split(":")
    for n in range(13):
        line = http.client.HTTPConnection(host, int(port), timeout=20)
        line.putrequest("POST", "/api/register")
        body = json.dumps({"complaint": "sore throat", "age": 30, "use_model": False}).encode()
        for name, value in (("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                            ("X-Forwarded-For", f"7.7.7.{n + 1}"), ("X-Forwarded-For", spammer)):
            line.putheader(name, value)
        line.endheaders(body)
        codes.append(line.getresponse().status)
        line.close()
    return codes == [200] * 10 + [429] * 3, f"13 registrations, each with its own made-up first line: {codes.count(200)} accepted"


@check("Spam and flooding", 3, "Requests that do nothing cannot close registration or \"I feel worse\" for everybody")
def _(staff):
    reg, key, phone = waiting_patient(staff)
    sent = 0
    for _ in range(100):                                    # a script behind ever new addresses, sending nothing real
        who = new_address()
        sent += 3
        call("POST", "/api/register", {}, who)
        call("POST", "/api/worse", {"reg_no": "nope"}, who)
        call("POST", "/api/checkin", {"reg_no": "nope"}, who)
    worse = call("POST", "/api/worse", {"reg_no": reg}, phone, {"X-Meddy-Visit": key})[0]
    told = call("GET", "/api/me/" + reg, address=phone, headers={"X-Meddy-Visit": key})[2].get("worse")
    new = register(new_address())[0]
    return (worse, told, new) == (200, True, 200), \
        f"after {sent} such requests: a real patient's \"I feel worse\": {worse} (nurse told: {told}); a new patient registers: {new}"


@check("Spam and flooding", 2, "A refused caller is told how long to wait, and another patient is not affected")
def _():
    spammer = new_address()
    for _ in range(10):
        register(spammer)
    status, headers, body = register(spammer)
    wait = headers.get("Retry-After")
    other = register(new_address())[0]
    return status == 429 and wait and 1 <= int(wait) <= 61 and other == 200, \
        f"refused with Retry-After {wait} s; a patient at another address: {other}"


@check("Spam and flooding", 2, "A request far larger than any questionnaire is refused before it is read")
def _():
    big = call("POST", "/api/register", raw=b"x" * 70000, address=new_address(), headers={"Content-Type": "application/json"})[0]
    return big == 413, f"70,000 bytes: {big}"


@check("Spam and flooding", 3, "A connection that keeps being refused is shut out of registering, and told to go to the desk")
def _(staff):
    reg, key, _ = waiting_patient(staff)
    spammer = new_address()
    codes = [call("POST", "/api/worse", {"reg_no": reg}, spammer)[0] for _ in range(40)]
    status, headers, body = register(spammer)
    told = isinstance(body, dict) and "staff" in str(body.get("detail", ""))
    return status == 429 and int(headers.get("Retry-After", 0)) > 120 and told, \
        f"after {codes.count(403) + codes.count(429)} refusals: registering gives {status}, wait {headers.get('Retry-After')} s"


@check("Spam and flooding", 2, "A blocked phone can still see its own place in line, and other patients are not blocked")
def _(staff):
    reg, key, phone = waiting_patient(staff)
    for _ in range(40):
        call("POST", "/api/worse", {"reg_no": "P-2026-9999"}, phone, {"X-Meddy-Visit": "x"})
        call("POST", "/api/register", raw=b"x" * 70000, address=phone, headers={"Content-Type": "application/json"})
    blocked = register(phone)[0]
    place = call("GET", "/api/me/" + reg, address=phone, headers={"X-Meddy-Visit": key})[0]
    other = register(new_address())[0]
    return blocked == 429 and place == 200 and other == 200, f"blocked phone registers: {blocked}; reads its place: {place}; another patient: {other}"


@check("Spam and flooding", 2, "Signed-in staff are never limited, even on an address that is blocked")
def _(staff):
    shared = new_address()                                   # the hospital's Wi-Fi: a spamming phone and the nurse
    for _ in range(35):
        call("POST", "/api/register", raw=b"x" * 70000, address=shared, headers={"Content-Type": "application/json"})
    stranger = register(shared)[0]
    codes = [call("POST", "/api/register", {"complaint": "walk-in at the desk", "age": 50, "use_model": False},
                  shared, cookie=staff)[0] for _ in range(12)]
    return stranger == 429 and codes == [200] * 12, f"a phone on that address: {stranger}; the nurse, 12 registrations: {codes.count(200)} accepted"


@check("Spam and flooding", 2, "Under a flood from many addresses, each single address gets a smaller share", last=True)
def _():
    sent = 0                    # the registrations so far in this audit are topped up to half of the limit for everybody
    while sent < 60:
        probe = new_address()
        codes = [register(probe)[0] for _ in range(4)]
        sent += 4
        if codes == [200, 200, 200, 429]:
            return True, "once half of the limit for everybody was used: 3 registrations a minute for one address instead of 10"
        if 200 not in codes:
            break
    return False, "the share of one address never went down"


@check("Spam and flooding", 2, "A flood from a great many addresses is held to the limit for everybody", last=True)
def _():
    codes = []
    for _ in range(120):
        codes.append(register(new_address())[0])
        if codes[-3:] == [429, 429, 429]:
            break
    return codes[-1] == 429, f"registrations from ever new addresses stopped being accepted after {codes.count(200)} more"


@check("Spam and flooding", 2, "A real patient can still register on the phone while such a flood lasts", known_gap=True, last=True)
def _():
    status = register(new_address())[0]
    return status == 200, f"a new patient during the flood: {status} (they must register at the desk)"


@check("Spam and flooding", 1, "The language model takes two questions at a time; a third does not wait in a queue")
def _():
    try:
        with guard.model_seat(), guard.model_seat():
            try:
                with guard.model_seat():
                    return False, "a third seat was given"
            except guard.ModelBusy:
                return True, "the third caller is turned away at once and gets the standard questions"
    except Exception as error:                              # pragma: no cover
        return False, repr(error)


# ================================================================ 2. other patients' visits

@check("Other patients' visits", 3, "Nobody but the patient's own phone can press \"I feel worse\" for a visit")
def _(staff):
    reg, key, phone = waiting_patient(staff)
    stranger = call("POST", "/api/worse", {"reg_no": reg}, new_address())[0]
    guess = call("POST", "/api/worse", {"reg_no": reg}, new_address(), {"X-Meddy-Visit": "A" * len(key)})[0]
    told = call("GET", "/api/me/" + reg, address=phone, headers={"X-Meddy-Visit": key})[2].get("worse")
    own = call("POST", "/api/worse", {"reg_no": reg}, phone, {"X-Meddy-Visit": key})[0]
    return (stranger, guess, told, own) == (403, 403, False, 200), \
        f"a stranger: {stranger}; a guessed key: {guess}; nurse called for nothing: {told}; the own phone: {own}"


@check("Other patients' visits", 3, "Nobody but the patient's own phone can finish their check-in")
def _():
    phone = new_address()
    arrival = register(phone)[2]
    body = {"reg_no": arrival["reg_no"], "arrival": "Walked in", "pain": 10, "answers": []}
    stranger = call("POST", "/api/checkin", body, new_address())[0]
    still = call("GET", "/api/me/" + arrival["reg_no"], address=phone)[2].get("status")
    own = call("POST", "/api/checkin", body, phone, {"X-Meddy-Visit": arrival["key"]})[0]
    return (stranger, still, own) == (403, "registered", 200), f"a stranger: {stranger}; the visit stayed '{still}'; the own phone: {own}"


@check("Other patients' visits", 3, "The doctor's note reaches only the phone that registered the visit")
def _(staff):
    phone = new_address()
    arrival = register(phone)[2]
    reg, key = arrival["reg_no"], arrival["key"]
    call("POST", "/api/triage", {"reg_no": reg}, cookie=staff)
    call("POST", "/api/refer", {"reg_no": reg, "level": 3, "reason": "Looks unwell", "doctor_reg": "DR-1001"}, cookie=staff)
    note = "Rest and fluids. Come back if the fever returns."
    call("POST", "/api/doctor/finish", {"reg_no": reg, "note": note}, cookie=staff)
    stranger = call("GET", "/api/me/" + reg, address=new_address())[2].get("note")
    guess = call("GET", "/api/me/" + reg, address=new_address(), headers={"X-Meddy-Visit": "a-guess"})[2].get("note")
    own = call("GET", "/api/me/" + reg, address=phone, headers={"X-Meddy-Visit": key})[2].get("note")
    return stranger is None and guess is None and own == note, \
        f"a stranger gets {stranger!r}, a guessed key {guess!r}, the own phone the note"


@check("Other patients' visits", 3, "What any phone can read about a visit holds no name, no complaint and no urgency level")
def _(staff):
    name = "Zebedee"
    phone = new_address()
    arrival = register(phone, complaint="crushing chest pain", first_name=name, last_name="Quillfeather")[2]
    reg = arrival["reg_no"]
    call("POST", "/api/checkin", {"reg_no": reg, "arrival": "Walked in", "pain": 9, "answers": []}, phone,
         {"X-Meddy-Visit": arrival["key"]})
    call("POST", "/api/triage", {"reg_no": reg}, cookie=staff)
    call("POST", "/api/line/join", {"reg_no": reg, "level": 2, "review": "checked"}, cookie=staff)
    seen = [json.dumps(call("GET", path, address=new_address())[2])
            for path in ("/api/me/" + reg, "/api/board", "/api/waiting-room", "/api/version")]
    seen.append(json.dumps(arrival))
    text = " ".join(seen).lower()
    leaks = [word for word in (name.lower(), "quillfeather", "crushing", '"level"', "ai_level", "urgent") if word in text]
    return not leaks, "nothing of the kind in /api/me, the board, the waiting room or the registration answer" if not leaks else f"found: {leaks}"


@check("Other patients' visits", 3, "Nobody else can read the follow-up questions, which repeat the patient's complaint")
def _():
    phone = new_address()
    arrival = register(phone, complaint="crushing chest pain")[2]
    path = "/api/follow-up/" + arrival["reg_no"]
    stranger = call("GET", path, address=new_address())
    guess = call("GET", path, address=new_address(), headers={"X-Meddy-Visit": "a-guess"})[0]
    own = call("GET", path, address=phone, headers={"X-Meddy-Visit": arrival["key"]})[0]
    return (stranger[0], guess, own) == (403, 403, 200) and "crushing" not in json.dumps(stranger[2]), \
        f"a stranger: {stranger[0]}; a guessed key: {guess}; the own phone: {own}"


@check("Other patients' visits", 2, "Typing somebody's insurance card number does not give away their visit")
def _():
    card = "KV-7731-0042"
    owner = register(new_address(), insurance="public", card=card)[2]
    status, _, body = register(new_address(), insurance="public", card=card)
    return status == 409 and owner["reg_no"] not in json.dumps(body), \
        f"the answer says the card has an open visit ({status}) and names no registration number"


@check("Other patients' visits", 2, "A stranger who knows a card number cannot register with it and keep its owner out", known_gap=True)
def _():
    card = "KV-9914-2200"
    stranger = register(new_address(), insurance="public", card=card)[0]
    owner = register(new_address(), insurance="public", card=card)[0]
    return owner == 200, f"the stranger registered first ({stranger}); the card's owner then gets {owner} until staff close that visit"


@check("Other patients' visits", 2, "The patient's phone is never sent a department", known_gap=True)
def _():
    arrival = register(new_address(), complaint="crushing chest pain")[2]
    named = [d for d in ("Cardiology", "Respiratory medicine", "Neurology") if d in json.dumps(arrival)]
    return not named, f"the registration answer names the department the key-word rule guessed ({', '.join(named)}); " \
        "the page uses it only to choose the comfort tips and never shows it" if named else "no department in the answer"


@check("Other patients' visits", 2, "Somebody who guesses a registration number learns nothing about that visit", known_gap=True)
def _(staff):
    reg, _, _ = waiting_patient(staff)
    status, _, body = call("GET", "/api/me/" + reg, address=new_address())
    shown = [k for k in ("ticket", "position", "room", "doctor") if isinstance(body, dict) and body.get(k) is not None]
    return status != 200, f"a stranger sees the visit's {', '.join(shown) or 'status'} (no name, complaint or level)"


# ================================================================ 3. staff screens

STAFF_ONLY = [("GET", "/api/line"), ("GET", "/api/nurse/patients"), ("GET", "/api/nurse/today"), ("GET", "/api/staff"),
              ("GET", "/api/events"), ("GET", "/api/doctor/DR-1001/patients"), ("GET", "/api/visit/P-2026-0001/history"),
              ("GET", "/api/nurse/patient/P-2026-0001"), ("POST", "/api/triage"), ("POST", "/api/line/join"),
              ("POST", "/api/line/next"), ("POST", "/api/refer"), ("POST", "/api/doctor/finish"), ("POST", "/api/reset"),
              ("POST", "/api/nurse/remove"), ("POST", "/api/staff/simulation"), ("POST", "/api/line/wait")]


@check("Staff screens", 3, "Without the staff PIN, no staff address gives any data or accepts any action")
def _():
    opened = [f"{method} {path}" for method, path in STAFF_ONLY
              if call(method, path, {} if method == "POST" else None, new_address())[0] != 401]
    return not opened, f"{len(STAFF_ONLY)} staff addresses tried: all refused" if not opened else f"open: {opened}"


@check("Staff screens", 3, "The chief doctor's panel and \"Erase everything\" need the chief's own PIN")
def _(staff):
    nobody = [call("GET", "/api/chief/report", address=new_address())[0],
              call("POST", "/api/chief/wipe", {"confirm": "ERASE"}, new_address())[0]]
    nurse = [call("GET", "/api/chief/report", cookie=staff)[0], call("POST", "/api/chief/wipe", {"confirm": "ERASE"}, cookie=staff)[0],
             call("GET", "/api/chief/security", cookie=staff)[0]]
    return nobody == [401, 401] and nurse == [401, 401, 401], f"without a PIN: {nobody}; with the nurse's PIN: {nurse}"


@check("Staff screens", 3, "Guessing the PIN is slowed down: after a few wrong tries the connection must wait")
def _():
    guesser = new_address()
    codes = [call("POST", "/api/staff/login", {"pin": f"{n:04d}"}, guesser)[0] for n in range(14)]
    right_pin_during_lock = call("POST", "/api/staff/login", {"pin": STAFF_PIN}, guesser)[0]
    return codes.count(401) <= 6 and codes[-1] == 429 and right_pin_during_lock == 429, \
        f"14 guesses: {codes.count(401)} were looked at, then refused; even the right PIN waits ({right_pin_during_lock})"


@check("Staff screens", 2, "Somebody guessing the PIN does not lock the real staff out")
def _():
    guesser = new_address()
    for n in range(40):
        call("POST", "/api/staff/login", {"pin": f"{n:04d}"}, guesser)
    nurse = sign_in(address=new_address())
    return nurse is not None, "a nurse at another address signs in as usual" if nurse else "the nurse could not sign in"


@check("Staff screens", 2, "A connection that was blocked for spamming can still be used by a nurse to sign in")
def _():
    shared = new_address()
    for _ in range(35):
        call("POST", "/api/register", raw=b"x" * 70000, address=shared, headers={"Content-Type": "application/json"})
    phone = register(shared)[0]
    nurse = sign_in(address=shared)
    return phone == 429 and nurse is not None, f"a phone on that connection registers: {phone}; the nurse signs in: {'yes' if nurse else 'no'}"


@check("Staff screens", 2, "A phone on the hospital's Wi-Fi cannot keep the nurse from signing in", known_gap=True)
def _():
    shared = new_address()
    for n in range(5):
        call("POST", "/api/staff/login", {"pin": f"{n:04d}"}, shared)
    nurse = call("POST", "/api/staff/login", {"pin": STAFF_PIN}, shared)[0]
    return nurse == 200, f"after five wrong PINs from the shared address, the right PIN gets {nurse} for a minute, and again every minute"


@check("Staff screens", 2, "A four-digit PIN cannot be found by trying from many addresses", known_gap=True)
def _():
    looked_at = sum(call("POST", "/api/staff/login", {"pin": f"{n:04d}"}, new_address())[0] == 401 for n in range(30))
    return looked_at < 30, f"{looked_at} of 30 guesses from 30 addresses were looked at; nothing but the number of addresses limits this"


@check("Staff screens", 2, "The staff cookie cannot be read by a script and is not sent with requests another site starts")
def _():
    _, headers, _ = call("POST", "/api/staff/login", {"pin": STAFF_PIN}, new_address())
    cookie = (headers.get_all("Set-Cookie") or [""])[0].lower()
    return "httponly" in cookie and "samesite=lax" in cookie, "HttpOnly and SameSite=Lax are set" if cookie else "no cookie"


@check("Staff screens", 2, "Another website, or a form slipped into a page, cannot press a staff button for a signed-in nurse")
def _(staff):
    form = [call("POST", path, raw=b"x=1", cookie=staff, headers={"Content-Type": kind})[0]
            for path in ("/api/reset", "/api/line/next") for kind in ("application/x-www-form-urlencoded", "text/plain")]
    other = call("POST", "/api/reset", {}, cookie=staff, headers={"Sec-Fetch-Site": "cross-site"})[0]
    own = call("POST", "/api/line/wait", {}, cookie=staff, headers={"Sec-Fetch-Site": "same-origin"})[0]
    return form == [403] * 4 and other == 403 and own == 200, \
        f"a plain form on \"Start a new day\" and \"next\": {form}; a request from another site: {other}; the nurse's own page: {own}"


@check("Staff screens", 2, "Every nurse and doctor signs in with an account of their own", known_gap=True)
def _():
    return False, "one shared staff PIN and one chief PIN; for the judges both are published"


@check("Staff screens", 1, "A copied staff cookie stops working when the screen is locked", known_gap=True)
def _():
    cookie = sign_in()
    call("POST", "/api/staff/logout", {}, new_address(), cookie=cookie)
    after = call("GET", "/api/line", cookie=cookie)[0]
    return after == 401, f"the old cookie after \"Lock this screen\": {after} (the cookie is the same for every sign-in)"


# ================================================================ 4. the browser

def policy_of(path):
    headers = call("GET", path, address=new_address())[1]
    return headers, {part.split(" ")[0]: part.split(" ")[1:] for part in headers.get("Content-Security-Policy", "").split("; ")}


PAGES = {"/": "index.html", "/nurse": "nurse.html", "/doctor": "doctor.html", "/board": "board.html",
         "/chief": "chief.html", "/privacy": "privacy.html"}


@check("The browser", 3, "A page runs only its own scripts: each one is listed by fingerprint, nothing else written into a page runs")
def _():
    wrong = []
    for path, name in PAGES.items():
        scripts = policy_of(path)[1].get("script-src", ["missing"])
        own = guard.script_marks((ROOT / "hospital" / "static" / name).read_text(encoding="utf-8"))
        if "'unsafe-inline'" in scripts or "'unsafe-eval'" in scripts or sorted(s for s in scripts if s.startswith("'sha256-")) != sorted(own):
            wrong.append(path)
    return not wrong, f"{len(PAGES)} pages: every script-src lists exactly the page's own scripts" if not wrong else f"wrong on {wrong}"


@check("The browser", 3, "A page may send data only to Meddy's own server")
def _():
    wrong = [path for path in PAGES if policy_of(path)[1].get("connect-src") != ["'self'"]
             or policy_of(path)[1].get("form-action") != ["'none'"] or policy_of(path)[1].get("base-uri") != ["'self'"]]
    return not wrong, "on every page: connect-src 'self', no form may be sent anywhere, base-uri 'self'" if not wrong else f"wrong on {wrong}"


@check("The browser", 2, "From outside, a page may load one script file only (the QR library), not everything its host serves")
def _():
    outside = {path: [s for s in policy_of(path)[1].get("script-src", []) if s.startswith("http")] for path in PAGES}
    wrong = [path for path, hosts in outside.items() if hosts != [guard.QR_LIBRARY]]
    return not wrong, f"script-src names only {guard.QR_LIBRARY.rsplit('/', 1)[1]} on every page" if not wrong else f"wrong on {wrong}: {outside[wrong[0]]}"


@check("The browser", 2, "A style sheet put into a page is not used (it could hide a danger warning on the nurse's screen)")
def _():
    wrong = []
    for path, name in PAGES.items():
        sheets = policy_of(path)[1].get("style-src-elem", ["missing"])
        own = guard.style_marks((ROOT / "hospital" / "static" / name).read_text(encoding="utf-8"))
        if "'unsafe-inline'" in sheets or sorted(s for s in sheets if s.startswith("'sha256-")) != sorted(own):
            wrong.append(path)
    return not wrong, "every page lists exactly its own style sheet by fingerprint" if not wrong else f"wrong on {wrong}"


@check("The browser", 2, "No other site can show Meddy inside a hidden frame")
def _():
    policy = policy_of("/nurse")[1]
    frames = policy.get("frame-ancestors", [])
    return frames == ["'self'", "https://huggingface.co"] and policy.get("frame-src") == ["'none'"], \
        f"may be framed by: {' '.join(frames)}; frames inside a page: {' '.join(policy.get('frame-src', ['allowed']))}"


@check("The browser", 1, "Answers about patients are not cached, file types are not guessed, the address is not passed on")
def _():
    headers = call("GET", "/api/board", address=new_address())[1]
    got = (headers.get("Cache-Control"), headers.get("X-Content-Type-Options"), headers.get("Referrer-Policy"))
    return got == ("no-store", "nosniff", "no-referrer"), " / ".join(str(g) for g in got)


@check("The browser", 2, "No page depends on a handler written inside a tag (the policy would not run it)")
def _():
    found = []
    for file in sorted((ROOT / "hospital" / "static").glob("*.html")) + sorted((ROOT / "hospital" / "static").glob("*.js")):
        found += [file.name for _ in re.findall(r"<[a-zA-Z][^<>]*\son[a-z]+\s*=\s*[\"']", file.read_text(encoding="utf-8"))]
    return not found, "none in the six pages and two script files" if not found else f"found in {sorted(set(found))}"


@check("The browser", 2, "A name with markup in it is refused at registration")
def _():
    codes = [register(new_address(), first_name=name, last_name="Person")[0]
             for name in ("<script>alert(1)</script>", "Ann<img src=x onerror=alert(1)>", "Ann\" onmouseover=\"x", "Robert'); DROP TABLE visits;--")]
    return codes == [422] * 4, f"four such names: {codes}"


@check("The browser", 1, "The list of every address of the server is not served")
def _():
    codes = [call("GET", path, address=new_address())[0] for path in ("/docs", "/redoc", "/openapi.json")]
    return codes == [404, 404, 404], f"/docs, /redoc, /openapi.json: {codes}"


@check("The browser", 1, "A style written inside a single tag is refused too", known_gap=True)
def _():
    styles = policy_of("/")[1].get("style-src-attr", ["'unsafe-inline'"])
    return "'unsafe-inline'" not in styles, "still allowed, because the pages use them; such a style can only change the tag it is written in"


# ================================================================ 5. bad input

@check("Bad input", 3, "Text meant to break a database query is stored as plain text and breaks nothing")
def _(staff):
    attack = "'; DROP TABLE visits; --"
    status, _, arrival = register(new_address(), complaint=attack)
    odd = [call("GET", "/api/me/" + path, address=new_address())[0] for path in ("x'%20OR%20'1'='1", "P-2026-0001'--", "%00")]
    today = call("GET", "/api/nurse/today", cookie=staff)[2]
    kept = any(p.get("complaint") == attack for p in today.get("patients", [])) if isinstance(today, dict) else False
    return status == 200 and kept and all(code in (404, 422) for code in odd), \
        f"registered as text: {kept}; odd registration numbers: {odd}; the table of visits still answers"


@check("Bad input", 2, "Malformed requests to the open addresses are refused cleanly, never with a server error")
def _():
    bodies = [b"", b"{", b"[]", b"null", b'"text"', b"12", b'{"complaint": null}', b'{"complaint": 5, "age": "old"}',
              b'{"complaint": "x", "age": 1e999}', b'{"complaint": "x", "age": -1}', b'{"complaint": {"a": 1}, "age": 30}',
              b'{"reg_no": null}', b'{"reg_no": 7}', b'{"reg_no": ["P-2026-0001"]}', b'{"reg_no": "P-2026-0001", "answers": "no"}',
              b'{"reg_no": "P-2026-0001", "pain": 99}', b'{"pin": 1234}', b'{"pin": null}', b'{"text": 5}', "{\"complaint\": \"\u0000😀\", \"age\": 30}".encode("utf-8", "surrogatepass"),
              b"\xff\xfe\x00", b'{"complaint": "' + b"a" * 5000 + b'", "age": 30}']
    worst, tried = [], 0
    for path in ("/api/register", "/api/checkin", "/api/worse", "/api/spelling", "/api/staff/login"):
        for body in bodies:
            tried += 1
            status = call("POST", path, raw=body, address=new_address(), headers={"Content-Type": "application/json"})[0]
            if status >= 500:
                worst.append(f"{path} {body[:30]!r}: {status}")
    for cookie in ("meddy_staff=\xe9", "meddy_chief=\xe9\xff", "meddy_staff=" + "a" * 5000):
        tried += 1
        status = call("GET", "/api/line", address=new_address(), headers={"Cookie": cookie})[0]
        if status >= 500:
            worst.append(f"cookie {cookie[:16]!r}: {status}")
    for path in ("/api/me/%F0%9F%98%80", "/api/me/" + "9" * 3000, "/api/follow-up/..%2F..%2Fetc", "/api/spelling?text=%00", "/static/..%2F..%2Fserver.py"):
        tried += 1
        status = call("GET", path, address=new_address())[0]
        if status >= 500 or (path.startswith("/static") and status == 200):
            worst.append(f"{path[:40]}: {status}")
    return not worst, f"{tried} malformed requests: none gave a server error" if not worst else "; ".join(worst[:3])


@check("Bad input", 1, "Characters that cannot be seen but turn the text around are taken out of what a patient types")
def _(staff):
    sent = "no\u202e chest pain\u0000 at all\u2066"
    reg = register(new_address(), complaint=sent)[2].get("reg_no")
    today = call("GET", "/api/nurse/today", cookie=staff)[2]
    kept = next((p.get("complaint") for p in today.get("patients", []) if p.get("reg_no") == reg), None)
    return kept == "no chest pain at all", f"stored as {kept!r}"


@check("Bad input", 2, "A request that does not say how large it is cannot be used to send an endless body")
def _():
    host, port = BASE.replace("http://", "").split(":")
    with socket.create_connection((host, int(port)), timeout=10) as line:
        line.sendall(b"POST /api/register HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                     b"Transfer-Encoding: chunked\r\nX-Forwarded-For: " + new_address().encode() + b"\r\n\r\n5\r\nhello\r\n")
        line.settimeout(5)
        try:
            first = line.recv(200).decode("latin-1").split("\r\n")[0]
        except socket.timeout:
            first = "no answer (the server kept waiting for more)"
    return " 413" in first, first


# ================================================================ 6. the record of a visit

def seal(*parts):
    text = json.dumps([None if part is None else str(part) for part in parts], ensure_ascii=False)
    return hmac.new(BUILT_IN_LOG_KEY, text.encode("utf-8"), hashlib.sha256).hexdigest()


def tamper(staff):
    """Change the logs of a dozen visits straight in the database file, each in another way, as somebody with the
    file could. Then ask the server once whether its log is whole. Returns what it said about each visit."""
    names = ["changed", "removed", "cut", "added", "unsealed", "cut_then_used", "emptied", "garbled", "orphaned",
             "forged", "gone", "row_changed", "untouched"]
    visits = {name: waiting_patient(staff) for name in names}
    reg = {name: visit[0] for name, visit in visits.items()}
    file = sqlite3.connect(DATABASE, timeout=20)
    file.row_factory = sqlite3.Row
    last_line = "id = (SELECT MAX(id) FROM events WHERE visit_reg = ?)"
    file.execute("UPDATE events SET detail = replace(detail, 'level 4', 'level 2') WHERE visit_reg = ? AND what = 'in line'", (reg["changed"],))
    file.execute("DELETE FROM events WHERE visit_reg = ? AND what = 'checked in'", (reg["removed"],))
    file.execute(f"DELETE FROM events WHERE {last_line}", (reg["cut"],))
    file.execute("INSERT INTO events (visit_reg, day, clock, at, what, who, detail, seal) SELECT visit_reg, day, clock, at, "
                 "'in line', 'nurse', 'level 1, ticket A001', seal FROM events WHERE visit_reg = ? ORDER BY id DESC LIMIT 1", (reg["added"],))
    # "these lines are from before sealing": take the seals off, change a line, move the mark where sealing began
    file.execute("UPDATE events SET seal = NULL, detail = replace(detail, 'level 4', 'level 2') WHERE visit_reg = ?", (reg["unsealed"],))
    file.execute("UPDATE visits SET log_seal = NULL WHERE reg_no = ?", (reg["unsealed"],))
    file.execute("INSERT INTO meta (key, value) VALUES ('sealed_from', 1000000000) ON CONFLICT(key) DO UPDATE SET value = excluded.value")
    file.execute(f"DELETE FROM events WHERE {last_line}", (reg["cut_then_used"],))
    file.execute("DELETE FROM events WHERE visit_reg = ?", (reg["emptied"],))
    file.execute("UPDATE visits SET log_seal = NULL WHERE reg_no = ?", (reg["emptied"],))
    file.execute(f"UPDATE events SET seal = x'00ff' WHERE {last_line}", (reg["garbled"],))
    file.execute("DELETE FROM answers WHERE visit_reg = ?", (reg["orphaned"],))
    file.execute("DELETE FROM visits WHERE reg_no = ?", (reg["orphaned"],))
    # the forgery: change the last line AND make new seals with the key that is written in the code
    log_id = file.execute("SELECT value FROM meta WHERE key = 'log_id'").fetchone()["value"]
    lines = file.execute("SELECT * FROM events WHERE visit_reg = ? ORDER BY id", (reg["forged"],)).fetchall()
    last, before = lines[-1], (lines[-2]["seal"] if len(lines) > 1 else None)
    new = seal(log_id, before, reg["forged"], last["day"], last["clock"], last["at"], last["what"], last["who"], "level 1, forged")
    file.execute("UPDATE events SET detail = 'level 1, forged', seal = ? WHERE id = ?", (new, last["id"]))
    file.execute("UPDATE visits SET log_seal = ? WHERE reg_no = ?", (seal(log_id, "last line", reg["forged"], new), reg["forged"]))
    for table, column in (("events", "visit_reg"), ("answers", "visit_reg"), ("visits", "reg_no")):
        file.execute(f"DELETE FROM {table} WHERE {column} = ?", (reg["gone"],))
    file.execute("UPDATE visits SET level = 5 WHERE reg_no = ?", (reg["row_changed"],))
    file.commit()
    file.close()
    # the two visits whose log was cut go on: the patient presses "I feel worse", which writes a new line
    used = [call("POST", "/api/worse", {"reg_no": visits[name][0]}, visits[name][2], {"X-Meddy-Visit": visits[name][1]})[0]
            for name in ("cut_then_used", "emptied")]
    status, _, report = call("GET", "/api/chief/security", cookie=sign_in(CHIEF_PIN, "chief"))
    log = report["log"] if isinstance(report, dict) and "log" in report else {"broken": [], "broken_count": 0, "lines": 0}
    # the panel names five visits; for the audit every changed visit is looked up in the file's own answer
    found = {b["visit"]: b["how"] for b in log["broken"]}
    said = {name: found.get(reg[name]) for name in names}
    return {**said, "named": log.get("broken_count"), "listed": len(log["broken"]), "status": status, "used": used,
            "lines": log.get("lines"), "report": report if isinstance(report, dict) else {}}


def noticed(tampered, name):
    """Did the server name this visit? The panel lists five visits by name and counts the rest, so a visit that is
    not among the five listed is settled by the count: all ten that should be named, and no more."""
    if tampered[name]:
        return True, tampered[name]
    should = ["changed", "removed", "cut", "added", "unsealed", "cut_then_used", "emptied", "garbled", "orphaned"]
    if tampered["named"] == len(should) + bool(tampered["forged"]) + bool(tampered["gone"]) + bool(tampered["row_changed"]):
        return True, f"counted among the {tampered['named']} visits whose log was changed"
    return False, f"not noticed ({tampered['named']} visits named, {len(should)} were changed)"


@check("The record of a visit", 3, "A changed line of a visit's log is noticed (for example the line that says which level was set)")
def _(tampered):
    return noticed(tampered, "changed")


@check("The record of a visit", 3, "A line taken out of a visit's log is noticed")
def _(tampered):
    return noticed(tampered, "removed")


@check("The record of a visit", 2, "The last line of a log cut off is noticed")
def _(tampered):
    return noticed(tampered, "cut")


@check("The record of a visit", 2, "A line added without the key is noticed")
def _(tampered):
    return noticed(tampered, "added")


@check("The record of a visit", 3, "Lines cannot be passed off as \"too old to check\" by taking their seals off")
def _(tampered):
    return noticed(tampered, "unsealed")


@check("The record of a visit", 3, "A cut log stays an alarm when the visit goes on: the next step does not repair it")
def _(tampered):
    ok, said = noticed(tampered, "cut_then_used")
    ok2, said2 = noticed(tampered, "emptied")
    return ok and ok2 and tampered["used"] == [200, 200], f"after a new step on each: cut log: {said}; emptied log: {said2}"


@check("The record of a visit", 2, "A damaged seal is an alarm, not a crash that leaves the old \"Unchanged\" on the screen")
def _(tampered):
    ok, said = noticed(tampered, "garbled")
    return tampered["status"] == 200 and ok, f"the check answered {tampered['status']}; {said}"


@check("The record of a visit", 2, "A log whose visit was deleted is noticed")
def _(tampered):
    return noticed(tampered, "orphaned")


@check("The record of a visit", 2, "An untouched log is not blamed: only the visits that were changed are counted")
def _(tampered):
    expected = 9 + bool(tampered["forged"]) + bool(tampered["gone"]) + bool(tampered["row_changed"])
    return tampered["untouched"] is None and tampered["named"] == expected, \
        f"{tampered['lines']} lines checked; {tampered['named']} visits counted as changed, {expected} expected"


@check("The record of a visit", 2, "Somebody who has read the code cannot forge a seal", known_gap=True)
def _(tampered):
    return bool(tampered["forged"]), "a line changed and sealed again with the key written in the code was accepted " \
        "(MEDDY_LOG_KEY is not set, as on the live site)" if not tampered["forged"] else tampered["forged"]


@check("The record of a visit", 2, "A level changed in the visit's own row, not in its log, is noticed", known_gap=True)
def _(tampered):
    return bool(tampered["row_changed"]), "not noticed: the seals cover the log of steps, and nothing compares the row with it" \
        if not tampered["row_changed"] else tampered["row_changed"]


@check("The record of a visit", 1, "A visit removed together with all of its lines is noticed", known_gap=True)
def _(tampered):
    return bool(tampered["gone"]), "not noticed: nothing is left that a seal could be checked against" if not tampered["gone"] else tampered["gone"]


@check("The record of a visit", 2, "The chief doctor sees what was refused, and no visitor's address is shown")
def _(tampered):
    report = tampered["report"]
    refused = report.get("refused", {})
    addresses = re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", json.dumps(report))
    return refused.get("too_many", 0) > 0 and refused.get("wrong_phone", 0) > 0 and refused.get("too_large", 0) > 0 and not addresses, \
        f"refused so far: {refused}; addresses in the answer: {len(addresses)}"


# ================================================================ running and grading

def grade(share):
    return "A" if share >= 0.9 else "B" if share >= 0.8 else "C" if share >= 0.7 else "D" if share >= 0.6 else "F"


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start_server(folder):
    global BASE, DATABASE
    port = free_port()
    BASE, DATABASE = f"http://127.0.0.1:{port}", str(Path(folder) / "audit.db")
    settings = {**os.environ, "HOSPITAL_DB": DATABASE, "MEDDY_PORT": str(port), "MEDDY_SIMULATION": "0",
                "MEDDY_STAFF_PIN": STAFF_PIN, "MEDDY_CHIEF_PIN": CHIEF_PIN, "MEDDY_PROXY": "1", "MEDDY_LIMITS": "on",
                "MEDDY_STRICT_SCRIPTS": "on", "MEDDY_OLLAMA_TIMEOUT": "1"}
    for name in ("MEDDY_LOG_KEY", "MEDDY_LAN", "MEDDY_PUBLIC_URL", "SPACE_ID"):
        settings.pop(name, None)
    server = subprocess.Popen([sys.executable, str(ROOT / "hospital" / "server.py")], env=settings, cwd=str(ROOT),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            if call("GET", "/api/version")[0] == 200:
                return server
        except OSError:
            time.sleep(0.5)
    server.kill()
    raise SystemExit("The audit's own server did not start.")


def run():
    """Start a server, run every check, stop the server. Returns the results:
    a list of {area, weight, title, known_gap, passed, evidence}."""
    results = []
    with tempfile.TemporaryDirectory() as folder:
        server = start_server(folder)
        try:
            staff = sign_in()
            tampered = None
            outcome = {}
            for number, (area, weight, title, known_gap, last, function) in sorted(enumerate(CHECKS), key=lambda c: c[1][4]):
                wants = function.__code__.co_varnames[:function.__code__.co_argcount]
                try:
                    if "tampered" in wants and tampered is None:
                        tampered = tamper(staff)
                    passed, evidence = function(*[{"staff": staff, "tampered": tampered}[name] for name in wants])
                except Exception as error:              # a check that cannot run is a failed check, never a skipped one
                    passed, evidence = False, f"the check itself failed: {error!r}"
                outcome[number] = {"area": area, "weight": weight, "title": title, "known_gap": known_gap,
                                   "passed": bool(passed), "evidence": str(evidence)}
            results = [outcome[number] for number in sorted(outcome)]
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
    return results


def summary(results):
    """The grade of each area and of the whole: (name, passed weight, all weight, share, letter)."""
    rows = []
    for area in dict.fromkeys(r["area"] for r in results):
        mine = [r for r in results if r["area"] == area]
        got, of = sum(r["weight"] for r in mine if r["passed"]), sum(r["weight"] for r in mine)
        rows.append((area, got, of, got / of, grade(got / of)))
    got, of = sum(r["weight"] for r in results if r["passed"]), sum(r["weight"] for r in results)
    return rows, ("Whole site", got, of, got / of, grade(got / of))


def report(results):
    areas, whole = summary(results)
    passed = sum(r["passed"] for r in results)
    gaps = [r for r in results if not r["passed"] and r["known_gap"]]
    surprises = [r for r in results if not r["passed"] and not r["known_gap"]]
    fixed = [r for r in results if r["passed"] and r["known_gap"]]
    lines = ["# Security audit", "",
             f"Made by `python security_audit.py --write` on {datetime.now().strftime('%d %B %Y')}, on the code this file "
             "is committed with. Run it again after any change: the grade is only true for the code it ran on.", "",
             "The tool starts its own copy of the hospital server with a throwaway database and attacks it over real HTTP:",
             "floods, guessing, other patients' visits, oversized and malformed requests, and changes made straight in the",
             "database file. Every check is a sentence that should be true of a safe site, and it passes or fails.",
             "The checks marked *known gap* are things Meddy does not do (see [SECURITY.md](SECURITY.md)). They count",
             "against the grade.", "",
             f"## Result: {whole[4]} ({whole[1]} of {whole[2]} points, {whole[3]:.0%})", "",
             f"{passed} of {len(results)} checks passed. {len(gaps)} failed as expected (known gaps). "
             + (f"**{len(surprises)} failed unexpectedly.**" if surprises else "None failed unexpectedly."), "",
             "| Area | Points | Share | Grade |", "|---|---|---|---|"]
    lines += [f"| {name} | {got} of {of} | {share:.0%} | {letter} |" for name, got, of, share, letter in areas]
    lines += [f"| **{whole[0]}** | **{whole[1]} of {whole[2]}** | **{whole[3]:.0%}** | **{whole[4]}** |", "",
              "Points: 3 = a patient could be harmed or their data exposed, 2 = the service could be disturbed or the staff",
              "misled, 1 = extra hardening. Grade: A from 90%, B from 80%, C from 70%, D from 60%, else F. The weights and",
              "the scale are our own judgement, not a standard.", ""]
    for area in dict.fromkeys(r["area"] for r in results):
        lines += [f"## {area}", "", "| | Points | What should be true | What the audit saw |", "|---|---|---|---|"]
        for r in [r for r in results if r["area"] == area]:
            mark = "pass" if r["passed"] else "**FAIL** (known gap)" if r["known_gap"] else "**FAIL**"
            lines.append(f"| {mark} | {r['weight']} | {r['title']} | {r['evidence'].replace('|', '/')} |")
        lines.append("")
    lines += ["## What this audit does not cover", "",
              "- **It runs on a laptop, against a copy.** It does not show how the live site behaves behind Hugging Face's",
              "  proxy. What we tried on the live site by hand is in [SECURITY.md](SECURITY.md).",
              "- **It has no browser.** That a script hidden in a complaint or a note does not run on the staff screens was",
              "  tried by hand in a browser, not by this tool. The tool checks the policy the server sends.",
              "- **It was written by the team that wrote the code.** Nobody outside the team has tested the site.",
              "- **The language model is off** in the audit's server, so only its two seats are checked.",
              "- A passed check means this one attack failed, not that no attack of that kind can work.", ""]
    if fixed:
        lines += ["Marked as known gaps but passed (the mark is out of date): " + "; ".join(r["title"] for r in fixed), ""]
    return "\n".join(lines)


if __name__ == "__main__":
    found = run()
    text = report(found)
    print(text)
    if "--write" in sys.argv:
        (ROOT / "SECURITY_AUDIT.md").write_text(text + "\n", encoding="utf-8")
    sys.exit(1 if any(not r["passed"] and not r["known_gap"] for r in found) else 0)
