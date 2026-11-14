"""The hospital's internal database (SQLite, one file: hospital.db).

It holds four things:
- doctors : every doctor's registration number, department, room, whether they are on duty,
            and how many patients they have now (always equal to their open visits)
- visits  : every patient visit, under its own registration number, from arrival to the doctor
- answers : what each patient answered at check-in
- people  : the registry that keeps one open visit per person. It holds a one-way fingerprint of each
            insurance card, never the card number itself

No names are stored for patients. The doctors are made up for the demonstration.
"""

import hashlib
import json
import os
import re
import sqlite3
import threading
from pathlib import Path

import core

# The tests point this at a throwaway file so they never touch the demonstration database.
DB_PATH = Path(os.environ.get("HOSPITAL_DB", Path(__file__).parent / "hospital.db"))
_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS doctors (
    reg_no TEXT PRIMARY KEY, name TEXT NOT NULL, department TEXT NOT NULL, room TEXT NOT NULL,
    on_duty INTEGER NOT NULL DEFAULT 1, waiting INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS visits (
    reg_no TEXT PRIMARY KEY, ticket TEXT, complaint TEXT NOT NULL, age REAL, sex TEXT, injury INTEGER DEFAULT 0,
    level INTEGER, danger INTEGER DEFAULT 0, you INTEGER DEFAULT 0, arrived INTEGER,
    department TEXT, department_how TEXT,
    ai_level INTEGER, vitals TEXT, referred INTEGER DEFAULT 0, referral_reason TEXT,
    ai_odds TEXT, second_check TEXT,
    ai_department TEXT, quick_advised INTEGER, review TEXT,
    person TEXT REFERENCES people(person), insurance TEXT NOT NULL DEFAULT 'none', card_end TEXT,
    status TEXT NOT NULL DEFAULT 'registered', doctor_reg TEXT REFERENCES doctors(reg_no));
CREATE TABLE IF NOT EXISTS people (
    person TEXT PRIMARY KEY, insurance TEXT NOT NULL, card_end TEXT NOT NULL, visits INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, visit_reg TEXT NOT NULL REFERENCES visits(reg_no),
    question TEXT NOT NULL, answer TEXT, kind TEXT NOT NULL, topic TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""

# (registration number, name, department, room)
SEED_DOCTORS = [
    ("DR-1000", "Resuscitation team", "Resuscitation team", "Resus bay"),
    ("DR-1001", "Dr. Marlow", "Cardiology", "Room 1"),
    ("DR-1002", "Dr. Okafor", "Cardiology", "Room 2"),
    ("DR-1003", "Dr. Lindqvist", "Respiratory medicine", "Room 3"),
    ("DR-1004", "Dr. Haddad", "Neurology", "Room 4"),
    ("DR-1005", "Dr. Petrov", "Neurology", "Room 5"),
    ("DR-1006", "Dr. Castell", "Trauma and orthopaedics", "Room 6"),
    ("DR-1007", "Dr. Nwosu", "Trauma and orthopaedics", "Room 7"),
    ("DR-1008", "Dr. Baptiste", "Gastroenterology and surgery", "Room 8"),
    ("DR-1009", "Dr. Sorensen", "Eye, ear, nose and throat", "Room 9"),
    ("DR-1010", "Dr. Achebe", "General medicine", "Room 10"),
    ("DR-1011", "Dr. Ferrand", "General medicine", "Room 11"),
    ("DR-1012", "Dr. Varga", "Urology and kidney", "Room 12"),
    ("DR-1013", "Dr. Imani", "Obstetrics and gynaecology", "Room 13"),
]
# Made-up patients who are already with a doctor when the day starts, so every doctor's list is made of
# real visits that the doctor's screen can open. The nurse's level is made up; Meddy's level is really
# computed for each of them (see _seed_cards).
# (doctor, complaint, age, sex, nurse's level, vital signs, pain score, alertness, minutes since triage)
SEED_WITH_DOCTOR = [
    ("DR-1001", "chest tightness on exertion", 62, "Male", 3, "normal", 3, "Alert", 55),
    ("DR-1001", "palpitations", 48, "Female", 3, "feverish", 0, "Alert", 30),
    ("DR-1003", "cough and dyspnea", 71, "Male", 3, "feverish", 3, "Alert", 40),
    ("DR-1004", "headache", 35, "Female", 3, "normal", 5, "Alert", 25),
    ("DR-1005", "dizziness", 66, "Female", 3, "normal", 0, "Alert", 70),
    ("DR-1005", "left arm weakness", 74, "Male", 3, "unstable", 0, "Responds to voice", 15),
    ("DR-1005", "numbness in both hands", 52, "Male", 3, "normal", 0, "Alert", 50),
    ("DR-1006", "wrist pain after a fall", 29, "Female", 4, "normal", 5, "Alert", 60),
    ("DR-1006", "laceration, right hand", 41, "Male", 4, "normal", 3, "Alert", 35),
    ("DR-1007", "knee pain", 57, "Male", 4, "normal", 3, "Alert", 80),
    ("DR-1009", "ear pain", 23, "Female", 4, "normal", 5, "Alert", 45),
    ("DR-1010", "fever", 38, "Male", 3, "feverish", 3, "Alert", 65),
    ("DR-1010", "general weakness", 81, "Female", 3, "normal", 0, "Alert", 20),
    ("DR-1011", "rash on both arms", 19, "Female", 5, "normal", 0, "Alert", 90),
    ("DR-1012", "flank pain", 44, "Male", 3, "normal", 7, "Alert", 28),
]
_seed_levels = {}       # Meddy's level for each made-up patient, worked out once and kept
# Made-up patients who walk in while the demonstration runs, one for every 15 minutes that pass:
# (complaint, age, sex, nurse's level, vital signs, pain score)
SEED_ARRIVALS = [
    ("cough for a week", 33, "Male", 4, "normal", 0),
    ("cut finger", 27, "Female", 5, "normal", 4),
    ("abd pain and vomiting", 58, "Female", 3, "feverish", 4),
    ("back pain", 46, "Male", 4, "normal", 4),
    ("sore throat", 21, "Female", 5, "normal", 4),
    ("palpitations", 63, "Male", 3, "feverish", 0),
    ("ankle sprain", 30, "Male", 4, "normal", 4),
    ("ear pain", 19, "Female", 5, "normal", 4),
]
# Insurance of the made-up patients (type, last four characters of the card): made up as well.
MADE_UP_INSURANCE = [("public", "4471"), ("private", "0932"), ("public", "7715"), ("none", None), ("public", "2208")]
# Patients already in the waiting room when the demonstration starts: (complaint, level, minutes ago)
SEED_WAITING = [("sore throat", 5, 40), ("ankle pain", 4, 31), ("abd pain", 3, 22), ("headache", 4, 14), ("dizziness", 3, 6)]
START_CLOCK = 40


def _meta(key, value=None):
    if value is None:
        return _conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()["value"]
    _conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (key, value))


def _next_number(key):
    number = _meta(key) + 1
    _meta(key, number)
    return number


def reset():
    """Start the day again: staff back on duty, made-up patients already with the doctors,
    and five sample patients in the waiting room."""
    with _lock, _conn:
        _conn.executescript("DROP TABLE IF EXISTS answers; DROP TABLE IF EXISTS visits; DROP TABLE IF EXISTS doctors;"
                            "DROP TABLE IF EXISTS people; DROP TABLE IF EXISTS meta;" + SCHEMA)
        _conn.executemany("INSERT INTO doctors (reg_no, name, department, room) VALUES (?, ?, ?, ?)", SEED_DOCTORS)
        _meta("clock", START_CLOCK)
        _meta("visit", 0)
        _meta("ticket", 0)
        _meta("arrivals", 0)
        departments = {reg_no: department for reg_no, _, department, _ in SEED_DOCTORS}
        for doctor, complaint, age, sex, level, preset, pain, alert, minutes_ago in SEED_WITH_DOCTOR:
            _made_up_visit(complaint, age, sex, level, preset, pain, alert, START_CLOCK - minutes_ago,
                           departments[doctor], doctor)
            _conn.execute("UPDATE doctors SET waiting = waiting + 1 WHERE reg_no = ?", (doctor,))
        for complaint, level, minutes_ago in SEED_WAITING:
            _made_up_visit(complaint, 40, "Female", level, "normal", 3, "Alert", START_CLOCK - minutes_ago)


def _made_up_visit(complaint, age, sex, level, preset, pain, alert, arrived, department=None, doctor=None):
    """Write one made-up patient, already assessed: the nurse's level is invented, Meddy's level and its
    second check are really computed. With a doctor the visit is 'with doctor', otherwise it is waiting.
    The caller holds the lock and the transaction."""
    vitals = core.VITAL_PRESETS[preset]
    if complaint not in _seed_levels:
        result = core.assess(complaint, age, sex, alert=alert, pain=pain, vitals=vitals)
        _seed_levels[complaint] = (result["level"], [result["probabilities"][str(l)] for l in core.LEVELS])
    ai_level, odds = _seed_levels[complaint]
    number = _next_number("visit")
    reg_no, ticket = f"P-2026-{number:04d}", f"A{_next_number('ticket'):03d}"
    _conn.execute(
        "INSERT INTO visits (reg_no, ticket, complaint, age, sex, level, ai_level, ai_odds, second_check, vitals, arrived, "
        "department, department_how, insurance, card_end, status, doctor_reg) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (reg_no, ticket, complaint, age, sex, level, ai_level, json.dumps(odds), core.second_check(odds, level),
         json.dumps({**vitals, "alert": alert, "pain": pain}), arrived, department,
         "made-up patient" if department else None, *MADE_UP_INSURANCE[number % len(MADE_UP_INSURANCE)],
         "with doctor" if doctor else "waiting", doctor))
    return reg_no, ticket


# ---------------------------------------------------------------- patients

class AlreadyRegistered(Exception):
    """This person already has an open visit. The visit is carried along so the desk can point to it."""

    def __init__(self, visit):
        super().__init__("already registered")
        self.visit = visit


def person_key(card):
    """The person behind an insurance card, as a fingerprint of the card number.

    The registry never keeps the card number itself: only this one-way fingerprint (enough to see that the
    same card came twice) and the last four characters (enough for staff to recognise the card).
    Returns (cleaned number, fingerprint) or None when the number is not 6 to 20 letters and digits."""
    cleaned = re.sub(r"[\s\-./]", "", card or "").upper()
    if not re.fullmatch(r"[A-Z0-9]{6,20}", cleaned):
        return None
    return cleaned, hashlib.sha256(("meddy-registry:" + cleaned).encode()).hexdigest()[:24]


def register(complaint, age, sex, insurance="none", card=None):
    """A new visit gets its registration number the moment the patient arrives.

    One person, one open visit: a card that already has a visit which is not finished is refused with
    AlreadyRegistered. A patient without insurance has no card to recognise them by, so they are always
    registered; nobody is turned away or slowed down for having no insurance."""
    person, card_end = None, None
    if insurance != "none":
        cleaned, person = person_key(card)
        card_end = cleaned[-4:]
    with _lock, _conn:
        if person:
            open_visit = _conn.execute("SELECT reg_no, ticket, status FROM visits WHERE person = ? AND status != 'done'",
                                       (person,)).fetchone()
            if open_visit:
                raise AlreadyRegistered(dict(open_visit))
            _conn.execute("INSERT INTO people (person, insurance, card_end, visits) VALUES (?, ?, ?, 1) "
                          "ON CONFLICT(person) DO UPDATE SET visits = visits + 1, insurance = excluded.insurance",
                          (person, insurance, card_end))
        reg_no = f"P-2026-{_next_number('visit'):04d}"
        _conn.execute("INSERT INTO visits (reg_no, complaint, age, sex, you, person, insurance, card_end) "
                      "VALUES (?, ?, ?, ?, 1, ?, ?, ?)", (reg_no, complaint, age, sex, person, insurance, card_end))
    return reg_no


def save_answers(reg_no, answers, injury):
    with _lock, _conn:
        _conn.execute("DELETE FROM answers WHERE visit_reg = ?", (reg_no,))
        _conn.executemany("INSERT INTO answers (visit_reg, question, answer, kind, topic) VALUES (?, ?, ?, ?, ?)",
                          [(reg_no, a["question"], a.get("answer"), a["kind"], a.get("topic")) for a in answers])
        _conn.execute("UPDATE visits SET injury = ? WHERE reg_no = ?", (int(injury), reg_no))


def answers_for(reg_no):
    """The answers of one visit. AI-written questions carry a history topic ("onset") and its label ("Started"),
    so the doctor's screen can show them as a short overview."""
    with _lock:
        rows = [dict(r) for r in _conn.execute(
            "SELECT question, answer, kind, topic FROM answers WHERE visit_reg = ? ORDER BY id", (reg_no,))]
    return [{**r, "label": core.topic_label(r["topic"])} for r in rows]


def save_assessment(reg_no, ai_level, vitals, alert, pain, odds=None, department=None, quick=None):
    """Keep what the AI suggested and what was measured, so the doctor can see it later.

    ai_level is stored apart from level: level is the nurse's final decision and may be different.
    The six vital signs, the alertness and the pain score go into one JSON text column."""
    record = json.dumps({**vitals, "alert": alert, "pain": pain})
    with _lock, _conn:
        _conn.execute("UPDATE visits SET ai_level = ?, vitals = ?, ai_odds = ?, ai_department = ?, quick_advised = ? "
                      "WHERE reg_no = ?", (ai_level, record, json.dumps(odds) if odds else None, department,
                                           None if quick is None else int(quick), reg_no))


def _flag(reg_no, level):
    """Meddy's second check on the nurse's level ("strong", "mild" or None), from the odds saved at triage."""
    saved = _conn.execute("SELECT ai_odds FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
    return core.second_check(json.loads(saved["ai_odds"]), level) if saved and saved["ai_odds"] else None


def visit_for(reg_no):
    """One visit as a plain dictionary, or None when the registration number is unknown."""
    with _lock:
        row = _conn.execute("SELECT * FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
    return dict(row) if row else None


def join_line(reg_no, level, danger, department=None, department_how=None, review=None):
    """The nurse's decision puts the patient in the line.

    Joining a second time (the nurse changed the level) keeps the same ticket and the same arrival time,
    so nobody gets two tickets or loses the minutes they have already waited."""
    with _lock, _conn:
        visit = _conn.execute("SELECT ticket, arrived FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
        ticket = (visit and visit["ticket"]) or f"A{_next_number('ticket'):03d}"
        arrived = visit["arrived"] if visit and visit["arrived"] is not None else _meta("clock")
        _conn.execute("UPDATE visits SET ticket = ?, level = ?, danger = ?, arrived = ?, department = ?, "
                      "department_how = ?, second_check = ?, review = COALESCE(?, review), status = 'waiting' "
                      "WHERE reg_no = ?",
                      (ticket, level, int(danger), arrived, department, department_how, _flag(reg_no, level), review, reg_no))
    return ticket


# ---------------------------------------------------------------- the line

def _waiting():
    clock = _meta("clock")
    rows = [dict(r) for r in _conn.execute("SELECT * FROM visits WHERE status = 'waiting'")]
    rows.sort(key=lambda p: core.place(p, clock))
    return clock, rows


def line():
    with _lock:
        clock, rows = _waiting()
    return {"clock": clock, "patients": [
        {"reg_no": p["reg_no"], "ticket": p["ticket"], "who": p["complaint"], "level": p["level"],
         "name": core.LEVEL_NAMES[p["level"]], "danger": bool(p["danger"]), "you": bool(p["you"]),
         "waited": clock - p["arrived"], "position": i}
        for i, p in enumerate(rows, 1)]}


def where_is(reg_no):
    """What the patient's own phone may show: the ticket, the place in line and, once called, the doctor.

    On purpose there is no level, no danger mark and no waiting time in here. Patients must never see
    their urgency level (it could frighten them), so the server does not even send it. The answer is
    built from a short list of allowed keys, never by copying the visit row."""
    with _lock:
        visit = _conn.execute("SELECT * FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
        if visit is None:
            return None
        _, rows = _waiting()            # the same order as the line the nurse sees
        doctor = _conn.execute("SELECT name, room FROM doctors WHERE reg_no = ?", (visit["doctor_reg"],)).fetchone()
    me = {"ticket": visit["ticket"], "status": "registered", "position": None, "ahead": None, "doctor": None, "room": None}
    if visit["status"] == "waiting":
        position = [p["reg_no"] for p in rows].index(reg_no) + 1
        me.update(status="waiting", position=position, ahead=position - 1)
    elif visit["status"] == "with doctor":
        me.update(status="called", doctor=doctor["name"], room=doctor["room"])
    elif visit["status"] == "done":
        me.update(status="finished")
    return me


def wait(minutes=15):
    with _lock, _conn:
        _meta("clock", _meta("clock") + minutes)


def pass_time(minutes=15):
    """Fifteen minutes in a working department: the clock moves, the first patient in line is called,
    and one made-up patient walks in. This is what makes waiting visible in the demonstration: a patient
    who has waited long is overtaken by a more urgent newcomer but stays ahead of a less urgent one."""
    wait(minutes)
    called = call_next()
    with _lock, _conn:
        turn = _next_number("arrivals")
        complaint, age, sex, level, preset, pain = SEED_ARRIVALS[(turn - 1) % len(SEED_ARRIVALS)]
        reg_no, ticket = _made_up_visit(complaint, age, sex, level, preset, pain, "Alert", _meta("clock"))
    return {"called": called, "arrived": {"reg_no": reg_no, "ticket": ticket, "who": complaint, "level": level}}


def call_next(reg_no=None):
    """Call the first patient in line, match them with a doctor, and record it all.

    With a registration number, that waiting patient is called instead, whoever is first. The server uses
    this for levels 1 and 2, so an emergency never sits in the line waiting for somebody to press a button."""
    with _lock, _conn:
        clock, rows = _waiting()
        if reg_no is not None:
            rows = [p for p in rows if p["reg_no"] == reg_no]
        if not rows:
            return None
        patient = rows[0]
        doctor, reason = _send_to_doctor(patient)
    return {"reg_no": patient["reg_no"], "ticket": patient["ticket"], "who": patient["complaint"],
            "level": patient["level"], "you": bool(patient["you"]), "waited": clock - patient["arrived"],
            "department": doctor["department"], "doctor": doctor["name"], "doctor_reg": doctor["reg_no"],
            "room": doctor["room"], "reason": reason}


def refer(reg_no, level, reason, department=None):
    """The nurse sends a critical patient straight to a doctor, past the line.

    Her reason is stored so the doctor can read it. The doctor is chosen by the same rules as
    call_next (levels 1-2 always go to the resuscitation team), inside one locked transaction."""
    with _lock, _conn:
        visit = _conn.execute("SELECT * FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
        if visit is None:
            return None
        ticket = visit["ticket"] or f"A{_next_number('ticket'):03d}"      # keep the ticket if they already have one
        # Sending a patient straight in is always the nurse's own check, never a quick acceptance.
        _conn.execute("UPDATE visits SET ticket = ?, level = ?, referred = 1, referral_reason = ?, second_check = ?, "
                      "review = 'checked', arrived = COALESCE(arrived, ?) WHERE reg_no = ?",
                      (ticket, level, reason, _flag(reg_no, level), _meta("clock"), reg_no))
        if department:
            _conn.execute("UPDATE visits SET department = ?, department_how = 'nurse' WHERE reg_no = ?",
                          (department, reg_no))
        patient = dict(_conn.execute("SELECT * FROM visits WHERE reg_no = ?", (reg_no,)).fetchone())
        doctor, match_reason = _send_to_doctor(patient)
    return {"ticket": ticket, "department": doctor["department"], "doctor": doctor["name"],
            "doctor_reg": doctor["reg_no"], "room": doctor["room"], "referral_reason": reason,
            "match_reason": match_reason, "reason": match_reason}


# ---------------------------------------------------------------- doctors

def _on_duty(department):
    return [dict(r) for r in _conn.execute(
        "SELECT * FROM doctors WHERE department = ? AND on_duty = 1 ORDER BY waiting, reg_no", (department,))]


def _send_to_doctor(patient):
    """Match one patient with a doctor and write it down: the visit gets the doctor, the doctor gets one more patient.

    Shared by call_next and refer. The caller already holds the lock and the transaction, so both
    changes are saved together or not at all."""
    doctor, reason = _pick_doctor(patient)
    _conn.execute("UPDATE doctors SET waiting = waiting + 1 WHERE reg_no = ?", (doctor["reg_no"],))
    _conn.execute("UPDATE visits SET status = 'with doctor', doctor_reg = ? WHERE reg_no = ?",
                  (doctor["reg_no"], patient["reg_no"]))
    return doctor, reason


def likely_doctor(level, department, complaint="", injury=False):
    """Who this patient would be sent to right now, without sending them: Meddy's report shows it to the nurse.
    It is an approximation, because the lists can change before the patient is called."""
    with _lock:
        doctor, reason = _pick_doctor({"level": level, "department": department, "department_how": None,
                                       "complaint": complaint, "injury": injury})
    return {"doctor": doctor["name"], "doctor_reg": doctor["reg_no"], "room": doctor["room"],
            "department": doctor["department"], "waiting": doctor["waiting"]}


def review_stats():
    """The test regime: how the nurses judged Meddy's reports so far.

    Every report the nurse sees is either accepted as it is or checked in person. Counting those, and how
    often a personal check changed the level or the department, is a running test of the app in use."""
    with _lock:
        rows = [dict(r) for r in _conn.execute(
            "SELECT review, level, ai_level, department, ai_department, quick_advised FROM visits WHERE review IS NOT NULL")]
    accepted = [r for r in rows if r["review"] == "approved"]
    checked = [r for r in rows if r["review"] == "checked"]
    return {"reviewed": len(rows), "accepted": len(accepted), "checked": len(checked),
            "accepted_against_advice": sum(r["quick_advised"] == 0 for r in accepted),
            "level_changed": sum(r["ai_level"] is not None and r["level"] != r["ai_level"] for r in checked),
            "department_changed": sum(bool(r["ai_department"]) and r["department"] != r["ai_department"] for r in checked)}


def _pick_doctor(patient):
    """Levels 1-2: resuscitation team. Otherwise the least busy doctor ON DUTY in the patient's department.
    If nobody is on duty there, general medicine covers; if nobody is there either, any doctor on duty."""
    if patient["level"] <= 2:
        return _on_duty("Resuscitation team")[0], "Level 1 or 2: straight to the resuscitation team."
    # The department was decided at the nurse station. Sample patients have none, so the rule decides.
    department = patient["department"] or core.department_for(patient["complaint"], bool(patient["injury"]))
    how = patient["department_how"] or "key-word rule"
    doctors = _on_duty(department)
    if doctors:
        return doctors[0], (f"Matched to {department.lower()} by the {how}. {doctors[0]['name']} is on duty there "
                            f"with the shortest list ({doctors[0]['waiting']} waiting).")
    cover = _on_duty(core.DEFAULT_DEPARTMENT)
    if cover:
        return cover[0], (f"Matched to {department.lower()} by the {how}, but nobody is on duty there right now, "
                          f"so general medicine covers. {cover[0]['name']} has the shortest list.")
    anyone = [dict(r) for r in _conn.execute(
        "SELECT * FROM doctors WHERE on_duty = 1 AND department != 'Resuscitation team' ORDER BY waiting, reg_no")]
    if anyone:
        return anyone[0], (f"Nobody is on duty in {department.lower()} or general medicine, so the patient goes to "
                           f"the least busy doctor on duty, {anyone[0]['name']}.")
    return _on_duty("Resuscitation team")[0], "No doctor is on duty, so the resuscitation team takes the patient."


def _measured(saved):
    """The saved measurements of one visit, with the pain score also given as the word the patient tapped.
    None for a visit that was never assessed (the sample patients)."""
    if not saved:
        return None
    vitals = json.loads(saved)
    return {**vitals, "pain_word": core.pain_word(vitals.get("pain"))}


def doctor_patients(doctor_reg):
    """The doctor's screen: who the doctor is, and the full card of every patient now with them.

    Unlike the patient's phone, the doctor sees everything: the nurse's level, the AI's level, the vital
    signs and the answers. Patients the nurse sent straight in come first, then the most urgent."""
    with _lock:
        doctor = _conn.execute("SELECT reg_no, name, department, room FROM doctors WHERE reg_no = ?",
                               (doctor_reg,)).fetchone()
        if doctor is None:
            return None
        clock = _meta("clock")
        rows = [dict(r) for r in _conn.execute(
            "SELECT * FROM visits WHERE status = 'with doctor' AND doctor_reg = ?", (doctor_reg,))]
    rows.sort(key=lambda p: (-p["referred"], p["level"], p["reg_no"]))
    return {"doctor": dict(doctor), "patients": [
        {"reg_no": p["reg_no"], "ticket": p["ticket"], "complaint": p["complaint"], "age": p["age"], "sex": p["sex"],
         "level": p["level"], "ai_level": p["ai_level"], "second_check": p["second_check"], "review": p["review"],
         "insurance": p["insurance"], "card_end": p["card_end"],
         "vitals": _measured(p["vitals"]),
         "answers": answers_for(p["reg_no"]), "referred": bool(p["referred"]),
         "referral_reason": p["referral_reason"], "waited": clock - p["arrived"]}
        for p in rows]}


def finish_visit(reg_no):
    """The doctor has seen the patient: the visit is closed and the doctor's list gets one shorter.
    Returns None when the visit is unknown or the patient is not with a doctor."""
    with _lock, _conn:
        visit = _conn.execute("SELECT status, doctor_reg FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
        if visit is None or visit["status"] != "with doctor":
            return None
        _conn.execute("UPDATE visits SET status = 'done' WHERE reg_no = ?", (reg_no,))
        _conn.execute("UPDATE doctors SET waiting = MAX(waiting - 1, 0) WHERE reg_no = ?", (visit["doctor_reg"],))
    return {"reg_no": reg_no, "doctor_reg": visit["doctor_reg"], "status": "done"}


def staff():
    with _lock:
        doctors = [dict(r) for r in _conn.execute("SELECT * FROM doctors ORDER BY reg_no")]
        counts = {r["status"]: r["n"] for r in _conn.execute("SELECT status, COUNT(*) AS n FROM visits GROUP BY status")}
        people = _conn.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"]
    regular = [d for d in doctors if d["department"] != "Resuscitation team"]
    return {"doctors": [{**d, "on_duty": bool(d["on_duty"])} for d in doctors],
            "on_duty": sum(d["on_duty"] for d in regular), "total": len(regular),
            "visits": {"registered": sum(counts.values()), "waiting": counts.get("waiting", 0),
                       "with doctor": counts.get("with doctor", 0), "done": counts.get("done", 0)},
            "people": people, "reviews": review_stats()}


def set_duty(reg_no, on_duty):
    """Returns False when there is no doctor with this registration number."""
    if reg_no == "DR-1000":
        return True     # the resuscitation team is always on duty
    with _lock, _conn:
        return _conn.execute("UPDATE doctors SET on_duty = ? WHERE reg_no = ?", (int(on_duty), reg_no)).rowcount == 1


# Create the tables on first use, and rebuild them if the file is from an older version of the code.
with _conn:
    _conn.executescript(SCHEMA)
_columns = {r["name"] for r in _conn.execute("PRAGMA table_info(visits)")} | {
    "answers." + r["name"] for r in _conn.execute("PRAGMA table_info(answers)")}
if not {"department", "referral_reason", "second_check", "person", "review", "answers.topic"} <= _columns or _conn.execute("SELECT COUNT(*) AS n FROM doctors").fetchone()["n"] != len(SEED_DOCTORS):
    reset()
