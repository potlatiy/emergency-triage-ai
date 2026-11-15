"""The guard against spam and abuse (hospital/guard.py): limits, body size, the browser's headers, the rule that
only a visit's own phone may change it, and the two seats at the language model."""

import pytest

import core
import db
import guard
import server
from starlette.testclient import TestClient
from test_hospital import client, fresh_day, in_the_waiting_room, new_visit  # noqa: F401  (fresh_day: a new day before every test)

stranger = TestClient(server.app)          # a phone that never registered anything: it holds no visit's key


@pytest.fixture
def limits(monkeypatch):
    """The limits are off in the other tests (see conftest.py). Here they are on, counting starts from nothing, and
    the server stands behind a proxy, as it does online."""
    monkeypatch.setattr(guard, "ON", True)
    monkeypatch.setattr(guard, "BEHIND_PROXY", True)
    guard.forget()
    yield
    guard.forget()


# ---------------------------------------------------------------- who is asking

def test_the_address_is_the_one_the_proxy_wrote_not_one_the_visitor_typed(monkeypatch):
    monkeypatch.setattr(guard, "BEHIND_PROXY", True)
    typed_by_visitor = {"x-forwarded-for": "1.2.3.4, 93.184.216.34"}      # the proxy adds the real address at the end
    assert guard.client_address(typed_by_visitor, "10.0.0.5") == "93.184.216.34"
    assert guard.client_address({"x-forwarded-for": "93.184.216.34, 10.16.0.3"}, "10.0.0.5") == "93.184.216.34"   # in-house hops skipped
    assert guard.client_address({}, "127.0.0.1") == "127.0.0.1"           # on a laptop there is no proxy
    assert guard.client_address({"x-forwarded-for": "not an address"}, "127.0.0.1") == "127.0.0.1"
    assert guard.client_address({}, None) == "unknown"
    monkeypatch.setattr(guard, "BEHIND_PROXY", False)                     # on a laptop the caller wrote all of the header
    assert guard.client_address(typed_by_visitor, "192.168.1.30") == "192.168.1.30"


def test_without_a_proxy_a_typed_header_does_not_make_new_addresses(monkeypatch):
    monkeypatch.setattr(guard, "ON", True)
    guard.forget()
    body = {"complaint": "sore throat", "age": 30, "sex": "Female", "use_model": False}
    codes = [client.post("/api/register", json=body, headers={"X-Forwarded-For": f"7.7.7.{n}"}).status_code for n in range(12)]
    guard.forget()
    assert codes == [200] * 10 + [429] * 2


# ---------------------------------------------------------------- limits

def test_one_address_cannot_fill_the_nurses_list(limits):
    body = {"complaint": "sore throat", "age": 30, "sex": "Female", "use_model": False}
    spammer = {"X-Forwarded-For": "93.184.216.34"}
    answers = [client.post("/api/register", json=body, headers=spammer) for _ in range(12)]
    assert [a.status_code for a in answers] == [200] * 10 + [429] * 2
    assert 1 <= int(answers[-1].headers["retry-after"]) <= 61 and "Too many" in answers[-1].json()["detail"]
    # A header the spammer writes in front does not make them somebody else...
    assert client.post("/api/register", json=body, headers={"X-Forwarded-For": "8.8.8.8, 93.184.216.34"}).status_code == 429
    # ...and a patient at another address registers as usual.
    assert client.post("/api/register", json=body, headers={"X-Forwarded-For": "151.101.1.69"}).status_code == 200


def test_a_refused_call_is_not_counted_and_the_wait_ends():
    guard.forget()
    try:
        old = guard.ON
        guard.ON = True
        assert [guard.too_many("a", "/api/worse", now=t) for t in range(6)] == [None] * 6
        assert guard.too_many("a", "/api/worse", now=10) == 51              # the first call leaves the minute at 60
        assert guard.too_many("a", "/api/worse", now=59.5) == 1
        assert guard.too_many("a", "/api/worse", now=60) is None            # refused calls did not push it further
        assert guard.too_many("a", "/api/line", now=60) is None             # an address without a limit
    finally:
        guard.ON = old
        guard.forget()


def test_everybody_together_has_a_limit_too(limits, monkeypatch):
    """A spammer behind many addresses is held by the limit for everybody together."""
    monkeypatch.setitem(guard.LIMITS, "/api/worse", ((6, 60), (8, 600)))
    waits = [guard.too_many(f"93.184.216.{n}", "/api/worse", now=n) for n in range(10)]
    assert waits[:8] == [None] * 8 and waits[8] and waits[9]


def test_limits_cover_what_they_should():
    assert guard.limited("/api/me/P-2026-0001")[0] == "/api/me/"
    assert guard.limited("/api/follow-up/P-2026-0001")[0] == "/api/follow-up/"
    assert guard.limited("/api/register")[0] == "/api/register"
    assert guard.limited("/api/registered") is None and guard.limited("/api/line") is None and guard.limited("/") is None
    for path in guard.LIMITS:                                   # every limited address is one a patient may call
        assert path == "/api/staff/login" or path.startswith(server.FOR_PATIENTS), path


def test_guessing_the_staff_pin_is_slowed_whatever_header_is_sent(limits, monkeypatch):
    monkeypatch.setattr(server, "STAFF_PIN", "4821")
    tries = [stranger.post("/api/staff/login", json={"pin": f"{n:04d}"},
                           headers={"X-Forwarded-For": f"9.9.9.{n}, 93.184.216.34"}).status_code for n in range(12)]
    assert 200 not in tries and tries[-1] == 429


# ---------------------------------------------------------------- size

def test_an_oversized_or_unmeasured_body_is_refused_before_it_is_read():
    huge = "x" * (guard.MAX_BODY + 1)
    assert client.post("/api/register", content=huge, headers={"Content-Type": "application/json"}).status_code == 413
    assert guard.body_too_large({"content-length": str(guard.MAX_BODY)}) is False
    assert guard.body_too_large({"content-length": "many"}) is True
    assert guard.body_too_large({"transfer-encoding": "chunked"}) is True
    assert guard.body_too_large({}) is False                                # a POST without a body


def test_the_largest_real_request_fits():
    reg = new_visit()
    answers = [{"question": "q" * 300, "answer": "a" * 200, "kind": "extra", "topic": "feel"}] * 20
    body = {"reg_no": reg, "arrival": "Private car", "injury": False, "pain": 5, "answers": answers}
    assert client.post("/api/checkin", json=body).status_code == 200


# ---------------------------------------------------------------- what every answer tells the browser

def test_every_page_and_answer_carries_the_browser_protections():
    for path in ("/", "/nurse", "/doctor", "/board", "/chief", "/privacy", "/api/version", "/api/no-such-thing"):
        headers = client.get(path).headers
        policy = headers["content-security-policy"]
        assert "connect-src 'self'" in policy and "object-src 'none'" in policy and "frame-ancestors" in policy, path
        assert headers["x-content-type-options"] == "nosniff" and headers["referrer-policy"] == "no-referrer", path
    assert client.get("/api/version").headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in client.get("/").headers                  # plain http on a laptop
    assert "strict-transport-security" in client.get("/", headers={"X-Forwarded-Proto": "https"}).headers


def test_a_refusal_carries_them_too(monkeypatch):
    monkeypatch.setattr(server, "STAFF_PIN", "4821")
    refused = stranger.get("/api/line")
    assert refused.status_code == 401 and "content-security-policy" in refused.headers
    assert refused.headers["cache-control"] == "no-store"


def test_the_pages_load_nothing_the_policy_forbids():
    """Every outside address a page loads from must be one the policy names; otherwise the page breaks online."""
    import re
    allowed = ("https://cdnjs.cloudflare.com", "https://fonts.googleapis.com", "https://fonts.gstatic.com")
    loads = re.compile(r"<(?:script|img|video|audio|source|iframe|link)\b[^>]*?(?:src|href)=[\"'](https?://[^\"']+)", re.I)
    seen = 0
    for page in sorted(server.STATIC.glob("*.html")):
        for address in loads.findall(page.read_text(encoding="utf-8")):
            seen += 1
            assert address.startswith(allowed), (page.name, address)
    assert seen >= 1                                            # the search does find the QR library and the fonts


# ---------------------------------------------------------------- only the visit's own phone

def test_only_the_phone_that_registered_may_press_i_feel_worse():
    reg = in_the_waiting_room()
    assert stranger.post("/api/worse", json={"reg_no": reg}).status_code == 403
    assert stranger.post("/api/worse", json={"reg_no": reg}, headers={"X-Meddy-Visit": "a guess"}).status_code == 403
    assert stranger.post("/api/worse", json={"reg_no": reg}, headers={"X-Meddy-Visit": "ключ".encode()}).status_code == 403
    assert db.visit_for(reg)["worse_at"] is None                            # the nurse was not called for nothing
    own = {"X-Meddy-Visit": db.phone_key(reg)}
    assert stranger.post("/api/worse", json={"reg_no": reg}, headers=own).status_code == 200
    assert db.visit_for(reg)["worse_at"] is not None
    assert stranger.post("/api/worse", json={"reg_no": "P-0000-0000"}).status_code == 404


def test_only_the_phone_that_registered_may_finish_check_in():
    reg = new_visit()
    body = {"reg_no": reg, "arrival": "Walked in", "injury": False, "pain": 0, "answers": []}
    assert stranger.post("/api/checkin", json=body).status_code == 403
    assert db.visit_for(reg)["status"] == "registered"                      # nobody else answered for this patient
    assert stranger.post("/api/checkin", json=body, headers={"X-Meddy-Visit": db.phone_key(reg)}).status_code == 200


def test_a_visit_from_before_keys_stays_open():
    reg = in_the_waiting_room()
    with db._lock:
        db._conn.execute("UPDATE visits SET phone_key = NULL WHERE reg_no = ?", (reg,))
        db._conn.commit()
    assert stranger.post("/api/worse", json={"reg_no": reg}).status_code == 200


# ---------------------------------------------------------------- the language model

def test_a_third_question_does_not_queue_at_the_language_model():
    with guard.model_seat(), guard.model_seat():
        with pytest.raises(guard.ModelBusy):
            with guard.model_seat():
                pass
    with guard.model_seat():                                                # both seats are free again
        pass


def test_a_busy_model_gives_the_standard_questions_at_once(monkeypatch):
    asked = []
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *a, **k: asked.append(1))
    with guard.model_seat(), guard.model_seat():
        with pytest.raises(guard.ModelBusy):
            core.questions_from_model("chest pain since this morning")
    assert asked == []                                                      # the model was not even called


def test_a_seat_is_given_back_when_the_model_fails(monkeypatch):
    def broken(*a, **k):
        raise OSError("the model is off")
    monkeypatch.setattr(core.urllib.request, "urlopen", broken)
    for _ in range(5):
        with pytest.raises(OSError):
            core.questions_from_model("chest pain since this morning")
    assert guard._model_seats.acquire(blocking=False) and guard._model_seats.acquire(blocking=False)
    guard._model_seats.release(); guard._model_seats.release()
