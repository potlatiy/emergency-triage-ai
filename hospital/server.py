"""The digital hospital's web server.

Run:   python hospital/server.py      then open http://localhost:8000
       MEDDY_LAN=1 python hospital/server.py   also lets phones on the same Wi-Fi open it (the QR code
                                               on the entrance step then points to this computer)

It serves the walk-through page (static/index.html), the doctor's screen at /doctor (static/doctor.html)
and a small API the pages talk to.
Everything is kept in the internal database, hospital.db (see db.py).
"""

import hashlib
import hmac
import os
import socket
import time
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

import core
import db

STATIC = Path(__file__).parent / "static"
# A fingerprint of the pages and the code this server started with. An open page compares it from time to time
# and offers a reload when a new version is online: otherwise an old tab keeps showing the old screens.
VERSION = hashlib.sha256(b"".join(f.read_bytes() for f in sorted([*STATIC.iterdir(), *Path(__file__).parent.glob("*.py")])
                                  if f.is_file())).hexdigest()[:12]
PORT = int(os.environ.get("MEDDY_PORT", 8000))
# Off by default: the demo stays on this computer unless someone starts it with MEDDY_LAN=1.
LAN = os.environ.get("MEDDY_LAN") == "1"
# Set when the hospital runs online (see deploy/huggingface): the QR code then points to this address.
PUBLIC_URL = os.environ.get("MEDDY_PUBLIC_URL") or None
app = FastAPI(title="Digital hospital")


# ---------------------------------------------------------------- staff only
# The nurse's and the doctor's screens show patients' names and complaints, and their buttons can close every
# visit. Online they are locked with a staff PIN, set as the Space secret MEDDY_STAFF_PIN. Without it (on a
# laptop, in the tests) the screens are open as before. The patient's phone never needs the PIN: it may only
# register, answer, and ask about its own visit.
STAFF_PIN = os.environ.get("MEDDY_STAFF_PIN") or None
STAFF_COOKIE = "meddy_staff"
FOR_PATIENTS = ("/api/register", "/api/follow-up/", "/api/checkin", "/api/worse", "/api/me/", "/api/version",
                "/api/spelling", "/api/address", "/api/waiting-room", "/api/staff/login", "/api/staff/session",
                "/api/staff/logout",
                "/api/board")
_wrong_pins = {}            # address -> (wrong tries, refused until)


def _staff_token():
    return hmac.new(STAFF_PIN.encode(), b"meddy staff screens", hashlib.sha256).hexdigest()


def is_staff(request):
    return STAFF_PIN is None or hmac.compare_digest(request.cookies.get(STAFF_COOKIE, ""), _staff_token())


# The chief doctor's panel has a PIN of its own (Space secret MEDDY_CHIEF_PIN): it shows the whole department at
# once. Without that secret the staff PIN opens it too.
CHIEF_PIN = os.environ.get("MEDDY_CHIEF_PIN") or None
CHIEF_COOKIE = "meddy_chief"


def _chief_token():
    return hmac.new(CHIEF_PIN.encode(), b"meddy chief doctor's panel", hashlib.sha256).hexdigest()


def is_chief(request):
    if CHIEF_PIN is None:
        return is_staff(request)
    return hmac.compare_digest(request.cookies.get(CHIEF_COOKIE, ""), _chief_token())


@app.middleware("http")
async def watched(request, call_next):
    """Every request from a Meddy screen tells the simulated rest of the hospital that somebody is watching.
    A request for a staff screen's data without the staff PIN is refused."""
    path = request.url.path
    if path.startswith("/api/"):
        db.seen()
        if path.startswith("/api/chief/"):
            if not is_chief(request):
                return JSONResponse({"detail": "Chief doctor only: enter the chief doctor's PIN."}, status_code=401)
        elif not path.startswith(FOR_PATIENTS) and not is_staff(request):
            return JSONResponse({"detail": "Staff only: enter the staff PIN."}, status_code=401)
    return await call_next(request)


def lan_ip():
    """This computer's address on the local network, or None. Connecting a UDP socket sends nothing."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            ip = probe.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


@app.exception_handler(RequestValidationError)
def refused(request, error):
    """Say what was wrong with a request without echoing the value back: a number such as NaN or a broken
    character in the rejected value cannot be written as JSON and used to turn the refusal into a crash."""
    problems = [{"where": [str(part) for part in e["loc"]], "problem": e["msg"]} for e in error.errors()]
    return JSONResponse({"detail": problems}, status_code=422)


@app.exception_handler(UnicodeEncodeError)
def unreadable_text(request, error):
    """Text with a broken character cannot be stored. Nothing was saved: the write is undone as a whole."""
    return JSONResponse({"detail": "The text contains a character that cannot be stored."}, status_code=422)


Sex = Literal["Female", "Male"]


class Arrival(BaseModel):
    # Spaces are trimmed before the length check, so a complaint made only of spaces is refused.
    model_config = ConfigDict(str_strip_whitespace=True)
    complaint: str = Field(min_length=1, max_length=200)
    age: float = Field(ge=15, le=110)
    sex: Sex = "Female"
    use_model: bool = True
    # Insurance only identifies the person, so that one person has one open visit. It is never given to
    # either model and never changes the level, the department or the place in line.
    insurance: Literal["public", "private", "none"] = "none"
    card: str | None = Field(default=None, max_length=40)
    # The name lets the nurse and the doctor call the patient. Letters, spaces, hyphens and apostrophes in any
    # alphabet. It goes into the database for staff only: never to the models or the language model.
    first_name: str | None = Field(default=None, min_length=1, max_length=40, pattern=r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$")
    last_name: str | None = Field(default=None, min_length=1, max_length=40, pattern=r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$")


class Answer(BaseModel):
    question: str = Field(max_length=300)
    answer: str | None = Field(default=None, max_length=200)
    kind: Literal["danger", "extra"]         # a hand-written danger question, or one for the doctor's history
    topic: str | None = Field(default=None, max_length=30)   # the history topic, for the doctor's overview


class Vitals(BaseModel):
    """What the nurse measured, typed in by hand. The limits only keep out typing slips; they are wide enough for
    a very sick patient, because a value the form refuses is a sick patient the model never sees."""
    SBP: float = Field(ge=40, le=260)           # blood pressure, upper number
    DBP: float = Field(ge=20, le=160)           # blood pressure, lower number
    HR: float = Field(ge=20, le=250)            # pulse
    RR: float = Field(ge=4, le=70)              # breaths a minute
    BT: float = Field(ge=30, le=43)             # temperature, degrees Celsius
    Saturation: float = Field(ge=50, le=100)    # oxygen, percent


class Assessment(BaseModel):
    # Every choice is spelled out. An unknown value is refused rather than quietly read as "normal" or "alert":
    # a silent default here would make a sick patient look well.
    reg_no: str
    # Who the patient is comes from the registered visit, never from this request: otherwise a level worked
    # out for "chest pain, 70" could be saved against a visit registered as "sore throat, 20".
    # The three fields are still accepted so that older pages keep working; they are ignored.
    complaint: str | None = Field(default=None, max_length=200)
    age: float | None = None
    sex: str | None = None
    arrival: Literal["Walked in", "Public ambulance", "Private car", "Private ambulance"] = "Walked in"
    injury: bool = False
    alert: Literal["Alert", "Responds to voice", "Responds to pain", "Unresponsive"] = "Alert"
    pain: int = Field(default=0, ge=0, le=10)
    # One of core.VITAL_PRESETS, or the nurse's own measurements.
    vitals: Literal["normal", "feverish", "unstable"] | Vitals = "normal"
    answers: list[Answer] = Field(default=[], max_length=20)


class CheckIn(BaseModel):
    """What the patient's phone sends when check-in is done. The nurse measures the rest on her own screen."""
    reg_no: str
    arrival: Literal["Walked in", "Public ambulance", "Private car", "Private ambulance"] = "Walked in"
    injury: bool = False
    pain: int = Field(default=0, ge=0, le=10)
    answers: list[Answer] = Field(default=[], max_length=20)


class Joining(BaseModel):
    reg_no: str
    level: int = Field(ge=1, le=5)
    danger: bool = False
    department: str | None = None
    department_how: str | None = Field(default=None, max_length=120)
    # What the nurse did with Meddy's report: accepted it as it was, or checked the patient in person.
    review: Literal["approved", "checked"] | None = None
    # For the doctor's patient summary: her own note, and her word on the AI's two sentences
    # (False: "this is not right, do not pass it on"). Both optional, so older pages keep working.
    # A request with no note at all keeps the note she wrote before (a re-check); an empty one clears it.
    note: str | None = Field(default=None, max_length=300)
    summary_ok: bool = True
    # The doctor the nurse chose from the short list, if she chose one: the patient then waits for this doctor.
    doctor_reg: str | None = Field(default=None, pattern=r"^DR-\d{4}$")


class Referral(BaseModel):
    # Spaces are trimmed before the length check, so a reason made only of spaces is refused.
    model_config = ConfigDict(str_strip_whitespace=True)
    reg_no: str
    level: int = Field(ge=1, le=5)
    reason: str = Field(min_length=3, max_length=300)       # required: the doctor must know why
    department: str | None = None
    note: str | None = Field(default=None, max_length=300)  # for the doctor's patient summary, like in Joining
    summary_ok: bool = True
    # The doctor the nurse chose from the short list, if she chose one: the patient then waits for this doctor.
    doctor_reg: str | None = Field(default=None, pattern=r"^DR-\d{4}$")


class Duty(BaseModel):
    reg_no: str
    on_duty: bool


class Finished(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reg_no: str
    note: str | None = Field(default=None, max_length=300)      # the doctor's note for the patient, confirmed


class Tidying(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=300)


class Worse(BaseModel):
    reg_no: str


class Calling(BaseModel):
    doctor_reg: str
    reg_no: str | None = None          # "Accept": this patient, who must be waiting for this doctor
    # The doctor's own screen sends this: a doctor sees one patient at a time, so they close the visit they are in
    # before they call the next one. (The nurse's "Call next patient" and the tests do not send it.)
    one_at_a_time: bool = False


class Simulation(BaseModel):
    on: bool


class Managing(BaseModel):
    reg_no: str
    why: Literal["left without being seen", "sent home by the nurse", "registered by mistake"] | None = None


class Wiping(BaseModel):
    confirm: Literal["ERASE"]                 # typed on purpose: erasing everything is never one stray click


class Login(BaseModel):
    pin: str = Field(min_length=1, max_length=40)
    role: Literal["staff", "chief"] = "staff"


class Matching(BaseModel):
    match: dict[str, float] = Field(max_length=20)


def open_visit(reg_no):
    """The visit for a step at the desk or the nurse station. Refused when the registration number is unknown
    (404) or the patient has already gone to a doctor (409), so no step can be done twice or out of order."""
    visit = db.visit_for(reg_no)
    if visit is None:
        raise HTTPException(404, "No visit with this registration number.")
    if visit["status"] in ("with doctor", "done"):
        raise HTTPException(409, "This patient has already been sent to a doctor.")
    return visit


def checked_answers(visit, answers, injury=False):
    """The answers as plain dictionaries, refused (422) when they cannot have come from this visit's questionnaire.

    A yes to a danger question moves the patient up the nurse's list and the line, so the server does not
    take the phone's word for what was asked: a danger answer must belong to one of the hand-written
    questions for this complaint, be yes or no, and appear once. Other answers are information for the
    doctor; they must carry a topic we know, or none."""
    allowed, seen = core.danger_questions_for(visit["complaint"], injury or bool(visit["injury"])), set()
    answers = [a.model_dump() for a in answers]
    for a in answers:
        if a["kind"] == "danger":
            if a["question"] not in allowed or a["question"] in seen:
                raise HTTPException(422, "This is not one of the danger questions for this patient's complaint.")
            if a["answer"] not in ("Yes", "No", None):
                raise HTTPException(422, "A danger question is answered with Yes or No.")
            seen.add(a["question"])
        elif a["topic"] is not None and not core.known_topic(a["topic"]):
            raise HTTPException(422, "Unknown topic for an answer.")
    return answers


def known_department(name):
    if name is not None and name not in core.DEPARTMENT_NAMES:
        raise HTTPException(422, "No such department. Choose one of: " + ", ".join(core.DEPARTMENT_NAMES) + ".")


@app.get("/")
def page():
    # no-cache: the browser always asks for the latest page, so an update is seen without a hard refresh
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/doctor")
def doctor_page():
    """The doctor's own screen (static/doctor.html). It reads GET /api/doctor/{doctor_reg}/patients."""
    page = STATIC / "doctor.html"
    if not page.exists():
        raise HTTPException(404, "The doctor's page is not built yet. It belongs in hospital/static/doctor.html.")
    return FileResponse(page, headers={"Cache-Control": "no-cache"})


@app.get("/nurse")
def nurse_page():
    """The nurse's own screen (static/nurse.html): the patients waiting for her, one card at a time."""
    return FileResponse(STATIC / "nurse.html", headers={"Cache-Control": "no-cache"})


@app.post("/api/register")
def register(body: Arrival):
    """The patient arrives: give the visit a registration number and build its questionnaire."""
    if body.insurance != "none" and db.person_key(body.card) is None:
        raise HTTPException(422, "Enter the card number: 6 to 20 letters and digits. Without a card, choose no insurance.")
    try:
        reg_no = db.register(body.complaint, body.age, body.sex, body.insurance, body.card, body.first_name, body.last_name)
    except db.AlreadyRegistered as already:
        # One person, one open visit. The answer points to the visit that is already open.
        raise HTTPException(409, {"message": "This card already has an open visit. One person, one visit at a time.",
                                  **already.visit})
    # "key": only this phone gets it. It sends it back when it asks about its visit, and the doctor's note for the
    # patient is given to nobody else (registration numbers follow one another and can be guessed).
    return {"reg_no": reg_no, "day": db.day(), "key": db.phone_key(reg_no),
            **core.questionnaire(body.complaint, use_model=body.use_model)}


@app.get("/api/follow-up/{reg_no}")
def follow_up(reg_no: str):
    """The optional questions for the doctor, written by the language model for this visit's complaint.

    Each has a history topic and short answers to tap. The page asks for them after registering, so the
    patient never waits for the model. The list is empty when the model is switched off."""
    visit = db.visit_for(reg_no)
    if visit is None:
        raise HTTPException(404, "No visit with this registration number.")
    return core.follow_up(visit["complaint"], bool(visit["injury"]))


@app.post("/api/checkin")
def checkin(body: CheckIn):
    """The patient's phone finishes check-in. The patient now waits for the nurse, and the phone shows a short
    code; the nurse finds the patient by it on her own screen. Refused (409) once the nurse has seen them."""
    visit = open_visit(body.reg_no)
    answers = checked_answers(visit, body.answers, body.injury)
    try:
        code = db.check_in(body.reg_no, body.arrival, body.injury, body.pain, answers)
    except db.AlreadyWithNurse:
        raise HTTPException(409, "The nurse has already seen this patient.")
    except db.AlreadySent:
        raise HTTPException(409, "This patient has already been sent to a doctor.")
    if code is None:
        raise HTTPException(404, "No visit with this registration number.")
    return {"reg_no": body.reg_no, "code": code, "status": "nurse"}


@app.post("/api/worse")
def worse(body: Worse):
    """The patient pressed "I feel worse" while waiting. Nothing about their level or place changes: the nurse is
    told, and the patient goes to the top of her list for another look. Refused (409) when they are not waiting."""
    try:
        status = db.feel_worse(body.reg_no)
    except db.NotWaiting:
        raise HTTPException(409, "This patient is not waiting, so there is nobody to call. Speak to the staff.")
    if status is None:
        raise HTTPException(404, "No visit with this registration number.")
    return {"reg_no": body.reg_no, "status": status, "worse": True}


@app.get("/api/nurse/patients")
def nurse_patients():
    """The nurse's list: patients who finished check-in and wait for her (someone who feels worse first, then
    danger signs), and "recheck": patients in the waiting room who need her again."""
    return db.nurse_patients()


@app.post("/api/nurse/still-fine")
def still_fine(body: Managing):
    """The nurse looked again at a patient in the waiting room and nothing changed: the re-check starts again."""
    try:
        done = db.still_fine(body.reg_no)
    except db.NotHere:
        raise HTTPException(409, "This patient is not in the waiting room.")
    if done is None:
        raise HTTPException(404, "No visit with this registration number.")
    return done


@app.post("/api/nurse/remove")
def remove(body: Managing):
    """The nurse takes a patient off her list or out of the line, with the reason. The visit is closed and kept."""
    if body.why is None:
        raise HTTPException(422, "Say why the patient is removed.")
    try:
        done = db.remove_visit(body.reg_no, body.why)
    except db.NotHere:
        raise HTTPException(409, "This patient is already with a doctor or gone.")
    if done is None:
        raise HTTPException(404, "No visit with this registration number.")
    return done


@app.get("/api/nurse/today")
def todays_patients():
    """Today's patients and where each one went, for the nurse."""
    return {"patients": db.todays_patients()}


@app.get("/api/staff/session")
def staff_session(request: Request, role: Literal["staff", "chief"] = "staff"):
    """Does this browser still need the staff PIN (or, for the chief doctor's panel, the chief's)? "pin": whether
    there is a PIN at all, so the page knows whether to offer "Lock this screen"."""
    return {"locked": not (is_chief(request) if role == "chief" else is_staff(request)),
            "pin": (CHIEF_PIN or STAFF_PIN if role == "chief" else STAFF_PIN) is not None}


@app.post("/api/staff/login")
def staff_login(body: Login, request: Request):
    """The staff PIN opens the nurse's and the doctor's screens on this browser for 12 hours. After five wrong
    tries from one address, it waits a minute before trying again."""
    chief = body.role == "chief" and CHIEF_PIN is not None
    pin, cookie, token = (CHIEF_PIN, CHIEF_COOKIE, _chief_token) if chief else (STAFF_PIN, STAFF_COOKIE, _staff_token)
    if pin is None:
        return {"locked": False}
    who = request.headers.get("x-forwarded-for", request.client.host if request.client else "").split(",")[0].strip()
    tries, until = _wrong_pins.get(who, (0, 0))
    if until > time.time():
        raise HTTPException(429, "Too many wrong tries. Wait a minute.")
    # Compared as bytes: as text, a PIN typed with letters outside English made the comparison itself fail.
    if not hmac.compare_digest(body.pin.strip().encode(), pin.encode()):
        _wrong_pins[who] = (0, time.time() + 60) if tries + 1 >= 5 else (tries + 1, 0)
        raise HTTPException(401, "That is not the staff PIN.")
    _wrong_pins.pop(who, None)
    answer = JSONResponse({"locked": False})
    answer.set_cookie(cookie, token(), max_age=12 * 3600, httponly=True, samesite="lax",
                      secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
    return answer


@app.post("/api/staff/logout")       # open to anyone: locking a screen needs no PIN
def staff_logout():
    answer = JSONResponse({"locked": STAFF_PIN is not None})
    answer.delete_cookie(STAFF_COOKIE)
    answer.delete_cookie(CHIEF_COOKIE)
    return answer


@app.get("/api/waiting-room")
def waiting_room():
    """How many people wait, for the seats on the phone's floor plan. Nothing else: the staff list is staff only."""
    return {"waiting": db.staff()["visits"]["waiting"]}


@app.get("/api/board")
def waiting_room_board(ticket: str | None = None):
    """The waiting room's board (public): tickets called and to which room, how many wait, the usual wait now,
    and with ?ticket=A004 where that ticket stands. No names, complaints or levels."""
    return db.board(ticket[:10] if ticket else None)


@app.get("/board")
def board_page():
    """The board on the waiting room's wall (static/board.html)."""
    return FileResponse(STATIC / "board.html", headers={"Cache-Control": "no-cache"})


@app.get("/chief")
def chief_page():
    """The chief doctor's panel (static/chief.html): the whole department at a glance."""
    return FileResponse(STATIC / "chief.html", headers={"Cache-Control": "no-cache"})


@app.post("/api/chief/wipe")
def chief_wipe(body: Wiping):
    """Erase everything and start as on the first day (chief doctor only, and only with the word ERASE)."""
    return db.wipe_everything()


@app.get("/api/chief/report")
def chief_report():
    """Everything the chief doctor's panel shows, worked out from the database (db.chief_report)."""
    return db.chief_report()


@app.get("/api/version")
def version():
    return {"version": VERSION}


class Typed(BaseModel):
    text: str = Field(default="", max_length=200)


@app.post("/api/spelling")
def spelling(body: Typed):
    """The complaint with typing slips put right, for the phone's "Did you mean ...?". Nothing is stored.

    The words travel in the body of the request, never in its address: what a patient types about their health
    must not end up in the address logs of a proxy standing in front of the hospital."""
    fixed = core.spelling(body.text)
    # "understood": Meddy found a symptom word it knows. When it does not (another language, no symptom named),
    # the phone asks the patient to say it in English: the models read English only.
    return {"text": fixed, "changed": fixed != body.text, "understood": bool(core.known_words(fixed))}


@app.get("/api/spelling")
def spelling_from_an_older_page(text: str = ""):
    """The same, for a phone page opened before this version (it reloads itself when it sees the new one)."""
    return spelling(Typed(text=text[:200]))


@app.get("/api/nurse/patient/{reg_no}")
def nurse_patient(reg_no: str):
    """One patient's card for the nurse: the complaint, how they came, and every answer from check-in."""
    card = db.nurse_card(reg_no)
    if card is None:
        raise HTTPException(404, "No visit with this registration number.")
    return card


@app.get("/api/nurse/summary/{reg_no}")
def nurse_summary(reg_no: str):
    """Two sentences from the language model that sum up what the patient reported. The page asks for it apart
    from the card, so the nurse never waits for the model; "source" is "none" when there is no summary."""
    card = db.nurse_card(reg_no)
    if card is None:
        raise HTTPException(404, "No visit with this registration number.")
    return core.nurse_summary(reg_no, card["complaint"], card["age"], card["sex"], card["answers"], card["injury"])


@app.post("/api/triage")
def triage(body: Assessment):
    visit = open_visit(body.reg_no)
    vitals = core.VITAL_PRESETS[body.vitals] if isinstance(body.vitals, str) else body.vitals.model_dump()
    answers = checked_answers(visit, body.answers, body.injury)
    injury = body.injury or bool(visit["injury"])
    if answers:
        db.save_answers(body.reg_no, answers, injury)
    else:
        # No answers in the request: the ones the patient gave at check-in stand. (Saving an empty list here
        # used to wipe them, and the doctor's summary then had nothing the patient said.)
        answers = db.answers_for(body.reg_no)
    result = core.assess(visit["complaint"], visit["age"], visit["sex"], body.arrival, injury, body.alert,
                         body.pain, vitals)
    # Only the hand-written danger questions can mark a patient; AI-written ones are information only.
    result["danger"] = any(a["kind"] == "danger" and a["answer"] == "Yes" for a in answers)
    result["vitals"] = vitals
    # Meddy's report for the nurse: can she accept it after a quick look, and who would the doctor be?
    # "asked" is false for a patient the nurse took over before they answered the danger questions on the phone.
    result["asked"] = any(a["kind"] == "danger" and a["answer"] in ("Yes", "No") for a in answers)
    # A patient who is already in the waiting room is being re-checked: what stood before, and did they ask for it?
    said_worse = visit["worse_at"] is not None
    if visit["status"] == "waiting":
        # "earlier" is what stood when the nurse last decided, not what an earlier press of "Measure" in this
        # same re-check has since written over it.
        then = visit["ai_level_then"] if visit["ai_level_then"] is not None else visit["ai_level"]
        result["recheck"] = {"earlier_level": visit["level"], "earlier_ai_level": then, "worse": said_worse}
    result["advice"] = core.review_advice(result, result["danger"], result["asked"], worse=said_worse)
    for option in result["department_options"]:
        option["doctor"] = db.likely_doctor(result["level"], option["department"])
    result["likely_doctor"] = db.likely_doctor(result["level"], result["department"])
    # The best doctor for this patient and a short list of three more, each with how busy they are now.
    result["doctors"] = db.doctor_choices(result["department_match"])
    # Keep the AI's suggestion and the measurements: the doctor's screen and the test regime need them later.
    try:        # the state is checked again inside the database: a doctor may have called the patient meanwhile
        db.save_assessment(body.reg_no, result["level"], vitals, body.alert, body.pain,
                           [result["probabilities"][str(l)] for l in core.LEVELS],
                           result["department"], result["advice"]["quick"])
    except db.AlreadySent:
        raise HTTPException(409, "This patient has already been sent to a doctor.")
    return result


@app.post("/api/line/join")
def join(body: Joining):
    """The nurse's level puts the patient in the line. Levels 1 and 2 do not wait there: the patient is sent
    to the resuscitation team at once, and "called" in the answer says where they went."""
    visit = open_visit(body.reg_no)
    known_department(body.department)
    if visit["ai_level"] is None:
        raise HTTPException(409, "This patient has not been assessed at the nurse station yet.")
    try:        # the state is checked again inside the database, together with the change (two quick clicks)
        ticket, called = db.admit(body.reg_no, body.level, body.danger, body.department, body.department_how, body.review,
                                  note=(body.note or "").strip() or None, summary_ok=body.summary_ok,
                                  keep_note=body.note is None, doctor_reg=body.doctor_reg)
    except db.AlreadySent:
        raise HTTPException(409, "This patient has already been sent to a doctor.")
    except db.UnknownDoctor:
        raise HTTPException(422, "No such doctor to choose. Choose one from the list.")
    if called:
        called["answers"] = db.answers_for(called["reg_no"])
    return {"ticket": ticket, "called": called, **db.line()}


@app.get("/api/line")
def line():
    return db.line()


@app.get("/api/me/{reg_no}")
def me(reg_no: str, request: Request):
    """The patient's phone: ticket, place in line and doctor. Never the urgency level (see db.where_is).
    The phone's own key travels in a header, never in the address."""
    status = db.where_is(reg_no, request.headers.get("x-meddy-visit"))
    if status is None:
        raise HTTPException(404, "No visit with this registration number.")
    return status


@app.post("/api/line/wait")
def wait():
    """Fifteen minutes pass in the department: the first patient is called and a made-up patient walks in."""
    happened = db.pass_time(15)
    if happened["called"]:
        happened["called"]["answers"] = db.answers_for(happened["called"]["reg_no"])
    return {**happened, **db.line()}


@app.post("/api/line/next")
def call_next():
    """Call the first patient in line and match them with a doctor who is on duty."""
    called = db.call_next()
    if called:
        called["answers"] = db.answers_for(called["reg_no"])
    return {"called": called, **db.line()}


@app.post("/api/refer")
def refer(body: Referral):
    """The nurse sends a critical patient straight to a doctor, past the line. Her reason is required."""
    open_visit(body.reg_no)
    known_department(body.department)
    try:
        return db.refer(body.reg_no, body.level, body.reason, body.department,
                        note=(body.note or "").strip() or None, summary_ok=body.summary_ok, keep_note=body.note is None,
                        doctor_reg=body.doctor_reg)
    except db.AlreadySent:
        raise HTTPException(409, "This patient has already been sent to a doctor.")
    except db.UnknownDoctor:
        raise HTTPException(422, "No such doctor to choose. Choose one from the list.")


@app.get("/api/doctor/{doctor_reg}/patients")
def doctor_patients(doctor_reg: str):
    """The doctor's screen: every patient now with this doctor, with the full card."""
    screen = db.doctor_patients(doctor_reg)
    if screen is None:
        raise HTTPException(404, "No doctor with this registration number.")
    db.watching(doctor_reg)         # a person plays this doctor now: the simulation leaves them alone
    return screen


@app.post("/api/doctor/call")
def doctor_call(body: Calling):
    """The doctor calls their next patient: the first in line for their department. "called" is None when
    nobody is waiting for this doctor. A doctor who is off duty is refused (409)."""
    try:
        if body.one_at_a_time and db.doctor_patients(body.doctor_reg) and db.doctor_patients(body.doctor_reg)["patients"]:
            raise HTTPException(409, "Finish the visit you are in first: press Visit finished, then call the next patient.")
        called = db.call_for_doctor(body.doctor_reg, body.reg_no)
    except LookupError:
        raise HTTPException(404, "No doctor with this registration number.")
    except db.OffDuty:
        raise HTTPException(409, "This doctor is off duty and cannot call a patient.")
    except db.NotForYou:
        raise HTTPException(409, "This patient is no longer waiting for you.")
    if called:
        called["answers"] = db.answers_for(called["reg_no"])
    return {"called": called, **db.line()}


@app.post("/api/doctor/finish")
def finish(body: Finished):
    """The doctor closes a visit. The patient leaves the doctor's list, which gets one shorter."""
    if db.visit_for(body.reg_no) is None:
        raise HTTPException(404, "No visit with this registration number.")
    closed = db.finish_visit(body.reg_no, body.note or None)
    if closed is None:
        raise HTTPException(409, "This patient is not with a doctor.")
    return closed


@app.post("/api/doctor/tidy")
def tidy(body: Tidying):
    """Meddy puts spelling and punctuation right in the doctor's note, and nothing else. Nothing is stored: the
    doctor chooses the tidied note or their own, and confirms it when closing the visit."""
    try:
        tidied = core.tidy_note(body.text)
    except ValueError:
        raise HTTPException(503, "Meddy could not tidy the note just now. Your own words are fine.")
    return {**tidied, "changed": tidied["text"] != body.text}


@app.get("/api/staff")
def staff():
    return db.staff()


@app.post("/api/staff/duty")
def duty(body: Duty):
    if body.reg_no == "DR-1000":
        raise HTTPException(400, "The resuscitation team is always on duty.")
    if not db.set_duty(body.reg_no, body.on_duty):
        raise HTTPException(404, "No doctor with this registration number.")
    return db.staff()


@app.post("/api/doctor-choices")
def doctor_choices(body: Matching):
    """The doctors the nurse can choose from, asked for again while she reads the report: who is free now."""
    match = {name: value for name, value in body.match.items() if name in core.DEPARTMENT_NAMES and 0 <= value <= 1}
    return db.doctor_choices(match)


@app.post("/api/staff/simulation")
def simulation(body: Simulation):
    """Switch the simulated rest of the hospital on or off (made-up patients coming to the doctors and leaving)."""
    db.set_simulation(body.on)
    return db.staff()


@app.post("/api/reset")
def reset():
    """"Start a new day": open visits are closed and kept, the made-up patients are back. Nothing is deleted and
    registration numbers go on counting (see db.new_day)."""
    closed = db.new_day()
    return {**closed, **db.line()}


@app.get("/api/visit/{reg_no}/history")
def visit_history(reg_no: str):
    """Every step of one visit with its time and who did it: staff only, like the nurse's and doctor's screens."""
    steps = db.history(reg_no)
    if steps is None:
        raise HTTPException(404, "No visit with this registration number.")
    return {"reg_no": reg_no, "steps": steps}


@app.get("/api/events")
def events(limit: int = 20):
    """The newest steps in the whole department, for the nurse's "behind the scenes" panel."""
    return {"events": db.recent_events(max(1, min(limit, 200)))}


@app.get("/api/address")
def address():
    """Where a phone can open the hospital, for the QR code: the online address when there is one,
    otherwise this computer on the same Wi-Fi when started with MEDDY_LAN=1, otherwise None."""
    if PUBLIC_URL:
        return {"url": PUBLIC_URL}
    ip = lan_ip() if LAN else None
    return {"url": f"http://{ip}:{PORT}/" if ip else None}


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if __name__ == "__main__":
    # The database is kept between starts: everything from earlier is still there ("Start a new day" closes the
    # open visits). Online, a copy goes to lasting storage every few seconds (see db.start_keeping).
    db.start_keeping()
    db.start_simulation()
    online = LAN or PUBLIC_URL
    uvicorn.run(app, host="0.0.0.0" if online else "127.0.0.1", port=PORT, log_level="warning")
