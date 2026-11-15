"""The second layer of the guard: only a page's own scripts run, an address that keeps being refused is blocked,
limits tighten under pressure, the chief doctor sees what was refused, and the log of the visits is sealed."""

import base64
import hashlib

import pytest

import db
import guard
import server
from starlette.testclient import TestClient
from test_hospital import client, fresh_day, in_the_waiting_room, new_visit  # noqa: F401  (fresh_day: a new day before every test)

stranger = TestClient(server.app)
BODY = {"complaint": "sore throat", "age": 30, "sex": "Female", "use_model": False}


@pytest.fixture
def limits(monkeypatch):
    monkeypatch.setattr(guard, "ON", True)
    monkeypatch.setattr(guard, "BEHIND_PROXY", True)
    guard.forget()
    yield
    guard.forget()


# ---------------------------------------------------------------- only a page's own scripts run

def test_a_page_lists_the_fingerprint_of_each_of_its_own_scripts():
    html = "<p>x</p><script src='/static/a.js'></script><script>let a = 1;\r\n</script><SCRIPT type=module>go()</SCRIPT >"
    marks = guard.script_marks(html)
    expected = ["'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"
                for text in ("let a = 1;\n", "go()")]              # as the browser reads it: \r\n is one line end
    assert list(marks) == expected                                  # the script from a file needs no fingerprint


def test_every_page_allows_its_own_scripts_and_no_others():
    for path, name in server.PAGES.items():
        policy = client.get(path).headers["content-security-policy"]
        scripts = next(part for part in policy.split("; ") if part.startswith("script-src"))
        assert "'unsafe-inline'" not in scripts and "'unsafe-eval'" not in scripts, path
        marks = guard.script_marks((server.STATIC / name).read_text(encoding="utf-8"))
        assert all(mark in scripts for mark in marks), path
        assert scripts.count("'sha256-") == len(marks), path
    api = client.get("/api/version").headers["content-security-policy"]
    assert "'unsafe-inline'" not in api.split("; ")[1] and "'sha256-" not in api


def test_no_page_relies_on_a_handler_written_inside_a_tag():
    """The policy does not run them (that is what stops <img onerror=...> in a patient's text), so a page that
    used one would silently lose that behaviour."""
    import re
    for page in sorted(server.STATIC.glob("*.html")) + sorted(server.STATIC.glob("*.js")):
        found = re.findall(r"<[a-zA-Z][^<>]*\son[a-z]+\s*=\s*[\"']", page.read_text(encoding="utf-8"))
        assert found == [], (page.name, found[:2])


def test_the_strict_policy_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(guard, "STRICT_SCRIPTS", False)
    assert "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com" in client.get("/").headers["content-security-policy"]


def test_the_list_of_every_address_is_not_served():
    assert [client.get(path).status_code for path in ("/docs", "/redoc", "/openapi.json")] == [404, 404, 404]


# ---------------------------------------------------------------- blocking

def test_an_address_that_keeps_being_refused_is_blocked_and_longer_each_time(limits):
    for n in range(guard.STRIKES - 1):
        guard.note("wrong_phone", "5.6.7.8", "/api/worse", now=n)
    assert guard.blocked("5.6.7.8", "/api/register", now=30) is None
    guard.note("wrong_phone", "5.6.7.8", "/api/worse", now=30)
    assert guard.blocked("5.6.7.8", "/api/register", now=31) == guard.BLOCK_FOR
    assert guard.blocked("5.6.7.8", "/api/follow-up/P-2026-0001", now=31)
    assert guard.blocked("5.6.7.8", "/api/me/P-2026-0001", now=31) is None      # the phone still shows its place
    assert guard.blocked("5.6.7.8", "/api/board", now=31) is None
    assert guard.blocked("9.9.9.9", "/api/register", now=31) is None            # nobody else is touched
    assert guard.blocked("5.6.7.8", "/api/register", now=31 + guard.BLOCK_FOR) is None
    for n in range(guard.STRIKES):                                              # the same again: twice as long
        guard.note("too_many", "5.6.7.8", "/api/register", now=1000 + n)
    assert guard.blocked("5.6.7.8", "/api/register", now=1030) > guard.BLOCK_FOR


def test_old_refusals_are_forgotten(limits):
    for n in range(guard.STRIKES * 2):
        guard.note("wrong_phone", "5.6.7.8", "/api/worse", now=n * guard.STRIKE_WINDOW / 10)
    assert guard.blocked("5.6.7.8", "/api/worse", now=10 ** 6) is None and not guard.report()["blocked_now"]


def test_reading_too_often_never_blocks(limits):
    for _ in range(guard.STRIKES + 5):
        guard.note("too_many", "5.6.7.8", "/api/me/P-2026-0001", strike=False)
    assert guard.blocked("5.6.7.8", "/api/register") is None


def test_a_script_trying_other_patients_visits_is_shut_out(limits):
    reg = in_the_waiting_room()
    spammer = {"X-Forwarded-For": "5.6.7.8"}
    codes = [stranger.post("/api/worse", json={"reg_no": reg}, headers=spammer).status_code for _ in range(40)]
    assert codes[:6] == [403] * 6 and set(codes[6:]) == {429}
    refused = stranger.post("/api/register", json=BODY, headers=spammer)        # blocked from registering too
    assert refused.status_code == 429 and "speak to the staff" in refused.json()["detail"]
    assert int(refused.headers["retry-after"]) > 60
    assert stranger.get("/api/me/" + reg, headers=spammer).status_code == 200  # reading is never blocked
    assert stranger.post("/api/register", json=BODY, headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 200
    assert db.visit_for(reg)["worse_at"] is None


def test_signed_in_staff_are_never_limited_or_blocked(limits, monkeypatch):
    """A nurse may share the hospital's Wi-Fi, and so its address, with the phone that spams."""
    monkeypatch.setattr(server, "STAFF_PIN", "4821")
    wifi = {"X-Forwarded-For": "5.6.7.8"}
    for n in range(guard.STRIKES):
        guard.note("wrong_phone", "5.6.7.8", "/api/worse")
    assert stranger.post("/api/register", json=BODY, headers=wifi).status_code == 429
    nurse = TestClient(server.app)
    assert nurse.post("/api/staff/login", json={"pin": "4821"}, headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 200
    codes = [nurse.post("/api/register", json=BODY, headers=wifi).status_code for _ in range(12)]
    assert codes == [200] * 12


def test_addresses_are_forgotten_when_no_longer_needed(limits):
    """What the privacy page promises: no visitor's address stays in memory for more than two hours."""
    guard.too_many("5.6.7.8", "/api/register", now=0)
    for n in range(guard.STRIKES):
        guard.note("wrong_phone", "5.6.7.8", "/api/worse", now=n)
    assert guard.blocked("5.6.7.8", "/api/worse", now=40)
    guard.too_many("9.9.9.9", "/api/register", now=2 * 3600 + 60)          # somebody else, two hours later
    kept = str(guard._calls) + str(guard._strikes) + str(guard._blocked)
    assert "5.6.7.8" not in kept and "9.9.9.9" in kept


# ---------------------------------------------------------------- limits under pressure

def test_under_pressure_one_address_gets_a_smaller_share(limits, monkeypatch):
    monkeypatch.setitem(guard.LIMITS, "/api/register", ((10, 60), (20, 600)))
    for n in range(10):                                             # ten addresses, one registration each: half of 20
        assert guard.too_many(f"8.8.{n}.1", "/api/register", now=n) is None
    assert "/api/register" in guard.report(now=10)["pressure"]
    waits = [guard.too_many("5.6.7.8", "/api/register", now=11) for _ in range(4)]
    assert waits[:3] == [None] * 3 and waits[3]                     # 3 instead of 10
    assert guard.too_many("8.8.200.1", "/api/register", now=12) is None         # a new patient still gets in


# ---------------------------------------------------------------- what the chief doctor sees

def test_the_chief_doctor_sees_what_was_refused_without_any_address(limits):
    reg = new_visit()
    stranger.post("/api/checkin", json={"reg_no": reg}, headers={"X-Forwarded-For": "5.6.7.8"})
    stranger.post("/api/register", content="x" * (guard.MAX_BODY + 1), headers={"Content-Type": "application/json"})
    seen = client.get("/api/chief/security").json()
    assert seen["refused"]["wrong_phone"] == 1 and seen["refused"]["too_large"] == 1 and seen["limits_on"] is True
    assert [line["why"] for line in seen["recent"]] == ["too_large", "wrong_phone"]
    assert seen["recent"][1]["where"] == "/api/checkin" and len(seen["recent"][1]["who"]) == 8
    assert "5.6.7.8" not in str(seen) and "testclient" not in str(seen)
    assert seen["log"]["whole"] is True and seen["log"]["lines"] > 0


def test_the_security_figures_are_for_the_chief_doctor_only(monkeypatch):
    monkeypatch.setattr(server, "STAFF_PIN", "4821")
    assert stranger.get("/api/chief/security").status_code == 401


def test_a_wrong_pin_is_written_down(limits, monkeypatch):
    monkeypatch.setattr(server, "STAFF_PIN", "4821")
    assert stranger.post("/api/staff/login", json={"pin": "0000"}).status_code == 401
    assert guard.report()["refused"]["wrong_pin"] == 1


# ---------------------------------------------------------------- the sealed log

def change(sql, *values):
    """What somebody with the database file could do behind Meddy's back."""
    with db._lock:
        db._conn.execute(sql, values)
        db._conn.commit()


def broken():
    check = db.log_check()
    return {b["visit"]: b["how"] for b in check["broken"]} if not check["whole"] else {}


def test_an_untouched_log_is_whole_through_a_whole_visit():
    reg = in_the_waiting_room()
    client.post("/api/worse", json={"reg_no": reg})
    client.post("/api/nurse/still-fine", json={"reg_no": reg})
    db.call_next(reg)
    check = db.log_check()
    assert check["whole"] is True and check["broken"] == [] and check["older"] == 0
    assert check["lines"] == db._conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] > 5


def test_a_changed_line_is_noticed():
    reg, other = in_the_waiting_room(level=4), in_the_waiting_room(level=3)
    change("UPDATE events SET detail = replace(detail, 'level 4', 'level 2') WHERE visit_reg = ? AND what = 'in line'", reg)
    assert list(broken()) == [reg] and "changed" in broken()[reg]              # the other visit's log is still whole
    assert other not in broken()


def test_a_line_taken_out_of_the_middle_is_noticed():
    reg = in_the_waiting_room()
    change("DELETE FROM events WHERE visit_reg = ? AND what = 'checked in'", reg)
    assert reg in broken()


def test_the_end_of_a_log_cut_off_is_noticed():
    reg = in_the_waiting_room()
    client.post("/api/worse", json={"reg_no": reg})
    change("DELETE FROM events WHERE visit_reg = ? AND what = 'said they feel worse'", reg)
    assert "cut off" in broken()[reg]
    change("DELETE FROM events WHERE visit_reg = ?", reg)
    assert "every line" in broken()[reg]


def test_a_line_added_without_the_key_is_noticed():
    reg = in_the_waiting_room()
    change("INSERT INTO events (visit_reg, day, clock, at, what, who, detail, seal) "
           "SELECT visit_reg, day, clock, at, 'in line', 'nurse', 'level 1', seal FROM events WHERE visit_reg = ? LIMIT 1", reg)
    assert reg in broken()
    change("DELETE FROM events WHERE visit_reg = ? AND detail = 'level 1'", reg)
    assert broken() == {}
    change("INSERT INTO events (visit_reg, day, clock, at, what) SELECT visit_reg, day, clock, at, 'in line' FROM events "
           "WHERE visit_reg = ? LIMIT 1", reg)                                   # a line with no seal at all
    assert "lost its seal" in broken()[reg]


def test_lines_from_before_sealing_are_counted_not_blamed():
    reg = in_the_waiting_room()
    last = db._conn.execute("SELECT MAX(id) AS n FROM events").fetchone()["n"]
    change("UPDATE events SET seal = NULL")
    change("UPDATE visits SET log_seal = NULL")
    change("UPDATE meta SET value = ? WHERE key = 'sealed_from'", last + 1)     # as _upgrade leaves an older file
    client.post("/api/worse", json={"reg_no": reg})                             # the first sealed line of this visit
    check = db.log_check()
    assert check["whole"] is True and check["older"] == last and check["lines"] == 1


def test_removing_old_made_up_visits_leaves_the_log_whole():
    made_up = next(p for p in db.line()["patients"] if not p["you"])["reg_no"]
    change("UPDATE visits SET status = 'done', closed_at = '2020-01-01T00:00:00+00:00' WHERE reg_no = ?", made_up)
    assert db.forget_old_made_up() >= 1 and db.log_check()["whole"] is True


def test_a_new_day_and_starting_from_scratch_leave_the_log_whole():
    in_the_waiting_room()
    db.new_day()
    assert db.log_check()["whole"] is True
    db.wipe_everything()
    assert db.log_check()["whole"] is True and db.log_check()["older"] == 0
