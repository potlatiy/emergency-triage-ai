"""Checks on the digital hospital: questionnaire, line order, database and doctor matching.

The language model is switched off here (use_model=False) so the tests are fast and give the
same answer every time.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

import core
import db
import server


@pytest.fixture(autouse=True)
def fresh_day():
    db.reset()
    db._watched.clear()          # no doctor's screen is open at the start of a test


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
    assert len(options) == 4 and options[0] == result["department"]          # the best and three more


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
    day_shift = [d for d in db.SEED_DOCTORS if d[4] == "day"]
    assert len(staff["doctors"]) == len(db.SEED_DOCTORS) == 38
    assert staff["total"] == 37 and staff["on_duty"] == len(day_shift) == 26          # the night shift is off duty
    assert {d["department"] for d in staff["doctors"]} == {name for name, _, _ in db.SEED_DEPARTMENTS}
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
    for doctor in db.staff()["doctors"]:              # every gastroenterology doctor goes off duty
        if doctor["department"] == "Gastroenterology and surgery":
            db.set_duty(doctor["reg_no"], False)
    reg = db.register("abd pain", 50, "Male")
    db.join_line(reg, 3, True, "Gastroenterology and surgery", "key-word rule")
    while (called := db.call_next())["reg_no"] != reg:
        pass
    assert called["department"] == "General medicine" and "nobody is on duty" in called["reason"]


def test_the_resuscitation_team_cannot_go_off_duty():
    db.set_duty("DR-1000", False)
    assert next(d for d in db.staff()["doctors"] if d["reg_no"] == "DR-1000")["on_duty"]


# ---------------------------------------------------------------- the web API, end to end

class OwnPhone(TestClient):
    """The test client plays the phone that registered each visit: when it finishes check-in or presses "I feel
    worse", it shows that visit's key, as the real page does. A test that plays a stranger passes its own headers."""
    def post(self, url, **kwargs):
        if url in ("/api/checkin", "/api/worse") and "headers" not in kwargs:
            key = db.phone_key((kwargs.get("json") or {}).get("reg_no", ""))
            if key:
                kwargs["headers"] = {"X-Meddy-Visit": key}
        return super().post(url, **kwargs)


client = OwnPhone(server.app)


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
        {"question": "Have you had this chest pain before?", "topic": "before"},
        {"question": "Have you taken anything for the chest pain?", "topic": "medicine"}])
    answer = client.get(f"/api/follow-up/{reg_no}").json()
    assert answer["source"] == "ai" and len(answer["questions"]) == core.FOLLOW_UPS == 3
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
    assert [q["topic"] for q in answer["questions"]] == ["onset", "course", "medicine"]
    assert all(q["choices"] and q["question"].endswith("?") for q in answer["questions"])


def test_a_topic_the_complaint_already_answers_is_not_asked():
    assert core.already_answered("headache for three days, getting worse") >= {"onset", "course"}
    assert "background" in core.already_answered("i feel unwell")           # general medicine asks it as a danger sign
    assert core.already_answered("chest pain") == set()
    topics = [q["topic"] for q in core.follow_up("headache for three days, getting worse", use_model=False)["questions"]]
    assert topics == ["medicine", "before", "trigger"]


def test_the_plain_code_check_on_what_the_model_writes():
    good = [{"topic": "onset", "question": "When did the pain start?"}, {"topic": "before", "question": "Have you had it before?"},
            {"topic": "medicine", "question": "Have you taken anything for it?"}]
    assert [q["topic"] for q in core.checked_questions(good)] == ["onset", "before", "medicine"]
    for bad in ([good[0], good[1], good[0]],                                                      # the same topic twice
                [good[0], good[1], {"topic": "course", "question": "You should call an ambulance?"}],   # advice
                [good[0], good[1], {"topic": "weather", "question": "Is it raining?"}],            # not one of our topics
                [good[0], good[1], {"topic": "course", "question": "Tell me more"}],               # not a question
                "nonsense"):
        with pytest.raises(ValueError):
            core.checked_questions(bad)
    with pytest.raises(ValueError):
        core.checked_questions(good, skip={"onset"})                                      # a topic that is not allowed


def test_pain_is_only_described_and_the_model_gets_a_typical_score():
    pain = client.post("/api/register", json={"complaint": "chest pain", "age": 60, "use_model": False}).json()["pain"]
    assert pain["none"] == "No pain" and pain["feel"]["topic"] == "feel" and len(pain["feel"]["choices"]) >= 4
    assert "scale" not in pain                                # "how bad is the pain" is not asked
    assert 1 <= pain["score"] <= 10 and pain["score"] == core.PAIN_TYPICAL == 4      # the median of patients with pain
    # having pain never makes the suggested level less urgent than having none
    assert core.assess("abd pain", 50, pain=pain["score"])["level"] <= core.assess("abd pain", 50, pain=0)["level"]


def test_the_doctor_is_told_whether_there_is_pain():
    assert [core.pain_word(score) for score in (None, 0, 4, 10)] == [None, "No pain", "Has pain", "Has pain"]
    reg_no = new_visit()
    client.post("/api/triage", json={"reg_no": reg_no, "complaint": "chest pain", "age": 67, "sex": "Male", "pain": 4})
    sent = client.post("/api/refer", json={"reg_no": reg_no, "level": 3, "reason": "Looks unwell"}).json()
    card = next(p for p in client.get(f"/api/doctor/{sent['doctor_reg']}/patients").json()["patients"] if p["reg_no"] == reg_no)
    assert card["vitals"]["pain"] == 4 and card["vitals"]["pain_word"] == "Has pain"


def test_the_topic_of_an_answer_reaches_the_doctor():
    reg_no = new_visit()
    answers = [{"question": "When did the chest pain start?", "answer": "Under an hour ago", "kind": "extra", "topic": "onset"},
               {"question": "Does the pain spread to your arm, jaw or back?", "answer": "Yes", "kind": "danger"}]
    client.post("/api/triage", json={"reg_no": reg_no, "complaint": "chest pain", "age": 67, "sex": "Male", "answers": answers})
    saved = db.answers_for(reg_no)
    assert saved[0]["topic"] == "onset" and saved[0]["label"] == "Started"
    assert saved[1]["topic"] is None and saved[1]["label"] is None
    assert core.topic_label("feel") == "Pain"


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


def test_the_doctor_screen_reads_the_doctor_api_and_carries_the_disclaimer():
    page = client.get("/doctor")
    assert page.status_code == 200
    assert "/api/doctor/" in page.text and "/patients" in page.text
    assert "Prototype, not medical advice. Meddy never gives a diagnosis." in page.text


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
    reg = assessed_visit("headache", 30, "Female")
    client.post("/api/line/join", json={"reg_no": reg, "level": 4})
    me = client.get(f"/api/me/{reg}").json()
    assert set(me) == {"ticket", "status", "position", "ahead", "doctor", "room", "day", "code", "worse", "closed", "estimate", "note", "doctor_busy"}
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
    reg = assessed_visit("bad stomach pain", 50, "Female")
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
    joined = client.post("/api/line/join", json={"reg_no": called, "level": 2}).json()
    assert joined["called"]["reg_no"] == called              # level 2 goes in at once, without "call next"
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


def test_a_patient_sent_in_before_any_measurement_still_works_everywhere():
    """A collapsed patient goes straight to a doctor with no vital signs and no AI level. Nothing may break."""
    reg = new_visit("headache", 30, "Female")
    sent = refer(reg, level=3, reason="Fainted at the desk").json()
    me = client.get(f"/api/me/{reg}").json()
    assert me["status"] == "called" and me["doctor"] == sent["doctor"]
    card = next(p for p in client.get(f"/api/doctor/{sent['doctor_reg']}/patients").json()["patients"]
                if p["reg_no"] == reg)
    assert card["ai_level"] is None and card["vitals"] is None and card["second_check"] is None
    assert card["referred"] is True and card["level"] == 3 and card["insurance"] == "none"


# ---------------------------------------------------------------- weak links in the chain

def assessed_visit(complaint="chest pain", age=60, sex="Male", **more):
    """A visit that has been through the nurse station, ready to join the line."""
    reg_no = new_visit(complaint, age, sex)
    client.post("/api/triage", json={"reg_no": reg_no, "complaint": complaint, "age": age, "sex": sex, **more})
    return reg_no


def test_an_unknown_registration_number_is_refused_at_every_step():
    tickets_before = db._meta("ticket")
    assert client.post("/api/triage", json={"reg_no": "P-0000-0000", "complaint": "x", "age": 50}).status_code == 404
    assert client.post("/api/line/join", json={"reg_no": "P-0000-0000", "level": 3}).status_code == 404
    assert client.post("/api/doctor/finish", json={"reg_no": "P-0000-0000"}).status_code == 404
    assert client.post("/api/staff/duty", json={"reg_no": "DR-9999", "on_duty": False}).status_code == 404
    assert db._meta("ticket") == tickets_before              # no ticket was handed to nobody


def test_joining_the_line_twice_keeps_the_ticket_and_the_time_waited():
    reg_no = assessed_visit()
    first = client.post("/api/line/join", json={"reg_no": reg_no, "level": 4}).json()["ticket"]
    client.post("/api/line/wait")
    again = client.post("/api/line/join", json={"reg_no": reg_no, "level": 3}).json()      # the nurse changed the level
    assert again["ticket"] == first
    me = next(p for p in again["patients"] if p["reg_no"] == reg_no)
    assert me["level"] == 3 and me["waited"] == 15


def test_a_patient_with_a_doctor_cannot_be_put_back_in_the_line():
    reg_no = assessed_visit()
    doctor = refer(reg_no).json()["doctor_reg"]
    list_before = next(d for d in db.staff()["doctors"] if d["reg_no"] == doctor)["waiting"]
    assert client.post("/api/line/join", json={"reg_no": reg_no, "level": 4}).status_code == 409
    assert client.post("/api/triage", json={"reg_no": reg_no, "complaint": "chest pain", "age": 60}).status_code == 409
    assert db.visit_for(reg_no)["status"] == "with doctor"
    assert next(d for d in db.staff()["doctors"] if d["reg_no"] == doctor)["waiting"] == list_before


def test_two_emergencies_at_once_both_go_straight_in():
    """Levels 1 and 2 never sit in the line, so one emergency cannot be called in place of another."""
    first, second = assessed_visit("cant breathe"), assessed_visit("chest pain")
    for reg_no, level in ((first, 1), (second, 2)):
        joined = client.post("/api/line/join", json={"reg_no": reg_no, "level": level}).json()
        assert joined["called"]["reg_no"] == reg_no and joined["called"]["doctor_reg"] == "DR-1000"
        assert all(p["reg_no"] != reg_no for p in joined["patients"])
    assert all(p["level"] >= 3 for p in db.line()["patients"])
    calm = client.post("/api/line/join", json={"reg_no": assessed_visit("sore throat"), "level": 5}).json()
    assert calm["called"] is None                            # levels 3 to 5 wait for their turn


def test_values_the_hospital_does_not_know_are_refused_not_guessed():
    reg_no = new_visit()
    base = {"reg_no": reg_no, "complaint": "chest pain", "age": 60}
    assert client.post("/api/triage", json={**base, "vitals": "dying"}).status_code == 422
    assert client.post("/api/triage", json={**base, "alert": "Asleep"}).status_code == 422
    assert client.post("/api/triage", json={**base, "arrival": "Helicopter"}).status_code == 422
    assert client.post("/api/line/join", json={"reg_no": reg_no, "level": 3, "department": "Wizardry"}).status_code == 422
    assert client.post("/api/refer", json={"reg_no": reg_no, "level": 3, "department": "Wizardry",
                                           "reason": "testing"}).status_code == 422
    assert client.post("/api/register", json={"complaint": "    ", "age": 40}).status_code == 422
    assert db.visit_for(reg_no)["status"] == "registered"    # none of it moved the patient


def test_the_doctor_closes_a_visit_and_the_list_gets_shorter():
    reg_no = assessed_visit()
    doctor = refer(reg_no, department="Cardiology").json()["doctor_reg"]
    list_before = next(d for d in db.staff()["doctors"] if d["reg_no"] == doctor)["waiting"]
    assert client.post("/api/doctor/finish", json={"reg_no": reg_no}).json()["status"] == "done"
    assert next(d for d in db.staff()["doctors"] if d["reg_no"] == doctor)["waiting"] == list_before - 1
    assert all(p["reg_no"] != reg_no for p in client.get(f"/api/doctor/{doctor}/patients").json()["patients"])
    assert client.get(f"/api/me/{reg_no}").json()["status"] == "finished"
    assert client.post("/api/doctor/finish", json={"reg_no": reg_no}).status_code == 409       # not twice
    waiting = client.post("/api/line/join", json={"reg_no": assessed_visit("sore throat"), "level": 5}).json()
    assert client.post("/api/doctor/finish", json={"reg_no": waiting["patients"][-1]["reg_no"]}).status_code == 409


def test_every_doctors_list_is_made_of_real_visits():
    """The number in the staff table and the doctor's own screen must always agree, from the first minute."""
    staff = db.staff()
    assert staff["visits"]["with doctor"] == len(db.SEED_WITH_DOCTOR) == sum(d["waiting"] for d in staff["doctors"])
    for doctor in staff["doctors"]:
        screen = client.get(f"/api/doctor/{doctor['reg_no']}/patients").json()
        assert len(screen["patients"]) == doctor["waiting"]
        assert all(p["vitals"] and 1 <= p["ai_level"] <= 5 and p["level"] >= 3 for p in screen["patients"])
    reg_no = assessed_visit()
    doctor = refer(reg_no, department="Neurology").json()["doctor_reg"]
    client.post("/api/doctor/finish", json={"reg_no": reg_no})
    after = db.staff()
    assert after["visits"]["with doctor"] == sum(d["waiting"] for d in after["doctors"])


READABLE = ["ant. chest pain", "ha", "my chest hurts", "it burns when i pee", "dizzy and weak", "i twisted my ankle",
            "my mother cant breathe properly", "my kid swallowed a coin", "sore throat and a runny nose", "toothache"]
UNREADABLE = ["болит живот и тошнит", "mal di testa da tre giorni", "j'ai mal au ventre", "bauchschmerzen seit gestern",
              "dolor de cabeza y fiebre", "ik heb hoofdpijn", "zzzz qqqq kkkk", "asdf qwer", "12345",
              "i feel terrible", "help me"]


@pytest.mark.parametrize("complaint", READABLE)
def test_a_complaint_with_a_symptom_meddy_knows_is_read(complaint):
    assert core.assess(complaint, 50)["unread"] is False


@pytest.mark.parametrize("complaint", UNREADABLE)
def test_the_nurse_is_told_when_meddy_cannot_read_the_complaint(complaint):
    """Another language, typed nonsense, or no symptom named: the level then rests on the vital signs only."""
    assert core.assess(complaint, 50)["unread"] is True


def test_nearly_every_nurse_note_in_the_data_is_read():
    notes = core._data[core.TEXT].fillna("")
    assert sum(not core.known_words(note) for note in notes) <= 5          # only shorthand like "f/c, c/s"


# ---------------------------------------------------------------- the second check, in two strengths

def test_the_flag_follows_how_sure_meddy_is():
    result = core.assess("crushing chest pain and sweating", 67, "Male", vitals=core.VITAL_PRESETS["unstable"])
    assert result["level"] == 2 and result["careful_level"] <= result["level"]
    assert result["second_check"]["5"] == "strong" and result["second_check"]["1"] is None
    assert result["more_urgent"]["1"] == 0 and result["more_urgent"]["5"] > 0.9
    for level in core.LEVELS:                                  # strong means more likely than not, by definition
        assert (result["second_check"][str(level)] == "strong") == (result["more_urgent"][str(level)] >= 0.5)


def test_the_flag_is_saved_with_the_nurses_level_for_the_doctor():
    reg_no = assessed_visit("crushing chest pain and sweating", 67, "Male", vitals="unstable")
    doctor = client.post("/api/refer", json={"reg_no": reg_no, "level": 4, "department": "Cardiology",
                                             "reason": "Looks worse than he says"}).json()["doctor_reg"]
    card = next(p for p in client.get(f"/api/doctor/{doctor}/patients").json()["patients"] if p["reg_no"] == reg_no)
    assert card["level"] == 4 and card["ai_level"] == 2 and card["second_check"] == "strong"
    calm = assessed_visit("crushing chest pain and sweating", 67, "Male", vitals="unstable")
    joined = client.post("/api/line/join", json={"reg_no": calm, "level": 2}).json()
    resus = client.get(f"/api/doctor/{joined['called']['doctor_reg']}/patients").json()["patients"]
    assert next(p for p in resus if p["reg_no"] == calm)["second_check"] is None       # the nurse agreed


# ---------------------------------------------------------------- the line is alive

def test_time_passing_calls_a_patient_and_brings_a_new_one():
    before = db.line()["patients"]
    after = client.post("/api/line/wait").json()
    assert after["clock"] == db.START_CLOCK + 15
    assert after["called"]["reg_no"] == before[0]["reg_no"]                 # the first in line went to a doctor
    assert after["arrived"]["reg_no"] in [p["reg_no"] for p in after["patients"]]
    assert len(after["patients"]) == len(before)                            # one out, one in
    staff = db.staff()
    assert staff["visits"]["with doctor"] == sum(d["waiting"] for d in staff["doctors"])


def test_a_long_wait_keeps_you_ahead_of_newcomers_who_are_no_more_urgent():
    """The rule made visible: after 45 minutes a level 5 patient counts as level 4, so a level 4 patient who
    walks in later queues behind them, while a level 3 newcomer still goes ahead."""
    me = assessed_visit("sore throat", 25, "Female")
    client.post("/api/line/join", json={"reg_no": me, "level": 5})
    for _ in range(3):
        client.post("/api/line/wait")
    line = db.line()["patients"]
    mine = next(p for p in line if p["reg_no"] == me)
    assert mine["waited"] == 45
    for other in line:
        if other["waited"] == 0 and other["level"] >= 4:
            assert other["position"] > mine["position"]
        if other["waited"] == 0 and other["level"] == 3:
            assert other["position"] < mine["position"]


def test_the_line_needs_an_assessment_but_a_critical_referral_does_not():
    reg_no = new_visit()
    assert client.post("/api/line/join", json={"reg_no": reg_no, "level": 3}).status_code == 409
    assert db.visit_for(reg_no)["status"] == "registered"
    assert refer(new_visit("collapsed", 70, "Female"), level=1, reason="Collapsed, pulse not felt").status_code == 200
    every = db._conn.execute("SELECT COUNT(*) AS n FROM visits WHERE status = 'waiting' AND ai_level IS NULL").fetchone()["n"]
    assert every == 0                                           # the sample patients are assessed too


# ---------------------------------------------------------------- insurance and one open visit per person

def arrive(insurance="public", card="MD-4829 1375", complaint="chest pain"):
    return client.post("/api/register", json={"complaint": complaint, "age": 60, "sex": "Male", "use_model": False,
                                              "insurance": insurance, "card": card})


def test_one_person_has_one_open_visit():
    first = arrive()
    assert first.status_code == 200
    again = arrive(card="md48291375", complaint="headache")              # the same card, written differently
    assert again.status_code == 409
    assert again.json()["detail"]["reg_no"] == first.json()["reg_no"] and "one visit" in again.json()["detail"]["message"].lower()
    assert arrive(card="ZZ-0000-1111").status_code == 200                 # another person
    assert db.staff()["people"] == 2


def test_the_same_person_can_come_back_after_the_visit_is_finished():
    reg_no = arrive().json()["reg_no"]
    client.post("/api/triage", json={"reg_no": reg_no, "complaint": "chest pain", "age": 60, "sex": "Male"})
    refer(reg_no)
    assert arrive().status_code == 409                                    # still with the doctor
    client.post("/api/doctor/finish", json={"reg_no": reg_no})
    back = arrive()
    assert back.status_code == 200 and back.json()["reg_no"] != reg_no
    assert db._conn.execute("SELECT visits FROM people").fetchone()["visits"] == 2


def test_no_insurance_is_never_turned_away():
    for _ in range(3):
        assert arrive(insurance="none", card=None).status_code == 200
    assert db.staff()["people"] == 0                                      # nothing to recognise them by, nothing kept


def test_a_card_number_must_look_like_one():
    assert arrive(card=None).status_code == 422
    assert arrive(card="123").status_code == 422
    assert arrive(card="not a card number!!").status_code == 422
    assert arrive(insurance="gold", card="MD48291375").status_code == 422


def test_the_registry_never_keeps_the_card_number():
    reg_no = arrive(card="MD-4829 1375").json()["reg_no"]
    # Every piece of text the two tables hold. (Numbers are left out: a clock time such as 1794748291.4 can hold
    # "4829" by chance, which made this test fail for ten seconds now and then.)
    # The phone's key and the seal of the log are left out too: they are random-looking and can hold any four digits.
    everything = json.dumps([value for table in ("people", "visits") for r in db._conn.execute(f"SELECT * FROM {table}")
                             for name, value in dict(r).items()
                             if isinstance(value, str) and name not in ("phone_key", "log_seal")])
    assert "MD48291375" not in everything and "4829" not in everything
    visit = db.visit_for(reg_no)
    assert visit["insurance"] == "public" and visit["card_end"] == "1375" and len(visit["person"]) == 24


def test_insurance_never_changes_the_level_or_the_place_in_line():
    places = []
    for insurance, card in (("public", "AA111111"), ("private", "BB222222"), ("none", None)):
        db.reset()
        reg_no = arrive(insurance, card, "abd pain").json()["reg_no"]
        result = client.post("/api/triage", json={"reg_no": reg_no, "complaint": "abd pain", "age": 60, "sex": "Male"}).json()
        joined = client.post("/api/line/join", json={"reg_no": reg_no, "level": result["level"]}).json()
        places.append((result["level"], result["department"], next(p["position"] for p in joined["patients"] if p["reg_no"] == reg_no)))
    assert places[0] == places[1] == places[2]


# ---------------------------------------------------------------- the QR code's address

def test_address_is_empty_unless_the_lan_is_switched_on(monkeypatch):
    monkeypatch.setattr(server, "LAN", False)
    assert TestClient(server.app).get("/api/address").json() == {"url": None}


def test_address_points_phones_to_this_computer(monkeypatch):
    monkeypatch.setattr(server, "LAN", True)
    monkeypatch.setattr(server, "lan_ip", lambda: "192.168.1.20")
    assert TestClient(server.app).get("/api/address").json() == {"url": "http://192.168.1.20:8000/"}


def test_online_the_address_is_the_public_one(monkeypatch):
    # On the Hugging Face Space the QR code must point to the Space, not to the container's own address.
    monkeypatch.setattr(server, "LAN", True)
    monkeypatch.setattr(server, "PUBLIC_URL", "https://meddy-example.hf.space/")
    assert TestClient(server.app).get("/api/address").json() == {"url": "https://meddy-example.hf.space/"}


# ---------------------------------------------------------------- Meddy's report and the nurse's verdict

def report_for(complaint, age=30, sex="Female", yes=False, **more):
    """Register, answer the danger questions, and return (registration number, Meddy's report)."""
    arrival = client.post("/api/register", json={"complaint": complaint, "age": age, "sex": sex, "use_model": False}).json()
    answers = [{**q, "answer": "Yes" if yes and i == 0 else "No"} for i, q in enumerate(arrival["questions"])]
    report = client.post("/api/triage", json={"reg_no": arrival["reg_no"], "complaint": complaint, "age": age, "sex": sex,
                                              "answers": answers, **more}).json()
    return arrival["reg_no"], report


def test_a_quick_review_is_advised_only_for_a_sure_calm_report():
    _, calm = report_for("ankle pain", pain=4)
    assert max(calm["probabilities"].values()) >= core.QUICK_SURE and calm["level"] >= 3
    assert calm["advice"] == {"quick": True, "emergency": False, "reasons": [], "rule_only": False}
    _, danger = report_for("ankle pain", pain=4, yes=True)                      # the same patient, one danger sign
    assert danger["advice"]["quick"] is False and "danger sign" in " ".join(danger["advice"]["reasons"])
    _, feverish = report_for("ankle pain", pain=4, vitals="feverish")           # the same patient, with a fever
    assert feverish["advice"]["quick"] is False and "high fever" in " ".join(feverish["advice"]["reasons"])
    _, unsure = report_for("sore throat", pain=4)
    assert unsure["advice"]["quick"] is False and "sure of the level" in " ".join(unsure["advice"]["reasons"])
    _, unread = report_for("zzzz qqqq", pain=4)
    assert unread["advice"]["quick"] is False and "no symptom" in " ".join(unread["advice"]["reasons"])
    _, emergency = report_for("crushing chest pain and sweating", 67, "Male", vitals="unstable")
    assert emergency["level"] <= 2 and emergency["advice"]["emergency"] is True


def test_the_warning_signs_in_the_hospital_are_the_ones_from_the_tested_rule():
    import triage
    assert core.vital_warnings(core.VITAL_PRESETS["normal"]) == []
    assert core.vital_warnings(core.VITAL_PRESETS["feverish"]) == ["high fever"]
    assert set(core.vital_warnings(core.VITAL_PRESETS["unstable"])) == {"fast or slow pulse", "fast breathing",
                                                                         "low blood pressure", "low oxygen"}
    assert "not fully alert" in core.vital_warnings(core.VITAL_PRESETS["normal"], alert="Responds to voice")
    data = core._data
    quick = triage.quick_review(triage.fair_probabilities(data), triage.vital_sign_rule(data))
    assert 0.20 < quick.mean() < 0.32                                           # about 1 patient in 4, as reported


def test_the_report_names_the_likely_doctor_without_sending_the_patient():
    before = {d["reg_no"]: d["waiting"] for d in db.staff()["doctors"]}
    reg_no, report = report_for("ankle pain", pain=4)
    likely = report["likely_doctor"]
    assert likely["department"] == report["department"] and likely["doctor"].startswith("Dr.")
    assert all(option["doctor"]["doctor"] for option in report["department_options"])
    assert {d["reg_no"]: d["waiting"] for d in db.staff()["doctors"]} == before        # nobody was assigned yet
    assert db.visit_for(reg_no)["status"] == "nurse"                                   # measured, not sent anywhere
    client.post("/api/line/join", json={"reg_no": reg_no, "level": report["level"], "department": report["department"],
                                        "review": "approved"})
    while (called := client.post("/api/line/next").json()["called"])["reg_no"] != reg_no:
        pass
    assert called["department"] == likely["department"]


def test_the_test_regime_counts_what_the_nurse_did_with_each_report():
    assert db.staff()["reviews"]["reviewed"] == 0                               # the made-up patients do not count
    a, report = report_for("ankle pain", pain=4)                                # accepted as it was
    client.post("/api/line/join", json={"reg_no": a, "level": report["level"], "department": report["department"],
                                        "review": "approved"})
    b, report = report_for("sore throat", pain=4)                               # accepted although Meddy advised a check
    client.post("/api/line/join", json={"reg_no": b, "level": report["level"], "department": report["department"],
                                        "review": "approved"})
    c, report = report_for("ankle pain", pain=4)                                # checked: level and department changed
    client.post("/api/line/join", json={"reg_no": c, "level": report["level"] - 1, "department": "General medicine",
                                        "review": "checked"})
    d, report = report_for("headache", pain=4)                                  # sent straight in: always a personal check
    refer(d, level=report["level"], department=report["department"])
    assert db.staff()["reviews"] == {"reviewed": 4, "accepted": 2, "checked": 2, "accepted_against_advice": 1,
                                     "level_changed": 1, "department_changed": 1}
    card = client.get(f"/api/doctor/{db.visit_for(d)['doctor_reg']}/patients").json()["patients"]
    assert next(p for p in card if p["reg_no"] == d)["review"] == "checked"
    assert client.post("/api/line/join", json={"reg_no": a, "level": 3, "review": "liked it"}).status_code == 422


# ---------------------------------------------------------------- Meddy never gives a diagnosis

def test_a_question_that_diagnoses_is_thrown_away():
    fine = [{"topic": "onset", "question": "When did the pain start?"}, {"topic": "before", "question": "Have you had it before?"}]
    for diagnosing in ("Has a doctor given you a diagnosis before?", "Were you diagnosed with something?",
                       "Could this be a heart attack?", "Do you think it is appendicitis?", "Is it an infection?"):
        with pytest.raises(ValueError):
            core.checked_questions(fine + [{"topic": "medicine", "question": diagnosing}])
    # a condition the patient named in their own complaint may be repeated back
    own_words = fine + [{"topic": "medicine", "question": "Have you taken anything for the asthma attack?"}]
    assert len(core.checked_questions(own_words, complaint="asthma attack since this morning")) == 3
    with pytest.raises(ValueError):
        core.checked_questions(own_words, complaint="i cant breathe")


def test_the_models_can_only_answer_with_a_level_or_a_department():
    """What the two trained models can say is a fixed list. A diagnosis is not on it, and never an input either."""
    import assignment
    import triage
    assert sorted(core._model.classes_) == [1, 2, 3, 4, 5]
    assert set(core._assignment.classes_) == set(core.DEPARTMENT_NAMES)
    diagnoses = set(core._data["Diagnosis in ED"].dropna().str.strip().str.lower())
    assert not diagnoses & {name.lower() for name in core.DEPARTMENT_NAMES}
    inputs = triage.NUMBERS + triage.CATEGORIES + [triage.TEXT]
    assert "Diagnosis in ED" not in inputs and assignment.make_assignment_model().named_steps["prepare"] is not None


def test_nothing_the_ai_returns_is_called_a_diagnosis():
    reg_no, report = report_for("crushing chest pain and sweating", 67, "Male", vitals="unstable")
    sent = client.post("/api/refer", json={"reg_no": reg_no, "level": report["level"], "reason": "Looks very unwell"}).json()
    card = client.get(f"/api/doctor/{sent['doctor_reg']}/patients").json()
    for answer in (report, client.get(f"/api/me/{reg_no}").json(), card, core.follow_up("chest pain", use_model=False)):
        assert not any("diagnos" in key.lower() for key in all_keys(answer))
    assert report["department"] in core.DEPARTMENT_NAMES
    assert {option["department"] for option in report["department_options"]} <= set(core.DEPARTMENT_NAMES)


def test_the_screens_say_that_meddy_does_not_diagnose():
    page = client.get("/").text
    assert "Meddy never gives a diagnosis." in page
    assert "filed under" not in page                          # the patient is not shown a department as a verdict
    nurse = client.get("/nurse").text                         # Meddy's report is on the nurse's own screen now
    assert "Meddy never gives a diagnosis." in nurse
    assert "Meddy never gives a diagnosis" in client.get("/doctor").text


# ---------------------------------------------------------------- found by the code review

@pytest.mark.parametrize("complaint, department", [
    ("face drooping and slurred speech", "Neurology"),           # "pee" used to match inside "speech"
    ("chest pain during exercise", "Cardiology"),                # "urin" used to match inside "during"
    ("short of breath during the night", "Respiratory medicine"),
    ("gallbladder pain", "Gastroenterology and surgery"),        # "bladder" used to send it to urology
    ("i feel warm and shivery", "General medicine"),             # "arm " used to match inside "warm "
    ("tired for a year now", "General medicine"),                # "ear " used to match inside "year "
    ("pain in my leg.", "Trauma and orthopaedics"),              # a full stop or a plural used to be missed
    ("both arms hurt", "Trauma and orthopaedics"),
    ("my ears hurt", "Eye, ear, nose and throat"),
    ("heartburn after eating", "Gastroenterology and surgery"),  # not the heart
])
def test_key_words_are_whole_words(complaint, department):
    assert core.department_for(complaint) == department
    assert core.questionnaire(complaint, use_model=False)["topic"] == department


@pytest.mark.parametrize("text", ["what type of hospital is this", "I like this hospital", "lorem ipsum dolor sit amet",
                                  "no hay nada", "died", "during"])
def test_ordinary_words_are_not_symptoms(text):
    assert core.known_words(text) == []
    result = core.assess(text, 40)
    assert result["unread"] is True and core.review_advice(result)["quick"] is False


def test_the_question_check_closes_the_loopholes_the_review_found():
    fine = [{"topic": "onset", "question": "When did the pain start?"}, {"topic": "before", "question": "Have you had it before?"}]
    cases = [("Have you had heart attacks before?", "chest pain"),            # a plural
             ("Are you diabetic?", "very thirsty"),                            # a derived word
             ("Do you think it is the flu?", "heartburn and reflux"),          # "flu" is not in "reflux"
             ("Is this a stroke?", "sunstroke after the beach"),
             ("This is very serious. When did it start?", "chest pain"),       # an opinion in front of the question
             ("Take two aspirin now. When did the chest pain start?", "chest pain"),
             ("You shouldn't wait any longer, when did it start?", "chest pain"),
             ("What is your telephone number?", "chest pain"),
             ("A" * 500 + "?", "chest pain")]
    for question, complaint in cases:
        with pytest.raises(ValueError):
            core.checked_questions(fine + [{"topic": "medicine", "question": question}], complaint=complaint)


@pytest.mark.parametrize("complaint, skipped", [
    ("i am in agony", set()),                         # "ago" used to match inside "agony"
    ("worse when i breathe in", set()),               # one "worse" does not say how it has changed
    ("headache for four days", {"onset"}),
    ("chest pain, started last week", {"onset"}),
    ("chest pain since this morning, getting much worse", {"onset", "course"}),
])
def test_only_what_the_complaint_really_says_is_skipped(complaint, skipped):
    assert core.already_answered(complaint) & {"onset", "course"} == skipped


def test_the_written_questions_are_kept_per_allowed_topics(monkeypatch):
    calls = []
    def writer(complaint, skip=()):
        calls.append(set(skip))
        return [{"question": f"Question about {t}?", "topic": t} for t in core.TOPICS if t not in skip][:core.FOLLOW_UPS]
    monkeypatch.setattr(core, "_written", {})
    monkeypatch.setattr(core, "questions_from_model", writer)
    with_injury = core.follow_up("i feel strange", injury=True)       # trauma: every topic is allowed
    without = core.follow_up("i feel strange", injury=False)          # general medicine: no "background"
    assert len(calls) == 2 and "background" in calls[1] and "background" not in calls[0]
    assert "background" not in [q["topic"] for q in without["questions"]]
    assert core.follow_up("i feel strange", injury=True) == with_injury and len(calls) == 2      # kept the second time


def test_the_80_percent_rule_uses_the_real_probability_not_the_rounded_one():
    result = {"level": 4, "unread": False, "warnings": [], "sure": 0.7996,
              "probabilities": {"1": 0.0, "2": 0.0, "3": 0.2, "4": 0.8, "5": 0.0}}       # 0.7996 shown as 0.8
    assert core.review_advice(result)["quick"] is False
    assert core.review_advice({**result, "sure": 0.8001})["quick"] is True


def test_a_vital_sign_that_could_not_be_measured_is_a_warning():
    vitals = {**core.VITAL_PRESETS["normal"], "Saturation": None}
    result = core.assess("ankle pain", 30, vitals=vitals, pain=4)
    assert "unmeasurable vital signs" in result["warnings"] and core.review_advice(result)["quick"] is False


def test_the_level_is_worked_out_for_the_registered_patient_not_for_the_request():
    reg_no = new_visit("sore throat", 20, "Female")
    honest = client.post("/api/triage", json={"reg_no": reg_no}).json()
    forged = client.post("/api/triage", json={"reg_no": reg_no, "complaint": "cardiac arrest, not breathing",
                                              "age": 95, "sex": "Male"}).json()
    assert forged["level"] == honest["level"] and forged["probabilities"] == honest["probabilities"]
    assert db.visit_for(reg_no)["complaint"] == "sore throat"


def test_numbers_and_text_that_cannot_be_stored_are_refused_not_crashed_on():
    crashes = TestClient(server.app, raise_server_exceptions=False)
    for body in ('{"complaint": "cough", "age": NaN, "use_model": false}',
                 '{"complaint": "cough", "age": 1e400, "use_model": false}',
                 '{"complaint": "cough \\ud800", "age": 30, "use_model": false}'):
        assert crashes.post("/api/register", content=body, headers={"Content-Type": "application/json"}).status_code == 422
    reg_no = assessed_visit()
    assert crashes.post("/api/line/join", json={"reg_no": reg_no, "level": 3, "department_how": "x" * 500}).status_code == 422
    too_long = [{"question": "q" * 5000, "answer": "Yes", "kind": "danger"}]
    assert crashes.post("/api/triage", json={"reg_no": new_visit(), "answers": too_long}).status_code == 422


def test_a_second_click_cannot_send_one_patient_to_two_doctors():
    """The state is checked inside the database together with the change, not in a separate step before it."""
    reg_no = assessed_visit()
    before = db.staff()
    db.refer(reg_no, 3, "Looks unwell", "Cardiology")
    for again in (lambda: db.refer(reg_no, 3, "Looks unwell", "Cardiology"),
                  lambda: db.join_line(reg_no, 3, False),
                  lambda: db.admit(reg_no, 1, False)):
        with pytest.raises(db.AlreadySent):
            again()
    after = db.staff()
    assert after["visits"]["with doctor"] == before["visits"]["with doctor"] + 1 == sum(d["waiting"] for d in after["doctors"])
    assert refer(reg_no).status_code == 409


def test_an_emergency_is_joined_and_sent_in_one_step():
    reg_no = assessed_visit("cant breathe")
    ticket, called = db.admit(reg_no, 1, False, "Respiratory medicine", "nurse")
    assert called["reg_no"] == reg_no and called["doctor_reg"] == "DR-1000" and called["ticket"] == ticket
    assert db.visit_for(reg_no)["status"] == "with doctor"
    ticket, called = db.admit(assessed_visit("sore throat"), 5, False)
    assert called is None


def test_joining_again_keeps_the_nurses_department():
    reg_no = assessed_visit()
    client.post("/api/line/join", json={"reg_no": reg_no, "level": 4, "department": "Cardiology",
                                        "department_how": "nurse, from the suggested departments", "review": "checked"})
    client.post("/api/line/join", json={"reg_no": reg_no, "level": 3})           # only the level changes
    visit = db.visit_for(reg_no)
    assert visit["department"] == "Cardiology" and visit["department_how"].startswith("nurse") and visit["level"] == 3
    other, report = report_for("headache", pain=4)
    refer(other, level=report["level"])                                          # no department given: not "changed"
    assert db.staff()["reviews"]["department_changed"] == 0


def test_every_made_up_patient_has_the_ai_level_of_that_patient():
    rows = db._conn.execute("SELECT complaint, age, sex, ai_level, vitals FROM visits WHERE you = 0").fetchall()
    assert len(rows) == 20
    for row in rows:
        vitals = json.loads(row["vitals"])
        measured = {k: v for k, v in vitals.items() if k not in ("alert", "pain")}
        fresh = core.assess(row["complaint"], row["age"], row["sex"], alert=vitals["alert"], pain=vitals["pain"], vitals=measured)
        assert fresh["level"] == row["ai_level"], row["complaint"]


def test_a_phone_can_tell_a_new_day_from_the_old_one():
    """A new day closes the open visit: the phone sees "finished" and a new day. Numbers are never given twice."""
    first = client.post("/api/register", json={"complaint": "cough", "age": 30, "use_model": False}).json()
    assert client.get(f"/api/me/{first['reg_no']}").json()["day"] == first["day"]
    client.post("/api/reset")
    second = client.post("/api/register", json={"complaint": "collapsed", "age": 70, "use_model": False}).json()
    assert second["reg_no"] > first["reg_no"]                 # the counter goes on: never the same number twice
    old = client.get(f"/api/me/{first['reg_no']}").json()
    assert old["status"] == "finished" and old["day"] == second["day"] != first["day"]


def test_the_last_two_doctor_fallbacks():
    for doctor in db.staff()["doctors"]:
        if doctor["department"] in ("Cardiology", "General medicine"):
            db.set_duty(doctor["reg_no"], False)
    sent = refer(assessed_visit(), department="Cardiology").json()
    assert sent["department"] not in ("Cardiology", "General medicine", "Resuscitation team") and "least busy" in sent["reason"]
    for doctor in db.staff()["doctors"]:
        db.set_duty(doctor["reg_no"], False)
    sent = refer(assessed_visit(), department="Cardiology").json()
    assert sent["doctor_reg"] == "DR-1000" and "No doctor is on duty" in sent["reason"]


def test_the_mild_flag_is_the_part_between_the_two_rules():
    odds = [0.0, 0.05, 0.40, 0.55, 0.0]            # 45% chance of being more urgent than level 4: not strong
    assert core.second_check(odds, 4) == "mild"     # but playing safe Meddy would still say level 3
    assert core.second_check([0.0, 0.05, 0.50, 0.45, 0.0], 4) == "strong"
    assert core.second_check(odds, 3) is None and core.second_check(None, 3) is None



# ---------------------------------------------------------------- one screen per role: phone, nurse, doctor

def checked_in(complaint="chest pain since this morning", danger_yes=False, **extra):
    """Register and finish check-in from the patient's phone, as the page does. Returns the registration number."""
    reg = new_visit(complaint)
    answers = [{"question": core.danger_questions_for(complaint)[0], "answer": "Yes" if danger_yes else "No",
                "kind": "danger"},
               {"question": "What does the pain feel like?", "answer": "Pressure or tightness", "kind": "extra",
                "topic": "feel"}]
    body = {"reg_no": reg, "arrival": "Private car", "injury": False, "pain": 5, "answers": answers, **extra}
    assert client.post("/api/checkin", json=body).status_code == 200
    return reg


def test_check_in_puts_the_patient_on_the_nurses_list_and_the_phone_shows_a_code():
    reg = checked_in()
    me = client.get(f"/api/me/{reg}").json()
    assert me["status"] == "nurse" and me["code"] == reg.rsplit("-", 1)[-1] and me["ticket"] is None
    assert not all_keys(me) & HIDDEN_FROM_PATIENT
    waiting_for_nurse = client.get("/api/nurse/patients").json()["patients"]
    assert [p["reg_no"] for p in waiting_for_nurse] == [reg] and waiting_for_nurse[0]["code"] == me["code"]
    assert reg not in [p["reg_no"] for p in client.get("/api/line").json()["patients"]]     # not in the line yet


def test_the_nurses_card_has_what_the_patient_reported():
    card = client.get(f"/api/nurse/patient/{checked_in(danger_yes=True)}").json()
    assert card["complaint"] == "chest pain since this morning" and card["arrival"] == "Private car"
    assert card["pain"] == 5 and card["pain_word"] == "Has pain" and card["status"] == "nurse"
    assert [a["answer"] for a in card["answers"]] == ["Yes", "Pressure or tightness"]
    assert client.get("/api/nurse/patient/P-2026-9999").status_code == 404


def test_a_danger_sign_puts_the_patient_first_on_the_nurses_list():
    calm, worried = checked_in("sore throat"), checked_in(danger_yes=True)
    order = [p["reg_no"] for p in client.get("/api/nurse/patients").json()["patients"]]
    assert order == [worried, calm]


def test_the_nurse_assesses_and_the_phone_moves_on_by_itself():
    reg = checked_in()
    card = client.get(f"/api/nurse/patient/{reg}").json()
    result = client.post("/api/triage", json={"reg_no": reg, "arrival": card["arrival"], "injury": card["injury"],
                                              "pain": card["pain"], "vitals": "normal",
                                              "answers": card["answers"]}).json()
    client.post("/api/line/join", json={"reg_no": reg, "level": max(result["level"], 3), "review": "approved"})
    assert client.get(f"/api/me/{reg}").json()["status"] == "waiting"
    assert reg not in [p["reg_no"] for p in client.get("/api/nurse/patients").json()["patients"]]
    assert len(db.answers_for(reg)) == 2                         # the nurse's step kept the patient's answers
    assert client.post("/api/checkin", json={"reg_no": reg}).status_code == 409     # check-in is over


def test_check_in_twice_before_the_nurse_replaces_the_answers_and_keeps_the_place():
    reg = checked_in()
    first = db.visit_for(reg)["checked_in"]
    db.pass_time(15)
    assert client.post("/api/checkin", json={"reg_no": reg, "answers": []}).status_code == 200
    assert db.visit_for(reg)["checked_in"] == first and db.answers_for(reg) == []
    assert client.post("/api/checkin", json={"reg_no": "P-2026-9999"}).status_code == 404


def test_the_nurses_summary_is_checked_and_is_none_without_the_model(monkeypatch):
    reg = checked_in()
    monkeypatch.setattr(core, "OLLAMA_URL", "http://127.0.0.1:9/api/chat")       # nothing listens there
    assert client.get(f"/api/nurse/summary/{reg}").json() == {"source": "none", "text": None}
    assert client.get("/api/nurse/summary/P-2026-9999").status_code == 404
    complaint = "chest pain since this morning"
    ok = "The patient has had chest pain since this morning. It feels like pressure."
    assert core.checked_summary(ok, complaint) == ok
    assert core.checked_summary("One. Two. Three sentences.", complaint) == "One. Two."             # cut to two
    assert core.checked_summary("The patient fell. It is likely due to ice. The knee is swollen.", "knee pain") ==         "The patient fell. The knee is swollen."                     # a sentence that guesses the cause is dropped
    for bad in ["The patient may be having a heart attack.",                 # a condition the patient did not name
                "You should call an ambulance.",                             # advice
                "word " * 60 + ".",                                          # too long
                "It is probably nothing.",                                   # only a guess is left: nothing
                ""]:
        with pytest.raises(ValueError):
            core.checked_summary(bad, complaint)


def test_the_nurse_page_is_served():
    page = client.get("/nurse")
    assert page.status_code == 200 and "Nurse" in page.text


# ---------------------------------------------------------------- the doctor calls; the patient writes

def test_a_doctor_calls_the_first_patient_of_their_own_department():
    cardiac = assessed_visit("chest pain since this morning", 67, "Male")
    client.post("/api/line/join", json={"reg_no": cardiac, "level": 3, "department": "Cardiology"})
    called = client.post("/api/doctor/call", json={"doctor_reg": "DR-1002"}).json()["called"]
    assert called["reg_no"] == cardiac and called["doctor_reg"] == "DR-1002" and called["room"] == "Room 2"
    me = client.get(f"/api/me/{cardiac}").json()
    assert me["status"] == "called" and me["room"] == "Room 2"           # the phone moves on by itself
    assert cardiac in [p["reg_no"] for p in client.get("/api/doctor/DR-1002/patients").json()["patients"]]
    assert client.post("/api/doctor/call", json={"doctor_reg": "DR-1002"}).json()["called"] is None   # nobody left
    assert client.post("/api/doctor/call", json={"doctor_reg": "DR-9999"}).status_code == 404


def test_general_medicine_covers_the_line_when_nobody_of_its_own_waits():
    line = client.get("/api/line").json()["patients"]
    general = [p for p in line if core.department_for(p["who"]) == core.DEFAULT_DEPARTMENT]
    expected = (general or line)[0]["reg_no"]
    assert client.post("/api/doctor/call", json={"doctor_reg": "DR-1010"}).json()["called"]["reg_no"] == expected


def test_the_patients_own_words_reach_the_nurse_and_the_summary():
    words = "I was carrying shopping upstairs and my chest went tight, then my arm felt heavy"
    reg = new_visit()
    story = {"question": core.OWN_WORDS["question"], "answer": words, "kind": "extra", "topic": "story"}
    client.post("/api/checkin", json={"reg_no": reg, "answers": [story]})
    card = client.get(f"/api/nurse/patient/{reg}").json()
    assert card["answers"][0]["answer"] == words and card["answers"][0]["label"] == "In their own words"
    report = core._report_for_summary(card["complaint"], card["age"], card["sex"], card["answers"])
    assert f'in their own words: "{words}"' in report


def test_an_injury_is_not_asked_before_or_medicine():
    for complaint in ["cut finger", "knee pain after a fall", "my child swallowed a coin", "burn on my arm"]:
        assert {"before", "medicine"} <= core.already_answered(complaint), complaint
        topics = [q["topic"] for q in core.follow_up(complaint, use_model=False)["questions"]]
        assert not {"before", "medicine"} & set(topics), (complaint, topics)
    assert "before" not in core.already_answered("headache")          # an illness keeps every topic


def test_a_summary_sentence_about_something_the_patient_never_said_is_dropped():
    report = core._report_for_summary("knee pain after a fall", 30, "Male", [])
    text = "The patient reports knee pain after a fall. The pain spreads to the arm, jaw and back."
    assert core.checked_summary(text, "knee pain after a fall", report) == "The patient reports knee pain after a fall."
    dizzy = core._report_for_summary("dizzy and weak", 80, "Female", [])
    assert core.checked_summary("The patient reports dizziness and weakness.", "dizzy and weak", dizzy)   # same word, other form


def test_a_guessed_cause_is_cut_off_and_the_rest_is_kept():
    text = "The patient reports knee pain after a fall, likely due to a recent injury. The pain is sharp."
    assert core.checked_summary(text, "knee pain after a fall") == "The patient reports knee pain after a fall. The pain is sharp."


def test_a_summary_never_says_how_serious_it_is():
    text = "The patient reports knee pain following a fall. He is a 30-year-old male with no signs of a serious condition."
    assert core.checked_summary(text, "knee pain after a fall") == "The patient reports knee pain following a fall."


def test_the_same_thing_in_other_words_is_not_invented():
    report = core._report_for_summary("bad stomach pain and vomiting", 40, "Female", [])
    assert core.checked_summary("The patient reports abdominal pain and vomiting.", "bad stomach pain", report)
    assert core.checked_summary("The patient reports belly pain and feeling sick.", "bad stomach pain", report)


# ---------------------------------------------------------------- the patient summary on the doctor's screen

class FakeModel:
    """Stands in for the language model: answers every request with the text it was given."""

    def __init__(self, text):
        self.text = text

    def __call__(self, request, timeout=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *problem):
        return False

    def read(self):
        return json.dumps({"message": {"content": self.text}}).encode()


def patient_at_the_nurse(complaint="crushing chest pain and sweating", story="I was climbing the stairs when my chest went tight"):
    """A patient who has filled in the whole questionnaire on the phone. Returns the registration number."""
    arrival = client.post("/api/register", json={"complaint": complaint, "age": 67, "sex": "Male", "use_model": False}).json()
    answers = [{"question": q["question"], "kind": "danger", "answer": "Yes" if i == 0 else "No"}
               for i, q in enumerate(arrival["questions"])]
    answers += [{"question": "When did the chest pain start?", "kind": "extra", "topic": "onset", "answer": "Under an hour ago"},
                {"question": arrival["pain"]["feel"]["question"], "kind": "extra", "topic": "feel", "answer": "Pressure or tightness"}]
    if story:
        answers.append({"question": core.OWN_WORDS["question"], "kind": "extra", "topic": "story", "answer": story})
    assert client.post("/api/checkin", json={"reg_no": arrival["reg_no"], "pain": 4, "answers": answers}).status_code == 200
    return arrival["reg_no"]


def summary_on_the_doctors_card(reg_no):
    doctor = db.visit_for(reg_no)["doctor_reg"]
    card = next(p for p in client.get(f"/api/doctor/{doctor}/patients").json()["patients"] if p["reg_no"] == reg_no)
    return card["summary"]


def test_the_whole_chain_from_the_questionnaire_to_the_summary_on_the_doctors_screen(monkeypatch):
    monkeypatch.setattr(core, "OLLAMA_URL", "http://127.0.0.1:9/api/chat")       # the language model is off
    reg_no = patient_at_the_nurse()
    card = client.get(f"/api/nurse/patient/{reg_no}").json()
    assert card["status"] == "nurse" and len(card["answers"]) == 6
    report = client.post("/api/triage", json={"reg_no": reg_no, "vitals": "feverish", "pain": 4}).json()    # no answers sent
    assert len(db.answers_for(reg_no)) == 6                  # the answers from check-in stand
    assert report["danger"] is True
    client.post("/api/line/join", json={"reg_no": reg_no, "level": 3, "department": "Cardiology", "review": "checked",
                                        "note": "  Lives alone, daughter is on her way  "})
    called = client.post("/api/doctor/call", json={"doctor_reg": "DR-1002"}).json()["called"]     # a cardiology doctor
    assert called["reg_no"] == reg_no
    summary = summary_on_the_doctors_card(reg_no)
    said, nurse, meddy = summary["patient_said"], summary["nurse_found"], summary["meddy_suggested"]
    assert "crushing chest pain and sweating" in said["text"] and said["written_by"] == "code"
    assert said["own_words"] == "I was climbing the stairs when my chest went tight"
    assert len(said["danger_yes"]) == 1 and said["danger_yes"][0] == card["answers"][0]["question"]
    assert {"label": "Started", "value": "Under an hour ago"} in said["facts"]
    assert {"label": "Pain", "value": "Pressure or tightness"} in said["facts"]
    assert nurse["level"] == 3 and nurse["review"] == "checked" and nurse["note"] == "Lives alone, daughter is on her way"
    assert nurse["measured"]["BT"] == 38.9 and nurse["warnings"] == ["high fever"] and nurse["referral_reason"] is None
    assert meddy["level"] == report["level"] and 0 < meddy["sure"] <= 1 and meddy["department"] == report["department"]
    assert meddy["flag"] == report["second_check"]["3"]


def test_without_the_language_model_the_summary_uses_only_the_patients_words(monkeypatch):
    monkeypatch.setattr(core, "OLLAMA_URL", "http://127.0.0.1:9/api/chat")
    reg_no = patient_at_the_nurse(story=None)
    client.post("/api/triage", json={"reg_no": reg_no})
    refer(reg_no, department="Cardiology")
    said = summary_on_the_doctors_card(reg_no)["patient_said"]
    assert said["written_by"] == "code"
    template = set("the patient a year old man woman reports report they answered yes no to of every danger sign signs "
                   "describe pain as and say it was caused by an injury".split())
    patients_own = set("crushing chest pain and sweating pressure or tightness".split()) | {"67", "1", "3"}
    words = set(re.findall(r"[a-z0-9]+", said["text"].lower()))
    assert words <= template | patients_own, words - template - patients_own


@pytest.mark.parametrize("written", [
    "The patient has chest pain. It is likely a heart attack.",                         # names a condition, guesses
    "The patient has chest pain. This is a serious emergency.",                         # judges how serious it is
    "The patient has chest pain. You should call an ambulance.",                        # advice
    "The patient has pain spreading to the jaw and a swollen knee.",                    # things the patient never said
])
def test_ai_sentences_that_break_a_rule_never_reach_the_doctor(monkeypatch, written):
    monkeypatch.setattr(core, "_summaries", {})
    monkeypatch.setattr(core.urllib.request, "urlopen", FakeModel(written))
    reg_no = patient_at_the_nurse()
    client.get(f"/api/nurse/summary/{reg_no}")                                           # the nurse's screen asks the model
    client.post("/api/triage", json={"reg_no": reg_no})
    refer(reg_no, department="Cardiology")
    text = summary_on_the_doctors_card(reg_no)["patient_said"]["text"].lower()
    for broken in ("heart attack", "likely", "serious", "emergency", "you should", "ambulance", "jaw", "knee"):
        assert broken not in text


def test_good_ai_sentences_reach_the_doctor_unless_the_nurse_says_they_are_not_right(monkeypatch):
    good = "The patient reports crushing chest pain and sweating. The pain started under an hour ago and feels like pressure."
    monkeypatch.setattr(core, "_summaries", {})
    monkeypatch.setattr(core.urllib.request, "urlopen", FakeModel(good))
    passed_on, held_back = patient_at_the_nurse(), patient_at_the_nurse()
    for reg_no in (passed_on, held_back):
        assert client.get(f"/api/nurse/summary/{reg_no}").json() == {"source": "ai", "text": good}
        client.post("/api/triage", json={"reg_no": reg_no})
    client.post("/api/refer", json={"reg_no": passed_on, "level": 3, "reason": "Looks unwell", "department": "Cardiology"})
    client.post("/api/refer", json={"reg_no": held_back, "level": 3, "reason": "Looks unwell", "department": "Cardiology",
                                    "summary_ok": False, "note": "The pain is in the back, not the chest"})
    said = summary_on_the_doctors_card(passed_on)["patient_said"]
    assert said["text"] == good and said["written_by"] == "ai"
    other = summary_on_the_doctors_card(held_back)
    assert other["patient_said"]["written_by"] == "code" and other["patient_said"]["text"] != good
    assert other["nurse_found"]["note"] == "The pain is in the back, not the chest"


def test_the_nurses_note_is_short_and_a_blank_one_is_dropped():
    reg_no = assessed_visit()
    too_long = client.post("/api/line/join", json={"reg_no": reg_no, "level": 4, "note": "x" * 301})
    assert too_long.status_code == 422 and db.visit_for(reg_no)["status"] == "nurse"
    client.post("/api/refer", json={"reg_no": reg_no, "level": 3, "reason": "Looks unwell", "note": "     "})
    assert summary_on_the_doctors_card(reg_no)["nurse_found"]["note"] is None
    assert db.visit_for(reg_no)["nurse_note"] is None


def test_the_summary_is_written_once_and_cannot_change_after_the_patient_is_called(monkeypatch):
    monkeypatch.setattr(core, "_summaries", {})
    monkeypatch.setattr(core.urllib.request, "urlopen", FakeModel("The patient reports chest pain. The pain feels like pressure."))
    reg_no = patient_at_the_nurse()
    client.get(f"/api/nurse/summary/{reg_no}")
    client.post("/api/triage", json={"reg_no": reg_no})
    client.post("/api/line/join", json={"reg_no": reg_no, "level": 4, "note": "first note"})
    client.post("/api/line/join", json={"reg_no": reg_no, "level": 3, "note": "second note"})   # still in the line: rewritten
    assert json.loads(db.visit_for(reg_no)["summary"])["nurse_found"] == {**json.loads(db.visit_for(reg_no)["summary"])["nurse_found"],
                                                                          "level": 3, "note": "second note"}
    assert client.post("/api/doctor/call", json={"doctor_reg": "DR-1002"}).json()["called"]["reg_no"] == reg_no
    before = summary_on_the_doctors_card(reg_no)
    monkeypatch.setattr(core, "_summaries", {})                                         # the model would answer differently now
    monkeypatch.setattr(core.urllib.request, "urlopen", FakeModel("The patient reports sweating. They also report chest pain."))
    client.get(f"/api/nurse/summary/{reg_no}")
    assert client.post("/api/line/join", json={"reg_no": reg_no, "level": 5, "note": "third note"}).status_code == 409
    assert summary_on_the_doctors_card(reg_no) == before
    # The model left the sweating out, so the complaint itself goes first.
    assert before["patient_said"]["text"] == ('Came with "crushing chest pain and sweating". '
                                              "The patient reports chest pain. The pain feels like pressure.")


def test_a_patient_sent_in_before_any_measurement_still_has_a_summary():
    reg_no = new_visit("collapsed at the entrance", 70, "Female")
    refer(reg_no, level=1, reason="Collapsed, pulse not felt")
    summary = summary_on_the_doctors_card(reg_no)
    assert summary["nurse_found"]["measured"] is None and summary["nurse_found"]["warnings"] == []
    assert summary["nurse_found"]["referral_reason"] == "Collapsed, pulse not felt" and summary["nurse_found"]["level"] == 1
    assert summary["meddy_suggested"] is None
    assert summary["patient_said"]["text"] == 'The patient, a 70-year-old woman, reports "collapsed at the entrance".'


def test_every_patient_on_a_doctors_screen_has_a_summary_also_the_made_up_ones():
    for doctor in db.staff()["doctors"]:
        for patient in client.get(f"/api/doctor/{doctor['reg_no']}/patients").json()["patients"]:
            summary = patient["summary"]
            assert set(summary) == {"patient_said", "nurse_found", "meddy_suggested"}
            assert patient["complaint"] in summary["patient_said"]["text"] and summary["patient_said"]["written_by"] == "code"


def test_the_summary_never_reaches_the_patients_phone_and_is_never_called_a_diagnosis():
    reg_no = patient_at_the_nurse()
    client.post("/api/triage", json={"reg_no": reg_no})
    refer(reg_no, department="Cardiology", reason="Looks unwell")
    me = client.get(f"/api/me/{reg_no}").json()
    assert set(me) == {"ticket", "status", "position", "ahead", "doctor", "room", "day", "code", "worse", "closed", "estimate", "note", "doctor_busy"}
    summary = summary_on_the_doctors_card(reg_no)
    assert not any("diagnos" in key.lower() for key in all_keys(summary))
    assert "diagnos" not in json.dumps(summary).lower()




# ---------------------------------------------------------------- a database that keeps everything

def test_a_new_day_keeps_history_and_never_reuses_a_number():
    reg = checked_in()
    numbers_before = db.visit_for(reg)["reg_no"]
    assert client.post("/api/reset").json()["closed"] >= 1
    kept = db.visit_for(reg)
    assert kept["status"] == "done" and kept["closed_why"] == "new day"                   # closed, not deleted
    assert db.answers_for(reg)                                                            # its answers are kept
    assert [s["what"] for s in db.history(reg)][-1] == "closed"
    fresh = new_visit()
    assert fresh > numbers_before and db.visit_for(fresh)["day"] == db.day() != kept["day"]
    assert db.staff()["visits"]["done"] == 0                                              # today's counts start again
    assert db.staff()["all days"]["visits"] > db.staff()["visits"]["registered"]


def test_a_card_is_free_again_after_a_new_day():
    first = client.post("/api/register", json={"complaint": "cough", "age": 30, "use_model": False,
                                               "insurance": "public", "card": "DEMO12345678"})
    assert first.status_code == 200
    again = {"complaint": "cough", "age": 30, "use_model": False, "insurance": "public", "card": "DEMO12345678"}
    assert client.post("/api/register", json=again).status_code == 409
    client.post("/api/reset")
    assert client.post("/api/register", json=again).status_code == 200


def test_every_step_of_a_visit_is_in_the_log():
    reg = checked_in()
    card = client.get(f"/api/nurse/patient/{reg}").json()
    client.post("/api/triage", json={"reg_no": reg, "arrival": card["arrival"], "pain": card["pain"],
                                     "answers": card["answers"]})
    client.post("/api/line/join", json={"reg_no": reg, "level": 3, "department": "Cardiology"})
    client.post("/api/doctor/call", json={"doctor_reg": "DR-1002"})
    client.post("/api/doctor/finish", json={"reg_no": reg})
    steps = client.get(f"/api/visit/{reg}/history").json()["steps"]
    assert [s["what"] for s in steps] == ["registered", "checked in", "assessed", "in line", "called by the doctor", "finished"]
    assert all(s["at"] for s in steps) and steps[-1]["who"] == "DR-1002"
    assert db.visit_for(reg)["closed_why"] == "finished by the doctor"
    assert client.get("/api/visit/P-2026-9999/history").status_code == 404
    assert client.get("/api/events?limit=3").json()["events"][0]["what"] == "finished"


def test_an_older_database_is_upgraded_without_losing_anything():
    reg = new_visit()
    with db._lock, db._conn:
        db._conn.execute("ALTER TABLE visits DROP COLUMN closed_why")       # as a file from before this version
        db._conn.execute("DELETE FROM doctors WHERE reg_no = 'DR-1037'")
        db._conn.execute("UPDATE visits SET day = NULL")                     # visits from before days were kept
    db._upgrade()
    assert db.staff()["visits"]["waiting"] == len(db.line()["patients"]) == 5   # they count as today's
    assert db.visit_for(reg)["complaint"] == "chest pain since this morning" and "closed_why" in db.visit_for(reg)
    assert any(d["reg_no"] == "DR-1037" for d in db.staff()["doctors"])


def test_the_database_is_copied_to_lasting_storage(monkeypatch, tmp_path):
    import sqlite3
    monkeypatch.setattr(db, "KEPT", tmp_path / "hospital.db")
    monkeypatch.setattr(db, "_kept_changes", None)
    reg = new_visit()
    assert db.keep_copy() is True and db.keep_copy() is False                  # nothing changed the second time
    copy = sqlite3.connect(tmp_path / "hospital.db")
    assert copy.execute("SELECT complaint FROM visits WHERE reg_no = ?", (reg,)).fetchone()[0] == "chest pain since this morning"
    copy.close()
    new_visit()
    assert db.keep_copy() is True                                               # a change: copied again


# ---------------------------------------------------------------- the chain's guards: what each screen may not do

def test_a_danger_answer_must_be_one_of_this_complaints_own_questions():
    """A yes to a danger question moves the patient up, so the server does not take the phone's word for
    what was asked: a made-up question, another complaint's question or the same one twice is refused."""
    reg = new_visit("sore throat")
    own = core.danger_questions_for("sore throat")

    def check_in(answers):
        return client.post("/api/checkin", json={"reg_no": reg, "answers": answers}).status_code

    assert check_in([{"question": "Are you dying?", "answer": "Yes", "kind": "danger"}]) == 422
    assert check_in([{"question": core.DANGER_QUESTIONS["Cardiology"][0], "answer": "Yes", "kind": "danger"}]) == 422
    assert check_in([{"question": own[0], "answer": "Yes", "kind": "danger"}] * 2) == 422       # one yes, counted once
    assert check_in([{"question": own[0], "answer": "Very much", "kind": "danger"}]) == 422
    assert check_in([{"question": own[0], "answer": "Yes", "kind": "banana"}]) == 422
    assert check_in([{"question": "Since when?", "answer": "Today", "kind": "extra", "topic": "nope"}]) == 422
    assert db.visit_for(reg)["status"] == "registered" and db.answers_for(reg) == []            # nothing was kept
    assert client.get("/api/nurse/patients").json()["patients"] == []

    assert check_in([{"question": own[0], "answer": "Yes", "kind": "danger"},
                     {"question": "Since when?", "answer": "Today", "kind": "extra", "topic": "onset"}]) == 200
    assert client.get("/api/nurse/patients").json()["patients"][0]["danger"] == 1


def test_an_injury_brings_its_own_danger_questions():
    """The questionnaire is built before anyone knows about an injury, so both readings of the complaint count."""
    with_injury = core.danger_questions_for("pain in my leg", injury=True)
    assert set(core.DANGER_QUESTIONS["Trauma and orthopaedics"]) <= set(with_injury)
    assert set(core.danger_questions_for("pain in my leg")) <= set(with_injury)


def test_check_in_is_closed_once_the_nurse_has_measured():
    """Meddy's report is worked out from the answers as they were. New answers afterwards would leave the
    nurse reading a report that no longer fits them, so the phone is told the nurse already has the patient."""
    reg = checked_in(danger_yes=False)
    before = db.answers_for(reg)
    again = {"reg_no": reg, "answers": [{"question": core.danger_questions_for("chest pain since this morning")[0],
                                         "answer": "Yes", "kind": "danger"}]}
    assert client.post("/api/checkin", json=again).status_code == 200            # before the nurse: answers can change
    assert client.post("/api/checkin", json={"reg_no": reg, "answers": before}).status_code == 200
    assert client.post("/api/triage", json={"reg_no": reg}).json()["danger"] is False
    assert client.post("/api/checkin", json=again).status_code == 409            # after she measured: closed
    assert db.answers_for(reg) == before


def test_the_nurse_can_take_a_patient_who_never_finished_check_in():
    """Too unwell to tap, or no phone: the nurse measures anyway. The visit is then with the nurse, the phone's
    late answers are refused, and Meddy asks for a personal check because nobody answered the danger questions."""
    reg = new_visit("sore throat", age=30)
    report = client.post("/api/triage", json={"reg_no": reg}).json()
    assert report["asked"] is False and report["advice"]["quick"] is False
    assert "the patient did not answer the danger-sign questions" in report["advice"]["reasons"]
    visit = db.visit_for(reg)
    assert visit["status"] == "nurse" and visit["checked_in"] is not None
    assert [p["reg_no"] for p in client.get("/api/nurse/patients").json()["patients"]] == [reg]
    assert client.get("/api/me/" + reg).json()["status"] == "nurse"
    assert client.post("/api/checkin", json={"reg_no": reg, "answers": []}).status_code == 409
    assert client.post("/api/line/join", json={"reg_no": reg, "level": 4, "review": "checked"}).status_code == 200


def test_answering_no_to_every_danger_question_is_not_the_same_as_not_being_asked():
    result = {"level": 4, "probabilities": {"4": 0.9}, "sure": 0.9, "unread": False, "warnings": []}
    assert core.review_advice(result, danger=False, asked=True)["quick"] is True
    assert core.review_advice(result, danger=False, asked=False)["quick"] is False
    assert core.review_advice({**result, "level": 2}, asked=False) == {"quick": True, "emergency": True, "reasons": [],
                                                                               "rule_only": False}


def test_a_doctor_who_is_off_duty_cannot_call_a_patient():
    reg = assessed_visit("sore throat", age=30)
    client.post("/api/line/join", json={"reg_no": reg, "level": 4, "department": "Eye, ear, nose and throat"})
    doctor = next(d["reg_no"] for d in db.staff()["doctors"] if d["department"] == "Eye, ear, nose and throat")
    client.post("/api/staff/duty", json={"reg_no": doctor, "on_duty": False})
    waiting = len(db.line()["patients"])
    refused = client.post("/api/doctor/call", json={"doctor_reg": doctor})
    assert refused.status_code == 409 and "off duty" in refused.json()["detail"]
    assert len(db.line()["patients"]) == waiting and db.visit_for(reg)["status"] == "waiting"      # nobody was moved
    client.post("/api/staff/duty", json={"reg_no": doctor, "on_duty": True})
    assert client.post("/api/doctor/call", json={"doctor_reg": doctor}).json()["called"]["doctor_reg"] == doctor


def test_not_asked_is_never_summed_up_as_no_danger_signs():
    """Seen on the nurse's screen: for a patient who answered nothing, the language model wrote that he
    "denies experiencing any danger signs". The model is now told he was not asked, and such a sentence is cut."""
    report = core._report_for_summary("my father fainted", 81, "Male", answers=[])
    assert core.NOT_ASKED in report and "answered no to all" not in report
    written = "The patient reports his father fainted. He denies experiencing any danger signs."
    assert core.checked_summary(written, "my father fainted", report) == "The patient reports his father fainted."
    answered = core._report_for_summary("my father fainted", 81, "Male", answers=[
        {"question": core.danger_questions_for("my father fainted")[0], "answer": "No", "kind": "danger"}])
    assert "answered no to all" in answered and core.checked_summary(written, "my father fainted", answered) == written

    reg = new_visit("sore throat", age=30)                       # the nurse takes over; the doctor is told
    client.post("/api/triage", json={"reg_no": reg})
    client.post("/api/line/join", json={"reg_no": reg, "level": 4, "review": "checked"})
    assert json.loads(db.visit_for(reg)["summary"])["patient_said"]["danger_asked"] is False
    done = checked_in("sore throat")
    client.post("/api/triage", json={"reg_no": done})
    client.post("/api/line/join", json={"reg_no": done, "level": 4, "review": "checked"})
    assert json.loads(db.visit_for(done)["summary"])["patient_said"]["danger_asked"] is True


# ---------------------------------------------------------------- the patient's name: for staff only

def named_visit(first="Anna", last="Keller", complaint="chest pain since this morning"):
    answer = client.post("/api/register", json={"complaint": complaint, "age": 52, "sex": "Female", "use_model": False,
                                                "first_name": first, "last_name": last})
    assert answer.status_code == 200, answer.text
    return answer.json()["reg_no"]


def test_the_name_is_kept_and_shown_to_the_nurse_and_the_doctor():
    reg = named_visit("Mary-Jane", "O'Neil")
    assert db.visit_for(reg)["first_name"] == "Mary-Jane" and db.visit_for(reg)["last_name"] == "O'Neil"
    client.post("/api/checkin", json={"reg_no": reg, "answers": []})
    assert next(p for p in client.get("/api/nurse/patients").json()["patients"] if p["reg_no"] == reg)["patient"] == "Mary-Jane O'Neil"
    card = client.get(f"/api/nurse/patient/{reg}").json()
    assert card["first_name"] == "Mary-Jane" and card["patient"] == "Mary-Jane O'Neil"
    client.post("/api/triage", json={"reg_no": reg, "answers": []})
    client.post("/api/line/join", json={"reg_no": reg, "level": 3, "department": "Cardiology"})
    assert next(p for p in client.get("/api/line").json()["patients"] if p["reg_no"] == reg)["patient"] == "Mary-Jane O'Neil"
    called = client.post("/api/doctor/call", json={"doctor_reg": "DR-1002"}).json()["called"]
    doctor = client.get(f"/api/doctor/{called['doctor_reg']}/patients").json()
    assert next(p for p in doctor["patients"] if p["reg_no"] == reg)["patient"] == "Mary-Jane O'Neil"


def test_the_name_never_reaches_the_phone_or_the_language_model():
    reg = named_visit("Ivanna", "Shevchenko")
    client.post("/api/checkin", json={"reg_no": reg, "answers": [
        {"question": "In your own words", "answer": "chest went tight on the stairs", "kind": "extra", "topic": "story"}]})
    me = client.get(f"/api/me/{reg}").json()
    assert "Ivanna" not in json.dumps(me) and "Shevchenko" not in json.dumps(me)
    card = client.get(f"/api/nurse/patient/{reg}").json()
    report = core._report_for_summary(card["complaint"], card["age"], card["sex"], card["answers"], card["injury"])
    assert "Ivanna" not in report and "Shevchenko" not in report


def test_names_in_any_alphabet_and_nothing_else():
    assert named_visit("Денис", "van der Berg")
    for first, last in [("Anna2", "Keller"), ("Anna", "<b>Keller</b>"), ("   ", "Keller"), ("A" * 41, "Keller")]:
        body = {"complaint": "cough", "age": 30, "use_model": False, "first_name": first, "last_name": last}
        assert client.post("/api/register", json=body).status_code == 422, (first, last)
    assert client.post("/api/register", json={"complaint": "cough", "age": 30, "use_model": False}).status_code == 200


def test_the_made_up_patients_have_made_up_names():
    assert all(p["patient"] for p in client.get("/api/line").json()["patients"])


# ---------------------------------------------------------------- honest advice: figures only where they were measured

def test_a_test_figure_is_only_shown_for_a_reason_the_tests_measured():
    """The 1,267 test patients have no check-in answers. Advice that rests only on a danger-sign answer is a
    safety rule, and the nurse's screen must not print "right only 63% of the time" next to it."""
    sure = {"level": 4, "probabilities": {"4": 0.92}, "sure": 0.92, "unread": False, "warnings": []}
    assert core.review_advice(sure)["rule_only"] is False                                    # quick: its own figure
    assert core.review_advice(sure, danger=True) == {
        "quick": False, "emergency": False, "reasons": ["the patient answered yes to a danger sign"], "rule_only": True}
    assert core.review_advice(sure, asked=False)["rule_only"] is True
    assert core.review_advice({**sure, "unread": True})["rule_only"] is True
    unsure = {**sure, "probabilities": {"4": 0.5}, "sure": 0.5}
    assert core.review_advice(unsure, danger=True)["rule_only"] is False                     # measured: 63% against 84%
    assert core.review_advice({**sure, "warnings": ["low oxygen"]}, danger=True)["rule_only"] is False
    assert core.review_advice({**sure, "level": 2}, danger=True)["rule_only"] is False


def test_the_doctor_is_told_when_the_nurse_accepted_against_meddys_advice():
    reg = checked_in("sore throat", danger_yes=True)
    report = client.post("/api/triage", json={"reg_no": reg}).json()
    assert report["advice"]["quick"] is False
    client.post("/api/line/join", json={"reg_no": reg, "level": report["level"], "review": "approved"})
    assert json.loads(db.visit_for(reg)["summary"])["nurse_found"]["against_advice"] is True
    checked = checked_in("sore throat", danger_yes=True)
    client.post("/api/triage", json={"reg_no": checked})
    client.post("/api/line/join", json={"reg_no": checked, "level": 4, "review": "checked"})
    assert json.loads(db.visit_for(checked)["summary"])["nurse_found"]["against_advice"] is False


def test_the_log_says_when_the_nurse_took_a_patient_over():
    reg = new_visit("sore throat", age=30)
    client.post("/api/triage", json={"reg_no": reg})
    client.post("/api/triage", json={"reg_no": reg})                              # measuring again is not a second take-over
    steps = [step["what"] for step in client.get(f"/api/visit/{reg}/history").json()["steps"]]
    assert steps == ["registered", "taken by the nurse", "assessed", "assessed"]
    done = checked_in("sore throat")
    client.post("/api/triage", json={"reg_no": done})
    assert "taken by the nurse" not in [s["what"] for s in client.get(f"/api/visit/{done}/history").json()["steps"]]


# ---------------------------------------------------------------- the range of levels and the patients like this one

def test_the_report_gives_a_range_of_levels_and_five_similar_patients():
    reg = checked_in()
    report = client.post("/api/triage", json={"reg_no": reg}).json()
    low, high = report["range"]["low"], report["range"]["high"]
    assert 1 <= low <= report["level"] <= high <= 5 and report["range"]["promise"] == 0.9
    for level in core.LEVELS:                               # every level that reaches the cut is inside the range
        if report["probabilities"][str(level)] >= core._RANGE_CUT + 0.001:
            assert low <= level <= high
    patients = report["similar"]["patients"]
    assert len(patients) == 5 and all(1 <= p["expert_level"] <= 5 and 1 <= p["nurse_level"] <= 5 for p in patients)
    assert report["similar"]["complaint_matched"] is True and "chest" in patients[0]["complaint"]
    assert report["similar"]["under_rated"] == sum(p["nurse_level"] > p["expert_level"] for p in patients)
    assert "diagnos" not in json.dumps(report["similar"]).lower()


def test_a_complaint_nobody_in_the_data_had_is_said_to_be_unmatched():
    odd = core.assess("zxqv blorp", 40)
    assert odd["similar"]["complaint_matched"] is False and all(p["other_complaint"] for p in odd["similar"]["patients"])
    # Everyday words find the patients the data describes in clinical words: the key-word rule reads the kind.
    assert core.kind_of_complaint("it burns when i pee") == "Urology and kidney" and core.kind_of_complaint("zxqv blorp") is None
    everyday = core.assess("it burns when i pee", 29)["similar"]
    assert everyday["complaint_matched"] is True and not any(p["other_complaint"] for p in everyday["patients"])
    assert all(core.kind_of_complaint(p["complaint"]) == "Urology and kidney" for p in everyday["patients"])


def test_the_range_reaches_the_doctor_and_never_the_patients_phone():
    reg = checked_in()
    report = client.post("/api/triage", json={"reg_no": reg}).json()
    client.post("/api/line/join", json={"reg_no": reg, "level": report["level"], "review": "checked"})
    summary = json.loads(db.visit_for(reg)["summary"])
    assert summary["meddy_suggested"]["range"] == report["range"]
    phone = json.dumps(client.get("/api/me/" + reg).json())
    assert "range" not in phone and "similar" not in phone and "level" not in phone


# ---------------------------------------------------------------- "I feel worse" and the re-check times

def in_the_waiting_room(complaint="sore throat", level=4, **more):
    """A patient who has been through check-in and the nurse and now waits with a ticket."""
    reg = checked_in(complaint, **more)
    client.post("/api/triage", json={"reg_no": reg})
    assert client.post("/api/line/join", json={"reg_no": reg, "level": level, "review": "checked"}).status_code == 200
    return reg


def test_feeling_worse_calls_the_nurse_and_changes_neither_level_nor_place():
    reg = in_the_waiting_room()
    before = client.get("/api/me/" + reg).json()
    level = db.visit_for(reg)["level"]
    said = client.post("/api/worse", json={"reg_no": reg})
    assert said.status_code == 200 and said.json() == {"reg_no": reg, "status": "waiting", "worse": True}
    after = client.get("/api/me/" + reg).json()
    assert after["worse"] is True and after["position"] == before["position"] and after["ticket"] == before["ticket"]
    assert db.visit_for(reg)["level"] == level and "level" not in json.dumps(after)
    again = client.get("/api/nurse/patients").json()["recheck"]
    assert [p["reg_no"] for p in again] == [reg] and again[0]["worse"] is True
    assert next(p for p in db.line()["patients"] if p["reg_no"] == reg)["worse"] is True
    client.post("/api/worse", json={"reg_no": reg})                               # pressing twice asks once
    assert db.visit_for(reg)["worse_said"] == 1
    steps = [s["what"] for s in client.get(f"/api/visit/{reg}/history").json()["steps"]]
    assert steps.count("said they feel worse") == 1


def test_the_nurse_rechecks_and_the_patient_keeps_ticket_and_waiting_time():
    reg = in_the_waiting_room()
    ticket, arrived = db.visit_for(reg)["ticket"], db.visit_for(reg)["arrived"]
    db.pass_time(15)
    client.post("/api/worse", json={"reg_no": reg})
    card = client.get("/api/nurse/patient/" + reg).json()
    assert card["status"] == "waiting" and card["worse"] is True and card["level"] == 4
    report = client.post("/api/triage", json={"reg_no": reg, "vitals": "feverish"}).json()
    assert report["recheck"] == {"earlier_level": 4, "earlier_ai_level": card["ai_level"], "worse": True}
    assert report["advice"]["quick"] is False and "the patient says they feel worse" in report["advice"]["reasons"]
    joined = client.post("/api/line/join", json={"reg_no": reg, "level": 3, "review": "checked", "note": "Now feverish."})
    assert joined.status_code == 200 and joined.json()["ticket"] == ticket
    visit = db.visit_for(reg)
    assert (visit["level"], visit["arrived"], visit["worse_at"], visit["rechecks"]) == (3, arrived, None, 1)
    assert visit["seen_at"] == db.line()["clock"]
    assert client.get("/api/me/" + reg).json()["worse"] is False
    assert client.get("/api/nurse/patients").json()["recheck"] == []
    found = json.loads(visit["summary"])["nurse_found"]
    assert found["said_worse"] == 1 and found["rechecks"] == 1 and found["level"] == 3 and found["note"] == "Now feverish."
    steps = client.get(f"/api/visit/{reg}/history").json()["steps"]
    assert steps[-1]["what"] == "re-checked" and steps[-1]["detail"] == "level 4 to level 3, after the patient said they felt worse"


def test_a_recheck_to_level_two_goes_straight_to_the_resuscitation_team():
    reg = in_the_waiting_room()
    client.post("/api/worse", json={"reg_no": reg})
    client.post("/api/triage", json={"reg_no": reg, "vitals": "unstable"})
    sent = client.post("/api/line/join", json={"reg_no": reg, "level": 2, "review": "checked"}).json()
    assert sent["called"]["doctor_reg"] == "DR-1000" and db.visit_for(reg)["status"] == "with doctor"


def test_feeling_worse_before_the_nurse_puts_the_patient_first_on_her_list():
    first = checked_in("sore throat", danger_yes=True)
    second = checked_in("sore throat")
    assert [p["reg_no"] for p in client.get("/api/nurse/patients").json()["patients"]] == [first, second]
    assert client.post("/api/worse", json={"reg_no": second}).json()["status"] == "nurse"
    listed = client.get("/api/nurse/patients").json()["patients"]
    assert [p["reg_no"] for p in listed] == [second, first] and listed[0]["worse"] is True
    report = client.post("/api/triage", json={"reg_no": second}).json()            # the first look also counts it
    assert "the patient says they feel worse" in report["advice"]["reasons"] and "recheck" not in report
    client.post("/api/line/join", json={"reg_no": second, "level": 4, "review": "checked"})
    assert db.visit_for(second)["worse_at"] is None and db.visit_for(second)["rechecks"] == 0


def test_feeling_worse_is_refused_when_nobody_is_waiting():
    assert client.post("/api/worse", json={"reg_no": "P-0000-0000"}).status_code == 404
    reg = new_visit("sore throat")                                               # still checking in
    assert client.post("/api/worse", json={"reg_no": reg}).status_code == 409
    waiting = in_the_waiting_room()
    client.post("/api/line/join", json={"reg_no": waiting, "level": 2})          # now with the resuscitation team
    assert client.post("/api/worse", json={"reg_no": waiting}).status_code == 409
    assert db.visit_for(waiting)["worse_said"] == 0


def test_a_patient_is_due_a_recheck_after_the_time_their_level_allows():
    """Level 3 every 30 minutes, 4 every 60, 5 every 120 (the reassessment times of the Canadian scale, which the
    Korean scale of our data was built from), counted from the last time the nurse decided on them."""
    assert core.RECHECK_AFTER == {3: 30, 4: 60, 5: 120}
    assert core.recheck_due({"level": 3, "arrived": 0, "seen_at": None}, 29) is False
    assert core.recheck_due({"level": 3, "arrived": 0, "seen_at": None}, 30) is True
    assert core.recheck_due({"level": 3, "arrived": 0, "seen_at": 20}, 45) is False      # seen again at minute 20
    assert core.recheck_due({"level": 5, "arrived": 0, "seen_at": None}, 119) is False
    assert core.recheck_due({"level": 2, "arrived": 0, "seen_at": None}, 500) is False   # levels 1-2 do not wait here
    reg = in_the_waiting_room(level=3)
    mine = lambda: next(p for p in db.line()["patients"] if p["reg_no"] == reg)          # noqa: E731
    assert mine()["recheck_due"] is False
    db._meta("clock", db._meta("clock") + 30)                                            # half an hour, nobody called
    assert mine()["recheck_due"] is True
    due = client.get("/api/nurse/patients").json()["recheck"]
    assert reg in [p["reg_no"] for p in due] and all(p["recheck_due"] or p["worse"] for p in due)
    client.post("/api/worse", json={"reg_no": reg})                                      # who asked comes before who is due
    assert client.get("/api/nurse/patients").json()["recheck"][0]["reg_no"] == reg
    client.post("/api/triage", json={"reg_no": reg})
    client.post("/api/line/join", json={"reg_no": reg, "level": 3, "review": "checked"})
    assert mine()["recheck_due"] is False and mine()["worse"] is False


def test_an_older_database_file_gets_the_new_columns_and_still_works():
    """A file from before this version: the columns are added, and a patient who was already waiting in it
    (nothing in the new columns) is still listed, can say they feel worse and can be re-checked."""
    reg = in_the_waiting_room()
    added = ["seen_at", "worse_at", "worse_said", "rechecks", "ai_level_then", "remeasured", "held_at", "quick_pending"]
    with db._lock, db._conn:
        for column in added:
            db._conn.execute(f"ALTER TABLE visits DROP COLUMN {column}")
    db._upgrade()
    columns = {r["name"] for r in db._conn.execute("PRAGMA table_info(visits)")}
    assert set(added) <= columns
    old = db.visit_for(reg)
    assert (old["seen_at"], old["worse_at"], old["worse_said"], old["rechecks"], old["remeasured"]) == (None, None, 0, 0, 0)
    assert next(p for p in db.line()["patients"] if p["reg_no"] == reg)["recheck_due"] is False
    assert client.get("/api/nurse/patient/" + reg).json()["since_seen"] == 0
    assert client.post("/api/worse", json={"reg_no": reg}).status_code == 200
    report = client.post("/api/triage", json={"reg_no": reg}).json()
    assert report["recheck"]["earlier_ai_level"] == old["ai_level"]
    assert client.post("/api/line/join", json={"reg_no": reg, "level": 4, "review": "checked"}).status_code == 200
    assert db.visit_for(reg)["rechecks"] == 1 and db.visit_for(reg)["ticket"] == old["ticket"]


def test_the_patients_phone_never_downloads_the_whole_line():
    """The line holds every waiting patient's name, complaint and level. The phone page used to fetch it just to
    read the clock. It may only ask about its own visit."""
    page = (server.STATIC / "index.html").read_text()
    assert "/api/line" not in page and "/api/nurse" not in page and "/api/doctor" not in page
    assert "/api/me/" in page and "/api/worse" in page


# ---------------------------------------------------------------- what the audit found

def test_the_nurse_and_the_doctor_always_see_the_same_range():
    """The doctor's range is worked out again from the stored probabilities, which are rounded to three decimals.
    The nurse's is now worked out from the same rounded figures, so the two cannot differ."""
    import triage
    for odds in triage.fair_probabilities(triage.load())[::7]:
        shown = [round(float(p), 3) for p in odds]
        assert core.level_range_of(odds) == core.level_range_of(shown)
    one = core.level_range_of([0.01, 0.02, 0.02, 0.93, 0.02])
    assert (one["low"], one["high"]) == (4, 4) and one["inside"] == core._RANGE["inside"][1]
    wide = core.level_range_of([0.2, 0.2, 0.2, 0.2, 0.2])
    assert (wide["low"], wide["high"]) == (1, 5) and wide["inside"] == core._RANGE["inside"][3]


def doctors_view(reg):
    doctor = db.visit_for(reg)["doctor_reg"]
    return next(p for p in client.get(f"/api/doctor/{doctor}/patients").json()["patients"] if p["reg_no"] == reg)


def test_the_doctor_is_told_about_a_call_the_nurse_did_not_get_to():
    reg = in_the_waiting_room()
    client.post("/api/worse", json={"reg_no": reg})
    assert db.call_next(reg)["reg_no"] == reg                          # called before the nurse looked again
    found = doctors_view(reg)["summary"]["nurse_found"]
    assert (found["said_worse"], found["rechecks"], found["worse_unanswered"]) == (1, 0, True)
    assert client.get("/api/me/" + reg).json()["worse"] is False       # the phone only shows the room now


def test_a_press_before_the_nurses_first_look_is_not_called_unanswered():
    reg = checked_in("sore throat")
    client.post("/api/worse", json={"reg_no": reg})
    client.post("/api/triage", json={"reg_no": reg})
    client.post("/api/line/join", json={"reg_no": reg, "level": 4, "review": "checked"})
    db.call_next(reg)
    found = doctors_view(reg)["summary"]["nurse_found"]
    assert (found["said_worse"], found["rechecks"], found["worse_unanswered"]) == (1, 0, False)


def test_sending_a_waiting_patient_straight_in_counts_as_the_recheck():
    reg = in_the_waiting_room()
    client.post("/api/worse", json={"reg_no": reg})
    client.post("/api/triage", json={"reg_no": reg, "vitals": "unstable"})
    sent = client.post("/api/refer", json={"reg_no": reg, "level": 2, "reason": "Now short of breath and grey."})
    assert sent.status_code == 200
    visit = db.visit_for(reg)
    assert (visit["status"], visit["worse_at"], visit["rechecks"], visit["remeasured"]) == ("with doctor", None, 1, 0)
    found = doctors_view(reg)["summary"]["nurse_found"]
    assert (found["said_worse"], found["rechecks"], found["worse_unanswered"]) == (1, 1, False)
    steps = client.get(f"/api/visit/{reg}/history").json()["steps"]
    assert "felt worse" in next(step["detail"] for step in steps if step["what"] == "sent straight in")


def test_a_recheck_keeps_the_nurses_note_and_her_word_on_the_summary():
    reg = checked_in("sore throat")
    client.post("/api/triage", json={"reg_no": reg})
    client.post("/api/line/join", json={"reg_no": reg, "level": 4, "review": "checked",
                                        "note": "Lives alone, daughter is on her way."})
    card = client.get("/api/nurse/patient/" + reg).json()
    assert card["note"] == "Lives alone, daughter is on her way." and card["summary_held_back"] is False
    client.post("/api/triage", json={"reg_no": reg})
    client.post("/api/line/join", json={"reg_no": reg, "level": 4, "review": "approved"})            # no note sent: it stays
    assert db.visit_for(reg)["nurse_note"] == "Lives alone, daughter is on her way."
    assert json.loads(db.visit_for(reg)["summary"])["nurse_found"]["note"] == "Lives alone, daughter is on her way."
    client.post("/api/line/join", json={"reg_no": reg, "level": 4, "review": "approved", "note": ""})   # cleared on purpose
    assert db.visit_for(reg)["nurse_note"] is None


def test_the_nurse_card_remembers_that_she_held_the_ai_summary_back():
    reg = checked_in("sore throat")
    card = client.get("/api/nurse/patient/" + reg).json()
    report = core._report_for_summary(card["complaint"], card["age"], card["sex"], card["answers"], card["injury"])
    core._summaries[(reg, report)] = {"source": "ai", "text": "The patient reports a sore throat."}
    client.post("/api/triage", json={"reg_no": reg})
    client.post("/api/line/join", json={"reg_no": reg, "level": 4, "review": "checked", "summary_ok": False})
    said = json.loads(db.visit_for(reg)["summary"])["patient_said"]
    assert said["written_by"] == "code" and said["held_back"] is True
    assert client.get("/api/nurse/patient/" + reg).json()["summary_held_back"] is True


def test_measuring_twice_in_one_recheck_keeps_what_stood_before():
    reg = in_the_waiting_room()
    first = db.visit_for(reg)["ai_level"]
    client.post("/api/triage", json={"reg_no": reg, "vitals": "unstable"})
    again = client.post("/api/triage", json={"reg_no": reg, "vitals": "unstable"}).json()
    assert again["recheck"]["earlier_ai_level"] == first and again["recheck"]["earlier_level"] == 4
    assert db.visit_for(reg)["remeasured"] == 1
    db.call_next(reg)                                                  # the doctor calls before the nurse decides
    assert doctors_view(reg)["summary"]["nurse_found"]["remeasured"] is True
    decided = in_the_waiting_room()
    client.post("/api/triage", json={"reg_no": decided})
    client.post("/api/line/join", json={"reg_no": decided, "level": 4, "review": "checked"})
    assert db.visit_for(decided)["remeasured"] == 0 and db.visit_for(decided)["ai_level_then"] == db.visit_for(decided)["ai_level"]


# ---------------------------------------------------------------- the re-check, attacked

def test_a_patient_the_nurse_is_rechecking_is_not_called_away():
    """She measured again and has not decided. A doctor's call would lose her decision, so the patient stays with
    her: for fifteen minutes at most, so that a re-check nobody finishes cannot keep them out of the line for ever."""
    reg = in_the_waiting_room("sore throat", level=3)
    doctor = next(d["reg_no"] for d in db.staff()["doctors"] if d["department"] == "Eye, ear, nose and throat" and d["on_duty"])
    client.post("/api/triage", json={"reg_no": reg, "vitals": "unstable"})
    held = next(p for p in db.line()["patients"] if p["reg_no"] == reg)
    assert held["with_nurse"] is True and db.visit_for(reg)["status"] == "waiting"
    called = client.post("/api/doctor/call", json={"doctor_reg": doctor}).json()["called"]
    assert called is None or called["reg_no"] != reg
    while (called := client.post("/api/line/next").json()["called"]) is not None:       # everyone else is called
        assert called["reg_no"] != reg
    assert db.visit_for(reg)["status"] == "waiting"
    sent = client.post("/api/line/join", json={"reg_no": reg, "level": 2, "review": "checked"})   # her decision lands
    assert sent.status_code == 200 and sent.json()["called"]["doctor_reg"] == "DR-1000"

    left = in_the_waiting_room("sore throat", level=3)                                  # a re-check nobody finishes
    client.post("/api/triage", json={"reg_no": left})
    assert client.post("/api/line/next").json()["called"] is None
    db._meta("clock", db._meta("clock") + db.HOLD_FOR)
    assert next(p for p in db.line()["patients"] if p["reg_no"] == left)["with_nurse"] is False
    assert client.post("/api/line/next").json()["called"]["reg_no"] == left
    assert doctors_view(left)["summary"]["nurse_found"]["remeasured"] is True           # and the doctor is told


def test_joining_again_without_measuring_is_not_a_recheck():
    reg = checked_in("sore throat")
    client.post("/api/triage", json={"reg_no": reg})
    join = {"reg_no": reg, "level": 4, "review": "checked"}
    assert client.post("/api/line/join", json=join).status_code == 200
    assert client.post("/api/line/join", json=join).status_code == 200                 # a second click, a retry
    assert db.visit_for(reg)["rechecks"] == 0
    client.post("/api/worse", json={"reg_no": reg})
    seen = db.visit_for(reg)["seen_at"]
    db._meta("clock", db._meta("clock") + 10)
    client.post("/api/line/join", json={"reg_no": reg, "level": 3})                    # a new level, nobody measured
    visit = db.visit_for(reg)
    assert (visit["level"], visit["rechecks"], visit["seen_at"]) == (3, 0, seen)
    assert visit["worse_at"] is not None and client.get("/api/me/" + reg).json()["worse"] is True
    steps = [step["what"] for step in client.get(f"/api/visit/{reg}/history").json()["steps"]]
    assert "re-checked" not in steps


def test_a_report_that_arrives_after_the_doctors_call_is_refused(monkeypatch):
    reg = in_the_waiting_room()
    before = db.visit_for(reg)
    real = core.assess
    monkeypatch.setattr(core, "assess", lambda *a, **k: (db.call_next(reg), real(*a, **k))[1])   # called while Meddy works
    late = client.post("/api/triage", json={"reg_no": reg, "vitals": "unstable"})
    assert late.status_code == 409
    after = db.visit_for(reg)
    assert after["status"] == "with doctor" and (after["vitals"], after["ai_level"]) == (before["vitals"], before["ai_level"])
    steps = [step["what"] for step in client.get(f"/api/visit/{reg}/history").json()["steps"]]
    assert steps[-1] != "assessed"


def test_the_test_regime_counts_the_advice_the_nurse_had_when_she_decided():
    reg, report = report_for("ankle pain", pain=4)
    assert report["advice"]["quick"] is True
    client.post("/api/line/join", json={"reg_no": reg, "level": report["level"], "review": "approved"})
    assert db.staff()["reviews"]["accepted_against_advice"] == 0
    client.post("/api/worse", json={"reg_no": reg})
    again = client.post("/api/triage", json={"reg_no": reg}).json()                     # now Meddy advises a check
    assert again["advice"]["quick"] is False
    assert db.staff()["reviews"]["accepted_against_advice"] == 0                         # she has not decided again
    db.call_next(reg)                                                                   # called before she does
    assert db.staff()["reviews"]["accepted_against_advice"] == 0
    other, report = report_for("ankle pain", pain=4)
    client.post("/api/line/join", json={"reg_no": other, "level": report["level"], "review": "approved"})
    client.post("/api/worse", json={"reg_no": other})
    client.post("/api/triage", json={"reg_no": other})
    client.post("/api/line/join", json={"reg_no": other, "level": report["level"]})    # a re-check with no verdict given
    assert db.visit_for(other)["review"] == "checked"
    assert json.loads(db.visit_for(other)["summary"])["nurse_found"]["against_advice"] is False


def test_a_made_up_patient_can_be_rechecked_and_keeps_what_meddy_said_before():
    waiting = next(p for p in db.line()["patients"] if not p["you"])
    reg, first = waiting["reg_no"], db.visit_for(waiting["reg_no"])["ai_level"]
    client.post("/api/triage", json={"reg_no": reg, "vitals": "unstable", "alert": "Unresponsive"})
    again = client.post("/api/triage", json={"reg_no": reg, "vitals": "unstable", "alert": "Unresponsive"}).json()
    assert again["recheck"]["earlier_ai_level"] == first and again["recheck"]["worse"] is False
    client.post("/api/line/join", json={"reg_no": reg, "level": waiting["level"], "review": "checked"})
    visit = db.visit_for(reg)
    assert visit["ticket"] == waiting["ticket"] and visit["rechecks"] == 1


def test_who_asked_first_is_seen_first_and_the_due_are_not_forgotten():
    danger = checked_in("sore throat", danger_yes=True)
    early, late = checked_in("sore throat"), checked_in("sore throat")
    client.post("/api/worse", json={"reg_no": late})
    assert client.get("/api/me/" + late).json()["worse"] is True                        # shown while waiting for the nurse
    db._meta("clock", db._meta("clock") + 5)
    client.post("/api/worse", json={"reg_no": danger})
    assert [p["reg_no"] for p in client.get("/api/nurse/patients").json()["patients"]] == [late, danger, early]

    due = in_the_waiting_room(level=3)
    asked = in_the_waiting_room(level=5)
    db._meta("clock", db._meta("clock") + 30)
    assert client.get("/api/nurse/patient/" + due).json()["recheck_due"] is True
    client.post("/api/worse", json={"reg_no": asked})
    listed = [p["reg_no"] for p in client.get("/api/nurse/patients").json()["recheck"]]
    assert listed[0] == asked and due in listed                                         # asked first, the due still listed
    report = client.post("/api/triage", json={"reg_no": due}).json()                    # a re-check nobody asked for
    assert report["recheck"]["worse"] is False and report["recheck"]["earlier_level"] == 3


def test_a_second_press_after_a_recheck_counts_and_none_counts_once_the_visit_is_over():
    reg = in_the_waiting_room()
    for _ in range(2):
        client.post("/api/worse", json={"reg_no": reg})
        client.post("/api/triage", json={"reg_no": reg})
        client.post("/api/line/join", json={"reg_no": reg, "level": 4, "review": "checked"})
    assert (db.visit_for(reg)["worse_said"], db.visit_for(reg)["rechecks"]) == (2, 2)
    client.post("/api/worse", json={"reg_no": reg})
    db.call_next(reg)
    assert client.get("/api/nurse/patient/" + reg).json()["worse"] is False            # no longer the nurse's to answer
    assert client.post("/api/worse", json={"reg_no": reg}).status_code == 409
    client.post("/api/doctor/finish", json={"reg_no": reg})
    assert client.post("/api/worse", json={"reg_no": reg}).status_code == 409
    assert db.visit_for(reg)["worse_said"] == 3



# ---------------------------------------------------------------- everyday words, the doctor the nurse chooses, the simulation

@pytest.mark.parametrize("patient, nurse", [
    ("my heart hurts since this morning", "chest pain"),
    ("i cant breathe", "dyspnea"),
    ("it burns when i pee", "dysuria"),
    ("stomach ache", "abd pain"),
    ("I fainted at work", "syncope"),
    ("ant. chest pain", "ant. chest pain"),          # nurses' own words are left as they are
])
def test_everyday_words_are_put_in_the_nurses_words(patient, nurse):
    assert core.nurse_words(patient) == nurse


@pytest.mark.parametrize("complaint, department", [
    ("heart pain", "Cardiology"), ("pain when peeing", "Urology and kidney"), ("stomach ache", "Gastroenterology and surgery"),
    ("i cant breathe", "Respiratory medicine"), ("pregnant bleeding", "Obstetrics and gynaecology"),
    ("broken arm", "Trauma and orthopaedics"), ("sore throat", "Eye, ear, nose and throat"),
])
def test_everyday_complaints_find_their_department(complaint, department):
    """ "heart pain" used to come out as neurology 31%, urology 37% and the like: the model alone did not know the words."""
    result = core.assess(complaint, 45)
    options = result["department_options"]
    assert result["department"] == department == options[0]["department"]
    assert len({o["department"] for o in options}) == 4 and options[0]["chance"] > options[1]["chance"]
    assert abs(sum(result["department_match"].values()) - 1) < 0.01


def test_the_report_suggests_a_doctor_and_a_short_list_of_three_more():
    _, report = report_for("chest pain on and off", pain=4)
    best, others = report["doctors"]["suggested"], report["doctors"]["others"]
    assert best["department"] == report["department"] == "Cardiology" and 1 <= len(others) <= 3
    assert others[0]["department"] == "Cardiology"                         # the next cardiologist first
    # Other departments only when they fit; general medicine, which covers everyone, may close the list.
    assert all(d["department"] in ("Cardiology", core.DEFAULT_DEPARTMENT) or d["match"] >= db.SHORT_LIST_FIT for d in others)
    assert len({d["doctor_reg"] for d in [best, *others]}) == len(others) + 1
    on_duty = {d["reg_no"]: d for d in db.staff()["doctors"] if d["on_duty"]}
    for d in [best, *others]:
        assert d["doctor_reg"] in on_duty and d["busy"] == (on_duty[d["doctor_reg"]]["waiting"] > 0)
    # Of two cardiologists the less busy one is suggested.
    assert best["with_now"] <= others[0]["with_now"]


def test_a_busy_doctor_can_still_be_chosen_and_the_patient_waits_for_them():
    busy = "DR-1001"                                         # Dr. Marlow starts the day with two patients
    assert db.staff()["doctors"][1]["busy"]
    reg_no = assessed_visit("chest pain", age=40)
    joined = client.post("/api/line/join", json={"reg_no": reg_no, "level": 3, "department": "Cardiology",
                                                 "review": "checked", "doctor_reg": busy})
    assert joined.status_code == 200 and db.visit_for(reg_no)["wanted_doctor"] == busy
    assert {d["reg_no"]: d["queue"] for d in db.staff()["doctors"]}[busy] == 1
    # Another cardiologist calls: the patient is waiting for Dr. Marlow, so they stay in line.
    other = client.post("/api/doctor/call", json={"doctor_reg": "DR-1002"}).json()["called"]
    assert other is None or other["reg_no"] != reg_no
    called = client.post("/api/doctor/call", json={"doctor_reg": busy}).json()["called"]
    assert called["reg_no"] == reg_no and called["doctor_reg"] == busy
    assert any(e["what"] == "doctor chosen" for e in db.history(reg_no))


def test_the_chosen_doctor_also_takes_a_patient_called_from_the_line_or_sent_straight_in():
    reg_no = assessed_visit("headache", age=40)
    client.post("/api/line/join", json={"reg_no": reg_no, "level": 3, "doctor_reg": "DR-1013"})   # obstetrics, on purpose
    assert db.visit_for(reg_no)["department"] == "Obstetrics and gynaecology"     # the visit takes the doctor's department
    called = db.call_next(reg_no)
    assert called["doctor_reg"] == "DR-1013" and "nurse chose" in called["reason"]
    sent = client.post("/api/refer", json={"reg_no": assessed_visit("cough"), "level": 3, "reason": "Looks very unwell",
                                           "doctor_reg": "DR-1016"}).json()
    assert sent["doctor_reg"] == "DR-1016"


def test_a_chosen_doctor_who_goes_off_duty_is_covered_by_the_department():
    reg_no = assessed_visit("chest pain", age=40)
    client.post("/api/line/join", json={"reg_no": reg_no, "level": 3, "doctor_reg": "DR-1001"})
    db.set_duty("DR-1001", False)
    assert client.post("/api/doctor/call", json={"doctor_reg": "DR-1002"}).json()["called"]["reg_no"] == reg_no


@pytest.mark.parametrize("doctor", ["DR-9999", "DR-1000", "Dr. Marlow"])
def test_only_a_doctor_from_the_staff_list_can_be_chosen(doctor):
    reg_no = assessed_visit("chest pain", age=40)
    answer = client.post("/api/line/join", json={"reg_no": reg_no, "level": 3, "doctor_reg": doctor})
    assert answer.status_code == 422
    assert db.visit_for(reg_no)["status"] == "nurse" and db.visit_for(reg_no)["wanted_doctor"] is None


def test_the_short_list_can_be_asked_for_again():
    _, report = report_for("ear pain", pain=4)
    again = client.post("/api/doctor-choices", json={"match": report["department_match"]}).json()
    assert again["suggested"]["department"] == report["doctors"]["suggested"]["department"]
    junk = client.post("/api/doctor-choices", json={"match": {"Nowhere": 1.0, "Cardiology": 7}}).json()
    assert junk == {"suggested": None, "others": []}


class FixedDice:
    """Dice that always roll the same, so a step of the simulation is known in advance."""
    def __init__(self, roll):
        self.roll = roll
    def random(self):
        return self.roll
    def uniform(self, low, high):
        return high
    def choice(self, items):
        return items[0]


def test_the_simulation_brings_and_finishes_made_up_patients_only():
    real = assessed_visit("chest pain", age=40)
    db.refer(real, 3, "Looks unwell", doctor_reg="DR-1001")          # a real patient with a doctor
    before = db.staff()["visits"]["with doctor"]
    db.simulate(FixedDice(0.99), now=1000)                           # the morning's patients get a time to leave
    assert db.staff()["visits"]["with doctor"] == before
    left = db.simulate(FixedDice(0.99), now=1000 + 10_000)["left"]   # long after: all made-up patients have left
    assert left == before - 1 and db.staff()["visits"]["with doctor"] == 1
    assert db.visit_for(real)["status"] == "with doctor"             # the real patient is never touched
    free = [d for d in db.staff()["doctors"] if d["on_duty"] and not d["busy"] and d["reg_no"] != "DR-1000"]
    waiting = assessed_visit("cough", age=40)
    client.post("/api/line/join", json={"reg_no": waiting, "level": 4})          # a real patient in the waiting room
    db.wait(30)                                                      # the made-up ones have waited long enough
    in_line = len(db.line()["patients"])
    came = db.simulate(FixedDice(0.0), now=20_000)["came"]           # every free doctor gets a patient
    called = in_line - len(db.line()["patients"]) + 1                # made-up patients called in (one walked in)
    assert came + called == len(free) and called > 0
    assert all(d["busy"] for d in db.staff()["doctors"] if d["on_duty"] and d["reg_no"] != "DR-1000")
    assert db.visit_for(waiting)["status"] == "waiting"              # a real patient in the line is never called


def test_the_simulation_leaves_a_watched_doctor_and_can_be_switched_off():
    client.get("/api/doctor/DR-1001/patients")                      # Dr. Marlow's screen is open
    db.simulate(FixedDice(0.99), now=1000)
    db.simulate(FixedDice(0.99), now=50_000)
    assert len(db.doctor_patients("DR-1001")["patients"]) == 2       # still the two of the morning
    db._watched.clear()
    client.post("/api/staff/simulation", json={"on": False})
    assert db.staff()["simulation"] is False
    assert db.simulate(FixedDice(0.0), now=90_000) == {"left": 0, "came": 0}
    client.post("/api/staff/simulation", json={"on": True})


# ---------------------------------------------------------------- the complaint in the summary, measured values, busy doctors

def test_the_summary_always_names_what_the_patient_came_with():
    """For "Hurt pain for one hour" the model wrote only "She describes the pain as sharp and stabbing."."""
    assert core.with_complaint("She describes the pain as sharp and stabbing.", "Hurt pain for one hour") == (
        'Came with "Hurt pain for one hour". She describes the pain as sharp and stabbing.')
    told = "The patient reports chest pain since this morning."
    assert core.with_complaint(told, "chest pain since this morning") == told          # already there: left alone


def test_the_nurse_can_type_in_what_she_measured():
    reg_no = new_visit("cough", age=50)
    measured = {"SBP": 92, "DBP": 58, "HR": 121, "RR": 27, "BT": 39.2, "Saturation": 90}
    report = client.post("/api/triage", json={"reg_no": reg_no, "vitals": measured}).json()
    assert report["vitals"] == measured
    assert json.loads(db.visit_for(reg_no)["vitals"])["HR"] == 121
    for wrong in [{**measured, "HR": 900}, {**measured, "Saturation": 140}, {k: v for k, v in measured.items() if k != "BT"}]:
        assert client.post("/api/triage", json={"reg_no": reg_no, "vitals": wrong}).status_code == 422
    assert client.post("/api/triage", json={"reg_no": reg_no, "vitals": "feverish"}).status_code == 200   # presets still work


def test_a_busy_doctor_says_since_when_and_about_when_they_are_free():
    db.simulate(FixedDice(0.99), now=1000)                       # the morning's made-up patients went in at 1000
    for _ in range(3):                                           # three finished visits of five minutes each
        reg = assessed_visit("chest pain", age=40)
        db.refer(reg, 3, "Looks unwell", doctor_reg="DR-1014")
        with db._lock, db._conn:
            db._conn.execute("UPDATE visits SET seen_from = 0 WHERE reg_no = ?", (reg,))
        db.finish_visit(reg)
        with db._lock, db._conn:
            db._conn.execute("UPDATE visits SET seen_to = 300 WHERE reg_no = ?", (reg,))
    with db._lock:
        times = db._doctor_times(now=1000 + 120)
    marlow = times["DR-1001"]                                    # two patients, the first in at 1000
    assert marlow["since"] == 1000 and marlow["usual_minutes"] == 5
    assert marlow["free_in_minutes"] == 8                        # two visits of 5 minutes, 2 minutes gone
    doctors = {d["reg_no"]: d for d in db.staff()["doctors"]}
    assert doctors["DR-1001"]["since"] == 1000 and doctors["DR-1014"]["since"] is None     # Dr. Kowalski is free


@pytest.mark.parametrize("typed, meant", [
    ("hurt pain for one hour", "heart pain for one hour"), ("breating problems", "breathing problems"),
    ("headake since morning", "headache since morning"), ("i have diarea", "i have diarrhea"),
    ("pain in my chets", "pain in my chest"), ("feaver and cough", "fever and cough"),
    ("it hurts when i swallow", "it hurts when i swallow"),        # real words and their endings are left alone
    ("burned my hand", "burned my hand"), ("abd pain", "abd pain"), ("i was walking and fell", "i was walking and fell"),
])
def test_typing_slips_are_put_right(typed, meant):
    assert core.spelling(typed) == meant
    answer = client.get("/api/spelling", params={"text": typed}).json()
    assert answer["text"] == meant and answer["changed"] == (typed != meant)


def test_a_typing_slip_still_finds_the_right_questions_and_department():
    assert core.questionnaire("breating problems", use_model=False)["topic"] == "Respiratory medicine"
    assert core.assess("hurt pain for one hour", 30)["department"] == "Cardiology"
    assert core.known_words("breating problems")
    reg = new_visit("hurt pain for one hour", age=30)
    assert client.get(f"/api/nurse/patient/{reg}").json()["complaint_read"] == "heart pain for one hour"
    report = core._report_for_summary("hurt pain for one hour", 30, "Female", [])
    assert 'typing slips corrected: "heart pain for one hour"' in report


def test_an_open_page_can_tell_a_new_version_is_online():
    assert re.fullmatch(r"[0-9a-f]{12}", client.get("/api/version").json()["version"])


def test_what_the_patient_types_is_never_put_in_an_address():
    """The phone asks for the spelling fix while the patient types. It sends the words in the body of the request:
    an address can be written down by any proxy in front of the hospital, and this is a health complaint."""
    fixed = client.post("/api/spelling", json={"text": "hurt pain for one hour"})
    assert fixed.status_code == 200 and fixed.json() == {"text": "heart pain for one hour", "changed": True, "understood": True}
    assert client.post("/api/spelling", json={"text": "chest pain"}).json() == {"text": "chest pain", "changed": False,
                                                                                "understood": True}
    assert client.post("/api/spelling", json={"text": "у меня болит голова"}).json()["understood"] is False
    assert client.post("/api/spelling", json={"text": "x" * 201}).status_code == 422
    page = (server.STATIC / "index.html").read_text(encoding="utf-8")
    assert "/api/spelling?" not in page and "?text=" not in page and "?complaint=" not in page


# ---------------------------------------------------------------- the staff PIN, tickets, real time, pain question

@pytest.fixture
def staff_pin(monkeypatch):
    monkeypatch.setattr(server, "STAFF_PIN", "4821")
    server._wrong_pins.clear()
    yield
    client.cookies.clear()


def test_with_a_staff_pin_the_staff_screens_need_it_and_the_phone_does_not(staff_pin):
    reg = new_visit("cough", age=40)                                     # the phone registers without a PIN
    assert client.get(f"/api/me/{reg}").status_code == 200
    assert client.get("/api/waiting-room").json()["waiting"] >= 0
    for path in ["/api/nurse/patients", f"/api/nurse/patient/{reg}", "/api/staff", "/api/line", "/api/events",
                 "/api/doctor/DR-1001/patients"]:
        assert client.get(path).status_code == 401, path
    for path, body in [("/api/reset", {}), ("/api/line/wait", {}), ("/api/triage", {"reg_no": reg}),
                       ("/api/staff/duty", {"reg_no": "DR-1001", "on_duty": False})]:
        assert client.post(path, json=body).status_code == 401, path
    assert client.get("/api/staff/session").json() == {"locked": True, "pin": True}
    assert client.post("/api/staff/login", json={"pin": "0000"}).status_code == 401
    assert client.post("/api/staff/login", json={"pin": "4821"}).status_code == 200
    assert client.get("/api/staff/session").json() == {"locked": False, "pin": True}
    assert client.get("/api/nurse/patients").status_code == 200
    client.post("/api/staff/logout")
    assert client.get("/api/nurse/patients").status_code == 401


def test_guessing_the_staff_pin_is_slowed_down(staff_pin):
    for _ in range(5):
        assert client.post("/api/staff/login", json={"pin": "1111"}).status_code == 401
    assert client.post("/api/staff/login", json={"pin": "4821"}).status_code == 429     # even the right one, for a minute


def test_without_a_staff_pin_the_screens_stay_open():
    assert server.STAFF_PIN is None
    assert client.get("/api/staff/session").json() == {"locked": False, "pin": False}
    assert client.get("/api/nurse/patients").status_code == 200


def test_simulated_patients_do_not_use_up_the_waiting_rooms_tickets():
    db.simulate(FixedDice(0.0), now=1000)                                 # every free doctor gets a simulated patient
    before = db._meta("ticket")
    # A tickets belong to the waiting room only: one for every visit that ever stood in the line today.
    a_tickets = db._conn.execute("SELECT COUNT(*) AS n FROM visits WHERE ticket LIKE 'A%'").fetchone()["n"]
    assert a_tickets == before
    tickets = [r["ticket"] for r in db._conn.execute("SELECT ticket FROM visits WHERE status = 'with doctor' AND ticket LIKE 'B%'")]
    assert tickets and tickets[0] == "B001"
    reg = assessed_visit("cough", age=40)
    joined = client.post("/api/line/join", json={"reg_no": reg, "level": 4}).json()
    assert joined["ticket"] == f"A{before + 1:03d}"


def test_the_clock_runs_in_real_minutes(monkeypatch):
    start = db._clock()
    real = db.time.time
    monkeypatch.setattr(db.time, "time", lambda: real() + 20 * 60)       # twenty real minutes later
    assert db._clock() == start + 20
    db.wait(15)                                                          # "15 minutes pass" still jumps ahead
    assert db._clock() == start + 35


@pytest.mark.parametrize("complaint, mentioned", [("chest pain", True), ("my knee hurts", True), ("headache", True),
                                                  ("breathing problems", False), ("fever and cough", False)])
def test_the_pain_question_depends_on_the_complaint(complaint, mentioned):
    assert core.questionnaire(complaint, use_model=False)["pain"]["mentioned"] is mentioned


def test_the_summary_drops_a_kind_of_pain_the_patient_never_described():
    report = core._report_for_summary("hurt pain for one hour", 30, "Female", [])
    with pytest.raises(ValueError):
        core.checked_summary("The patient reported experiencing a sharp, persistent pain for one hour.", "hurt pain for one hour", report)
    described = core._report_for_summary("chest pain", 30, "Female", [{**core.PAIN_FEEL, "answer": "Sharp or stabbing"}])
    assert core.checked_summary("She describes the pain as sharp.", "chest pain", described) == "She describes the pain as sharp."


# ---------------------------------------------------------------- what the review list still left open

def test_old_made_up_visits_are_removed_and_a_real_one_never():
    real = assessed_visit("chest pain", age=40)
    db.refer(real, 3, "Looks unwell", doctor_reg="DR-1001")
    client.post("/api/doctor/finish", json={"reg_no": real})
    db.simulate(FixedDice(0.99), now=1000)
    db.simulate(FixedDice(0.99), now=50_000)                                             # the morning's made-up patients have left
    made_up_done = db._conn.execute("SELECT COUNT(*) AS n FROM visits WHERE you = 0 AND status = 'done'").fetchone()["n"]
    assert made_up_done >= len(db.SEED_WITH_DOCTOR) - 1
    assert db.forget_old_made_up() == 0                                                  # closed today: all kept
    with db._lock, db._conn:
        db._conn.execute("UPDATE visits SET closed_at = '2026-01-01T00:00:00+00:00' WHERE status = 'done'")
    assert db.forget_old_made_up() == made_up_done
    assert db._conn.execute("SELECT COUNT(*) AS n FROM visits WHERE you = 0 AND status = 'done'").fetchone()["n"] == 0
    assert db.visit_for(real)["status"] == "done" and client.get(f"/api/visit/{real}/history").json()["steps"]
    assert db._conn.execute("SELECT COUNT(*) AS n FROM events WHERE visit_reg NOT IN (SELECT reg_no FROM visits)").fetchone()["n"] == 0
    assert len(db.line()["patients"]) == len(db.SEED_WAITING)                            # the waiting ones are untouched


def test_sharing_the_word_pain_is_not_sharing_a_complaint():
    """A patient who wrote only "pain" was shown five patients with "abd pain" as alike."""
    vague = core.assess("pain", 40)["similar"]
    assert vague["complaint_matched"] is False and all(p["other_complaint"] for p in vague["patients"])
    chest = core.assess("chest pain", 40)["similar"]
    assert chest["complaint_matched"] is True and not any(p["other_complaint"] for p in chest["patients"])
    assert core._telling_words("abd pain, lt") == {"abd"} and core._telling_words("pain") == set()


def test_a_pin_typed_in_another_alphabet_is_a_wrong_pin_and_not_a_crash(monkeypatch):
    monkeypatch.setattr(server, "STAFF_PIN", "4821")
    server._wrong_pins.clear()
    assert client.post("/api/staff/login", json={"pin": "пин-код"}).status_code == 401
    assert client.post("/api/staff/login", json={"pin": "4821"}).status_code == 200
    server._wrong_pins.clear(); client.cookies.clear()


def test_gemma_stays_loaded_between_patients():
    assert core.KEEP_GEMMA_LOADED == "12h"


# ---------------------------------------------------------------- made-up patients stay out of the nurse's way

def test_made_up_patients_have_their_own_numbers_and_fitting_names():
    seeded = [r for r in db._conn.execute("SELECT reg_no, sex, first_name, last_name FROM visits WHERE you = 0")]
    assert seeded and all(r["reg_no"].startswith("S-2026-") for r in seeded)
    assert all((r["first_name"], r["last_name"]) in (db.MADE_UP_WOMEN if r["sex"] == "Female" else db.MADE_UP_MEN)
               for r in seeded)
    doctors = {name.split()[-1] for _, name, *_ in db.SEED_DOCTORS}
    assert not doctors & {last for _, last in db.MADE_UP_NAMES}
    first = new_visit("cough", age=40)
    db.simulate(FixedDice(0.0), now=1000)                            # dozens of made-up patients
    assert int(new_visit("cough", age=40)[-4:]) == int(first[-4:]) + 1      # real numbers count real patients only


def test_made_up_patients_never_crowd_needs_you_again():
    db.wait(200)                                                     # every made-up patient in the line is overdue
    assert db.nurse_patients()["recheck"] == []
    reg = assessed_visit("cough", age=40)
    client.post("/api/line/join", json={"reg_no": reg, "level": 3})
    db.wait(45)
    assert [p["reg_no"] for p in db.nurse_patients()["recheck"]] == [reg]


# ---------------------------------------------------------------- the nurse manages her patients

def test_the_nurse_can_remove_a_patient_with_a_reason():
    waiting = db.line()["patients"][0]["reg_no"]
    answer = client.post("/api/nurse/remove", json={"reg_no": waiting, "why": "left without being seen"})
    assert answer.status_code == 200 and db.visit_for(waiting)["status"] == "done"
    assert db.visit_for(waiting)["closed_why"] == "left without being seen"
    assert waiting not in [p["reg_no"] for p in db.line()["patients"]]
    assert any(e["what"] == "removed" for e in db.history(waiting))
    at_the_desk = new_visit("cough", age=40)
    assert client.post("/api/nurse/remove", json={"reg_no": at_the_desk, "why": "registered by mistake"}).status_code == 200
    assert client.post("/api/nurse/remove", json={"reg_no": at_the_desk, "why": "registered by mistake"}).status_code == 409     # already closed
    assert client.post("/api/nurse/remove", json={"reg_no": new_visit("cough", age=40)}).status_code == 422      # no reason
    assert client.post("/api/nurse/remove", json={"reg_no": new_visit("cough", age=40), "why": "bored"}).status_code == 422
    assert client.post("/api/nurse/remove", json={"reg_no": "P-0000-0000", "why": "sent home by the nurse"}).status_code == 404


def test_still_fine_starts_the_re_check_again_and_keeps_the_place():
    reg = assessed_visit("cough", age=40)
    client.post("/api/line/join", json={"reg_no": reg, "level": 3})
    db.wait(45)                                                            # longer than a level 3 may wait unseen
    assert reg in [p["reg_no"] for p in db.nurse_patients()["recheck"]]
    place = [p["reg_no"] for p in db.line()["patients"]].index(reg)
    assert client.post("/api/nurse/still-fine", json={"reg_no": reg}).status_code == 200
    assert reg not in [p["reg_no"] for p in db.nurse_patients()["recheck"]]
    assert [p["reg_no"] for p in db.line()["patients"]].index(reg) == place and db.visit_for(reg)["level"] == 3
    assert client.post("/api/nurse/still-fine", json={"reg_no": new_visit("cough", age=40)}).status_code == 409


def test_todays_patients_say_where_each_one_went_and_the_phone_knows_its_doctor():
    waiting = assessed_visit("chest pain", age=40)
    client.post("/api/line/join", json={"reg_no": waiting, "level": 3, "doctor_reg": "DR-1002"})
    sent = assessed_visit("cough", age=40)
    db.refer(sent, 3, "Looks unwell", doctor_reg="DR-1016")
    gone = new_visit("cough", age=40)
    db.remove_visit(gone, "sent home by the nurse")
    where = {p["reg_no"]: p["where"] for p in client.get("/api/nurse/today").json()["patients"]}
    assert where[waiting] == "In the waiting room for Dr. Okafor"
    assert where[sent] == "With Dr. Moreau, Room 16"
    assert where[gone] == "Closed: sent home by the nurse"
    assert all(not r.startswith("P-") or r in where for r in where)                 # made-up patients are left out
    assert client.get(f"/api/me/{waiting}").json()["doctor"] == "Dr. Okafor"
    assert [p for p in db.line()["patients"] if p["reg_no"] == waiting][0]["doctor"] == "Dr. Okafor"


# ---------------------------------------------------------------- the doctor accepts, the phone says thank you

def test_the_doctor_sees_who_waits_for_them_and_accepts_one():
    chosen = assessed_visit("dizziness", age=60)
    client.post("/api/line/join", json={"reg_no": chosen, "level": 4, "doctor_reg": "DR-1004"})     # Dr. Haddad
    screen = client.get("/api/doctor/DR-1004/patients").json()
    waiting = {p["reg_no"]: p for p in screen["waiting_for_you"]}
    assert waiting[chosen]["chosen"] is True and waiting[chosen]["level"] == 4
    assert any(not p["chosen"] for p in screen["waiting_for_you"])        # the made-up neurology patient of the line
    assert chosen not in {p["reg_no"] for p in client.get("/api/doctor/DR-1005/patients").json()["waiting_for_you"]}
    # Another neurologist cannot accept a patient the nurse gave to Dr. Haddad.
    assert client.post("/api/doctor/call", json={"doctor_reg": "DR-1005", "reg_no": chosen}).status_code == 409
    called = client.post("/api/doctor/call", json={"doctor_reg": "DR-1004", "reg_no": chosen}).json()["called"]
    assert called["reg_no"] == chosen and called["room"] == "Room 4" and called["reason"] == "Accepted by Dr. Haddad."
    assert client.get(f"/api/me/{chosen}").json()["status"] == "called"
    assert chosen not in {p["reg_no"] for p in client.get("/api/doctor/DR-1004/patients").json()["waiting_for_you"]}
    assert client.post("/api/doctor/call", json={"doctor_reg": "DR-1004", "reg_no": chosen}).status_code == 409


def test_the_phone_knows_how_the_visit_ended():
    seen = assessed_visit("cough", age=40)
    db.refer(seen, 3, "Looks unwell", doctor_reg="DR-1016")
    assert client.get(f"/api/me/{seen}").json()["closed"] is None
    client.post("/api/doctor/finish", json={"reg_no": seen})
    me = client.get(f"/api/me/{seen}").json()
    assert me["status"] == "finished" and me["closed"] == "seen"
    removed = new_visit("cough", age=40)
    db.remove_visit(removed, "left without being seen")
    assert client.get(f"/api/me/{removed}").json()["closed"] == "removed"
    closed = new_visit("cough", age=40)
    db.new_day()
    assert db.where_is(closed)["closed"] == "closed"


def test_the_doctor_always_learns_when_it_started(monkeypatch):
    """Gemma 3 1B asked "doing, before, medicine" for almost every complaint and never when it started."""
    copied = [{"topic": "trigger", "question": "What were you doing when the chest pain started?"},
              {"topic": "before", "question": "Have you had this chest pain before?"},
              {"topic": "medicine", "question": "Have you taken anything for the chest pain?"}]
    monkeypatch.setattr(core, "questions_from_model", lambda complaint, skip: copied)
    monkeypatch.setattr(core, "_written", {})
    asked = core.follow_up("crushing chest pain", use_model=True)["questions"]
    assert [q["topic"] for q in asked] == ["onset", "trigger", "before"]
    told = core.follow_up("chest pain since this morning", use_model=True)["questions"]
    assert "onset" not in [q["topic"] for q in told]                    # the complaint already says when


def test_the_summary_line_with_the_complaint_only_when_something_is_missing():
    assert core.with_complaint("The patient reported chest pain and sweating.", "crushing chest pain and sweating") ==         "The patient reported chest pain and sweating."
    assert core.with_complaint("The patient reports breathing difficulties since last night.",
                               "breating problems since last night").startswith("The patient")
    assert core.with_complaint("The patient reports a headache.", "у меня болит голова").startswith('Came with "у меня')


def test_the_short_list_offers_no_department_that_does_not_fit():
    match = {"Gastroenterology and surgery": 0.96, "Urology and kidney": 0.02, "General medicine": 0.01,
             "Eye, ear, nose and throat": 0.01}
    choices = db.doctor_choices(match)
    assert {d["department"] for d in [choices["suggested"], *choices["others"]]} <= {"Gastroenterology and surgery",
                                                                                     "General medicine"}


def test_the_waiting_room_tickets_start_at_a001_each_day():
    assert sorted(p["ticket"] for p in db.line()["patients"]) == ["A001", "A002", "A003", "A004", "A005"]


# ---------------------------------------------------------------- estimate, board, doctor's note, chief doctor

def test_the_phone_gets_an_estimated_wait_as_a_range():
    reg = assessed_visit("sore throat", age=30)
    client.post("/api/line/join", json={"reg_no": reg, "level": 5})
    estimate = client.get(f"/api/me/{reg}").json()["estimate"]
    assert 0 <= estimate["low"] < estimate["high"] and estimate["high"] - estimate["low"] >= 5
    assert estimate["low"] % 5 == 0 and estimate["high"] % 5 == 0
    assert client.get(f"/api/me/{new_visit('cough', age=40)}").json()["estimate"] is None     # not in the line yet


def test_more_patients_ahead_for_the_same_doctor_means_a_longer_wait():
    first = assessed_visit("sore throat", age=30)
    client.post("/api/line/join", json={"reg_no": first, "level": 4, "doctor_reg": "DR-1009"})
    later = assessed_visit("ear pain", age=30)
    client.post("/api/line/join", json={"reg_no": later, "level": 5, "doctor_reg": "DR-1009"})
    assert db.wait_estimate(later)["minutes"] > db.wait_estimate(first)["minutes"]


def test_the_board_is_public_and_shows_no_name_complaint_or_level(staff_pin):
    reg = assessed_visit("cough", age=40)
    db.join_line(reg, 4, False)
    ticket = db.visit_for(reg)["ticket"]
    board = client.get("/api/board").json()                         # no PIN needed
    assert board["waiting"] == len(db.line()["patients"]) and board["typical"]["high"] > board["typical"]["low"]
    called = db.call_next(reg)
    board = client.get("/api/board", params={"ticket": ticket.lower()}).json()
    assert board["called"][0] == {"ticket": ticket, "room": called["room"], "doctor": called["doctor"], "minutes_ago": 0}
    assert board["ticket"] == {"ticket": ticket, "status": "called", "room": called["room"]}
    text = json.dumps(board)
    assert "cough" not in text and "level" not in text and "name" not in text and "patient" not in text
    assert client.get("/board").status_code == 200


def test_meddy_only_tidies_the_doctors_note():
    note = "rest today drink water come back if pain gets worse after 2 days"
    assert core.checked_tidy("Rest today, drink water. Come back if the pain gets worse after 2 days.", note)
    for wrong in ["Rest today. Take ibuprofen 400 mg three times a day.",            # added advice and a number
                  "Rest today, drink water, come back after 3 days.",                 # a number changed
                  ""]:
        with pytest.raises(ValueError):
            core.checked_tidy(wrong, note)


def test_the_doctors_note_reaches_the_patients_phone(monkeypatch):
    reg = assessed_visit("cough", age=40)
    db.refer(reg, 3, "Looks unwell", doctor_reg="DR-1016")
    monkeypatch.setattr(core, "tidy_note", lambda text: {"text": "Rest today, and drink plenty of water.", "source": "ai",
                                                         "added": [], "dropped": []})
    assert client.post("/api/doctor/tidy", json={"text": "rest today and drink plenty of water"}).json() == {
        "text": "Rest today, and drink plenty of water.", "source": "ai", "added": [], "dropped": [], "changed": True}
    def off(text):
        raise ValueError("no model")
    monkeypatch.setattr(core, "tidy_note", off)
    assert client.post("/api/doctor/tidy", json={"text": "rest"}).status_code == 503
    assert client.post("/api/doctor/finish", json={"reg_no": reg, "note": "x" * 301}).status_code == 422
    assert client.post("/api/doctor/finish", json={"reg_no": reg, "note": "Rest today."}).json()["note"] == "Rest today."
    me = client.get(f"/api/me/{reg}", headers={"X-Meddy-Visit": db.phone_key(reg)}).json()   # the patient's own phone
    assert me["closed"] == "seen" and me["note"] == "Rest today."
    assert {p["reg_no"]: p["note"] for p in client.get("/api/nurse/today").json()["patients"]}[reg] == "Rest today."


def test_the_chief_doctors_panel_has_its_own_pin(staff_pin, monkeypatch):
    monkeypatch.setattr(server, "CHIEF_PIN", "9911")
    client.post("/api/staff/login", json={"pin": "4821"})                      # a nurse is signed in
    assert client.get("/api/nurse/patients").status_code == 200
    assert client.get("/api/chief/report").status_code == 401                 # the staff PIN does not open the panel
    assert client.get("/api/staff/session", params={"role": "chief"}).json() == {"locked": True, "pin": True}
    assert client.post("/api/staff/login", json={"pin": "4821", "role": "chief"}).status_code == 401
    assert client.post("/api/staff/login", json={"pin": "9911", "role": "chief"}).status_code == 200
    report = client.get("/api/chief/report").json()
    assert set(report) >= {"flow", "registered", "levels", "departments", "meddy"} and len(report["levels"]) == 5


def test_without_its_own_pin_the_staff_pin_opens_the_chief_doctors_panel(staff_pin):
    assert server.CHIEF_PIN is None
    assert client.get("/api/chief/report").status_code == 401
    client.post("/api/staff/login", json={"pin": "4821"})
    assert client.get("/api/chief/report").status_code == 200


def test_the_chief_doctor_sees_how_the_day_went():
    seen = assessed_visit("cough", age=40)
    db.refer(seen, 3, "Looks unwell", doctor_reg="DR-1016")
    db.finish_visit(seen)
    gone = new_visit("cough", age=40)
    db.remove_visit(gone, "left without being seen")
    report = db.chief_report()
    assert report["registered"] == 2 and report["flow"]["seen and finished"] == 1 and report["flow"]["removed by the nurse"] == 1
    assert report["levels"][2]["seen"] == 1 and report["levels"][2]["door_median"] is not None
    assert report["meddy"]["assessed"] == 1


# ---------------------------------------------------------------- anal pain, the tidy check, the waiting patient

@pytest.mark.parametrize("complaint", ["anal pain", "rectal bleeding", "hemorrhoids", "piles", "bleeding from my anus"])
def test_the_bottom_end_of_the_gut_goes_to_gastroenterology_and_surgery(complaint):
    """"anal pain" went to general medicine and "rectal bleeding" to trauma (because of "bleeding")."""
    assert core.department_for(complaint) == core.GUT == core.assess(complaint, 40)["department"]
    assert core.department_for("analysis of my blood") != core.GUT          # whole words only


def test_meddy_may_put_a_typo_right_but_not_drop_a_word():
    assert core.checked_tidy("Take a rest.", "taka a rest") == "Take a rest."
    assert core.checked_tidy("Do not drive today.", "do not drive today") == "Do not drive today."
    for wrong, note in [("Drive today.", "do not drive today"),
                        ("Ice the ankle for 2 days.", "no fracture. ice the ankle and keep it up for 2 days"),
                        ("Take a rest and see a cardiologist.", "taka a rest")]:
        with pytest.raises(ValueError):
            core.checked_tidy(wrong, note)


def test_a_patient_next_for_a_busy_doctor_is_told_so():
    reg = assessed_visit("chest tightness", age=40)
    client.post("/api/line/join", json={"reg_no": reg, "level": 5, "doctor_reg": "DR-1001"})    # Dr. Marlow has patients
    me = client.get(f"/api/me/{reg}").json()
    assert me["doctor"] == "Dr. Marlow" and me["doctor_busy"] is True
    for p in db.doctor_patients("DR-1001")["patients"]:
        db.finish_visit(p["reg_no"])
    assert client.get(f"/api/me/{reg}").json()["doctor_busy"] is False


# ---------------------------------------------------------------- tidy that always helps, one patient at a time, the chief

def test_plain_code_tidies_punctuation_without_changing_a_word():
    assert core.plain_tidy("wash you anal and put hot compres") == "Wash you anal and put hot compress."
    assert core.plain_tidy("rest today.drink water , i will see you in 2 days") ==         "Rest today. Drink water, I will see you in 2 days."


@pytest.mark.parametrize("model_wrote, source, added, dropped", [
    ("Wash the anal area and apply a hot compress.", "ai", ["area", "apply"], ["put"]),     # shown, with what changed
    ("I'm sorry, but I cannot fulfill this request. My purpose is to be helpful.", "plain", [], []),   # Gemma refused
    ("Wash and put hot compress.", "ai", [], ["anal"]),         # one word gone: shown, with the doctor told which
])
def test_meddy_tidies_the_note_or_falls_back_to_plain_code(monkeypatch, model_wrote, source, added, dropped):
    import io
    class Answer(FakeModel):
        def read(self):
            return json.dumps({"message": {"content": self.text}}).encode()
    monkeypatch.setattr(core.urllib.request, "urlopen", Answer(model_wrote))
    tidied = core.tidy_note("wash you anal and put hot compres")
    assert tidied["source"] == source and tidied["added"] == added and tidied["dropped"] == dropped
    if source == "plain":
        assert tidied["text"] == "Wash you anal and put hot compress."


def test_a_dropped_no_or_not_is_never_offered(monkeypatch):
    class Answer(FakeModel):
        def read(self):
            return json.dumps({"message": {"content": self.text}}).encode()
    monkeypatch.setattr(core.urllib.request, "urlopen", Answer("Drive today."))
    assert core.tidy_note("do not drive today") == {"text": "Do not drive today.", "source": "plain", "added": [], "dropped": []}


def test_the_doctor_sees_one_patient_at_a_time():
    reg = assessed_visit("sore throat", age=30)
    client.post("/api/line/join", json={"reg_no": reg, "level": 5, "doctor_reg": "DR-1009"})   # Dr. Sorensen has a patient
    answer = client.post("/api/doctor/call", json={"doctor_reg": "DR-1009", "reg_no": reg, "one_at_a_time": True})
    assert answer.status_code == 409 and "Finish the visit" in answer.json()["detail"]
    for p in db.doctor_patients("DR-1009")["patients"]:
        db.finish_visit(p["reg_no"])
    called = client.post("/api/doctor/call", json={"doctor_reg": "DR-1009", "reg_no": reg, "one_at_a_time": True}).json()
    assert called["called"]["reg_no"] == reg


def test_the_nurse_can_remove_a_patient_who_is_with_a_doctor():
    reg = assessed_visit("cough", age=40)
    db.refer(reg, 3, "Looks unwell", doctor_reg="DR-1016")
    before = {d["reg_no"]: d["waiting"] for d in db.staff()["doctors"]}["DR-1016"]
    assert client.post("/api/nurse/remove", json={"reg_no": reg, "why": "left without being seen"}).status_code == 200
    assert {d["reg_no"]: d["waiting"] for d in db.staff()["doctors"]}["DR-1016"] == before - 1
    assert db.visit_for(reg)["status"] == "done"


def test_the_chief_doctor_can_start_from_scratch(staff_pin):
    for _ in range(3):
        new_visit("cough", age=40)
    assert client.post("/api/chief/wipe", json={"confirm": "ERASE"}).status_code == 401           # chief only
    client.post("/api/staff/login", json={"pin": "4821", "role": "chief"})
    assert client.post("/api/chief/wipe", json={"confirm": "yes"}).status_code == 422             # the word, on purpose
    assert client.post("/api/chief/wipe", json={"confirm": "ERASE"}).json() == {"wiped": True}
    assert db.staff()["all days"]["visits"] == len(db.SEED_WITH_DOCTOR) + len(db.SEED_WAITING)   # only the made-up ones
    assert new_visit("cough", age=40) == "P-2026-0001"


def test_meddy_suggests_from_the_numbers():
    tips = db.chief_report()["suggestions"]
    assert tips and all(isinstance(t, str) for t in tips)
    db.wait(200)                                                       # the made-up line has waited very long
    reg = assessed_visit("cough", age=40)
    client.post("/api/line/join", json={"reg_no": reg, "level": 3})
    db.wait(80)
    assert any("waiting room" in t and "re-check" in t for t in db.chief_report()["suggestions"])


def test_the_patients_page_does_not_lead_to_the_staff_screens():
    page = (server.STATIC / "index.html").read_text(encoding="utf-8")
    assert 'href="/nurse"' not in page and 'href="/doctor"' not in page and 'href="/board"' in page


def test_plain_code_puts_right_slips_of_common_note_words():
    assert core.plain_tidy("taka a rest no running for 2 days and come back if pain gets worse") ==         "Take a rest no running for 2 days and come back if pain gets worse."
    assert core.plain_tidy("drnk plenty of fluds and rest") == "Drink plenty of fluids and rest."
    assert core.plain_tidy("see your GP on monday") == "See your GP on monday."         # unknown words left alone


def test_a_tight_chest_counts_as_pain():
    assert core.mentions_pain("my chest feels tight when i walk upstairs")
    assert core.mentions_pain("pressure in my chest")


def test_the_privacy_page_names_every_cookie_and_browser_item_the_site_sets():
    """/privacy is open to everyone and lists every cookie the server sets and every item the pages save in the
    browser, so the page cannot quietly fall behind the code. Every screen links to it."""
    page = client.get("/privacy")
    assert page.status_code == 200 and "Privacy and cookies" in page.text
    static = server.STATIC
    code = "".join(p.read_text(encoding="utf-8") for p in static.glob("*.html")) + (static / "meddy-site.js").read_text(encoding="utf-8")
    for cookie in (server.STAFF_COOKIE, server.CHIEF_COOKIE):
        assert cookie in page.text
    saved = set(re.findall(r'(?:const \w+ = |Storage\.setItem\()"(meddy-[a-z-]+)"', code)) | {"meddy-offered-"}
    assert saved >= {"meddy-visit", "meddy-doctor", "meddy-large", "meddy-curtain", "meddy-privacy-seen"}
    for item in saved:
        assert item in page.text, item
    for screen in ("index", "nurse", "doctor", "board", "chief"):
        assert 'href="/privacy"' in (static / f"{screen}.html").read_text(encoding="utf-8"), screen
