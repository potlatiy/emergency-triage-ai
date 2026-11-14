"""The digital hospital's web server.

Run:   python hospital/server.py      then open http://localhost:8000

It serves the walk-through page (static/index.html), the doctor's screen at /doctor (static/doctor.html)
and a small API the pages talk to.
Everything is kept in the internal database, hospital.db (see db.py).
"""

from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

import core
import db

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Digital hospital")


Sex = Literal["Female", "Male"]


class Arrival(BaseModel):
    # Spaces are trimmed before the length check, so a complaint made only of spaces is refused.
    model_config = ConfigDict(str_strip_whitespace=True)
    complaint: str = Field(min_length=1, max_length=200)
    age: float = Field(ge=15, le=110)
    sex: Sex = "Female"
    use_model: bool = True


class Answer(BaseModel):
    question: str
    answer: str | None = None
    kind: str
    topic: str | None = None        # the history topic of an AI-written question, for the doctor's overview


class Assessment(BaseModel):
    # Every choice is spelled out. An unknown value is refused rather than quietly read as "normal" or "alert":
    # a silent default here would make a sick patient look well.
    reg_no: str
    complaint: str
    age: float
    sex: Sex = "Female"
    arrival: Literal["Walked in", "Public ambulance", "Private car", "Private ambulance"] = "Walked in"
    injury: bool = False
    alert: Literal["Alert", "Responds to voice", "Responds to pain", "Unresponsive"] = "Alert"
    pain: int = Field(default=0, ge=0, le=10)
    vitals: Literal["normal", "feverish", "unstable"] = "normal"       # one of core.VITAL_PRESETS
    answers: list[Answer] = []


class Joining(BaseModel):
    reg_no: str
    level: int = Field(ge=1, le=5)
    danger: bool = False
    department: str | None = None
    department_how: str | None = None


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


@app.post("/api/register")
def register(body: Arrival):
    """The patient arrives: give the visit a registration number and build its questionnaire."""
    reg_no = db.register(body.complaint.strip(), body.age, body.sex)
    return {"reg_no": reg_no, **core.questionnaire(body.complaint, use_model=body.use_model)}


@app.get("/api/follow-up/{reg_no}")
def follow_up(reg_no: str):
    """The optional questions for the doctor, written by the language model for this visit's complaint.

    Each has a history topic and short answers to tap. The page asks for them after registering, so the
    patient never waits for the model. The list is empty when the model is switched off."""
    visit = db.visit_for(reg_no)
    if visit is None:
        raise HTTPException(404, "No visit with this registration number.")
    return core.follow_up(visit["complaint"], bool(visit["injury"]))


@app.post("/api/triage")
def triage(body: Assessment):
    open_visit(body.reg_no)
    vitals = core.VITAL_PRESETS[body.vitals]
    answers = [a.model_dump() for a in body.answers]
    db.save_answers(body.reg_no, answers, body.injury)
    result = core.assess(body.complaint, body.age, body.sex, body.arrival, body.injury, body.alert, body.pain, vitals)
    # Only the hand-written danger questions can mark a patient; AI-written ones are information only.
    result["danger"] = any(a["kind"] == "danger" and a["answer"] == "Yes" for a in answers)
    result["vitals"] = vitals
    # Keep the AI's suggestion and the measurements: the doctor's screen needs them later.
    db.save_assessment(body.reg_no, result["level"], vitals, body.alert, body.pain)
    return result


@app.post("/api/line/join")
def join(body: Joining):
    """The nurse's level puts the patient in the line. Levels 1 and 2 do not wait there: the patient is sent
    to the resuscitation team at once, and "called" in the answer says where they went."""
    open_visit(body.reg_no)
    known_department(body.department)
    ticket = db.join_line(body.reg_no, body.level, body.danger, body.department, body.department_how)
    called = db.call_next(body.reg_no) if body.level <= 2 else None
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
    db.wait(15)
    return db.line()


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
    return db.refer(body.reg_no, body.level, body.reason, body.department)


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


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if __name__ == "__main__":
    db.reset()                 # every start of the server begins a fresh demonstration day
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
