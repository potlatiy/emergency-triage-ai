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


def test_follow_up_questions_come_from_their_own_request(monkeypatch):
    """Registration with the model off answers at once; the AI's questions come from a separate request."""
    reg_no = client.post("/api/register", json={"complaint": "chest pain", "age": 60, "use_model": False}).json()["reg_no"]
    monkeypatch.setattr(core, "_written", {})
    monkeypatch.setattr(core, "questions_from_model", lambda complaint, skip=(): [
        {"question": "When did the chest pain start?", "topic": "onset"},
        {"question": "Have you had this chest pain before?", "topic": "before"}])
    answer = client.get(f"/api/follow-up/{reg_no}").json()
    assert answer["source"] == "ai" and len(answer["questions"]) == core.FOLLOW_UPS == 2
    first = answer["questions"][0]
    assert first == {"question": "When did the chest pain start?", "topic": "onset", "kind": "extra", "label": "Started",
                     "choices": ["Under an hour ago", "A few hours ago", "Yesterday", "Longer ago"]}
    assert client.get("/api/follow-up/P-0000-0000").status_code == 404


def test_standard_questions_when_the_language_model_is_off(monkeypatch):
    def switched_off(complaint, skip=()):
        raise OSError("connection refused")
    monkeypatch.setattr(core, "_written", {})
    monkeypatch.setattr(core, "questions_from_model", switched_off)
    reg_no = client.post("/api/register", json={"complaint": "chest pain", "age": 60, "use_model": False}).json()["reg_no"]
    answer = client.get(f"/api/follow-up/{reg_no}").json()
    assert answer["source"] == "list"
    assert [q["topic"] for q in answer["questions"]] == ["onset", "course"]
    assert all(q["choices"] and q["question"].endswith("?") for q in answer["questions"])


def test_a_topic_the_complaint_already_answers_is_not_asked():
    assert core.already_answered("headache for three days, getting worse") >= {"onset", "course"}
    assert "background" in core.already_answered("i feel unwell")           # general medicine asks it as a danger sign
    assert core.already_answered("chest pain") == set()
    topics = [q["topic"] for q in core.follow_up("headache for three days, getting worse", use_model=False)["questions"]]
    assert topics == ["medicine", "before"]


def test_the_plain_code_check_on_what_the_model_writes():
    good = [{"topic": "onset", "question": "When did the pain start?"}, {"topic": "before", "question": "Have you had it before?"}]
    assert [q["topic"] for q in core.checked_questions(good)] == ["onset", "before"]
    for bad in ([good[0], good[0]],                                                      # the same topic twice
                [good[0], {"topic": "before", "question": "You should call an ambulance?"}],   # advice
                [good[0], {"topic": "weather", "question": "Is it raining?"}],            # not one of our topics
                [good[0], {"topic": "before", "question": "Tell me more"}],               # not a question
                "nonsense"):
        with pytest.raises(ValueError):
            core.checked_questions(bad)
    with pytest.raises(ValueError):
        core.checked_questions(good, skip={"onset"})                                      # a topic that is not allowed


def test_the_topic_of_an_answer_reaches_the_doctor():
    reg_no = new_visit()
    answers = [{"question": "When did the chest pain start?", "answer": "Under an hour ago", "kind": "extra", "topic": "onset"},
               {"question": "Does the pain spread to your arm, jaw or back?", "answer": "Yes", "kind": "danger"}]
    client.post("/api/triage", json={"reg_no": reg_no, "complaint": "chest pain", "age": 67, "sex": "Male", "answers": answers})
    saved = db.answers_for(reg_no)
    assert saved[0]["topic"] == "onset" and saved[0]["label"] == "Started"
    assert saved[1]["topic"] is None and saved[1]["label"] is None


def test_the_page_is_served():
    page = client.get("/")
    assert page.status_code == 200 and "Digital hospital" in page.text


def test_the_doctor_page_is_served_once_it_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATIC", tmp_path)                 # an empty folder: the page is not built yet
    missing = client.get("/doctor")
    assert missing.status_code == 404 and "doctor.html" in missing.json()["detail"]
    (tmp_path / "doctor.html").write_text("<h1>Doctor's screen</h1>")
    page = client.get("/doctor")
    assert page.status_code == 200 and "Doctor's screen" in page.text
    assert page.headers["cache-control"] == "no-cache"
    # the address of the page must not hide the doctor's data
    assert client.get("/api/doctor/DR-1001/patients").status_code == 200


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


# ---------------------------------------------------------------- the doctor's screen

def test_the_doctor_sees_only_their_patients_referred_first():
    # A: assessed at the nurse station, joins the line at level 2, then is called to the resuscitation team
    called = new_visit("chest pain", 67, "Male")
    questions = core.questionnaire("chest pain", use_model=False)["questions"]
    answers = [{**q, "answer": "Yes" if i == 0 else "No"} for i, q in enumerate(questions)]
    client.post("/api/triage", json={"reg_no": called, "complaint": "chest pain", "age": 67, "sex": "Male",
                                     "vitals": "unstable", "answers": answers})
    client.post("/api/line/join", json={"reg_no": called, "level": 2})
    assert client.post("/api/line/next").json()["called"]["reg_no"] == called
    # B: sent straight in by the nurse at level 1. C: sent to cardiology, so not on this doctor's screen.
    referred = new_visit("collapsed", 70, "Female")
    refer(referred, level=1, reason="Collapsed, pulse not felt")
    elsewhere = new_visit("palpitations", 55, "Male")
    assert refer(elsewhere, department="Cardiology").json()["doctor_reg"] != "DR-1000"

    screen = client.get("/api/doctor/DR-1000/patients").json()
    assert screen["doctor"] == {"reg_no": "DR-1000", "name": "Resuscitation team",
                                "department": "Resuscitation team", "room": "Resus bay"}
    assert [p["reg_no"] for p in screen["patients"]] == [referred, called]
    first, second = screen["patients"]
    assert first["referred"] is True and first["referral_reason"] == "Collapsed, pulse not felt"
    assert first["vitals"] is None and first["answers"] == []           # never went through the nurse station
    assert second["referred"] is False and second["level"] == 2 and 1 <= second["ai_level"] <= 5
    assert second["vitals"]["SBP"] == 88 and second["answers"][0]["answer"] == "Yes"
    assert client.get("/api/doctor/DR-9999/patients").status_code == 404


def test_an_old_style_visit_still_works_everywhere():
    """The current page joins the line without ai_level or vitals. Nothing may break for such a visit."""
    reg = new_visit("headache", 30, "Female")
    client.post("/api/line/join", json={"reg_no": reg, "level": 3, "danger": False})
    assert client.get(f"/api/me/{reg}").json()["status"] == "waiting"
    while (called := client.post("/api/line/next").json()["called"])["reg_no"] != reg:
        pass
    me = client.get(f"/api/me/{reg}").json()
    assert me["status"] == "called" and me["doctor"] == called["doctor"]
    card = next(p for p in client.get(f"/api/doctor/{called['doctor_reg']}/patients").json()["patients"]
                if p["reg_no"] == reg)
    assert card["ai_level"] is None and card["vitals"] is None and card["referred"] is False
    assert card["referral_reason"] is None and card["level"] == 3
