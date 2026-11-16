"""What three independent reviewers found in the guard, each with the test that keeps it closed.
(The sealed log's findings are with the other tests of the log, in test_guard_advanced.py.)"""

import re

import pytest

import core
import db
import guard
import server
from starlette.testclient import TestClient
from test_hospital import client, fresh_day, in_the_waiting_room, new_visit  # noqa: F401  (fresh_day: a new day before every test)

stranger = TestClient(server.app)
BODY = {"complaint": "sore throat", "age": 30, "sex": "Female", "use_model": False}


# ---------------------------------------------------------------- another patient's complaint

def test_the_follow_up_questions_are_for_the_visits_own_phone_only(monkeypatch):
    """The model words its questions with the patient's complaint. Anybody could ask for the questions of any
    registration number, and so read "When did the crushing chest pain start?" for a patient they never met."""
    monkeypatch.setattr(core, "_written", {})
    monkeypatch.setattr(core, "questions_from_model", lambda complaint, skip=(): [
        {"question": "When did the crushing chest pain start?", "topic": "onset"}])
    arrival = stranger.post("/api/register", json={**BODY, "complaint": "crushing chest pain"}).json()
    reg, key = arrival["reg_no"], arrival["key"]
    refused = stranger.get("/api/follow-up/" + reg)
    assert refused.status_code == 403 and "crushing" not in refused.text
    assert stranger.get("/api/follow-up/" + reg, headers={"X-Meddy-Visit": "a-guess"}).status_code == 403
    own = stranger.get("/api/follow-up/" + reg, headers={"X-Meddy-Visit": key})
    assert own.status_code == 200 and "crushing chest pain" in own.text
    stranger.post("/api/checkin", json={"reg_no": reg}, headers={"X-Meddy-Visit": key})
    assert stranger.get("/api/follow-up/" + reg, headers={"X-Meddy-Visit": key}).status_code == 409    # check-in is done


# ---------------------------------------------------------------- requests another website starts

@pytest.mark.parametrize("kind", ["application/x-www-form-urlencoded", "multipart/form-data; boundary=x", "text/plain",
                                  "Text/Plain; charset=utf-8"])
def test_a_plain_html_form_cannot_press_a_staff_button(kind):
    """"Start a new day" takes no body, so a form on any website (or one slipped into a page) could press it."""
    reg = in_the_waiting_room()
    for path in ("/api/reset", "/api/line/next", "/api/line/wait", "/api/staff/logout", "/api/register"):
        refused = client.post(path, content="x=1", headers={"Content-Type": kind})
        assert refused.status_code == 403 and "not sent by a Meddy page" in refused.json()["detail"], path
    assert db.visit_for(reg)["status"] == "waiting"                              # nobody's day was closed


def test_a_request_started_by_another_website_is_refused():
    assert client.post("/api/reset", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/register", json=BODY, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert guard.report()["refused"]["other_site"] >= 2
    # Meddy's own pages, a phone's first visit, and tools without a browser are not touched
    for site in ("same-origin", "same-site", "none"):
        assert client.post("/api/register", json=BODY, headers={"Sec-Fetch-Site": site}).status_code == 200, site
    assert client.post("/api/line/next").status_code in (200, 409)              # no body, no Content-Type: as the lock button sends
    assert client.get("/api/board", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200   # reading changes nothing


# ---------------------------------------------------------------- the browser's policy

def policy(path="/"):
    text = client.get(path).headers["content-security-policy"]
    return {part.split(" ")[0]: part.split(" ")[1:] for part in text.split("; ")}


def test_only_the_one_file_of_the_qr_library_may_be_loaded_from_its_host():
    """The whole host was allowed. It also serves libraries that run code written in a page's text, which would
    have made the fingerprints of the scripts worthless."""
    for path in server.PAGES:
        outside = [s for s in policy(path)["script-src"] if s.startswith("http")]
        assert outside == [guard.QR_LIBRARY], path
    assert guard.QR_LIBRARY in (server.STATIC / "index.html").read_text(encoding="utf-8")       # the file the page loads


def test_a_style_sheet_put_into_a_page_is_not_used():
    for path, name in server.PAGES.items():
        html = (server.STATIC / name).read_text(encoding="utf-8")
        sheets = policy(path)["style-src-elem"]
        assert "'unsafe-inline'" not in sheets, path
        assert [s for s in sheets if s.startswith("'sha256-")] == list(guard.style_marks(html)), path
        assert len(guard.style_marks(html)) == len(re.findall(r"<style\b", html, re.I)), path
        assert policy(path)["style-src-attr"] == ["'unsafe-inline'"]            # a style inside one tag: the pages use them


def test_no_frames_no_forms_sent_and_only_hugging_faces_own_page_may_frame_meddy():
    p = policy("/nurse")
    assert p["frame-src"] == ["'none'"] and p["form-action"] == ["'none'"] and p["object-src"] == ["'none'"]
    assert p["frame-ancestors"] == ["'self'", "https://huggingface.co"]         # not any Space of anybody


def test_every_script_block_of_every_page_gets_a_fingerprint():
    """If the search for scripts ever disagreed with the browser, a page's own script would silently not run."""
    for name in server.PAGES.values():
        html = (server.STATIC / name).read_text(encoding="utf-8")
        blocks = len(re.findall(r"<script\b", html, re.I))
        from_files = len(re.findall(r"<script\b[^>]*\ssrc\s*=", html, re.I))
        assert len(guard.script_marks(html)) == blocks - from_files, name
        assert "</script " not in html and "<!--<script" not in html.replace(" ", ""), name


def test_the_search_for_scripts_is_not_fooled_by_an_attribute_that_ends_in_src():
    assert len(guard.script_marks('<script data-src="x">go()</script>')) == 1
    assert guard.script_marks('<script src="/static/a.js"></script>') == ()


def test_an_answer_that_is_not_a_page_may_run_no_script_at_all():
    assert "'unsafe-inline'" not in guard.content_policy().split("; ")[1]       # asked without fingerprints: none, not all
    assert "'unsafe-inline'" not in guard.security_headers("/x", False)["Content-Security-Policy"].split("; ")[1]
    assert "'sha256-" not in client.get("/static/nurse.html").headers["content-security-policy"]   # the bare file: blocked


# ---------------------------------------------------------------- characters nobody can see

def test_characters_that_turn_text_around_or_cannot_be_seen_are_taken_out():
    """U+202E makes what follows read right to left: "no chest pain" can be shown as something else."""
    sent = "sore\u202e throat\u0000 since\u2066 Monday\u2029"
    reg = stranger.post("/api/register", json={**BODY, "complaint": sent}).json()["reg_no"]
    assert db.visit_for(reg)["complaint"] == "sore throat since Monday"
    kept = "ناراحتی\u200cقلبی\nsecond line"                                   # a zero-width non-joiner and a line break stay
    reg = stranger.post("/api/register", json={**BODY, "complaint": kept}).json()["reg_no"]
    assert db.visit_for(reg)["complaint"] == kept


def test_a_danger_answer_carries_no_topic():
    reg = new_visit()
    question = core.danger_questions_for(db.visit_for(reg)["complaint"])[0]
    body = {"reg_no": reg, "answers": [{"question": question, "answer": "No", "kind": "danger", "topic": "<b>"}]}
    assert client.post("/api/checkin", json=body).status_code == 200
    assert "<b>" not in str([dict(r) for r in db._conn.execute("SELECT * FROM answers WHERE visit_reg = ?", (reg,))])


# ---------------------------------------------------------------- requests that do nothing must not close the hospital

@pytest.fixture
def limits(monkeypatch):
    monkeypatch.setattr(guard, "ON", True)
    monkeypatch.setattr(guard, "BEHIND_PROXY", True)
    guard.forget()
    yield
    guard.forget()


def test_requests_that_do_nothing_do_not_use_up_the_limit_for_everybody(limits):
    """430 requests the hospital refuses anyway (no complaint, a number that does not exist, no PIN) used to close
    registration, check-in, "I feel worse" and the staff sign-in for every real patient and nurse."""
    reg = in_the_waiting_room()
    key = db.phone_key(reg)
    for n in range(100):                                            # a script behind ever new addresses
        who = {"X-Forwarded-For": f"8.8.{n}.1"}
        assert stranger.post("/api/register", json={}, headers=who).status_code == 422
        assert stranger.post("/api/worse", json={"reg_no": "nope"}, headers=who).status_code == 404
        assert stranger.post("/api/checkin", json={"reg_no": "nope"}, headers=who).status_code == 404
    patient = {"X-Forwarded-For": "5.6.7.8"}
    assert stranger.post("/api/worse", json={"reg_no": reg}, headers={**patient, "X-Meddy-Visit": key}).status_code == 200
    assert db.visit_for(reg)["worse_at"] is not None                # the nurse was told
    assert stranger.post("/api/register", json=BODY, headers=patient).status_code == 200
    assert guard.report()["pressure"] == [] and guard.report()["blocked_now"] == []


def test_i_feel_worse_and_check_in_are_never_refused_because_of_other_people():
    assert guard.limited("/api/worse")[1][1] is None and guard.limited("/api/checkin")[1][1] is None


def test_being_refused_because_everybody_is_over_the_limit_is_nobodys_strike(limits, monkeypatch):
    """Real patients were refused during a flood, each refusal counted against the waiting room's shared address,
    and after 30 of them the hospital's own Wi-Fi was blocked."""
    monkeypatch.setitem(guard.LIMITS, "/api/register", ((10, 60), (3, 600)))
    for n in range(3):
        assert stranger.post("/api/register", json=BODY, headers={"X-Forwarded-For": f"8.8.{n}.1"}).status_code == 200
    wifi = {"X-Forwarded-For": "5.6.7.8"}
    answers = [stranger.post("/api/register", json=BODY, headers=wifi) for _ in range(40)]
    assert {a.status_code for a in answers} == {429} and "very busy" in answers[0].json()["detail"]
    assert guard.blocked("5.6.7.8", "/api/register") is None and guard._strikes.get("5.6.7.8") is None


def test_one_address_cannot_keep_registering_all_hour(limits):
    """Ten a minute, for ever, was 58 made-up patients on the nurse's list in ten minutes without one refusal."""
    waits = [guard.check("5.6.7.8", "/api/register", now=minute * 61 + n) for minute in range(5) for n in range(10)]
    assert [w for w, _ in waits[:40]] == [None] * 40 and all(w and own for w, own in waits[40:])
    assert guard.check("5.6.7.8", "/api/register", now=3700)[0] is None         # an hour later


# ---------------------------------------------------------------- who is asking

def test_the_visitor_cannot_choose_who_they_are(limits):
    ask = guard.client_address
    assert ask("6.6.6.6, 93.184.216.34:51234", "10.0.0.5") == "93.184.216.34"           # a port does not hide the real one
    assert ask("6.6.6.6, [2a01:4f8:1:2:aaaa:bbbb:cccc:dddd]:443", "10.0.0.5") == "2a01:4f8:1:2::/64"
    assert ask("6.6.6.6, unknown", "10.0.0.5") == "10.0.0.5"                             # not an address: nothing left of it is believed
    assert ask("6.6.6.6, 2002:c000:204::1", "10.0.0.5") == "2002:c000:204::/64"          # an unusual address is still the visitor's
    assert ask("6.6.6.6, ::ffff:93.184.216.34", "10.0.0.5") == "93.184.216.34"           # the same visitor, written as IPv6
    assert ask("6.6.6.6, 93.184.216.34, 100.64.0.9, 10.1.2.3", "10.0.0.5") == "93.184.216.34"


def test_every_address_of_one_ipv6_block_is_one_visitor(limits):
    codes = [stranger.post("/api/register", json=BODY, headers={"X-Forwarded-For": f"2a01:4f8:1:2::{n:x}"}).status_code
             for n in range(1, 14)]
    assert codes == [200] * 10 + [429] * 3


def test_a_second_header_line_does_not_help(limits):
    """Only the first X-Forwarded-For line was read. With the visitor's own line first and the proxy's second, the
    visitor chose who they were."""
    import httpx
    codes = []
    for n in range(13):
        headers = httpx.Headers([("X-Forwarded-For", f"7.7.7.{n}"), ("X-Forwarded-For", "93.184.216.34")])
        codes.append(stranger.post("/api/register", json=BODY, headers=headers).status_code)
    assert codes == [200] * 10 + [429] * 3


# ---------------------------------------------------------------- the staff sign-in

def test_a_blocked_address_can_still_sign_in_as_staff(limits, monkeypatch):
    """The block used to cover the PIN: a phone on the hospital's Wi-Fi could shut the nurse out of signing in."""
    monkeypatch.setattr(server, "STAFF_PIN", "4821")
    wifi = {"X-Forwarded-For": "5.6.7.8"}
    for _ in range(guard.STRIKES):
        guard.note("wrong_phone", "5.6.7.8", "/api/checkin")
    assert stranger.post("/api/register", json=BODY, headers=wifi).status_code == 429
    assert TestClient(server.app).post("/api/staff/login", json={"pin": "4821"}, headers=wifi).status_code == 200


def test_a_cookie_with_a_letter_outside_english_is_refused_not_a_crash(monkeypatch):
    monkeypatch.setattr(server, "STAFF_PIN", "4821")
    monkeypatch.setattr(server, "CHIEF_PIN", "9173")
    odd = TestClient(server.app, raise_server_exceptions=False)
    assert odd.get("/api/line", headers={"Cookie": b"meddy_staff=\xe9"}).status_code == 401
    assert odd.get("/api/chief/report", headers={"Cookie": b"meddy_chief=\xe9"}).status_code == 401
    assert odd.get("/api/version", headers={"Cookie": b"meddy_staff=\xe9"}).status_code == 200


# ---------------------------------------------------------------- the language model and the memory

def test_patients_phones_can_hold_one_seat_at_the_model_so_staff_always_have_one():
    with guard.model_seat(patient=True):
        with pytest.raises(guard.ModelBusy):
            with guard.model_seat(patient=True):                    # a second phone: the standard questions
                pass
        with guard.model_seat():                                    # the nurse's summary still gets its seat
            pass
    with guard.model_seat(), guard.model_seat():
        with pytest.raises(guard.ModelBusy):
            with guard.model_seat(patient=True):
                pass
    with guard.model_seat(patient=True), guard.model_seat():        # every seat was given back
        pass


def test_a_flood_of_addresses_is_not_tidied_at_every_call(limits, monkeypatch):
    """Past 20,000 remembered addresses the whole table was walked at every request."""
    for n in range(20001):
        guard._calls[(f"x{n}", "/api/me/")] = guard.deque([100.0])
    monkeypatch.setattr(guard, "_tidied", 100.0)
    before = guard._tidied
    for n in range(50):
        guard.check(f"9.9.{n}.1", "/api/me/x", now=101 + n * 0.01)
    assert guard._tidied == before                                  # 50 calls inside five seconds: not once
    guard.check("9.9.99.1", "/api/me/x", now=106)
    assert guard._tidied == 106
