"""Checks on the digital hospital: questionnaire, line order, database and doctor matching.

The language model is switched off here (use_model=False) so the tests are fast and give the
same answer every time.
"""

import json

import pytest
from fastapi.testclient import TestClient

import core
import db
import server


@pytest.fixture(autouse=True)
def fresh_day():
    db.reset()


# ---------------------------------------------------------------- questionnaire and department rule

@pytest.mark.parametrize("complaint, department", [
    ("chest pain since this morning", "Cardiology"),
    ("i cant breathe", "Respiratory medicine"),
    ("bad stomach pain", "Gastroenterology and surgery"),
    ("i fell and my wrist hurts", "Trauma and orthopaedics"),
    ("it burns when i pee", "Urology and kidney"),
    ("blurry vision", "Eye, ear, nose and throat"),
    ("i am pregnant and bleeding", "Obstetrics and gynaecology"),
    ("feeling weird", "General medicine"),
])
def test_key_word_rule(complaint, department):
    assert core.department_for(complaint) == department


def test_questionnaire_without_the_language_model():
    form = core.questionnaire("chest pain", use_model=False)
    assert form["topic"] == "Cardiology" and form["source"] == "list"
    assert [q["kind"] for q in form["questions"]] == ["danger"] * 3


def test_every_department_has_three_danger_questions():
    for name, _ in core.DEPARTMENTS:
        assert len(core.DANGER_QUESTIONS[name]) == 3
    assert len(core.DANGER_QUESTIONS[core.DEFAULT_DEPARTMENT]) == 3


def test_assessment_shape():
    result = core.assess("ant. chest pain", 67, "Male", vitals=core.VITAL_PRESETS["unstable"], pain=7)
    assert 1 <= result["level"] <= 5
    assert abs(sum(result["probabilities"].values()) - 1) < 0.01
    options = [o["department"] for o in result["department_options"]]
    assert len(options) == 3 and options[0] == result["department"]


def test_sicker_vital_signs_never_lower_the_urgency():
    calm = core.assess("abd pain", 50, vitals=core.VITAL_PRESETS["normal"])["level"]
    sick = core.assess("abd pain", 50, vitals=core.VITAL_PRESETS["unstable"])["level"]
    assert sick <= calm                       # a smaller number is more urgent


# ---------------------------------------------------------------- the line

def patient(level, arrived, danger=False):
    return {"level": level, "arrived": arrived, "danger": danger}


def test_emergencies_always_go_first():
    clock = 300
    long_wait_minor = patient(5, arrived=0)
    new_emergency = patient(2, arrived=300)
    assert core.place(new_emergency, clock) < core.place(long_wait_minor, clock)


def test_waiting_moves_a_patient_up():
    clock = 90
    assert core.place(patient(5, arrived=0), clock) < core.place(patient(4, arrived=90), clock)


def test_same_level_is_first_come_first_served():
    assert core.place(patient(3, arrived=10), 60) < core.place(patient(3, arrived=20), 60)


def test_a_danger_sign_counts_as_half_a_level():
    clock = 10
    assert core.place(patient(4, 10, danger=True), clock) < core.place(patient(4, 10), clock)
    assert core.place(patient(3, 10), clock) < core.place(patient(4, 10, danger=True), clock)


# ---------------------------------------------------------------- the database

def test_a_new_day_has_staff_and_sample_patients():
    staff = db.staff()
    assert len(staff["doctors"]) == 14 and staff["on_duty"] == staff["total"] == 13
    assert len(db.line()["patients"]) == 5


def test_registration_numbers_are_unique_and_ordered():
    first, second = db.register("headache", 30, "Female"), db.register("fever", 40, "Male")
    assert first != second and first.startswith("P-2026-") and second > first


def test_an_emergency_goes_straight_to_resuscitation():
    reg = db.register("chest pain", 70, "Male")
    db.join_line(reg, 1, False, "Cardiology", "trained model, 90% sure")
    called = db.call_next()
    assert called["reg_no"] == reg and called["department"] == "Resuscitation team"


def test_the_least_busy_doctor_on_duty_is_chosen():
    reg = db.register("chest pain", 60, "Female")
    db.join_line(reg, 2, False)                       # level 2 so this patient is called first
    db.call_next()
    reg = db.register("palpitations", 60, "Female")
    db.join_line(reg, 3, True, "Cardiology", "trained model, 80% sure")
    while (called := db.call_next())["reg_no"] != reg:
        pass
    assert called["doctor"] == "Dr. Okafor"           # 0 waiting, against 2 for Dr. Marlow


def test_general_medicine_covers_when_nobody_is_on_duty():
    db.set_duty("DR-1008", False)                     # the only gastroenterology doctor
    reg = db.register("abd pain", 50, "Male")
    db.join_line(reg, 3, True, "Gastroenterology and surgery", "key-word rule")
    while (called := db.call_next())["reg_no"] != reg:
        pass
    assert called["department"] == "General medicine" and "nobody is on duty" in called["reason"]


def test_the_resuscitation_team_cannot_go_off_duty():
    db.set_duty("DR-1000", False)
    assert next(d for d in db.staff()["doctors"] if d["reg_no"] == "DR-1000")["on_duty"]


# ---------------------------------------------------------------- the web API, end to end

client = TestClient(server.app)


def test_a_full_visit_through_the_api():
    arrival = client.post("/api/register", json={"complaint": "bad stomach pain and vomiting", "age": 34,
                                                 "sex": "Female", "use_model": False}).json()
    assert arrival["reg_no"].startswith("P-2026-") and arrival["topic"] == "Gastroenterology and surgery"
    answers = [{**q, "answer": "Yes" if i == 0 else "No"} for i, q in enumerate(arrival["questions"])]
    result = client.post("/api/triage", json={"reg_no": arrival["reg_no"], "complaint": "bad stomach pain and vomiting",
                                              "age": 34, "pain": 6, "vitals": "feverish", "answers": answers}).json()
    assert result["danger"] is True and 1 <= result["level"] <= 5
    joined = client.post("/api/line/join", json={"reg_no": arrival["reg_no"], "level": result["level"],
                                                 "danger": result["danger"], "department": result["department"],
                                                 "department_how": result["department_how"]}).json()
    assert any(p["reg_no"] == arrival["reg_no"] for p in joined["patients"])
    for _ in range(10):
        called = client.post("/api/line/next").json()["called"]
        if called["reg_no"] == arrival["reg_no"]:
            break
    assert called["reg_no"] == arrival["reg_no"] and called["doctor_reg"].startswith("DR-")
    assert called["answers"][0]["answer"] == "Yes"


def test_the_api_rejects_impossible_input():
    assert client.post("/api/register", json={"complaint": "", "age": 30}).status_code == 422
    assert client.post("/api/register", json={"complaint": "cough", "age": 5}).status_code == 422
    assert client.post("/api/staff/duty", json={"reg_no": "DR-1000", "on_duty": False}).status_code == 400


def test_the_page_is_served():
    page = client.get("/")
    assert page.status_code == 200 and "Digital hospital" in page.text


# ---------------------------------------------------------------- saving the assessment

def new_visit(complaint="chest pain since this morning", age=67, sex="Male"):
    """Register a visit through the API (language model off) and return its registration number."""
    body = {"complaint": complaint, "age": age, "sex": sex, "use_model": False}
    return client.post("/api/register", json=body).json()["reg_no"]


def test_triage_saves_the_ai_level_and_the_vitals():
    reg = new_visit()
    result = client.post("/api/triage", json={"reg_no": reg, "complaint": "chest pain", "age": 67, "sex": "Male",
                                              "pain": 7, "vitals": "unstable"}).json()
    visit = db.visit_for(reg)
    assert visit["ai_level"] == result["level"] and visit["level"] is None     # level is still the nurse's to set
    saved = json.loads(visit["vitals"])
    assert saved["SBP"] == 88 and saved["Saturation"] == 89 and saved["pain"] == 7 and saved["alert"] == "Alert"


# ---------------------------------------------------------------- the patient's phone

HIDDEN_FROM_PATIENT = {"level", "ai_level", "danger", "name", "waited"}


def all_keys(data):
    """Every key in a JSON answer, however deeply it is nested."""
    if isinstance(data, dict):
        return set(data).union(*(all_keys(value) for value in data.values()))
    if isinstance(data, list):
        return set().union(*(all_keys(item) for item in data))
    return set()


def test_the_patient_phone_never_shows_the_level():
    reg = new_visit("headache", 30, "Female")
    client.post("/api/line/join", json={"reg_no": reg, "level": 4})
    me = client.get(f"/api/me/{reg}").json()
    assert set(me) == {"ticket", "status", "position", "ahead", "doctor", "room"}
    assert not all_keys(me) & HIDDEN_FROM_PATIENT
    in_line = next(p for p in client.get("/api/line").json()["patients"] if p["reg_no"] == reg)
    assert me["status"] == "waiting" and me["position"] == in_line["position"] and me["ahead"] == me["position"] - 1
    assert me["doctor"] is None and me["room"] is None
    while client.post("/api/line/next").json()["called"]["reg_no"] != reg:
        pass
    me = client.get(f"/api/me/{reg}").json()
    assert me["status"] == "called" and me["doctor"].startswith("Dr.") and me["room"].startswith("Room")
    assert not all_keys(me) & HIDDEN_FROM_PATIENT


def test_the_patient_phone_before_the_line_and_for_an_unknown_number():
    me = client.get(f"/api/me/{new_visit()}").json()
    assert me["status"] == "registered" and me["ticket"] is None and me["position"] is None
    assert client.get("/api/me/P-2026-9999").status_code == 404


# ---------------------------------------------------------------- the nurse sends a patient straight to a doctor

def refer(reg, level=3, reason="Rigid abdomen, looks septic", department=None):
    return client.post("/api/refer", json={"reg_no": reg, "level": level, "reason": reason, "department": department})


def test_a_referral_needs_a_reason_and_a_known_visit():
    reg = new_visit()
    assert client.post("/api/refer", json={"reg_no": reg, "level": 1}).status_code == 422     # no reason at all
    assert refer(reg, reason="   ").status_code == 422                                        # only spaces
    assert refer(reg, reason="x" * 301).status_code == 422
    assert refer("P-2026-9999").status_code == 404
    assert refer(reg).status_code == 200
    assert refer(reg).status_code == 409                                    # already with a doctor


def test_a_referred_patient_skips_the_line():
    reg = new_visit("bad stomach pain", 50, "Female")
    ticket = client.post("/api/line/join", json={"reg_no": reg, "level": 3}).json()["ticket"]
    before = next(d for d in db.staff()["doctors"] if d["reg_no"] == "DR-1008")["waiting"]
    sent = refer(reg, department="Gastroenterology and surgery").json()
    assert sent["ticket"] == ticket and sent["doctor_reg"] == "DR-1008"   # the only gastroenterology doctor
    assert sent["referral_reason"] == "Rigid abdomen, looks septic" and "by the nurse" in sent["match_reason"]
    visit = db.visit_for(reg)
    assert visit["status"] == "with doctor" and visit["doctor_reg"] == "DR-1008"
    assert visit["referred"] == 1 and visit["referral_reason"] == "Rigid abdomen, looks septic"
    assert visit["department_how"] == "nurse"
    assert all(p["reg_no"] != reg for p in client.get("/api/line").json()["patients"])
    assert next(d for d in db.staff()["doctors"] if d["reg_no"] == "DR-1008")["waiting"] == before + 1


@pytest.mark.parametrize("level", [1, 2])
def test_a_referral_at_level_1_or_2_always_goes_to_resuscitation(level):
    reg = new_visit("blurry vision", 40, "Male")
    sent = refer(reg, level=level, reason="Collapsed, pulse not felt", department="Eye, ear, nose and throat").json()
    assert sent["doctor_reg"] == "DR-1000" and sent["ticket"].startswith("A")    # a ticket is given on the way
