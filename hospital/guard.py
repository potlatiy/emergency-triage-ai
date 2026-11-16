"""The hospital's guard against spam and abuse. Plain rules, no outside service.

Meddy is open to the internet and the patient's side needs no sign-in: anybody can register a visit. Without a
guard, one script could fill the nurse's list with made-up patients, press "I feel worse" for every waiting
patient, keep the language model busy for everybody, or send a body so large the server runs out of memory.

What is here:
  - limits         how often one address, and everybody together, may call the open addresses; when an address is
                   under pressure from everybody, each single address gets less
  - blocking       an address that keeps being refused is shut out of the open addresses for a while, longer each time
  - the log        what was refused and why, for the chief doctor's panel; addresses only as fingerprints
  - body size      a request body larger than a questionnaire could ever be is refused
  - headers        what every answer tells the browser: which scripts a page may run (only its own, by fingerprint),
                   which outside addresses it may load from and send to, who may frame it, and not to cache
                   patients' data
  - model seats    at most two questions to the language model at once; a third gets the standard questions

What is not here, and why, is written down in SECURITY.md.
"""

import base64
import hashlib
import ipaddress
import os
import re
import threading
import time
from collections import Counter, deque
from datetime import datetime

# MEDDY_LIMITS=off switches the rate limits off (the tests, which register hundreds of patients a second).
ON = os.environ.get("MEDDY_LIMITS", "on") != "off"

# Is there a proxy in front that writes the visitor's address into X-Forwarded-For? On Hugging Face there is (every
# Space has SPACE_ID set); behind another proxy say so with MEDDY_PROXY=1. Started on a laptop, nothing stands in
# front, so the header is whatever the caller typed and only the connection itself is believed.
BEHIND_PROXY = bool(os.environ.get("SPACE_ID")) or os.environ.get("MEDDY_PROXY") == "1"

# Each open address: (what one address may do: so many calls in so many seconds), (what everybody together may do),
# and for registering a third, longer limit for one address. One address is generous enough for a waiting room
# behind one router; "everybody" bounds what a spammer can do who hides behind many addresses.
#
# "Everybody" counts only calls the hospital accepted (see served). It used to count every call, so a few hundred
# requests that did nothing (a registration with no complaint, "I feel worse" for a number that does not exist)
# used the limit up and closed the address for all real patients.
# "I feel worse" and check-in have no limit for everybody at all: each needs the visit's own key, so there can be
# no more of them than there are visits, and a patient who is getting worse must never be refused because of
# what other people sent.
# The addresses the pages ask again and again (a phone asks about its visit every few seconds, the board every
# five) only read, so their limits are far higher: they stop a flood, not a waiting room.
LIMITS = {
    "/api/register":     ((10, 60), (90, 600), (40, 3600)),
    "/api/checkin":      ((20, 60), None),
    "/api/worse":        ((6, 60), None),
    "/api/follow-up/":   ((20, 60), (180, 600)),
    "/api/spelling":     ((240, 60), (3000, 600)),
    "/api/staff/login":  ((10, 60), (120, 600)),
    "/api/me/":          ((1200, 60), None),
    "/api/board":        ((300, 60), None),
    "/api/waiting-room": ((300, 60), None),
}
KEPT = 3600                     # seconds a call is remembered: the longest limit above
MAX_BODY = 64 * 1024            # bytes; the largest real request (check-in with 20 answers) is under 10 KB
EVERYBODY = "everybody"

# Under pressure: once everybody together has used half of an address's limit, one address gets a third of its
# usual share (never less than 2). A real patient registers once and does not notice; a script that hides behind
# many addresses gets far less out of each of them.
PRESSURE_AT = 0.5
PRESSURE_SHARE = 3

# Blocking: an address refused STRIKES times within STRIKE_WINDOW seconds is shut out of the addresses a patient
# writes to (WRITES) for BLOCK_FOR seconds; twice as long each next time, an hour at most. Staff who signed in
# with the PIN are never blocked: a nurse may share the hospital's Wi-Fi, and so its address, with the spammer.
STRIKES, STRIKE_WINDOW = 30, 300
BLOCK_FOR, BLOCK_AT_MOST = 600, 3600
# The staff sign-in is not among them: a phone on the hospital's Wi-Fi must not be able to get the nurse's own
# address shut out of signing in.
WRITES = ("/api/register", "/api/checkin", "/api/worse", "/api/follow-up/")

_calls = {}                     # (address or EVERYBODY, limited address) -> the times of the last calls
_strikes = {}                   # address -> the times it was refused lately
_blocked = {}                   # address -> (blocked until, how many times it has been blocked)
_refused = Counter()            # why -> how many requests were refused since the server started
_log = deque(maxlen=200)        # the last refusals: (clock time, why, fingerprint of the address, where)
_since = datetime.now()
_secret = os.urandom(16)        # new at every start: a fingerprint cannot be turned back into an address
_tidied = 0.0                   # when addresses no longer needed were last forgotten (see _tidy)
_lock = threading.Lock()


# Addresses that belong to a proxy's own network, never to a visitor. Only these may be stepped over when reading
# X-Forwarded-For from the right.
_INTERNAL = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
                                               "169.254.0.0/16", "100.64.0.0/10", "::1/128", "fc00::/7", "fe80::/10")]


def _parsed(text):
    """An address out of one entry of the header, or None. "1.2.3.4:5678" and "[2a01::1]:443" carry a port."""
    text = (text or "").strip()
    if text.startswith("["):
        text = text[1:].split("]")[0]
    elif text.count(":") == 1:
        text = text.split(":")[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    return address.ipv4_mapped or address if address.version == 6 else address


def _who(address):
    """What an address counts as. A home or a phone gets a whole block of IPv6 addresses (a /64) and may use any of
    them, so the block counts as one visitor."""
    if address.version == 6:
        return str(ipaddress.ip_network((int(address) >> 64 << 64, 64)))
    return str(address)


def client_address(forwarded, peer):
    """Where a request really comes from. forwarded: every X-Forwarded-For line of the request, joined by commas.

    Online, every request reaches the server from Hugging Face's proxy, which writes the visitor's address at the
    END of X-Forwarded-For. Anything before that, the visitor may have typed themselves, so it is not believed.
    Reading from the right, addresses of a proxy's own network are stepped over; the first other address is the
    visitor. An entry that is not an address at all ends the search: nothing to its left is believed either.
    (The staff sign-in used to read the FIRST entry, and only the first of several header lines was read.)

    Without a proxy in front (BEHIND_PROXY) the header is not read at all: the caller wrote all of it."""
    for part in reversed((forwarded or "").split(",")) if BEHIND_PROXY else ():
        address = _parsed(part)
        if address is None:
            break
        if not any(address in network for network in _INTERNAL if network.version == address.version):
            return _who(address)
    address = _parsed(peer)
    return _who(address) if address is not None else (peer or "unknown")


def limited(path):
    """The limit a path falls under, as (name, (one address, everybody, one address over a longer time)), or None."""
    for name, limits in LIMITS.items():
        if path == name or (name.endswith("/") and path.startswith(name)):
            return name, (limits + (None, None))[:3]
    return None


def _recent(calls, seconds, now):
    return sum(1 for t in calls if now - t < seconds)


def check(address, path, now=None):
    """Count this call for its address. Returns (seconds to wait, own): the wait is None when the call may go
    ahead; own says whether the address went over ITS OWN limit (True) or was refused only because everybody
    together has used the address up (False). Only the first is the caller's fault."""
    found = limited(path) if ON else None
    if found is None:
        return None, False
    name, (one, everybody, longer) = found
    now = time.monotonic() if now is None else now
    with _lock:
        if everybody is not None and _pressure(name, everybody, now):
            one = (max(2, one[0] // PRESSURE_SHARE), one[1])
        mine = _calls.setdefault((address, name), deque())
        while mine and now - mine[0] >= KEPT:
            mine.popleft()
        waits = []
        for count, seconds in (limit for limit in (one, longer) if limit is not None):
            inside = [t for t in mine if now - t < seconds]
            if len(inside) >= count:
                waits.append(max(1, int(seconds - (now - inside[0])) + 1))
        if waits:
            return max(waits), True
        if everybody is not None:
            count, seconds = everybody
            served_lately = [t for t in _calls.get((EVERYBODY, name), ()) if now - t < seconds]
            if len(served_lately) >= count:
                return max(1, int(seconds - (now - served_lately[0])) + 1), False
        mine.append(now)
        _tidy(now)
    return None, False


def too_many(address, path, now=None):
    """The seconds to wait when this call is refused, else None (see check)."""
    return check(address, path, now)[0]


def served(path, now=None):
    """The hospital accepted a call (it answered 2xx): only now does it count towards the limit for everybody."""
    found = limited(path) if ON else None
    if found is None or found[1][1] is None:
        return
    now = time.monotonic() if now is None else now
    with _lock:
        calls = _calls.setdefault((EVERYBODY, found[0]), deque())
        calls.append(now)
        while calls and now - calls[0] >= KEPT:
            calls.popleft()


def _tidy(now):
    """Forget every address that is no longer needed: calls older than an hour, refusals older than STRIKE_WINDOW,
    and a block an hour after it ended (until then it decides how long the next one is). So no visitor's address
    stays in memory for more than two hours, and a flood of addresses cannot fill the memory. Done once a minute;
    under a flood of addresses every five seconds, never at every call. The caller holds the lock."""
    global _tidied
    if now - _tidied < (5 if len(_calls) >= 20000 else 60):
        return
    _tidied = now
    for key in [k for k, v in _calls.items() if not v or now - v[-1] > KEPT]:
        del _calls[key]
    for key in [k for k, v in _strikes.items() if not v or now - v[-1] > STRIKE_WINDOW]:
        del _strikes[key]
    for key in [k for k, (until, _) in _blocked.items() if now - until > 3600]:
        del _blocked[key]


def _pressure(name, everybody, now):
    """Has everybody together used half of this address's limit? The caller holds the lock."""
    count, seconds = everybody
    return _recent(_calls.get((EVERYBODY, name), ()), seconds, now) >= count * PRESSURE_AT


def fingerprint(address):
    """Eight characters that stand for an address in the log. The same address gives the same fingerprint until
    the server restarts; the address itself is never written down."""
    return hashlib.sha256(_secret + str(address).encode()).hexdigest()[:8]


def note(why, address, path, strike=True, now=None):
    """Write a refusal into the log. With strike, it also counts against the address: STRIKES of them within
    STRIKE_WINDOW seconds block it (see blocked). why: too_many, too_large, wrong_phone, wrong_pin, blocked,
    other_site."""
    now = time.monotonic() if now is None else now
    found = limited(path)
    with _lock:
        _refused[why] += 1
        _log.append((datetime.now().strftime("%H:%M:%S"), why, fingerprint(address), found[0] if found else path))
        if not strike or not ON:
            return
        strikes = _strikes.setdefault(address, deque())
        strikes.append(now)
        while strikes and now - strikes[0] >= STRIKE_WINDOW:
            strikes.popleft()
        if len(strikes) >= STRIKES:
            times = _blocked.get(address, (0, 0))[1] + 1
            _blocked[address] = (now + min(BLOCK_FOR * 2 ** (times - 1), BLOCK_AT_MOST), times)
            strikes.clear()
        _tidy(now)


def blocked(address, path, now=None):
    """The seconds this address is still shut out of this path, or None. Only the addresses a patient writes to
    (WRITES) are ever blocked: a blocked phone still shows its place in line."""
    if not ON or not any(path == w or (w.endswith("/") and path.startswith(w)) for w in WRITES):
        return None
    now = time.monotonic() if now is None else now
    with _lock:
        until = _blocked.get(address, (0, 0))[0]
    return max(1, int(until - now) + 1) if until > now else None


def report(now=None):
    """What the guard did since the server started, for the chief doctor's panel."""
    now = time.monotonic() if now is None else now
    with _lock:
        return {
            "since": _since.strftime("%d %b, %H:%M"),
            "limits_on": ON,
            "refused": {why: _refused.get(why, 0)
                        for why in ("too_many", "blocked", "wrong_phone", "wrong_pin", "too_large", "other_site")},
            "blocked_now": [{"who": fingerprint(a), "minutes": int((until - now) // 60) + 1, "times": times}
                            for a, (until, times) in _blocked.items() if until > now],
            "pressure": [name for name, limits in LIMITS.items()
                         if limits[1] is not None and _pressure(name, limits[1], now)],
            "recent": [{"at": at, "why": why, "who": who, "where": where} for at, why, who, where in list(_log)[-12:]][::-1],
        }


def forget():
    """Start counting from nothing (the tests)."""
    global _tidied
    with _lock:
        _tidied = 0.0
        _calls.clear()
        _strikes.clear()
        _blocked.clear()
        _refused.clear()
        _log.clear()


def body_too_large(headers):
    """Is the request body larger than any real request could be? Read from Content-Length, before the body is.
    A body sent in pieces without saying its length (Transfer-Encoding: chunked) is refused too: the pages never
    send one, and its size could not be checked before reading it."""
    if headers.get("transfer-encoding"):
        return True
    try:
        return int(headers.get("content-length") or 0) > MAX_BODY
    except ValueError:
        return True


# What a page may do in the browser.
#
# Scripts: a page may run the script files of Meddy itself, the QR library from its one host, and the scripts
# written inside the page - but only those whose fingerprint (SHA-256) the server lists here. The server works the
# fingerprints out from the page file itself, so a script that somebody managed to put into a page (in a patient's
# name, a complaint, a note) has no listed fingerprint and the browser does not run it. Handlers written inside a
# tag, like <img onerror=...>, are not run either. MEDDY_STRICT_SCRIPTS=off goes back to allowing every script
# written inside a page.
# Style sheets: the same. A page's own <style> block is listed by fingerprint, so a style sheet put into a page
# (to hide a danger warning on the nurse's screen, say) is not used. A style written inside one tag is still
# allowed, because the pages use them; it can only style that one tag.
# Outside: the one file of the QR library and no other file of that host; fonts only from Google Fonts; a page may
# send what it knows (connect-src) to Meddy's own server and nowhere else; no frames inside a page; no form may be
# sent anywhere (the pages' forms are handled by their scripts). frame-ancestors: only huggingface.co, whose page
# shows the Space, may show Meddy in a frame - not any other Space.
QR_LIBRARY = "https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"
STRICT_SCRIPTS = os.environ.get("MEDDY_STRICT_SCRIPTS", "on") != "off"
_INLINE_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.DOTALL | re.IGNORECASE)
_INLINE_STYLE = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.DOTALL | re.IGNORECASE)
_marks = {}                     # page file -> (when it was last changed, the fingerprints of its scripts, of its styles)


def _mark(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"


def script_marks(html):
    """The fingerprint of every script written inside a page, as the browser works it out: SHA-256 of the script's
    text, in base64. A script loaded from a file (src=) has none: it is allowed by where it comes from."""
    marks = []
    for attributes, text in _INLINE_SCRIPT.findall(html.replace("\r\n", "\n").replace("\r", "\n")):
        if not re.search(r"(?<![\w-])src\s*=", attributes, re.IGNORECASE):
            marks.append(_mark(text))
    return tuple(marks)


def style_marks(html):
    """The fingerprint of every style sheet written inside a page (a <style> block), worked out the same way."""
    return tuple(_mark(text) for text in _INLINE_STYLE.findall(html.replace("\r\n", "\n").replace("\r", "\n")))


def marks_of(page):
    """The fingerprints of a page file's own scripts and of its own style sheets, as (scripts, styles). Worked out
    again only when the file has changed."""
    changed = page.stat().st_mtime_ns
    kept = _marks.get(page)
    if kept is None or kept[0] != changed:
        html = page.read_text(encoding="utf-8")
        kept = _marks[page] = (changed, script_marks(html), style_marks(html))
    return kept[1], kept[2]


def content_policy(scripts=(), styles=()):
    """The policy for one answer. scripts, styles: the fingerprints of the page's own scripts and style sheets
    (see marks_of). An answer that is not a page has none, and so may run none."""
    if STRICT_SCRIPTS:
        own_scripts, own_styles = " ".join(scripts), " ".join(styles)
    else:
        own_scripts = own_styles = "'unsafe-inline'"
    return "; ".join(" ".join(part.split()) for part in [
        "default-src 'self'",
        f"script-src 'self' {own_scripts} {QR_LIBRARY}",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",         # for browsers without the two below
        f"style-src-elem 'self' {own_styles} https://fonts.googleapis.com",
        "style-src-attr 'unsafe-inline'",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: blob:",
        "media-src 'self'",
        "connect-src 'self'",
        "frame-src 'none'",
        "frame-ancestors 'self' https://huggingface.co",
        "base-uri 'self'",
        "form-action 'none'",
        "object-src 'none'",
    ])


# What a form on another website can send without asking the server first. Meddy's own pages always send JSON.
FORM_KINDS = ("application/x-www-form-urlencoded", "multipart/form-data", "text/plain")


def from_another_site(headers):
    """Was this request started by another website, or by a plain HTML form? The browser says where a request
    comes from (Sec-Fetch-Site); a form can only send the three FORM_KINDS. Meddy's pages do neither, so such a
    request to an address that changes something is refused: no other site, and no form slipped into a page, can
    press "Start a new day" for a nurse who has the screen open."""
    kind = (headers.get("content-type") or "").split(";")[0].strip().lower()
    return headers.get("sec-fetch-site") == "cross-site" or kind in FORM_KINDS


def security_headers(path, secure, scripts=(), styles=()):
    """The headers every answer carries (secure: the request came over https; scripts, styles: see content_policy)."""
    headers = {
        "Content-Security-Policy": content_policy(scripts, styles),
        "X-Content-Type-Options": "nosniff",            # a file is what the server says it is, never guessed
        "Referrer-Policy": "no-referrer",               # an address like /nurse?reg=... is not told to other sites
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }
    if path.startswith("/api/"):
        headers["Cache-Control"] = "no-store"           # answers about patients are never kept by a browser or proxy
    if secure:
        headers["Strict-Transport-Security"] = "max-age=31536000"
    return headers


# The language model answers one question at a time on a small server. Two seats: a third request does not wait
# in a queue that a spammer could fill, it gets the standard questions (or no summary) at once. Patients' phones
# may hold one of the two at most, so the other is always there for the nurse's summary and the doctor's note.
_model_seats = threading.BoundedSemaphore(2)
_patient_seat = threading.BoundedSemaphore(1)


class ModelBusy(Exception):
    """No seat at the language model is free for this caller."""


class model_seat:
    def __init__(self, patient=False):
        self.patient = patient

    def __enter__(self):
        if self.patient and not _patient_seat.acquire(blocking=False):
            raise ModelBusy()
        if not _model_seats.acquire(blocking=False):
            if self.patient:
                _patient_seat.release()
            raise ModelBusy()
        return self

    def __exit__(self, *error):
        _model_seats.release()
        if self.patient:
            _patient_seat.release()
        return False
