"""What the review of the whole project found in the hospital, each with the test that keeps it fixed."""

import os
import subprocess
import sys
from pathlib import Path

import core
import db
from test_hospital import client, fresh_day, in_the_waiting_room, new_visit  # noqa: F401  (fresh_day: a new day before every test)

NORMAL = dict(core.VITAL_PRESETS["normal"])


# ---------------------------------------------------------------- measurements the level model misjudges

def test_a_very_cold_patient_is_never_a_quick_review():
    """The model gives every number one weight in one direction, so a temperature of 31 degrees reads as "less
    urgent": level 5, 96% sure, no warning. The hospital now says so and asks the nurse to check in person."""
    cold = core.assess("cough", 40, vitals={**NORMAL, "BT": 31.0})
    assert cold["warnings"] == []                                   # the tested rule has no line for it
    assert cold["misjudged"] == ["very low temperature"]
    advice = core.review_advice(cold)
    assert advice["quick"] is False and "very low temperature" in advice["reasons"][-1]
    assert advice["rule_only"] is (cold["sure"] >= core.QUICK_SURE)             # a safety rule: no test figure beside it


def test_the_three_misjudged_measurements_and_nothing_else():
    assert core.misjudged_values(NORMAL) == []
    assert core.misjudged_values({**NORMAL, "RR": 9}) == ["very slow breathing"]
    assert core.misjudged_values({**NORMAL, "RR": 10, "BT": 35.0, "SBP": 199}) == []
    assert core.misjudged_values({**NORMAL, "RR": 6, "BT": 32, "SBP": 250}) == [
        "very slow breathing", "very low temperature", "very high blood pressure"]
    assert core.misjudged_values({**NORMAL, "BT": None}) == []                  # not measured is its own warning
    # The tested rule itself is untouched: its figures in the README stay what they were.
    assert core.vital_warnings({**NORMAL, "RR": 6, "BT": 32, "SBP": 250}) == []


def test_the_nurse_and_the_doctor_are_both_told():
    reg = new_visit("wrist pain")
    report = client.post("/api/triage", json={"reg_no": reg, "vitals": {**NORMAL, "RR": 6, "BT": 32.0}}).json()
    assert report["advice"]["quick"] is False
    assert any("misjudge very slow breathing, very low temperature" in reason for reason in report["advice"]["reasons"])
    client.post("/api/line/join", json={"reg_no": reg, "level": 2, "review": "checked"})
    found = core.patient_summary(db.visit_for(reg), db.answers_for(reg) if hasattr(db, "answers_for") else [])["nurse_found"]
    assert {"very slow breathing", "very low temperature"} <= set(found["warnings"])


# ---------------------------------------------------------------- nobody waits outside every doctor's list

def doctors_of(department):
    return [d for d in client.get("/api/staff").json()["doctors"] if d["department"] == department]


def lists_with(reg):
    return [d["department"] for d in client.get("/api/staff").json()["doctors"] if d["on_duty"]
            and any(p["reg_no"] == reg for p in client.get(f"/api/doctor/{d['reg_no']}/patients").json()["waiting_for_you"])]


def waiting(complaint, level, department):
    arrival = client.post("/api/register", json={"complaint": complaint, "age": 60, "use_model": False}).json()
    reg = arrival["reg_no"]
    client.post("/api/checkin", json={"reg_no": reg})
    client.post("/api/triage", json={"reg_no": reg})
    assert client.post("/api/line/join", json={"reg_no": reg, "level": level, "review": "checked",
                                               "department": department}).status_code == 200
    return reg


def test_a_patient_whose_department_has_nobody_on_duty_goes_to_general_medicine():
    """Every Neurology doctor is off duty. The level 3 patient was in no doctor's list, and general medicine called
    its three level 5 patients first while the phone said "you are next"."""
    for doctor in doctors_of("Neurology"):
        client.post("/api/staff/duty", json={"reg_no": doctor["reg_no"], "on_duty": False})
    urgent = waiting("sudden weakness of the left arm and slurred speech", 3, "Neurology")
    for _ in range(3):
        waiting("sore throat", 5, core.DEFAULT_DEPARTMENT)
    assert set(lists_with(urgent)) == {core.DEFAULT_DEPARTMENT}
    general = next(d for d in doctors_of(core.DEFAULT_DEPARTMENT) if d["on_duty"])
    queue = client.get(f"/api/doctor/{general['reg_no']}/patients").json()["waiting_for_you"]
    real = [p["reg_no"] for p in queue if p["reg_no"].startswith("P-")]
    assert real[0] == urgent                                        # in the line's order: the level 3 patient first


def test_with_general_medicine_off_duty_too_every_doctor_sees_such_a_patient():
    for department in ("Neurology", core.DEFAULT_DEPARTMENT):
        for doctor in doctors_of(department):
            client.post("/api/staff/duty", json={"reg_no": doctor["reg_no"], "on_duty": False})
    urgent = waiting("sudden weakness of the left arm and slurred speech", 3, "Neurology")
    seen = lists_with(urgent)
    assert len(seen) >= 5 and "Cardiology" in seen


def test_a_staffed_department_keeps_its_own_patients():
    reg = waiting("chest pain", 3, "Cardiology")
    waiting("sore throat", 5, core.DEFAULT_DEPARTMENT)             # general medicine has a patient of its own
    assert set(lists_with(reg)) == {"Cardiology"}


def test_the_phone_does_not_name_a_doctor_who_has_gone_home():
    doctor = next(d for d in doctors_of("Cardiology") if d["on_duty"])
    arrival = client.post("/api/register", json={"complaint": "chest pain", "age": 60, "use_model": False}).json()
    reg = arrival["reg_no"]
    client.post("/api/checkin", json={"reg_no": reg})
    client.post("/api/triage", json={"reg_no": reg})
    client.post("/api/line/join", json={"reg_no": reg, "level": 3, "review": "checked", "doctor_reg": doctor["reg_no"]})
    assert client.get("/api/me/" + reg).json()["doctor"] == doctor["name"]
    client.post("/api/staff/duty", json={"reg_no": doctor["reg_no"], "on_duty": False})
    told = client.get("/api/me/" + reg).json()
    assert told["doctor"] is None and told["room"] is None and told["status"] == "waiting"


# ---------------------------------------------------------------- a file that is not a database

def test_a_damaged_database_file_is_put_aside_and_the_hospital_starts_empty(tmp_path):
    """The server stopped on such a file at every start, and the start script started it again every second."""
    broken = tmp_path / "hospital.db"
    broken.write_bytes(os.urandom(5000))
    root = Path(__file__).resolve().parent.parent
    code = ("import sys; sys.path[:0] = ['hospital', '.']; import db; "
            "print(db.staff()['visits']['waiting'], db.log_check()['whole'])")
    ran = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120,
                         env={**os.environ, "HOSPITAL_DB": str(broken), "MEDDY_SIMULATION": "0"})
    assert ran.returncode == 0, ran.stderr[-400:]
    assert "put aside" in ran.stdout and ran.stdout.strip().endswith("True")
    aside = [f.name for f in tmp_path.iterdir() if ".broken-" in f.name]
    assert len(aside) == 1 and (tmp_path / aside[0]).stat().st_size == 5000      # nothing is thrown away


# ---------------------------------------------------------------- figures the README quotes from the review

def test_the_figures_the_readme_quotes_about_missed_urgent_patients_and_the_pain_score():
    """Two weak results a reviewer measured and the README now reports. Both on patients the model had not seen."""
    import numpy as np
    from sklearn.model_selection import StratifiedKFold

    import triage
    data = core._data.reset_index(drop=True)
    truth, nurse = data["KTAS_expert"].to_numpy(), data["KTAS_RN"].to_numpy()
    held_out = triage.fair_probabilities(data)
    shown, careful = triage.most_likely(held_out), triage.safety_first(held_out)
    urgent = truth <= 2
    assert urgent.sum() == 246
    assert [int((given[urgent] >= 3).sum()) for given in (shown, careful, nurse)] == [92, 65, 34]

    def as_the_hospital_feeds_it(rows):                 # pain only as 4 or 0, the usual "busy", the complaint tidied
        rows = rows.copy()
        rows["NRS_pain"] = np.where(rows["NRS_pain"].fillna(0) > 0, 4, 0)
        rows["Patients number per hour"] = core._BUSY
        rows[triage.TEXT] = rows[triage.TEXT].map(core.nurse_words)
        return rows
    fed = np.zeros_like(held_out)
    for train, test in StratifiedKFold(5, shuffle=True, random_state=triage.SEED).split(data, truth):
        model = triage.make_model().fit(data.iloc[train], truth[train])
        fed[test] = model.predict_proba(as_the_hospital_feeds_it(data.iloc[test]))
    given = triage.most_likely(fed)
    assert round(float((shown == truth).mean()), 2) == 0.68 and round(float((given == truth).mean()), 2) == 0.62
    assert (int((shown > truth).sum()), int((given > truth).sum())) == (200, 229)
