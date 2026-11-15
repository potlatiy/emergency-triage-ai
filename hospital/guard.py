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

# Each open address: (how many calls one address may make, in how many seconds), (the same for everybody together).
# One address is generous enough for a classroom behind one router; "everybody" bounds what a spammer can do who
# hides behind many addresses. The addresses the pages ask again and again (a phone asks about its visit every few
# seconds, the board every five) only read, so their limits are far higher: they stop a flood, not a waiting room.
LIMITS = {
    "/api/register":     ((10, 60), (90, 600)),
    "/api/checkin":      ((20, 60), (180, 600)),
    "/api/worse":        ((6, 60), (40, 600)),
    "/api/follow-up/":   ((20, 60), (180, 600)),
    "/api/spelling":     ((240, 60), (3000, 600)),
    "/api/staff/login":  ((10, 60), (120, 600)),
    "/api/me/":          ((1200, 60), None),
    "/api/board":        ((300, 60), None),
    "/api/waiting-room": ((300, 60), None),
}
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
WRITES = ("/api/register", "/api/checkin", "/api/worse", "/api/follow-up/", "/api/staff/login")

_calls = {}                     # (address or EVERYBODY, limited address) -> the times of the last calls
_strikes = {}                   # address -> the times it was refused lately
_blocked = {}                   # address -> (blocked until, how many times it has been blocked)
_refused = Counter()            # why -> how many requests were refused since the server started
_log = deque(maxlen=200)        # the last refusals: (clock time, why, fingerprint of the address, where)
_since = datetime.now()
_secret = os.urandom(16)        # new at every start: a fingerprint cannot be turned back into an address
_tidied = 0.0                   # when addresses no longer needed were last forgotten (see _tidy)
_lock = threading.Lock()


def client_address(headers, peer):
    """Where a request really comes from.

    Online, every request reaches the server from Hugging Face's proxy, which writes the visitor's address at the
    END of X-Forwarded-For. Anything before that, the visitor may have typed themselves, so it is not believed:
    the address used is the right-most one that is not a private (in-house) address. The staff sign-in used to
    read the FIRST one, so a script could change that header at every try and never be made to wait.

    Without a proxy in front (BEHIND_PROXY) the header is not read at all: the caller wrote all of it."""
    for part in reversed((headers.get("x-forwarded-for") or "").split(",")) if BEHIND_PROXY else ():
        try:
            address = ipaddress.ip_address(part.strip())
        except ValueError:
            continue
        if not (address.is_private or address.is_loopback or address.is_link_local):
            return str(address)
    return peer or "unknown"


def limited(path):
    """The limit a path falls under, as (name, limits), or None for a path without one."""
    for name, limits in LIMITS.items():
        if path == name or (name.endswith("/") and path.startswith(name)):
            return name, limits
    return None


def too_many(address, path, now=None):
    """Count this call. Returns the seconds to wait when the address, or everybody together, has called this path
    too often; None when the call may go ahead."""
    found = limited(path) if ON else None
    if found is None:
        return None
    name, (one, everybody) = found
    now = time.monotonic() if now is None else now
    with _lock:
        if everybody is not None and _pressure(name, everybody, now):
            one = (max(2, one[0] // PRESSURE_SHARE), one[1])
        waits = []
        for who, limit in ((address, one), (EVERYBODY, everybody)):
            if limit is None:
                continue
            count, seconds = limit
            calls = _calls.setdefault((who, name), deque())
            while calls and now - calls[0] >= seconds:
                calls.popleft()
            if len(calls) >= count:
                waits.append(max(1, int(seconds - (now - calls[0])) + 1))
        if waits:
            return max(waits)
        for who, limit in ((address, one), (EVERYBODY, everybody)):
            if limit is not None:
                _calls[(who, name)].append(now)
        _tidy(now)
    return None


def _tidy(now):
    """Forget every address that is no longer needed, once a minute: calls older than ten minutes, refusals older
    than STRIKE_WINDOW, and a block an hour after it ended (until then it decides how long the next one is).
    So no visitor's address stays in memory for more than two hours, and a flood of addresses cannot fill the
    memory. The caller holds the lock."""
    global _tidied
    if now - _tidied < 60 and len(_calls) < 20000:
        return
    _tidied = now
    for key in [k for k, v in _calls.items() if not v or now - v[-1] > 600]:
        del _calls[key]
    for key in [k for k, v in _strikes.items() if not v or now - v[-1] > STRIKE_WINDOW]:
        del _strikes[key]
    for key in [k for k, (until, _) in _blocked.items() if now - until > 3600]:
        del _blocked[key]


def _pressure(name, everybody, now):
    """Has everybody together used half of this address's limit? The caller holds the lock."""
    count, seconds = everybody
    calls = _calls.get((EVERYBODY, name), ())
    return sum(1 for t in calls if now - t < seconds) >= count * PRESSURE_AT


def fingerprint(address):
    """Eight characters that stand for an address in the log. The same address gives the same fingerprint until
    the server restarts; the address itself is never written down."""
    return hashlib.sha256(_secret + str(address).encode()).hexdigest()[:8]


def note(why, address, path, strike=True, now=None):
    """Write a refusal into the log. With strike, it also counts against the address: STRIKES of them within
    STRIKE_WINDOW seconds block it (see blocked). why: too_many, too_large, wrong_phone, wrong_pin, blocked."""
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
            "refused": {why: _refused.get(why, 0) for why in ("too_many", "blocked", "wrong_phone", "wrong_pin", "too_large")},
            "blocked_now": [{"who": fingerprint(a), "minutes": int((until - now) // 60) + 1, "times": times}
                            for a, (until, times) in _blocked.items() if until > now],
            "pressure": [name for name, (_, everybody) in LIMITS.items()
                         if everybody is not None and _pressure(name, everybody, now)],
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
# Outside: fonts only from Google Fonts; a page may send what it knows (connect-src) to Meddy's own server and
# nowhere else. frame-ancestors: only Hugging Face may show Meddy inside its own page.
STRICT_SCRIPTS = os.environ.get("MEDDY_STRICT_SCRIPTS", "on") != "off"
_INLINE_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.DOTALL | re.IGNORECASE)
_marks = {}                     # page file -> (when it was last changed, the fingerprints of its scripts)


def script_marks(html):
    """The fingerprint of every script written inside a page, as the browser works it out: SHA-256 of the script's
    text, in base64. A script loaded from a file (src=) has none: it is allowed by where it comes from."""
    marks = []
    for attributes, text in _INLINE_SCRIPT.findall(html.replace("\r\n", "\n").replace("\r", "\n")):
        if not re.search(r"\bsrc\s*=", attributes, re.IGNORECASE):
            marks.append("'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'")
    return tuple(marks)


def marks_of(page):
    """The script fingerprints of a page file, worked out again only when the file has changed."""
    changed = page.stat().st_mtime_ns
    kept = _marks.get(page)
    if kept is None or kept[0] != changed:
        kept = _marks[page] = (changed, script_marks(page.read_text(encoding="utf-8")))
    return kept[1]


def content_policy(scripts=None):
    """The policy for one answer. scripts: the fingerprints of the page's own scripts (see script_marks); None for
    an answer that is not a page."""
    inline = " ".join(scripts) if STRICT_SCRIPTS and scripts is not None else "'unsafe-inline'"
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' {inline} https://cdnjs.cloudflare.com".replace("  ", " "),
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: blob:",
        "media-src 'self'",
        "connect-src 'self'",
        "frame-ancestors 'self' https://huggingface.co https://*.hf.space",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ])


def security_headers(path, secure, scripts=None):
    """The headers every answer carries (secure: the request came over https; scripts: see content_policy)."""
    headers = {
        "Content-Security-Policy": content_policy(scripts),
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
# in a queue that a spammer could fill, it gets the standard questions (or no summary) at once.
_model_seats = threading.BoundedSemaphore(2)


class ModelBusy(Exception):
    """Both seats at the language model are taken."""


class model_seat:
    def __enter__(self):
        if not _model_seats.acquire(blocking=False):
            raise ModelBusy()
        return self

    def __exit__(self, *error):
        _model_seats.release()
        return False
