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
    assert "This project is a prototype and is not medical advice." in page.text
    assert "Meddy suggests, the nurse decides." in page.text


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
    everything = json.dumps([dict(r) for table in ("people", "visits") for r in db._conn.execute(f"SELECT * FROM {table}")])
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
