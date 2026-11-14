"""The hospital's internal database (SQLite, one file: hospital.db).

It holds three things:
- doctors : every doctor's registration number, department, room, whether they are on duty,
            and how many patients they already have
- visits  : every patient visit, under its own registration number, from arrival to the doctor
- answers : what each patient answered at check-in

No names are stored for patients. The doctors are made up for the demonstration.
"""

import json
import os
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
    status TEXT NOT NULL DEFAULT 'registered', doctor_reg TEXT REFERENCES doctors(reg_no));
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, visit_reg TEXT NOT NULL REFERENCES visits(reg_no),
    question TEXT NOT NULL, answer TEXT, kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""

# (registration number, name, department, room, patients already waiting)
SEED_DOCTORS = [
    ("DR-1000", "Resuscitation team", "Resuscitation team", "Resus bay", 0),
    ("DR-1001", "Dr. Marlow", "Cardiology", "Room 1", 2),
    ("DR-1002", "Dr. Okafor", "Cardiology", "Room 2", 0),
    ("DR-1003", "Dr. Lindqvist", "Respiratory medicine", "Room 3", 1),
    ("DR-1004", "Dr. Haddad", "Neurology", "Room 4", 1),
    ("DR-1005", "Dr. Petrov", "Neurology", "Room 5", 3),
    ("DR-1006", "Dr. Castell", "Trauma and orthopaedics", "Room 6", 2),
    ("DR-1007", "Dr. Nwosu", "Trauma and orthopaedics", "Room 7", 1),
    ("DR-1008", "Dr. Baptiste", "Gastroenterology and surgery", "Room 8", 0),
    ("DR-1009", "Dr. Sorensen", "Eye, ear, nose and throat", "Room 9", 1),
    ("DR-1010", "Dr. Achebe", "General medicine", "Room 10", 2),
    ("DR-1011", "Dr. Ferrand", "General medicine", "Room 11", 1),
    ("DR-1012", "Dr. Varga", "Urology and kidney", "Room 12", 1),
    ("DR-1013", "Dr. Imani", "Obstetrics and gynaecology", "Room 13", 0),
]
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
    """Start the day again: staff back on duty, and five sample patients in the waiting room."""
    with _lock, _conn:
        _conn.executescript("DROP TABLE IF EXISTS answers; DROP TABLE IF EXISTS visits; DROP TABLE IF EXISTS doctors;"
                            "DROP TABLE IF EXISTS meta;" + SCHEMA)
        _conn.executemany("INSERT INTO doctors (reg_no, name, department, room, waiting) VALUES (?, ?, ?, ?, ?)",
                          SEED_DOCTORS)
        _meta("clock", START_CLOCK)
        _meta("visit", 0)
        _meta("ticket", 0)
        for complaint, level, minutes_ago in SEED_WAITING:
            reg_no = f"P-2026-{_next_number('visit'):04d}"
            _conn.execute("INSERT INTO visits (reg_no, ticket, complaint, level, arrived, status) VALUES (?, ?, ?, ?, ?, 'waiting')",
                          (reg_no, f"A{_next_number('ticket'):03d}", complaint, level, START_CLOCK - minutes_ago))


# ---------------------------------------------------------------- patients

def register(complaint, age, sex):
    """A new visit gets its registration number the moment the patient arrives."""
    with _lock, _conn:
        reg_no = f"P-2026-{_next_number('visit'):04d}"
        _conn.execute("INSERT INTO visits (reg_no, complaint, age, sex, you) VALUES (?, ?, ?, ?, 1)",
                      (reg_no, complaint, age, sex))
    return reg_no


def save_answers(reg_no, answers, injury):
    with _lock, _conn:
        _conn.execute("DELETE FROM answers WHERE visit_reg = ?", (reg_no,))
        _conn.executemany("INSERT INTO answers (visit_reg, question, answer, kind) VALUES (?, ?, ?, ?)",
                          [(reg_no, a["question"], a.get("answer"), a["kind"]) for a in answers])
        _conn.execute("UPDATE visits SET injury = ? WHERE reg_no = ?", (int(injury), reg_no))


def answers_for(reg_no):
    with _lock:
        return [dict(r) for r in _conn.execute(
            "SELECT question, answer, kind FROM answers WHERE visit_reg = ? ORDER BY id", (reg_no,))]


def save_assessment(reg_no, ai_level, vitals, alert, pain):
    """Keep what the AI suggested and what was measured, so the doctor can see it later.

    ai_level is stored apart from level: level is the nurse's final decision and may be different.
    The six vital signs, the alertness and the pain score go into one JSON text column."""
    record = json.dumps({**vitals, "alert": alert, "pain": pain})
    with _lock, _conn:
        _conn.execute("UPDATE visits SET ai_level = ?, vitals = ? WHERE reg_no = ?", (ai_level, record, reg_no))


def visit_for(reg_no):
    """One visit as a plain dictionary, or None when the registration number is unknown."""
    with _lock:
        row = _conn.execute("SELECT * FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
    return dict(row) if row else None


def join_line(reg_no, level, danger, department=None, department_how=None):
    with _lock, _conn:
        ticket = f"A{_next_number('ticket'):03d}"
        _conn.execute("UPDATE visits SET ticket = ?, level = ?, danger = ?, arrived = ?, department = ?, "
                      "department_how = ?, status = 'waiting' WHERE reg_no = ?",
                      (ticket, level, int(danger), _meta("clock"), department, department_how, reg_no))
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
    return me


def wait(minutes=15):
    with _lock, _conn:
        _meta("clock", _meta("clock") + minutes)


def call_next():
    """Call the first patient in line, match them with a doctor, and record it all."""
    with _lock, _conn:
        clock, rows = _waiting()
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
        _conn.execute("UPDATE visits SET ticket = ?, level = ?, referred = 1, referral_reason = ?, "
                      "arrived = COALESCE(arrived, ?) WHERE reg_no = ?",
                      (ticket, level, reason, _meta("clock"), reg_no))
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


def staff():
    with _lock:
        doctors = [dict(r) for r in _conn.execute("SELECT * FROM doctors ORDER BY reg_no")]
        counts = {r["status"]: r["n"] for r in _conn.execute("SELECT status, COUNT(*) AS n FROM visits GROUP BY status")}
    regular = [d for d in doctors if d["department"] != "Resuscitation team"]
    return {"doctors": [{**d, "on_duty": bool(d["on_duty"])} for d in doctors],
            "on_duty": sum(d["on_duty"] for d in regular), "total": len(regular),
            "visits": {"registered": sum(counts.values()), "waiting": counts.get("waiting", 0),
                       "with doctor": counts.get("with doctor", 0)}}


def set_duty(reg_no, on_duty):
    if reg_no == "DR-1000":
        return          # the resuscitation team is always on duty
    with _lock, _conn:
        _conn.execute("UPDATE doctors SET on_duty = ? WHERE reg_no = ?", (int(on_duty), reg_no))


# Create the tables on first use, and rebuild them if the file is from an older version of the code.
with _conn:
    _conn.executescript(SCHEMA)
_columns = {r["name"] for r in _conn.execute("PRAGMA table_info(visits)")}
if not {"department", "referral_reason"} <= _columns or _conn.execute("SELECT COUNT(*) AS n FROM doctors").fetchone()["n"] != len(SEED_DOCTORS):
    reset()
