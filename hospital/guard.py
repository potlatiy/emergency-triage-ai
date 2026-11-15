"""The hospital's guard against spam and abuse. Plain rules, no outside service.

Meddy is open to the internet and the patient's side needs no sign-in: anybody can register a visit. Without a
guard, one script could fill the nurse's list with made-up patients, press "I feel worse" for every waiting
patient, keep the language model busy for everybody, or send a body so large the server runs out of memory.

What is here:
  - limits         how often one address, and everybody together, may call the open addresses
  - body size      a request body larger than a questionnaire could ever be is refused
  - headers        what every answer tells the browser: which outside addresses a page may load from and send to,
                   who may frame it, and not to cache patients' data
  - model seats    at most two questions to the language model at once; a third gets the standard questions

What is not here, and why, is written down in SECURITY.md.
"""

import ipaddress
import os
import threading
import time
from collections import deque

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

_calls = {}                     # (address or EVERYBODY, limited address) -> the times of the last calls
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
        if len(_calls) > 20000:                 # a flood of addresses must not fill the memory either
            for key in [k for k, v in _calls.items() if not v or now - v[-1] > 600]:
                del _calls[key]
    return None


def forget():
    """Start counting from nothing (the tests)."""
    with _lock:
        _calls.clear()


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


# What a page may do in the browser. The pages keep their scripts and styles inside the page, so 'unsafe-inline'
# stays; what this policy adds is the outside: a page may load scripts only from Meddy and the QR library's host,
# fonts only from Google Fonts, and may send what it knows (connect-src) to Meddy's own server and nowhere else.
# So even if somebody did get a script into a page, it could not post the patients' names to another site.
# frame-ancestors: only Hugging Face may show Meddy inside its own page; nobody can hide it under a fake screen.
CONTENT_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com",
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


def security_headers(path, secure):
    """The headers every answer carries (secure: the request came over https)."""
    headers = {
        "Content-Security-Policy": CONTENT_POLICY,
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
