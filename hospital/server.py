"""The digital hospital's web server.

Run:   python hospital/server.py      then open http://localhost:8000

It serves one page (static/index.html) and a small API the page talks to.
Everything is kept in the internal database, hospital.db (see db.py).
"""

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import core
import db

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Digital hospital")


class Arrival(BaseModel):
    complaint: str = Field(min_length=1, max_length=200)
    age: float = Field(ge=15, le=110)
    sex: str = "Female"
    use_model: bool = True


class Answer(BaseModel):
    question: str
    answer: str | None = None
    kind: str


class Assessment(BaseModel):
    reg_no: str
    complaint: str
    age: float
    sex: str = "Female"
    arrival: str = "Walked in"
    injury: bool = False
    alert: str = "Alert"
    pain: int = Field(default=0, ge=0, le=10)
    vitals: str = "normal"                  # one of core.VITAL_PRESETS
    answers: list[Answer] = []


class Joining(BaseModel):
    reg_no: str
    level: int = Field(ge=1, le=5)
    danger: bool = False


class Duty(BaseModel):
    reg_no: str
    on_duty: bool


@app.get("/")
def page():
    return FileResponse(STATIC / "index.html")


@app.post("/api/register")
def register(body: Arrival):
    """The patient arrives: give the visit a registration number and build its questionnaire."""
    reg_no = db.register(body.complaint.strip(), body.age, body.sex)
    return {"reg_no": reg_no, **core.questionnaire(body.complaint, use_model=body.use_model)}


@app.post("/api/triage")
def triage(body: Assessment):
    vitals = core.VITAL_PRESETS.get(body.vitals, core.VITAL_PRESETS["normal"])
    answers = [a.model_dump() for a in body.answers]
    db.save_answers(body.reg_no, answers, body.injury)
    result = core.assess(body.complaint, body.age, body.sex, body.arrival, body.injury, body.alert, body.pain, vitals)
    # Only the hand-written danger questions can mark a patient; AI-written ones are information only.
    result["danger"] = any(a["kind"] == "danger" and a["answer"] == "Yes" for a in answers)
    result["vitals"] = vitals
    return result


@app.post("/api/line/join")
def join(body: Joining):
    ticket = db.join_line(body.reg_no, body.level, body.danger)
    return {"ticket": ticket, **db.line()}


@app.get("/api/line")
def line():
    return db.line()


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


@app.get("/api/staff")
def staff():
    return db.staff()


@app.post("/api/staff/duty")
def duty(body: Duty):
    if body.reg_no == "DR-1000":
        raise HTTPException(400, "The resuscitation team is always on duty.")
    db.set_duty(body.reg_no, body.on_duty)
    return db.staff()


@app.post("/api/reset")
def reset():
    db.reset()
    return db.line()


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if __name__ == "__main__":
    db.reset()                 # every start of the server begins a fresh demonstration day
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
