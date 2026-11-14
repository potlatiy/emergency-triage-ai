"""The digital hospital's web server.

Run:   python hospital/server.py      then open http://localhost:8000

It serves one page (static/index.html) and a small API the page talks to.
All patient data stays in memory and is gone when the server stops.
"""

import copy
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import core

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Digital hospital")

# One shared waiting room and one staff list for the demonstration.
state = {"line": core.sample_line(), "staff": copy.deepcopy(core.DOCTORS)}


class Complaint(BaseModel):
    complaint: str
    injury: bool = False
    use_model: bool = True


class Patient(BaseModel):
    complaint: str
    age: float
    sex: str = "Female"
    arrival: str = "Walked in"
    injury: bool = False
    alert: str = "Alert"
    pain: int = 0
    vitals: str = "normal"          # one of core.VITAL_PRESETS
    danger: bool = False            # the patient answered yes to a danger-sign question


class Joining(BaseModel):
    complaint: str
    level: int
    danger: bool = False
    injury: bool = False


@app.get("/")
def page():
    return FileResponse(STATIC / "index.html")


@app.post("/api/questions")
def questions(body: Complaint):
    return core.follow_up_questions(body.complaint, body.injury, body.use_model)


@app.post("/api/triage")
def triage(body: Patient):
    result = core.assess(body.complaint, body.age, body.sex, body.arrival, body.injury, body.alert,
                         body.pain, core.VITAL_PRESETS.get(body.vitals, core.VITAL_PRESETS["normal"]))
    result["danger"] = body.danger
    result["vitals"] = core.VITAL_PRESETS.get(body.vitals, core.VITAL_PRESETS["normal"])
    return result


@app.post("/api/line/join")
def join(body: Joining):
    ticket = core.join(state["line"], body.complaint, body.level, body.danger, you=True)
    return {"ticket": ticket, **core.view(state["line"])}


@app.get("/api/line")
def line():
    return core.view(state["line"])


@app.post("/api/line/wait")
def wait():
    state["line"]["clock"] += 15
    return core.view(state["line"])


@app.post("/api/line/next")
def call_next():
    """Call the first patient in line and send them to a doctor."""
    patient = core.call_next(state["line"])
    if patient is None:
        return {"called": None, **core.view(state["line"])}
    doctor = core.assign(patient["who"], patient["level"], staff=state["staff"])
    return {"called": {**patient, **doctor}, **core.view(state["line"])}


@app.post("/api/reset")
def reset():
    state["line"] = core.sample_line()
    state["staff"] = copy.deepcopy(core.DOCTORS)
    return core.view(state["line"])


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
