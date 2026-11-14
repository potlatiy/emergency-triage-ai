"""The digital hospital's web server.

Run:   python hospital/server.py      then open http://localhost:8000
       MEDDY_LAN=1 python hospital/server.py   also lets phones on the same Wi-Fi open it (the QR code
                                               on the entrance step then points to this computer)

It serves the walk-through page (static/index.html), the doctor's screen at /doctor (static/doctor.html)
and a small API the pages talk to.
Everything is kept in the internal database, hospital.db (see db.py).
"""

import os
import socket
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

import core
import db

STATIC = Path(__file__).parent / "static"
PORT = int(os.environ.get("MEDDY_PORT", 8000))
# Off by default: the demo stays on this computer unless someone starts it with MEDDY_LAN=1.
LAN = os.environ.get("MEDDY_LAN") == "1"
# Set when the hospital runs online (see deploy/huggingface): the QR code then points to this address.
PUBLIC_URL = os.environ.get("MEDDY_PUBLIC_URL") or None
app = FastAPI(title="Digital hospital")


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


class Answer(BaseModel):
    question: str = Field(max_length=300)
    answer: str | None = Field(default=None, max_length=200)
    kind: str = Field(max_length=20)
    topic: str | None = Field(default=None, max_length=30)   # the history topic, for the doctor's overview


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
    vitals: Literal["normal", "feverish", "unstable"] = "normal"       # one of core.VITAL_PRESETS
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


class Referral(BaseModel):
    # Spaces are trimmed before the length check, so a reason made only of spaces is refused.
    model_config = ConfigDict(str_strip_whitespace=True)
    reg_no: str
    level: int = Field(ge=1, le=5)
    reason: str = Field(min_length=3, max_length=300)       # required: the doctor must know why
    department: str | None = None


class Duty(BaseModel):
    reg_no: str
    on_duty: bool


class Finished(BaseModel):
    reg_no: str


def open_visit(reg_no):
    """The visit for a step at the desk or the nurse station. Refused when the registration number is unknown
    (404) or the patient has already gone to a doctor (409), so no step can be done twice or out of order."""
    visit = db.visit_for(reg_no)
    if visit is None:
        raise HTTPException(404, "No visit with this registration number.")
    if visit["status"] in ("with doctor", "done"):
        raise HTTPException(409, "This patient has already been sent to a doctor.")
    return visit


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
        reg_no = db.register(body.complaint, body.age, body.sex, body.insurance, body.card)
    except db.AlreadyRegistered as already:
        # One person, one open visit. The answer points to the visit that is already open.
        raise HTTPException(409, {"message": "This card already has an open visit. One person, one visit at a time.",
                                  **already.visit})
    return {"reg_no": reg_no, "day": db.day(), **core.questionnaire(body.complaint, use_model=body.use_model)}


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
    open_visit(body.reg_no)
    try:
        code = db.check_in(body.reg_no, body.arrival, body.injury, body.pain, [a.model_dump() for a in body.answers])
    except db.AlreadyWithNurse:
        raise HTTPException(409, "The nurse has already seen this patient.")
    except db.AlreadySent:
        raise HTTPException(409, "This patient has already been sent to a doctor.")
    if code is None:
        raise HTTPException(404, "No visit with this registration number.")
    return {"reg_no": body.reg_no, "code": code, "status": "nurse"}


@app.get("/api/nurse/patients")
def nurse_patients():
    """The nurse's list: patients who finished check-in and wait for her, danger signs first."""
    return db.nurse_patients()


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
    vitals = core.VITAL_PRESETS[body.vitals]
    answers = [a.model_dump() for a in body.answers]
    db.save_answers(body.reg_no, answers, body.injury)
    result = core.assess(visit["complaint"], visit["age"], visit["sex"], body.arrival, body.injury, body.alert,
                         body.pain, vitals)
    # Only the hand-written danger questions can mark a patient; AI-written ones are information only.
    result["danger"] = any(a["kind"] == "danger" and a["answer"] == "Yes" for a in answers)
    result["vitals"] = vitals
    # Meddy's report for the nurse: can she accept it after a quick look, and who would the doctor be?
    result["advice"] = core.review_advice(result, result["danger"])
    for option in result["department_options"]:
        option["doctor"] = db.likely_doctor(result["level"], option["department"])
    result["likely_doctor"] = db.likely_doctor(result["level"], result["department"])
    # Keep the AI's suggestion and the measurements: the doctor's screen and the test regime need them later.
    db.save_assessment(body.reg_no, result["level"], vitals, body.alert, body.pain,
                       [result["probabilities"][str(l)] for l in core.LEVELS],
                       result["department"], result["advice"]["quick"])
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
        ticket, called = db.admit(body.reg_no, body.level, body.danger, body.department, body.department_how, body.review)
    except db.AlreadySent:
        raise HTTPException(409, "This patient has already been sent to a doctor.")
    if called:
        called["answers"] = db.answers_for(called["reg_no"])
    return {"ticket": ticket, "called": called, **db.line()}


@app.get("/api/line")
def line():
    return db.line()


@app.get("/api/me/{reg_no}")
def me(reg_no: str):
    """The patient's phone: ticket, place in line and doctor. Never the urgency level (see db.where_is)."""
    status = db.where_is(reg_no)
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
        return db.refer(body.reg_no, body.level, body.reason, body.department)
    except db.AlreadySent:
        raise HTTPException(409, "This patient has already been sent to a doctor.")


@app.get("/api/doctor/{doctor_reg}/patients")
def doctor_patients(doctor_reg: str):
    """The doctor's screen: every patient now with this doctor, with the full card."""
    screen = db.doctor_patients(doctor_reg)
    if screen is None:
        raise HTTPException(404, "No doctor with this registration number.")
    return screen


@app.post("/api/doctor/finish")
def finish(body: Finished):
    """The doctor closes a visit. The patient leaves the doctor's list, which gets one shorter."""
    if db.visit_for(body.reg_no) is None:
        raise HTTPException(404, "No visit with this registration number.")
    closed = db.finish_visit(body.reg_no)
    if closed is None:
        raise HTTPException(409, "This patient is not with a doctor.")
    return closed


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


@app.post("/api/reset")
def reset():
    db.reset()
    return db.line()


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
    db.reset()                 # every start of the server begins a fresh demonstration day
    online = LAN or PUBLIC_URL
    uvicorn.run(app, host="0.0.0.0" if online else "127.0.0.1", port=PORT, log_level="warning")
