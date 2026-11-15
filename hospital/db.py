"""The hospital's internal database (SQLite, one file: hospital.db).

It holds:
- departments : the nine departments and the resuscitation team
- doctors     : every doctor's registration number, department, room and shift, whether they are on duty,
                and how many patients they have now (always equal to their open visits)
- visits      : every patient visit, under its own registration number and the patient's first and last name,
                from arrival to the doctor, with the doctor the nurse chose if she chose one.
                Visits are never deleted: a new day closes the open ones and keeps them
- answers     : what each patient answered at check-in
- events      : the log of every step of every visit (registered, checked in, assessed, in line, sent to a
                doctor, finished, closed), with the time and who did it
- people      : the registry that keeps one open visit per person. It holds a one-way fingerprint of each
                insurance card, never the card number itself
- meta        : counters (registration numbers, tickets) and the demo clock

Registration numbers are never given twice: the counter keeps going across days and restarts.

The rest of the hospital is simulated (simulate): while somebody has a screen open, made-up patients seen by
other nurses keep coming to the doctors and leaving again, so doctors are busy or free as in a real department.
The simulation never touches a real patient, the waiting line, or a doctor whose own screen is open.
Online, a copy of the file is kept on lasting storage (MEDDY_KEEP_DIR, the Space's bucket) and taken back at
start, so a restart or a new version of the code loses nothing.

Patients give a first and last name so that staff can call them. The name is shown to the nurse and the doctor
only: it is never sent to either model or the language model, and the patient's phone does not get it back from
the server. In the demonstration everybody uses a made-up name. The doctors are made up too.
"""

import atexit
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import core

# The tests point this at a throwaway file so they never touch the demonstration database.
DB_PATH = Path(os.environ.get("HOSPITAL_DB", Path(__file__).parent / "hospital.db"))
# Lasting storage. On the Hugging Face Space the container's own disk is wiped at every restart, so a copy of the
# database is kept in the Space's bucket (mounted at /data, see deploy/huggingface/start.sh) and taken back here.
KEEP_DIR = os.environ.get("MEDDY_KEEP_DIR")
KEPT = Path(KEEP_DIR) / "hospital.db" if KEEP_DIR else None
if KEPT and KEPT.exists() and not DB_PATH.exists():
    shutil.copyfile(KEPT, DB_PATH)
    print(f"Database taken back from {KEPT}.", flush=True)
_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS departments (
    name TEXT PRIMARY KEY, code TEXT NOT NULL, wing TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS doctors (
    reg_no TEXT PRIMARY KEY, name TEXT NOT NULL, department TEXT NOT NULL REFERENCES departments(name), room TEXT NOT NULL,
    shift TEXT NOT NULL DEFAULT 'day', on_duty INTEGER NOT NULL DEFAULT 1, waiting INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS visits (
    reg_no TEXT PRIMARY KEY, ticket TEXT, complaint TEXT NOT NULL, age REAL, sex TEXT, injury INTEGER DEFAULT 0,
    level INTEGER, danger INTEGER DEFAULT 0, you INTEGER DEFAULT 0, arrived INTEGER,
    department TEXT, department_how TEXT,
    ai_level INTEGER, vitals TEXT, referred INTEGER DEFAULT 0, referral_reason TEXT,
    ai_odds TEXT, second_check TEXT,
    ai_department TEXT, quick_advised INTEGER, review TEXT,
    summary TEXT, nurse_note TEXT,
    person TEXT REFERENCES people(person), insurance TEXT NOT NULL DEFAULT 'none', card_end TEXT,
    arrival TEXT, pain INTEGER, checked_in INTEGER, first_name TEXT, last_name TEXT,
    seen_at INTEGER, worse_at INTEGER, worse_said INTEGER DEFAULT 0, rechecks INTEGER DEFAULT 0,
    ai_level_then INTEGER, remeasured INTEGER DEFAULT 0, held_at INTEGER, quick_pending INTEGER,
    day INTEGER, registered_at TEXT, closed_at TEXT, closed_why TEXT,
    wanted_doctor TEXT REFERENCES doctors(reg_no), sim_until REAL, seen_from REAL, seen_to REAL,
    status TEXT NOT NULL DEFAULT 'registered', doctor_reg TEXT REFERENCES doctors(reg_no));
CREATE TABLE IF NOT EXISTS people (
    person TEXT PRIMARY KEY, insurance TEXT NOT NULL, card_end TEXT NOT NULL, visits INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, visit_reg TEXT NOT NULL REFERENCES visits(reg_no),
    question TEXT NOT NULL, answer TEXT, kind TEXT NOT NULL, topic TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, visit_reg TEXT REFERENCES visits(reg_no), day INTEGER, clock INTEGER,
    at TEXT NOT NULL, what TEXT NOT NULL, who TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS events_by_visit ON events(visit_reg);
CREATE INDEX IF NOT EXISTS visits_by_status ON visits(status);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""

# (name, short code, wing)
SEED_DEPARTMENTS = [
    ("Resuscitation team", "RES", "Ground floor, by the ambulance entrance"),
    ("Cardiology", "CAR", "Wing A"), ("Respiratory medicine", "RSP", "Wing A"), ("Neurology", "NEU", "Wing B"),
    ("Trauma and orthopaedics", "TRA", "Wing C"), ("Gastroenterology and surgery", "GAS", "Wing C"),
    ("Eye, ear, nose and throat", "ENT", "Wing B"), ("General medicine", "GEN", "Wing A"),
    ("Urology and kidney", "URO", "Wing D"), ("Obstetrics and gynaecology", "OBG", "Wing D"),
]

# (registration number, name, department, room, shift). Day-shift doctors are on duty when a day starts; the
# night shift is off duty (switch them on in the staff table). The resuscitation team is always there.
SEED_DOCTORS = [
    ("DR-1000", "Resuscitation team", "Resuscitation team", "Resus bay", "always"),
    ("DR-1001", "Dr. Marlow", "Cardiology", "Room 1", "day"),
    ("DR-1002", "Dr. Okafor", "Cardiology", "Room 2", "day"),
    ("DR-1003", "Dr. Lindqvist", "Respiratory medicine", "Room 3", "day"),
    ("DR-1004", "Dr. Haddad", "Neurology", "Room 4", "day"),
    ("DR-1005", "Dr. Petrov", "Neurology", "Room 5", "day"),
    ("DR-1006", "Dr. Castell", "Trauma and orthopaedics", "Room 6", "day"),
    ("DR-1007", "Dr. Nwosu", "Trauma and orthopaedics", "Room 7", "day"),
    ("DR-1008", "Dr. Baptiste", "Gastroenterology and surgery", "Room 8", "day"),
    ("DR-1009", "Dr. Sorensen", "Eye, ear, nose and throat", "Room 9", "day"),
    ("DR-1010", "Dr. Achebe", "General medicine", "Room 10", "day"),
    ("DR-1011", "Dr. Ferrand", "General medicine", "Room 11", "day"),
    ("DR-1012", "Dr. Varga", "Urology and kidney", "Room 12", "day"),
    ("DR-1013", "Dr. Imani", "Obstetrics and gynaecology", "Room 13", "day"),
    ("DR-1014", "Dr. Kowalski", "Cardiology", "Room 14", "day"),
    ("DR-1015", "Dr. Tanaka", "Cardiology", "Room 15", "night"),
    ("DR-1016", "Dr. Moreau", "Respiratory medicine", "Room 16", "day"),
    ("DR-1017", "Dr. Adeyemi", "Respiratory medicine", "Room 17", "night"),
    ("DR-1018", "Dr. Silva", "Neurology", "Room 18", "night"),
    ("DR-1019", "Dr. Novak", "Trauma and orthopaedics", "Room 19", "day"),
    ("DR-1020", "Dr. Hughes", "Trauma and orthopaedics", "Room 20", "night"),
    ("DR-1021", "Dr. Rossi", "Trauma and orthopaedics", "Room 21", "day"),
    ("DR-1022", "Dr. Mendes", "Gastroenterology and surgery", "Room 22", "day"),
    ("DR-1023", "Dr. Larsen", "Gastroenterology and surgery", "Room 23", "night"),
    ("DR-1024", "Dr. Kim", "Eye, ear, nose and throat", "Room 24", "day"),
    ("DR-1025", "Dr. Ahmed", "Eye, ear, nose and throat", "Room 25", "night"),
    ("DR-1026", "Dr. Fischer", "General medicine", "Room 26", "day"),
    ("DR-1027", "Dr. Owusu", "General medicine", "Room 27", "day"),
    ("DR-1028", "Dr. Dubois", "General medicine", "Room 28", "night"),
    ("DR-1029", "Dr. Ivanova", "General medicine", "Room 29", "night"),
    ("DR-1030", "Dr. Brennan", "Urology and kidney", "Room 30", "day"),
    ("DR-1031", "Dr. Yilmaz", "Urology and kidney", "Room 31", "night"),
    ("DR-1032", "Dr. Lopez", "Obstetrics and gynaecology", "Room 32", "day"),
    ("DR-1033", "Dr. Nakamura", "Obstetrics and gynaecology", "Room 33", "night"),
    ("DR-1034", "Dr. Hartmann", "Respiratory medicine", "Room 34", "day"),
    ("DR-1035", "Dr. Okonkwo", "Neurology", "Room 35", "day"),
    ("DR-1036", "Dr. Virtanen", "Eye, ear, nose and throat", "Room 36", "day"),
    ("DR-1037", "Dr. Costa", "Cardiology", "Room 37", "night"),
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
# Names of the made-up patients: made up, and given in turn.
MADE_UP_NAMES = [("Anna", "Keller"), ("Omar", "Haddad"), ("Lena", "Moretti"), ("Yusuf", "Demir"), ("Clara", "Nilsson"),
                 ("Mateo", "Ruiz"), ("Ines", "Barros"), ("Tomas", "Novak"), ("Mia", "Laurent"), ("Daniel", "Okoro"),
                 ("Sara", "Lindgren"), ("Ivan", "Petrenko"), ("Hana", "Sato"), ("Leo", "Fischer"), ("Eva", "Kowalska"),
                 ("Noah", "Brennan"), ("Amira", "Saleh"), ("Jonas", "Weber"), ("Rosa", "Pires"), ("Felix", "Dumont")]
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


def _clock():
    """Minutes on the hospital's clock: real minutes since the day started, plus any jumps from "15 minutes pass".
    Waiting times, moving up the line and re-check times all run on it, so a patient who has really waited half
    an hour has waited 30 minutes, and the doctors' busy times (real time) agree with the line."""
    started = _setting("day_started", None)
    real = int((time.time() - started) // 60) if started else 0
    return _meta("clock") + real


def _next_number(key):
    number = _meta(key) + 1
    _meta(key, number)
    return number


def _now():
    """The real time, for the log: the demo clock only says how many minutes have passed in the demonstration."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event(reg_no, what, who=None, detail=None):
    """Write one step of a visit into the log. The caller holds the lock and the transaction."""
    _conn.execute("INSERT INTO events (visit_reg, day, clock, at, what, who, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (reg_no, _meta("day"), _clock(), _now(), what, who, detail))


def _seed_staff():
    """Departments and doctors from the lists above. Doctors added to the list later are added; nobody is
    removed and nobody's patients are touched. The caller holds the lock and the transaction."""
    _conn.executemany("INSERT OR IGNORE INTO departments (name, code, wing) VALUES (?, ?, ?)", SEED_DEPARTMENTS)
    _conn.executemany("INSERT OR IGNORE INTO doctors (reg_no, name, department, room, shift, on_duty) VALUES (?, ?, ?, ?, ?, ?)",
                      [(*d, int(d[4] != "night")) for d in SEED_DOCTORS])


def _start_day():
    """The parts of a day that start again: the demo clock, the tickets, the doctors' shifts, and the made-up
    patients (already with doctors, and five in the waiting room). The caller holds the lock and the transaction."""
    _meta("clock", START_CLOCK)
    _meta("day_started", int(time.time()))      # the clock runs in real minutes from here
    _meta("day", secrets.randbelow(10**9))      # a phone keeps this next to its number, to tell days apart
    _meta("ticket", 0)                          # tickets start at A001 each day; registration numbers never do
    _meta("sim_ticket", 0)                      # and the simulated patients' at B001
    _meta("arrivals", 0)
    _conn.execute("UPDATE doctors SET waiting = 0, on_duty = CASE WHEN shift = 'night' THEN 0 ELSE 1 END")
    departments = {reg_no: department for reg_no, _, department, _, _ in SEED_DOCTORS}
    for doctor, complaint, age, sex, level, preset, pain, alert, minutes_ago in SEED_WITH_DOCTOR:
        _made_up_visit(complaint, age, sex, level, preset, pain, alert, START_CLOCK - minutes_ago,
                       departments[doctor], doctor)
        _conn.execute("UPDATE doctors SET waiting = waiting + 1 WHERE reg_no = ?", (doctor,))
    for complaint, level, minutes_ago in SEED_WAITING:
        _made_up_visit(complaint, 40, "Female", level, "normal", 3, "Alert", START_CLOCK - minutes_ago)


def reset():
    """Wipe everything and start from nothing: an empty database with the staff and a first day.
    Used for a brand-new database and by the tests. "Start a new day" uses new_day, which keeps the history."""
    with _lock, _conn:
        _conn.executescript("DROP TABLE IF EXISTS events; DROP TABLE IF EXISTS answers; DROP TABLE IF EXISTS visits;"
                            "DROP TABLE IF EXISTS doctors; DROP TABLE IF EXISTS departments; DROP TABLE IF EXISTS people;"
                            "DROP TABLE IF EXISTS meta;" + SCHEMA)
        _seed_staff()
        _meta("visit", 0)
        _start_day()


def new_day():
    """Start a new day in the department without losing anything.

    Every visit still open is closed and kept (closed_why = "new day"), so its card is free again; the doctors'
    lists are empty and the day shift is on duty; the demo clock and the tickets start again; the made-up patients
    are back. The visits, answers and log of earlier days stay, and the registration numbers go on counting, so a
    number is never given twice."""
    with _lock, _conn:
        now = _now()
        open_visits = [r["reg_no"] for r in _conn.execute("SELECT reg_no FROM visits WHERE status != 'done'")]
        for reg_no in open_visits:
            _event(reg_no, "closed", "system", "new day")
        _conn.execute("UPDATE visits SET status = 'done', closed_at = ?, closed_why = 'new day' WHERE status != 'done'",
                      (now,))
        _start_day()
    return {"closed": len(open_visits)}


def _made_up_visit(complaint, age, sex, level, preset, pain, alert, arrived, department=None, doctor=None, series="A"):
    """Write one made-up patient, already assessed: the nurse's level is invented, Meddy's level and its
    second check are really computed. With a doctor the visit is 'with doctor', otherwise it is waiting.
    The caller holds the lock and the transaction."""
    vitals = core.VITAL_PRESETS[preset]
    who = (complaint, age, sex, preset, pain, alert)
    if who not in _seed_levels:
        result = core.assess(complaint, age, sex, alert=alert, pain=pain, vitals=vitals)
        _seed_levels[who] = (result["level"], [result["probabilities"][str(l)] for l in core.LEVELS])
    ai_level, odds = _seed_levels[who]
    number = _next_number("visit")
    # The simulated patients of other nurses get tickets of their own (B001, B002 ...), so the tickets of the
    # patients in the waiting room stay in step with the line: A079 with only five people waiting was confusing.
    if series == "A":
        ticket = f"A{_next_number('ticket'):03d}"
    else:
        _meta("sim_ticket", _setting("sim_ticket", 0) + 1)
        ticket = f"{series}{_meta('sim_ticket'):03d}"
    reg_no = f"P-2026-{number:04d}"
    _conn.execute(
        "INSERT INTO visits (reg_no, ticket, complaint, age, sex, level, ai_level, ai_odds, second_check, vitals, arrived, "
        "department, department_how, insurance, card_end, status, doctor_reg, day, registered_at, first_name, last_name, "
        "seen_from) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (reg_no, ticket, complaint, age, sex, level, ai_level, json.dumps(odds), core.second_check(odds, level),
         json.dumps({**vitals, "alert": alert, "pain": pain}), arrived, department,
         "made-up patient" if department else None, *MADE_UP_INSURANCE[number % len(MADE_UP_INSURANCE)],
         "with doctor" if doctor else "waiting", doctor, _meta("day"), _now(), *MADE_UP_NAMES[number % len(MADE_UP_NAMES)],
         time.time() if doctor else None))
    _event(reg_no, "made up", "system", "with " + doctor if doctor else "in the waiting room")
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


def full_name(visit):
    """"Anna Keller", or None for a visit registered without a name (older visits)."""
    return " ".join(n for n in (visit.get("first_name"), visit.get("last_name")) if n) or None


def register(complaint, age, sex, insurance="none", card=None, first_name=None, last_name=None):
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
        _conn.execute("INSERT INTO visits (reg_no, complaint, age, sex, you, person, insurance, card_end, day, registered_at, "
                      "first_name, last_name) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
                      (reg_no, complaint, age, sex, person, insurance, card_end, _meta("day"), _now(), first_name, last_name))
        _event(reg_no, "registered", "patient's phone", complaint)
    return reg_no


def save_answers(reg_no, answers, injury):
    with _lock, _conn:
        _conn.execute("DELETE FROM answers WHERE visit_reg = ?", (reg_no,))
        _conn.executemany("INSERT INTO answers (visit_reg, question, answer, kind, topic) VALUES (?, ?, ?, ?, ?)",
                          [(reg_no, a["question"], a.get("answer"), a["kind"], a.get("topic")) for a in answers])
        _conn.execute("UPDATE visits SET injury = ? WHERE reg_no = ?", (int(injury), reg_no))


def code_for(reg_no):
    """The short code the patient's phone shows and the nurse looks for: the visit's number, "P-2026-0021" → "0021"."""
    return reg_no.rsplit("-", 1)[-1]


class AlreadyWithNurse(Exception):
    """The nurse has already measured this patient, so check-in cannot be done again."""


def check_in(reg_no, arrival, injury, pain, answers):
    """The patient's phone finishes check-in: the answers are kept and the patient waits for the nurse.

    Nothing about urgency is worked out here. The nurse measures the vital signs on her own screen, and only
    then does Meddy suggest a level. Doing check-in again before the nurse comes (a reload, a changed answer)
    replaces the answers and keeps the time the patient first reached the nurse's list.

    Once the nurse has measured the patient, check-in is closed: Meddy's report was worked out from the
    answers as they were, so new answers would leave the nurse reading a report that no longer fits them.
    Returns the code, None for an unknown visit, or raises AlreadyWithNurse / AlreadySent."""
    with _lock, _conn:
        visit = _still_at_the_desk(reg_no)
        if visit is None:
            return None
        if visit["status"] == "waiting" or visit["ai_level"] is not None:
            raise AlreadyWithNurse(reg_no)
        _conn.execute("DELETE FROM answers WHERE visit_reg = ?", (reg_no,))
        _conn.executemany("INSERT INTO answers (visit_reg, question, answer, kind, topic) VALUES (?, ?, ?, ?, ?)",
                          [(reg_no, a["question"], a.get("answer"), a["kind"], a.get("topic")) for a in answers])
        _conn.execute("UPDATE visits SET arrival = ?, injury = ?, pain = ?, status = 'nurse', "
                      "checked_in = COALESCE(checked_in, ?) WHERE reg_no = ?",
                      (arrival, int(injury), pain, _clock(), reg_no))
        _event(reg_no, "checked in", "patient's phone", f"{len(answers)} answers")
    return code_for(reg_no)


def _danger_yes(reg_no):
    return _conn.execute("SELECT COUNT(*) AS n FROM answers WHERE visit_reg = ? AND kind = 'danger' AND answer = 'Yes'",
                         (reg_no,)).fetchone()["n"]


def nurse_patients():
    """The nurse's list: everyone who finished check-in and waits for her. A patient who answered yes to a
    danger sign comes first, then whoever has waited longest. No level yet: nothing has been measured."""
    with _lock:
        clock = _clock()
        rows = [dict(r) for r in _conn.execute("SELECT * FROM visits WHERE status = 'nurse'")]
        for row in rows:
            row["danger"] = _danger_yes(row["reg_no"])
        _, line_now = _waiting()
    # A patient who says they feel worse comes before everyone, then danger signs, then who has waited longest.
    rows.sort(key=lambda p: (p["worse_at"] is None, p["worse_at"] or 0, -p["danger"], p["checked_in"], p["reg_no"]))
    # Patients already in the waiting room who need the nurse again: first those who pressed "I feel worse"
    # (who asked first comes first), then those who have waited longer than their level allows (the more
    # urgent level first, then who has gone longest without a look).
    again = [p for p in line_now if p["worse_at"] is not None or core.recheck_due(p, clock)]
    again.sort(key=lambda p: (p["worse_at"] is None, p["worse_at"] if p["worse_at"] is not None else 0, p["level"],
                              p["seen_at"] if p["seen_at"] is not None else p["arrived"]))
    return {"clock": clock, "patients": [
        {"reg_no": p["reg_no"], "code": code_for(p["reg_no"]), "patient": full_name(p), "complaint": p["complaint"],
         "age": p["age"], "sex": p["sex"], "injury": bool(p["injury"]), "danger": p["danger"], "waited": clock - p["checked_in"],
         "worse": p["worse_at"] is not None}
        for p in rows],
        "recheck": [
        {"reg_no": p["reg_no"], "code": code_for(p["reg_no"]), "ticket": p["ticket"], "patient": full_name(p),
         "complaint": p["complaint"], "age": p["age"], "sex": p["sex"], "level": p["level"],
         "worse": p["worse_at"] is not None, "recheck_due": core.recheck_due(p, clock),
         "since_seen": clock - (p["seen_at"] if p["seen_at"] is not None else p["arrived"])}
        for p in again]}


def nurse_card(reg_no):
    """Everything the nurse needs to see one patient before measuring: who, what they reported and how they came.
    None for an unknown visit."""
    visit = visit_for(reg_no)
    if visit is None:
        return None
    with _lock:
        clock = _clock()
    return {"reg_no": reg_no, "code": code_for(reg_no), "status": visit["status"], "complaint": visit["complaint"],
            "complaint_read": core.spelling(visit["complaint"]),     # with typing slips put right, as the models read it
            "first_name": visit["first_name"], "last_name": visit["last_name"], "patient": full_name(visit),
            "age": visit["age"], "sex": visit["sex"], "arrival": visit["arrival"] or "Walked in",
            "injury": bool(visit["injury"]), "pain": visit["pain"] or 0, "pain_word": core.pain_word(visit["pain"]),
            "insurance": visit["insurance"], "card_end": visit["card_end"], "ticket": visit["ticket"],
            "waited": None if visit["checked_in"] is None else clock - visit["checked_in"],
            # For a re-check of a patient who is already in the waiting room (staff only, like the rest of the card).
            "level": visit["level"], "ai_level": visit["ai_level"],
            "worse": visit["worse_at"] is not None and visit["status"] in ("nurse", "waiting"),
            "recheck_due": visit["status"] == "waiting" and core.recheck_due(visit, clock),
            "since_seen": (clock - (visit["seen_at"] if visit["seen_at"] is not None else visit["arrived"])
                           if visit["status"] == "waiting" else None),
            "rechecks": visit["rechecks"] or 0,
            # What the nurse wrote and decided before, so that a re-check starts from it and does not wipe it.
            "note": visit["nurse_note"], "summary_held_back": _held_back(visit["summary"]),
            "answers": answers_for(reg_no)}


def _held_back(summary):
    """Did the nurse say "this summary is not right" the last time she decided? Then the stored summary was
    written by the template although the language model had written one."""
    if not summary:
        return False
    return json.loads(summary)["patient_said"].get("held_back", False)


def _answers(reg_no):
    """The answers of one visit. The caller holds the lock."""
    rows = [dict(r) for r in _conn.execute(
        "SELECT question, answer, kind, topic FROM answers WHERE visit_reg = ? ORDER BY id", (reg_no,))]
    return [{**r, "label": core.topic_label(r["topic"])} for r in rows]


def answers_for(reg_no):
    """The answers of one visit. AI-written questions carry a history topic ("onset") and its label ("Started"),
    so the doctor's screen can show them as a short overview."""
    with _lock:
        return _answers(reg_no)


def _summary_of(visit, note=None, summary_ok=True, with_ai=True):
    """The patient summary of one visit as it stands now (see core.patient_summary). The caller holds the lock.

    The AI sentences are the ones the nurse already saw on her screen: they are looked up, never asked for
    again here, so nobody waits for the language model and the doctor reads what the nurse read."""
    visit = dict(visit)
    answers = _answers(visit["reg_no"])
    ai = core.nurse_summary(visit["reg_no"], visit["complaint"], visit["age"], visit["sex"], answers,
                            bool(visit["injury"]), use_model=False) if with_ai else None
    return core.patient_summary(visit, answers, ai, summary_ok, note)


def _write_summary(reg_no, note, summary_ok, keep_note=False):
    """Write the summary down at the moment the nurse decides, in the same transaction as her decision. From
    then on it is read, never rebuilt: a model that answers differently later cannot change it. It is written
    again only when she decides again (a re-check). keep_note: the request carried no note at all, so the note
    she wrote before stays (a re-check must not wipe it by accident)."""
    visit = _conn.execute("SELECT * FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
    if keep_note:
        note = visit["nurse_note"]
    summary = _summary_of(visit, note, summary_ok)
    _conn.execute("UPDATE visits SET summary = ?, nurse_note = ? WHERE reg_no = ?", (json.dumps(summary), note, reg_no))


def save_assessment(reg_no, ai_level, vitals, alert, pain, odds=None, department=None, quick=None):
    """Keep what the AI suggested and what was measured, so the doctor can see it later.

    ai_level is stored apart from level: level is the nurse's final decision and may be different.
    The six vital signs, the alertness and the pain score go into one JSON text column.

    A patient the nurse measures before they finished check-in on the phone (too unwell to tap, no phone) is
    now with the nurse: the visit moves on, so it shows on her list and the phone stops asking questions."""
    record = json.dumps({**vitals, "alert": alert, "pain": pain})
    with _lock, _conn:
        before = _still_at_the_desk(reg_no)     # raises AlreadySent: a doctor called the patient while Meddy was working
        if before is None:
            return
        if before["status"] == "registered":
            _event(reg_no, "taken by the nurse", "nurse", "check-in on the phone was not finished")
        clock = _clock()
        # A patient who is already in the line is being measured again (a re-check) and the nurse has not decided yet:
        #   remeasured     says so, for the doctor, should the patient reach one before she decides
        #   held_at        keeps the patient with the nurse: no doctor calls them for HOLD_FOR minutes
        #   ai_level_then  keeps what Meddy suggested when she last decided (the right-hand sides see the old row)
        # Meddy's advice goes to quick_pending and becomes quick_advised when the nurse decides: the test regime
        # counts what she did against the advice she had at that moment.
        _conn.execute("UPDATE visits SET ai_level_then = CASE WHEN status = 'waiting' THEN COALESCE(ai_level_then, ai_level) "
                      "ELSE ai_level_then END, ai_level = ?, vitals = ?, ai_odds = ?, ai_department = ?, quick_pending = ?, "
                      "remeasured = CASE WHEN status = 'waiting' THEN 1 ELSE 0 END, "
                      "held_at = CASE WHEN status = 'waiting' THEN ? ELSE NULL END, "
                      "status = CASE WHEN status = 'registered' THEN 'nurse' ELSE status END, "
                      "checked_in = COALESCE(checked_in, ?) WHERE reg_no = ?",
                      (ai_level, record, json.dumps(odds) if odds else None, department,
                       None if quick is None else int(quick), clock, clock, reg_no))
        _event(reg_no, "assessed", "nurse", f"Meddy suggested level {ai_level}" + (f", {department}" if department else ""))


def _flag(reg_no, level):
    """Meddy's second check on the nurse's level ("strong", "mild" or None), from the odds saved at triage."""
    saved = _conn.execute("SELECT ai_odds FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
    return core.second_check(json.loads(saved["ai_odds"]), level) if saved and saved["ai_odds"] else None


def day():
    """A number that changes at every new day. Registration numbers start again each day, so a phone keeps
    this next to its number to know that the visit it remembers is today's."""
    with _lock:
        return _meta("day")


def visit_for(reg_no):
    """One visit as a plain dictionary, or None when the registration number is unknown."""
    with _lock:
        row = _conn.execute("SELECT * FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
    return dict(row) if row else None


class AlreadySent(Exception):
    """The patient has already gone to a doctor, so the step at the nurse station cannot be done (again)."""


def _still_at_the_desk(reg_no):
    """The visit, if it is still before the doctor. Called with the lock held, in the same transaction as the
    change it protects: checking in one step and writing in another let two quick clicks send one patient
    to two doctors."""
    visit = _conn.execute("SELECT * FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
    if visit is None:
        return None
    if visit["status"] in ("with doctor", "done"):
        raise AlreadySent(reg_no)
    return visit


class UnknownDoctor(Exception):
    """The nurse chose a doctor who is not in the staff list, or the resuscitation team (it takes levels 1-2 by itself)."""


def _choose_doctor(reg_no, doctor_reg):
    """The nurse chose this doctor for the patient: the patient waits in line for them, and the visit takes the
    doctor's department. A busy doctor can be chosen; the patient then simply waits longer. The caller holds the
    lock and the transaction."""
    doctor = _conn.execute("SELECT * FROM doctors WHERE reg_no = ?", (doctor_reg,)).fetchone()
    if doctor is None or doctor["department"] == "Resuscitation team":
        raise UnknownDoctor(doctor_reg)
    _conn.execute("UPDATE visits SET wanted_doctor = ?, department = ? WHERE reg_no = ?",
                  (doctor_reg, doctor["department"], reg_no))
    _event(reg_no, "doctor chosen", "nurse", f"{doctor['name']}, {doctor['department']}")


def _join(reg_no, level, danger, department, department_how, review, doctor_reg=None):
    visit = _still_at_the_desk(reg_no)
    ticket = (visit and visit["ticket"]) or f"A{_next_number('ticket'):03d}"
    clock = _clock()
    arrived = visit["arrived"] if visit and visit["arrived"] is not None else clock
    waiting = bool(visit and visit["status"] == "waiting")
    # A re-check: the patient is already in the line AND the nurse measured them again. Joining again without
    # measuring (a second click, a retry, a changed level) is not one: it answers no "I feel worse" and restarts
    # no re-check time.
    again = bool(waiting and visit["remeasured"])
    seen = (visit["seen_at"] if visit else None) if waiting and not again else clock
    worse = (visit["worse_at"] if visit else None) if waiting and not again else None
    if again:
        review = review or "checked"            # a re-check is the nurse looking again, whatever the request says
    # A department that is not given is left as it was: joining again must not wipe the nurse's choice.
    _conn.execute("UPDATE visits SET ticket = ?, level = ?, danger = ?, arrived = ?, department = COALESCE(?, department), "
                  "department_how = COALESCE(?, department_how), second_check = ?, review = COALESCE(?, review), "
                  "status = 'waiting', seen_at = ?, worse_at = ?, rechecks = COALESCE(rechecks, 0) + ?, "
                  "ai_level_then = ai_level, remeasured = 0, held_at = NULL, "
                  "quick_advised = COALESCE(quick_pending, quick_advised) WHERE reg_no = ?",
                  (ticket, level, int(danger), arrived, department, department_how, _flag(reg_no, level), review,
                   seen, worse, int(again), reg_no))
    if doctor_reg:
        _choose_doctor(reg_no, doctor_reg)
    if visit and again:
        _event(reg_no, "re-checked", "nurse", f"level {visit['level']} to level {level}"
               + (", after the patient said they felt worse" if visit["worse_at"] is not None else ""))
    elif visit:
        _event(reg_no, "in line", "nurse", f"level {level}, ticket {ticket}" + (f", {review}" if review else ""))
    return ticket


class NotWaiting(Exception):
    """The patient is not waiting (for the nurse or in the waiting room), so there is nobody to call back to them."""


def feel_worse(reg_no):
    """The patient pressed "I feel worse" on their phone while waiting.

    It does not change the level or the place in line: only a nurse can do that. It puts the patient on top of
    the nurse's list so that she looks at them again. Pressing twice asks once. Returns the visit's status,
    None for an unknown visit, or raises NotWaiting."""
    with _lock, _conn:
        visit = _conn.execute("SELECT * FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
        if visit is None:
            return None
        if visit["status"] not in ("nurse", "waiting"):
            raise NotWaiting(reg_no)
        if visit["worse_at"] is None:
            _conn.execute("UPDATE visits SET worse_at = ?, worse_said = COALESCE(worse_said, 0) + 1 WHERE reg_no = ?",
                          (_clock(), reg_no))
            _event(reg_no, "said they feel worse", "patient's phone",
                   "waiting for the nurse" if visit["status"] == "nurse" else "in the waiting room")
        return visit["status"]


def join_line(reg_no, level, danger, department=None, department_how=None, review=None, doctor_reg=None):
    """The nurse's decision puts the patient in the line, for the doctor she chose if she chose one.

    Joining a second time (the nurse changed the level) keeps the same ticket and the same arrival time,
    so nobody gets two tickets or loses the minutes they have already waited."""
    with _lock, _conn:
        return _join(reg_no, level, danger, department, department_how, review, doctor_reg)


def admit(reg_no, level, danger, department=None, department_how=None, review=None, note=None, summary_ok=True,
          keep_note=False, doctor_reg=None):
    """What the nurse station does through the web: join the line, write the patient summary for the doctor,
    and for level 1 or 2 go to the resuscitation team in the same breath. One lock for all of it, so nobody
    else can call the patient in between. Returns (ticket, where the patient was sent, or None when they wait)."""
    with _lock, _conn:
        ticket = _join(reg_no, level, danger, department, department_how, review, doctor_reg)
        _write_summary(reg_no, note, summary_ok, keep_note)
        if level > 2:
            return ticket, None
        patient = dict(_conn.execute("SELECT * FROM visits WHERE reg_no = ?", (reg_no,)).fetchone())
        return ticket, _called(patient, _clock())


def _called(patient, clock):
    """Send one patient to a doctor and describe where they went. The caller holds the lock."""
    doctor, reason = _send_to_doctor(patient)
    return {"reg_no": patient["reg_no"], "ticket": patient["ticket"], "who": patient["complaint"],
            "patient": full_name(patient),
            "level": patient["level"], "you": bool(patient["you"]), "waited": clock - patient["arrived"],
            "department": doctor["department"], "doctor": doctor["name"], "doctor_reg": doctor["reg_no"],
            "room": doctor["room"], "reason": reason}


# ---------------------------------------------------------------- the line

HOLD_FOR = 15       # minutes a patient the nurse is re-checking stays with her, out of every doctor's call


def _with_nurse(patient, clock):
    """Is this waiting patient with the nurse for a re-check right now? She measured them again and has not
    decided yet. Calling them away would lose her decision, so no doctor's call takes them, for HOLD_FOR minutes
    at most: a re-check nobody finishes must not keep a patient out of the line for ever."""
    return patient["held_at"] is not None and clock - patient["held_at"] < HOLD_FOR


def _waiting():
    clock = _clock()
    rows = [dict(r) for r in _conn.execute("SELECT * FROM visits WHERE status = 'waiting'")]
    rows.sort(key=lambda p: core.place(p, clock))
    return clock, rows


def line():
    with _lock:
        clock, rows = _waiting()
        names = {r["reg_no"]: r["name"] for r in _conn.execute("SELECT reg_no, name FROM doctors")}
    return {"clock": clock, "patients": [
        {"reg_no": p["reg_no"], "ticket": p["ticket"], "who": p["complaint"], "patient": full_name(p), "level": p["level"],
         "name": core.LEVEL_NAMES[p["level"]], "danger": bool(p["danger"]), "you": bool(p["you"]),
         "waited": clock - p["arrived"], "position": i,
         # For the nurse: the patient pressed "I feel worse", or has waited longer than their level allows
         # without a nurse looking at them again (core.RECHECK_AFTER).
         "worse": p["worse_at"] is not None, "recheck_due": core.recheck_due(p, clock),
         "with_nurse": _with_nurse(p, clock), "doctor": names.get(p["wanted_doctor"])}
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
        wanted = _conn.execute("SELECT name, room FROM doctors WHERE reg_no = ?", (visit["wanted_doctor"],)).fetchone()
        today = _meta("day")
    me = {"ticket": visit["ticket"], "status": "registered", "position": None, "ahead": None, "doctor": None, "room": None,
          "day": today, "code": code_for(reg_no), "worse": False, "closed": None}
    if visit["status"] == "nurse":
        # check-in done: the phone shows the code for the nurse. "worse": the nurse has been told they feel worse.
        me.update(status="nurse", worse=visit["worse_at"] is not None)
    elif visit["status"] == "waiting":
        position = [p["reg_no"] for p in rows].index(reg_no) + 1
        me.update(status="waiting", position=position, ahead=position - 1, worse=visit["worse_at"] is not None)
        if wanted:          # the nurse chose the doctor: the patient knows whom they wait for
            me.update(doctor=wanted["name"], room=wanted["room"])
    elif visit["status"] == "with doctor":
        me.update(status="called", doctor=doctor["name"], room=doctor["room"])
    elif visit["status"] == "done":
        why = visit["closed_why"] or ""
        me.update(status="finished", closed="seen" if why in ("finished by the doctor", "seen (simulated)")
                  else "removed" if why in REMOVED_BECAUSE else "closed")
    return me


# ---------------------------------------------------------------- the nurse manages her patients

REMOVED_BECAUSE = ("left without being seen", "sent home by the nurse", "registered by mistake")


class NotHere(Exception):
    """The patient is not waiting for the nurse or in the waiting room: there is nothing to manage."""


def still_fine(reg_no):
    """The nurse looked at a waiting patient again and nothing changed: the re-check time starts again and an
    "I feel worse" is answered. Level, ticket and place in line stay as they are. None for an unknown visit."""
    with _lock, _conn:
        visit = _conn.execute("SELECT status FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
        if visit is None:
            return None
        if visit["status"] != "waiting":
            raise NotHere(reg_no)
        _conn.execute("UPDATE visits SET seen_at = ?, worse_at = NULL WHERE reg_no = ?", (_clock(), reg_no))
        _event(reg_no, "looked at again", "nurse", "still fine, nothing changed")
    return {"reg_no": reg_no, "status": "waiting"}


def remove_visit(reg_no, why):
    """The nurse takes a patient off her list or out of the line: they left, she sent them home, or the visit was
    registered by mistake. The visit is closed and kept, like every visit. None for an unknown visit."""
    if why not in REMOVED_BECAUSE:
        raise ValueError(why)
    with _lock, _conn:
        visit = _conn.execute("SELECT status FROM visits WHERE reg_no = ?", (reg_no,)).fetchone()
        if visit is None:
            return None
        if visit["status"] not in ("registered", "nurse", "waiting"):
            raise NotHere(reg_no)
        _conn.execute("UPDATE visits SET status = 'done', closed_at = ?, closed_why = ? WHERE reg_no = ?",
                      (_now(), why, reg_no))
        _event(reg_no, "removed", "nurse", why)
    return {"reg_no": reg_no, "status": "done", "why": why}


def todays_patients(limit=60):
    """Today's real patients, newest first, and where each one is or went: for the nurse's "Today's patients".
    The simulated patients of other nurses are left out."""
    with _lock:
        rows = [dict(r) for r in _conn.execute(
            "SELECT v.*, d.name AS doctor_name, d.room AS doctor_room, w.name AS wanted_name FROM visits v "
            "LEFT JOIN doctors d ON d.reg_no = v.doctor_reg LEFT JOIN doctors w ON w.reg_no = v.wanted_doctor "
            "WHERE v.day = ? AND v.you = 1 ORDER BY v.reg_no DESC LIMIT ?", (_meta("day"), limit))]

    def where(v):
        if v["status"] == "registered":
            return "Checking in on the phone"
        if v["status"] == "nurse":
            return "Waiting for the nurse"
        if v["status"] == "waiting":
            return f"In the waiting room{' for ' + v['wanted_name'] if v['wanted_name'] else ''}"
        if v["status"] == "with doctor":
            return f"With {v['doctor_name']}, {v['doctor_room']}"
        if v["closed_why"] == "finished by the doctor":
            return f"Seen by {v['doctor_name']}: finished"
        return f"Closed: {v['closed_why'] or 'finished'}"
    return [{"reg_no": v["reg_no"], "code": code_for(v["reg_no"]), "ticket": v["ticket"], "patient": full_name(v),
             "complaint": v["complaint"], "level": v["level"], "status": v["status"], "where": where(v),
             "department": v["department"]} for v in rows]


def wait(minutes=15):
    with _lock, _conn:
        _meta("clock", _meta("clock") + minutes)      # "15 minutes pass": the clock jumps ahead


def pass_time(minutes=15):
    """Fifteen minutes in a working department: the clock moves, the first patient in line is called,
    and one made-up patient walks in. This is what makes waiting visible in the demonstration: a patient
    who has waited long is overtaken by a more urgent newcomer but stays ahead of a less urgent one."""
    wait(minutes)
    called = call_next()
    with _lock, _conn:
        turn = _next_number("arrivals")
        complaint, age, sex, level, preset, pain = SEED_ARRIVALS[(turn - 1) % len(SEED_ARRIVALS)]
        reg_no, ticket = _made_up_visit(complaint, age, sex, level, preset, pain, "Alert", _clock())
    return {"called": called, "arrived": {"reg_no": reg_no, "ticket": ticket, "who": complaint, "level": level}}


def call_next(reg_no=None):
    """Call the first patient in line, match them with a doctor, and record it all.

    With a registration number, that waiting patient is called instead, whoever is first. The server uses
    this for levels 1 and 2, so an emergency never sits in the line waiting for somebody to press a button."""
    with _lock, _conn:
        clock, rows = _waiting()
        if reg_no is not None:
            rows = [p for p in rows if p["reg_no"] == reg_no]
        else:
            rows = [p for p in rows if not _with_nurse(p, clock)]      # the nurse is re-checking them: not now
        if not rows:
            return None
        return _called(rows[0], clock)


class OffDuty(Exception):
    """A doctor who is off duty cannot call a patient: nobody would be in the room."""


def _waiting_for(doctor):
    """The patients in line this doctor may call, in the line's order: those the nurse chose this doctor for, and
    those of the doctor's department whom she gave to nobody (or to a doctor now off duty). General medicine covers
    everyone when nobody of its own waits. Patients the nurse is re-checking right now are left out. The caller
    holds the lock."""
    clock, rows = _waiting()
    rows = [p for p in rows if not _with_nurse(p, clock)]          # the nurse is re-checking them: not now
    department = doctor["department"]
    on_duty = {r["reg_no"] for r in _conn.execute("SELECT reg_no FROM doctors WHERE on_duty = 1")}
    free = [p for p in rows if p["wanted_doctor"] not in on_duty or p["wanted_doctor"] == doctor["reg_no"]]
    mine = [p for p in free if p["wanted_doctor"] == doctor["reg_no"]
            or (p["department"] or core.department_for(p["complaint"], bool(p["injury"]))) == department]
    if not mine and department == core.DEFAULT_DEPARTMENT:
        mine = free
    return clock, mine


class NotForYou(Exception):
    """The doctor asked for a patient who is not (or no longer) waiting for them."""


def call_for_doctor(doctor_reg, reg_no=None):
    """The doctor presses "Call my next patient": the first patient in line for this doctor's department comes
    to this doctor. Their phone then says which room to go to.

    The line's order is kept: the patient called is the first of this department, so a more urgent patient of
    the department is never overtaken. General medicine covers everyone, so it takes the first in line when
    nobody of its own is waiting. Returns the call, None when nobody is waiting for this doctor, or raises
    LookupError for an unknown doctor and OffDuty for one who is not on duty.

    With a registration number the doctor accepts that patient ("Accept" on their screen), who must be one of the
    patients waiting for them; NotForYou otherwise."""
    with _lock, _conn:
        doctor = _conn.execute("SELECT * FROM doctors WHERE reg_no = ?", (doctor_reg,)).fetchone()
        if doctor is None:
            raise LookupError(doctor_reg)
        if not doctor["on_duty"]:
            raise OffDuty(doctor_reg)
        clock, mine = _waiting_for(doctor)
        department = doctor["department"]
        if reg_no is not None:
            mine = [p for p in mine if p["reg_no"] == reg_no]
            if not mine:
                raise NotForYou(reg_no)
        if not mine:
            return None
        patient = mine[0]
        _conn.execute("UPDATE doctors SET waiting = waiting + 1 WHERE reg_no = ?", (doctor_reg,))
        _conn.execute("UPDATE visits SET status = 'with doctor', doctor_reg = ?, seen_from = ? WHERE reg_no = ?",
                      (doctor_reg, time.time(), patient["reg_no"]))
        _event(patient["reg_no"], "called by the doctor", doctor_reg, f"{doctor['name']}, {doctor['room']}")
    return {"reg_no": patient["reg_no"], "ticket": patient["ticket"], "who": patient["complaint"],
            "patient": full_name(patient),
            "level": patient["level"], "you": bool(patient["you"]), "waited": clock - patient["arrived"],
            "department": department, "doctor": doctor["name"], "doctor_reg": doctor_reg, "room": doctor["room"],
            "reason": f"Accepted by {doctor['name']}." if reg_no else f"Called by {doctor['name']}: first in line for {department.lower()}."}


def refer(reg_no, level, reason, department=None, note=None, summary_ok=True, keep_note=False, doctor_reg=None):
    """The nurse sends a critical patient straight to a doctor, past the line.

    Her reason is stored so the doctor can read it. The doctor is the one she chose, or one chosen by the same
    rules as call_next (levels 1-2 always go to the resuscitation team), inside one locked transaction."""
    with _lock, _conn:
        visit = _still_at_the_desk(reg_no)             # raises AlreadySent for a second click on "send"
        if visit is None:
            return None
        ticket = visit["ticket"] or f"A{_next_number('ticket'):03d}"      # keep the ticket if they already have one
        clock = _clock()
        again = visit["status"] == "waiting"           # sent in from the waiting room: that is a re-check too
        # Sending a patient straight in is always the nurse's own check, never a quick acceptance. She has seen
        # the patient now, so an "I feel worse" is answered.
        _conn.execute("UPDATE visits SET ticket = ?, level = ?, referred = 1, referral_reason = ?, second_check = ?, "
                      "review = 'checked', arrived = COALESCE(arrived, ?), seen_at = ?, worse_at = NULL, "
                      "rechecks = COALESCE(rechecks, 0) + ?, ai_level_then = ai_level, remeasured = 0, held_at = NULL, "
                      "quick_advised = COALESCE(quick_pending, quick_advised) WHERE reg_no = ?",
                      (ticket, level, reason, _flag(reg_no, level), clock, clock, int(again), reg_no))
        if department:
            _conn.execute("UPDATE visits SET department = ?, department_how = 'nurse' WHERE reg_no = ?",
                          (department, reg_no))
        if doctor_reg:
            _choose_doctor(reg_no, doctor_reg)
        _event(reg_no, "sent straight in", "nurse", f"level {level}: {reason}"
               + (" (after the patient said they felt worse)" if visit["worse_at"] is not None else ""))
        _write_summary(reg_no, note, summary_ok, keep_note)
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
    _conn.execute("UPDATE visits SET status = 'with doctor', doctor_reg = ?, seen_from = ? WHERE reg_no = ?",
                  (doctor["reg_no"], time.time(), patient["reg_no"]))
    _event(patient["reg_no"], "sent to a doctor", "system", f"{doctor['name']}, {doctor['room']}: {reason}")
    return doctor, reason


def likely_doctor(level, department, complaint="", injury=False):
    """Who this patient would be sent to right now, without sending them: Meddy's report shows it to the nurse.
    It is an approximation, because the lists can change before the patient is called."""
    with _lock:
        doctor, reason = _pick_doctor({"level": level, "department": department, "department_how": None,
                                       "complaint": complaint, "injury": injury})
    return {"doctor": doctor["name"], "doctor_reg": doctor["reg_no"], "room": doctor["room"],
            "department": doctor["department"], "waiting": doctor["waiting"]}


VISIT_MINUTES = 10      # how long a visit is taken to last until a doctor has finished a few


def _doctor_times(now=None):
    """For each doctor with patients: since when the patient they see now is with them, and how long a visit
    with this doctor usually takes (the average of their finished visits, or of all doctors while they have
    fewer than three). From these, about when they will be free. The caller holds the lock."""
    now = time.time() if now is None else now
    done = [dict(r) for r in _conn.execute(
        "SELECT doctor_reg, AVG(seen_to - seen_from) AS took, COUNT(*) AS n FROM visits "
        "WHERE seen_to > seen_from AND seen_from IS NOT NULL GROUP BY doctor_reg")]
    everyone = sum(d["took"] * d["n"] for d in done) / max(1, sum(d["n"] for d in done)) if done else VISIT_MINUTES * 60
    usual = {d["doctor_reg"]: d["took"] if d["n"] >= 3 else everyone for d in done}
    times = {}
    for r in _conn.execute("SELECT doctor_reg, MIN(seen_from) AS since, COUNT(*) AS n FROM visits "
                           "WHERE status = 'with doctor' GROUP BY doctor_reg"):
        took = usual.get(r["doctor_reg"], everyone)
        left = None if r["since"] is None else max(1, round((took * r["n"] - (now - r["since"])) / 60))
        times[r["doctor_reg"]] = {"since": r["since"], "usual_minutes": max(1, round(took / 60)), "free_in_minutes": left}
    return times


def _queues():
    """How many patients in the line wait for each doctor the nurse chose for them."""
    return {r["wanted_doctor"]: r["n"] for r in _conn.execute(
        "SELECT wanted_doctor, COUNT(*) AS n FROM visits WHERE status = 'waiting' AND wanted_doctor IS NOT NULL "
        "GROUP BY wanted_doctor")}


def _times_of(times, doctor):
    if doctor["waiting"] == 0:
        return {"since": None, "usual_minutes": None, "free_in_minutes": None}
    return times.get(doctor["reg_no"], {"since": None, "usual_minutes": None, "free_in_minutes": None})


def doctor_choices(match, others=3):
    """The doctors the nurse can choose from for one patient: the best one, and a short list of three more.

    match says how well each department fits the complaint (core.department_match). The best one is the least
    busy doctor on duty in the best-matching department, even when that doctor is busy: the nurse sees it and can
    still choose them, and the patient then waits in line for them. The short list holds the next doctor of the
    same department, then the least busy doctor of each next-best department. Departments with nobody on duty
    are left out."""
    with _lock:
        queue = _queues()
        times = _doctor_times()
        doctors = [dict(r) for r in _conn.execute(
            "SELECT * FROM doctors WHERE on_duty = 1 AND department != 'Resuscitation team'")]
    teams = {}
    for d in sorted(doctors, key=lambda d: (d["waiting"] + queue.get(d["reg_no"], 0), d["reg_no"])):
        teams.setdefault(d["department"], []).append(d)
    ranked = [name for name in sorted(match, key=match.get, reverse=True) if name in teams]
    if not ranked:
        return {"suggested": None, "others": []}

    def card(d):
        return {"doctor_reg": d["reg_no"], "doctor": d["name"], "room": d["room"], "department": d["department"],
                "match": round(match.get(d["department"], 0), 3), "with_now": d["waiting"],
                "queue": queue.get(d["reg_no"], 0), "busy": d["waiting"] > 0, **_times_of(times, d)}

    best = teams[ranked[0]]
    short = best[1:2] + [teams[name][0] for name in ranked[1:]]
    return {"suggested": card(best[0]), "others": [card(d) for d in short[:others]]}


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
            "department_changed": sum(bool(r["ai_department"]) and r["department"] is not None
                                      and r["department"] != r["ai_department"] for r in checked)}


def _pick_doctor(patient):
    """Levels 1-2: resuscitation team. Otherwise the least busy doctor ON DUTY in the patient's department.
    If nobody is on duty there, general medicine covers; if nobody is there either, any doctor on duty."""
    if patient["level"] <= 2:
        return _on_duty("Resuscitation team")[0], "Level 1 or 2: straight to the resuscitation team."
    wanted = patient.get("wanted_doctor") and _conn.execute(
        "SELECT * FROM doctors WHERE reg_no = ? AND on_duty = 1", (patient["wanted_doctor"],)).fetchone()
    if wanted:
        return dict(wanted), (f"The nurse chose {wanted['name']} ({wanted['department'].lower()}), "
                              f"who has {wanted['waiting']} other patient{'' if wanted['waiting'] == 1 else 's'} now.")
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
        doctor = _conn.execute("SELECT * FROM doctors WHERE reg_no = ?", (doctor_reg,)).fetchone()
        if doctor is None:
            return None
        _, queue = _waiting_for(doctor)
        doctor = {k: doctor[k] for k in ("reg_no", "name", "department", "room")}
        clock = _clock()
        rows = [dict(r) for r in _conn.execute(
            "SELECT * FROM visits WHERE status = 'with doctor' AND doctor_reg = ?", (doctor_reg,))]
        for row in rows:
            # A visit that never passed the nurse's screen (the made-up patients) has no stored summary:
            # it is put together here from what is stored, by the template, without the language model.
            row["summary"] = json.loads(row["summary"]) if row["summary"] else _summary_of(row, with_ai=False)
            # The summary was written when the nurse last decided. What happened in the waiting room after that
            # is added here, as it stands now: a press of "I feel worse" she did not get to, or new measurements
            # she had not decided on when the doctor called.
            row["summary"]["nurse_found"].update(
                said_worse=row["worse_said"] or 0, rechecks=row["rechecks"] or 0,
                worse_unanswered=row["worse_at"] is not None, remeasured=bool(row["remeasured"]))
    rows.sort(key=lambda p: (-p["referred"], p["level"], p["reg_no"]))

    def said(p):            # one line about the patient: what the nurse's summary says the patient said
        try:
            return json.loads(p["summary"])["patient_said"]["text"] if p["summary"] else None
        except (ValueError, KeyError, TypeError):
            return None
    return {"doctor": dict(doctor),
            # Waiting in line for this doctor: the ones the nurse chose this doctor for are marked "chosen". The
            # doctor's screen shows them with an "Accept" button, and asks about a newly chosen one at once.
            "waiting_for_you": [
                {"reg_no": p["reg_no"], "ticket": p["ticket"], "patient": full_name(p), "complaint": p["complaint"],
                 "age": p["age"], "sex": p["sex"], "level": p["level"], "waited": clock - p["arrived"],
                 "chosen": p["wanted_doctor"] == doctor["reg_no"], "said": said(p), "you": bool(p["you"])}
                for p in queue],
            "patients": [
        {"reg_no": p["reg_no"], "ticket": p["ticket"], "complaint": p["complaint"], "age": p["age"], "sex": p["sex"],
         "first_name": p["first_name"], "last_name": p["last_name"], "patient": full_name(p),
         "level": p["level"], "ai_level": p["ai_level"], "second_check": p["second_check"], "review": p["review"],
         "insurance": p["insurance"], "card_end": p["card_end"], "summary": p["summary"],
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
        _conn.execute("UPDATE visits SET status = 'done', closed_at = ?, closed_why = 'finished by the doctor', seen_to = ? "
                      "WHERE reg_no = ?", (_now(), time.time(), reg_no))
        _conn.execute("UPDATE doctors SET waiting = MAX(waiting - 1, 0) WHERE reg_no = ?", (visit["doctor_reg"],))
        _event(reg_no, "finished", visit["doctor_reg"])
    return {"reg_no": reg_no, "doctor_reg": visit["doctor_reg"], "status": "done"}


def staff():
    """The staff table and today's counts. Earlier days stay in the database and are counted under "all days"."""
    with _lock:
        doctors = [dict(r) for r in _conn.execute("SELECT * FROM doctors ORDER BY reg_no")]
        counts = {r["status"]: r["n"] for r in _conn.execute(
            "SELECT status, COUNT(*) AS n FROM visits WHERE day = ? GROUP BY status", (_meta("day"),))}
        people = _conn.execute("SELECT COUNT(*) AS n FROM people").fetchone()["n"]
        ever = _conn.execute("SELECT COUNT(*) AS n FROM visits").fetchone()["n"]
        logged = _conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
        queue = _queues()
        times = _doctor_times()
        simulation = bool(_setting("simulation", 1))
    regular = [d for d in doctors if d["department"] != "Resuscitation team"]
    return {"doctors": [{**d, "on_duty": bool(d["on_duty"]), "busy": d["waiting"] > 0,
                         "queue": queue.get(d["reg_no"], 0), **_times_of(times, d)} for d in doctors],
            "simulation": simulation,
            "on_duty": sum(d["on_duty"] for d in regular), "total": len(regular),
            "visits": {"registered": sum(counts.values()), "with nurse": counts.get("nurse", 0),
                       "waiting": counts.get("waiting", 0),
                       "with doctor": counts.get("with doctor", 0), "done": counts.get("done", 0)},
            "people": people, "reviews": review_stats(), "all days": {"visits": ever, "events": logged},
            "kept": keeping()}


def history(reg_no):
    """Every step of one visit, oldest first. None for an unknown visit."""
    with _lock:
        if not _conn.execute("SELECT 1 FROM visits WHERE reg_no = ?", (reg_no,)).fetchone():
            return None
        return [dict(r) for r in _conn.execute(
            "SELECT at, clock, what, who, detail FROM events WHERE visit_reg = ? ORDER BY id", (reg_no,))]


def recent_events(limit=20):
    """The newest steps in the whole department, newest first, for the nurse's "behind the scenes" panel."""
    with _lock:
        return [dict(r) for r in _conn.execute(
            "SELECT visit_reg, at, clock, what, who, detail FROM events ORDER BY id DESC LIMIT ?", (limit,))]


def set_duty(reg_no, on_duty):
    """Returns False when there is no doctor with this registration number."""
    if reg_no == "DR-1000":
        return True     # the resuscitation team is always on duty
    with _lock, _conn:
        return _conn.execute("UPDATE doctors SET on_duty = ? WHERE reg_no = ?", (int(on_duty), reg_no)).rowcount == 1


# ---------------------------------------------------------------- keeping the database

def _upgrade():
    """Bring a database file from an older version of the code up to date without losing what it holds.

    Missing tables are created and missing columns are added; doctors added to the list since are added. Only a
    brand-new, empty file gets a first day. (Earlier versions wiped the file whenever a column was missing.)"""
    model = sqlite3.connect(":memory:")
    model.executescript(SCHEMA)
    with _lock, _conn:
        _conn.executescript(SCHEMA)
        for (table,) in model.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'sqlite_sequence'"):
            have = {r["name"] for r in _conn.execute(f"PRAGMA table_info({table})")}
            for _, name, kind, _, default, _ in model.execute(f"PRAGMA table_info({table})"):
                if name not in have:
                    _conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}" + (f" DEFAULT {default}" if default else ""))
        _seed_staff()
        fresh = _conn.execute("SELECT COUNT(*) AS n FROM meta WHERE key = 'clock'").fetchone()["n"] == 0
        if not fresh and _setting("day_started", None) is None:      # a file from before the clock ran in real time
            _meta("day_started", int(time.time()))
        if not fresh:       # visits from before days were written down belong to the day that is open now
            _conn.execute("UPDATE visits SET day = (SELECT value FROM meta WHERE key = 'day') WHERE day IS NULL")
    model.close()
    if fresh:
        reset()


_kept_changes = None


def keep_copy():
    """Copy the whole database to lasting storage, if anything changed since the last copy.

    SQLite's own backup makes one consistent picture while the hospital keeps working; it is written next to
    the kept copy and then put in its place, so a copy is never half written. Returns True when a copy was made."""
    global _kept_changes
    if not KEPT:
        return False
    with _lock:
        changes = _conn.total_changes
        if changes == _kept_changes:
            return False
        fresh = KEPT.with_suffix(".new")
        target = sqlite3.connect(fresh)
        _conn.backup(target)
        target.close()
    try:
        os.replace(fresh, KEPT)
    except OSError:                       # some network storage cannot rename over a file
        shutil.copyfile(fresh, KEPT)
        fresh.unlink(missing_ok=True)
    _kept_changes = changes
    return True


def keeping():
    """Where the database is kept, for the staff panel: None on a laptop."""
    return {"where": str(KEPT), "copied": _kept_changes is not None} if KEPT else None


def start_keeping(every=10):
    """Copy the database to lasting storage every few seconds when it changed, and once more when the server stops."""
    if not KEPT:
        return
    def loop():
        while True:
            time.sleep(every)
            try:
                keep_copy()
            except Exception as error:             # never take the hospital down: say it and try again
                print(f"Could not copy the database to {KEPT}: {error}", flush=True)
    threading.Thread(target=loop, daemon=True, name="keep-database").start()
    atexit.register(keep_copy)


# ---------------------------------------------------------------- the rest of the hospital, simulated
# A real emergency department does not stand still while one nurse works: other nurses send patients to the
# doctors and the doctors finish with them. The simulation plays that part with made-up patients, so a doctor
# the nurse is about to choose may really be busy, and free again a few minutes later.
# It never touches a real patient, the waiting line, or a doctor whose own screen is open (a person is playing
# that doctor). It runs only while somebody has a Meddy screen open, so an empty Space does not fill its
# database. "Behind the scenes" can switch it off.
SIM_PATIENTS = {
    "Cardiology": [("chest tightness", 64, "Male", 3, "normal", 4), ("palpitations", 51, "Female", 3, "normal", 0),
                   ("chest pain on exertion", 70, "Male", 3, "normal", 5)],
    "Respiratory medicine": [("cough and fever", 45, "Female", 3, "feverish", 0), ("dyspnea", 72, "Male", 3, "normal", 0),
                             ("asthma, wheezing", 28, "Female", 3, "normal", 0)],
    "Neurology": [("headache", 39, "Female", 4, "normal", 5), ("dizziness", 67, "Male", 3, "normal", 0),
                  ("numbness in the left hand", 58, "Male", 3, "normal", 0)],
    "Trauma and orthopaedics": [("ankle sprain", 24, "Male", 4, "normal", 4), ("wrist pain after a fall", 33, "Female", 4, "normal", 5),
                                ("finger laceration", 41, "Male", 4, "normal", 3)],
    "Gastroenterology and surgery": [("abd pain", 47, "Female", 3, "normal", 5), ("vomiting and diarrhea", 30, "Male", 4, "feverish", 3),
                                     ("epigastric pain", 55, "Male", 3, "normal", 4)],
    "Eye, ear, nose and throat": [("ear pain", 19, "Female", 5, "normal", 4), ("sore throat", 26, "Male", 5, "feverish", 3),
                                  ("epistaxis", 61, "Male", 4, "normal", 0)],
    "General medicine": [("fever", 36, "Female", 4, "feverish", 0), ("general weakness", 79, "Male", 3, "normal", 0),
                         ("skin rash", 22, "Female", 5, "normal", 0)],
    "Urology and kidney": [("flank pain", 43, "Male", 3, "normal", 6), ("dysuria", 31, "Female", 4, "normal", 3),
                           ("hematuria", 66, "Male", 3, "normal", 0)],
    "Obstetrics and gynaecology": [("vaginal bleeding", 29, "Female", 3, "normal", 3), ("pelvic pain", 34, "Female", 4, "normal", 5),
                                   ("period pain", 22, "Female", 5, "normal", 5)],
}
SIM_EVERY = 15                  # seconds between two steps
SIM_SEEN = (120, 420)           # a simulated visit lasts 2 to 7 minutes
SIM_NEW_PATIENT = 0.12          # at each step, the chance that a free doctor gets a patient from another nurse
SIM_WATCHED = 90                # a doctor whose screen asked for their list in the last 90 seconds is left alone
SIM_AWAKE = 600                 # the simulation sleeps when no screen asked for anything in the last 10 minutes
_watched = {}                   # doctor -> when their screen last asked for their list
_last_seen = 0.0


def seen():
    """A Meddy screen asked the server for something: the hospital is being watched, so it keeps moving."""
    global _last_seen
    _last_seen = time.monotonic()


def watching(doctor_reg):
    """A doctor's own screen is open: a person plays this doctor, so the simulation leaves them alone."""
    _watched[doctor_reg] = time.monotonic()


def _setting(key, default):
    row = _conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return default if row is None else row["value"]


def simulation_on():
    with _lock:
        return bool(_setting("simulation", 1))


def set_simulation(on):
    with _lock, _conn:
        _meta("simulation", int(on))


def simulate(rng=None, now=None):
    """One step of the rest of the hospital. Made-up patients whose time is up leave their doctor, and a free
    doctor may get a new made-up patient from another nurse. Returns how many left and how many came."""
    rng = rng or secrets.SystemRandom()
    now = time.time() if now is None else now
    watched = {reg for reg, at in _watched.items() if time.monotonic() - at < SIM_WATCHED}
    left = came = 0
    with _lock, _conn:
        if not _setting("simulation", 1):
            return {"left": 0, "came": 0}
        for visit in _conn.execute("SELECT reg_no, doctor_reg, sim_until, seen_from FROM visits "
                                   "WHERE status = 'with doctor' AND you = 0").fetchall():
            if visit["doctor_reg"] in watched:
                continue
            if visit["sim_until"] is None:          # a made-up patient of the morning: give them a time to leave
                _conn.execute("UPDATE visits SET sim_until = ?, seen_from = ? WHERE reg_no = ?",
                              (now + rng.uniform(30, SIM_SEEN[1]), now, visit["reg_no"]))
            elif visit["sim_until"] <= now:
                _conn.execute("UPDATE visits SET status = 'done', closed_at = ?, closed_why = 'seen (simulated)', "
                              "seen_to = ? WHERE reg_no = ?", (_now(), now, visit["reg_no"]))
                _conn.execute("UPDATE doctors SET waiting = MAX(waiting - 1, 0) WHERE reg_no = ?", (visit["doctor_reg"],))
                _event(visit["reg_no"], "finished", visit["doctor_reg"], "simulated")
                left += 1
        free = _conn.execute("SELECT reg_no, department FROM doctors WHERE on_duty = 1 AND waiting = 0 "
                             "AND department != 'Resuscitation team' ORDER BY reg_no").fetchall()
        for doctor in free:
            if doctor["reg_no"] in watched or rng.random() >= SIM_NEW_PATIENT:
                continue
            complaint, age, sex, level, preset, pain = rng.choice(SIM_PATIENTS[doctor["department"]])
            reg_no, _ = _made_up_visit(complaint, age, sex, level, preset, pain, "Alert", _clock(),
                                       doctor["department"], doctor["reg_no"], series="B")
            _conn.execute("UPDATE visits SET sim_until = ?, seen_from = ? WHERE reg_no = ?",
                          (now + rng.uniform(*SIM_SEEN), now, reg_no))
            _conn.execute("UPDATE doctors SET waiting = waiting + 1 WHERE reg_no = ?", (doctor["reg_no"],))
            came += 1
    return {"left": left, "came": came}


KEEP_MADE_UP = 24 * 3600        # seconds a finished made-up visit is kept


def forget_old_made_up(older_than=KEEP_MADE_UP):
    """Remove finished made-up visits older than a day, with their answers and log lines. A real patient's visit
    is never removed. The simulation writes a few hundred made-up visits an hour; kept for ever they would only
    make the file, and its copy to lasting storage every few seconds, heavier. Returns how many were removed."""
    before = (datetime.now(timezone.utc) - timedelta(seconds=older_than)).isoformat(timespec="seconds")
    with _lock, _conn:
        old = [r["reg_no"] for r in _conn.execute(
            "SELECT reg_no FROM visits WHERE you = 0 AND status = 'done' AND closed_at IS NOT NULL AND closed_at < ?",
            (before,))]
        for start in range(0, len(old), 500):
            some = old[start:start + 500]
            marks = ",".join("?" * len(some))
            _conn.execute(f"DELETE FROM events WHERE visit_reg IN ({marks})", some)
            _conn.execute(f"DELETE FROM answers WHERE visit_reg IN ({marks})", some)
            _conn.execute(f"DELETE FROM visits WHERE reg_no IN ({marks})", some)
    return len(old)


def start_simulation(every=SIM_EVERY):
    """Run the rest of the hospital in the background while somebody is watching. MEDDY_SIMULATION=0 turns it off."""
    if os.environ.get("MEDDY_SIMULATION", "1") == "0":
        return
    def loop():
        steps = 0
        while True:
            time.sleep(every)
            if time.monotonic() - _last_seen > SIM_AWAKE:
                continue
            try:
                simulate()
                steps += 1
                if steps % 240 == 1:                # at the first step, then about once an hour
                    forget_old_made_up()
            except Exception as error:             # never take the hospital down: say it and try again
                print(f"The simulation stumbled: {error}", flush=True)
    threading.Thread(target=loop, daemon=True, name="simulate-hospital").start()


_upgrade()
