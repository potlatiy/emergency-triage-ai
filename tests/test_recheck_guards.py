"""Two guards on the re-check of a waiting patient, kept in a file of their own so that they do not collide with
the tests the team keeps adding at the end of test_hospital.py."""

import json

import db
from test_hospital import FixedDice, client, doctors_view, fresh_day, in_the_waiting_room  # noqa: F401  (fresh_day: a new day before every test)


def test_still_fine_ends_a_recheck_like_any_other_decision():
    """The nurse measured a waiting patient again and pressed "Still fine". That is her decision: the patient may
    be called again, the doctor is told she looked, and nothing says she "had not decided"."""
    reg = in_the_waiting_room(level=3)
    ticket, level = db.visit_for(reg)["ticket"], db.visit_for(reg)["level"]
    client.post("/api/worse", json={"reg_no": reg})
    client.post("/api/triage", json={"reg_no": reg, "vitals": "feverish"})
    assert next(p for p in db.line()["patients"] if p["reg_no"] == reg)["with_nurse"] is True
    assert client.post("/api/nurse/still-fine", json={"reg_no": reg}).status_code == 200
    visit = db.visit_for(reg)
    assert (visit["worse_at"], visit["rechecks"], visit["remeasured"], visit["held_at"]) == (None, 1, 0, None)
    assert (visit["ticket"], visit["level"]) == (ticket, level)
    assert next(p for p in db.line()["patients"] if p["reg_no"] == reg)["with_nurse"] is False
    assert json.loads(visit["summary"])["nurse_found"]["warnings"] == ["high fever"]          # from the new measurements
    db.call_next(reg)
    found = doctors_view(reg)["summary"]["nurse_found"]
    assert (found["said_worse"], found["rechecks"], found["worse_unanswered"], found["remeasured"]) == (1, 1, False, False)

    plain = in_the_waiting_room(level=3)                         # without measuring: counted, summary left as it was
    before = db.visit_for(plain)["summary"]
    client.post("/api/nurse/still-fine", json={"reg_no": plain})
    assert db.visit_for(plain)["rechecks"] == 1 and db.visit_for(plain)["summary"] == before


def test_the_simulation_does_not_call_away_a_made_up_patient_the_nurse_is_rechecking():
    """The made-up patients in the waiting room are called in by the simulation after a while. One the nurse has
    just measured again stays with her, like a real patient: otherwise her decision would be refused."""
    waiting = next(p for p in db.line()["patients"] if not p["you"])
    reg = waiting["reg_no"]
    client.post("/api/triage", json={"reg_no": reg})                              # the nurse measures them again
    db._meta("clock", db._clock() + db.SIM_LINE_WAIT)                             # long enough to be called, short of the hold's end
    with db._lock, db._conn:
        db._conn.execute("UPDATE visits SET held_at = ? WHERE reg_no = ?", (db._clock(), reg))   # measured just now
    db.simulate(FixedDice(0.0), now=10_000)                                       # every made-up patient who may be called is
    assert db.visit_for(reg)["status"] == "waiting"
    others = [p for p in db._conn.execute("SELECT status FROM visits WHERE you = 0 AND day = ? AND ticket LIKE 'A%'", (db._meta("day"),))]
    assert any(p["status"] == "with doctor" for p in others)                      # the others were called
    assert client.post("/api/line/join", json={"reg_no": reg, "level": waiting["level"], "review": "checked"}).status_code == 200
