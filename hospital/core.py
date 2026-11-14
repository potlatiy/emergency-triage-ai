"""The logic behind the digital hospital. No web code here, so it can be tested on its own.

Four jobs, and only the first two use AI:
1. questions   - follow-up questions for the patient (local language model, with a fixed fallback list)
2. triage      - the urgency level, from the model tested in the notebook
3. line        - who is seen next (plain rules)
4. doctors     - which department and doctor (plain rules)
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from triage import LEVELS, NUMBERS, TEXT, load, make_model, safety_first  # noqa: E402

LEVEL_NAMES = {1: "Resuscitation", 2: "Emergency", 3: "Urgent", 4: "Less urgent", 5: "Non-urgent"}
SURE_ENOUGH = 0.60
MOVE_UP_EVERY = 45          # minutes of waiting that lift a level 3-5 patient by one level
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma3:4b"

# ---------------------------------------------------------------- the urgency model

_data = load()
_model = make_model().fit(_data, _data["KTAS_expert"])     # demo model, trained on all 1,267 patients
_BUSY = float(_data["Patients number per hour"].median())

VITAL_PRESETS = {
    "normal": {"SBP": 124, "DBP": 78, "HR": 78, "RR": 16, "BT": 36.6, "Saturation": 98},
    "feverish": {"SBP": 132, "DBP": 82, "HR": 108, "RR": 22, "BT": 38.9, "Saturation": 96},
    "unstable": {"SBP": 88, "DBP": 54, "HR": 128, "RR": 28, "BT": 36.4, "Saturation": 89},
}


def assess(complaint, age, sex="Female", arrival="Walked in", injury=False, alert="Alert", pain=0, vitals=None):
    """Suggested urgency level for one patient, with the probability of each level."""
    vitals = {**VITAL_PRESETS["normal"], **(vitals or {})}
    row = pd.DataFrame([{
        TEXT: (complaint or "").lower(),
        "Age": age,
        "Sex": 1 if sex == "Female" else 2,
        "Arrival mode": {"Walked in": 1, "Public ambulance": 2, "Private car": 3, "Private ambulance": 4}.get(arrival, 1),
        "Injury": 2 if injury else 1,
        "Mental": {"Alert": 1, "Responds to voice": 2, "Responds to pain": 3, "Unresponsive": 4}.get(alert, 1),
        "Pain": int(pain > 0),
        "NRS_pain": pain,
        "Patients number per hour": _BUSY,
        **vitals,
        "unmeasurable": 0,
    }]).astype({c: float for c in NUMBERS})
    probabilities = _model.predict_proba(row)[0]
    level = int(safety_first(probabilities[None, :])[0])
    return {
        "level": level,
        "name": LEVEL_NAMES[level],
        "probabilities": {str(l): round(float(p), 3) for l, p in zip(LEVELS, probabilities)},
        "unsure": bool(probabilities.max() < SURE_ENOUGH),
    }


# ---------------------------------------------------------------- department and doctor (plain rules)

# Each department: the words in a complaint that send a patient there. First match wins.
DEPARTMENTS = [
    ("Cardiology", r"chest|palpitat|heart|흉통"),
    ("Respiratory medicine", r"dyspnea|breath|cough|asthma|sputum|hemoptysis"),
    ("Neurology", r"headache|dizz|vertigo|weakness|mental|seizure|syncope|numb|dysarthria|faint"),
    ("Trauma and orthopaedics", r"wound|fractur|lacerat|burn|injur|sprain|trauma|bite|fall|ankle|knee|wrist|"
                                r"shoulder|arm |leg |back pain|hip|finger|foot|hand "),
    ("Gastroenterology and surgery", r"abd|epigastr|vomit|diarrh|nausea|melena|hemateme|stool|flank|constipat|ruq|rlq"),
    ("Eye, ear, nose and throat", r"eye|ocular|visual|ear |throat|nose|epistaxis|foreign body"),
]
DEFAULT_DEPARTMENT = "General medicine"

# Made-up staff for the demonstration. "waiting" is how many patients each already has.
DOCTORS = {
    "Resuscitation team": [{"name": "Resuscitation team", "room": "Resus bay", "waiting": 0}],
    "Cardiology": [{"name": "Dr. Marlow", "room": "Room 1", "waiting": 2}, {"name": "Dr. Okafor", "room": "Room 2", "waiting": 0}],
    "Respiratory medicine": [{"name": "Dr. Lindqvist", "room": "Room 3", "waiting": 1}],
    "Neurology": [{"name": "Dr. Haddad", "room": "Room 4", "waiting": 1}, {"name": "Dr. Petrov", "room": "Room 5", "waiting": 3}],
    "Trauma and orthopaedics": [{"name": "Dr. Castell", "room": "Room 6", "waiting": 2}, {"name": "Dr. Nwosu", "room": "Room 7", "waiting": 1}],
    "Gastroenterology and surgery": [{"name": "Dr. Baptiste", "room": "Room 8", "waiting": 0}],
    "Eye, ear, nose and throat": [{"name": "Dr. Sorensen", "room": "Room 9", "waiting": 1}],
    "General medicine": [{"name": "Dr. Achebe", "room": "Room 10", "waiting": 2}, {"name": "Dr. Ferrand", "room": "Room 11", "waiting": 1}],
}


def department_for(complaint, injury=False):
    text = f" {(complaint or '').lower()} "
    for name, pattern in DEPARTMENTS:
        if re.search(pattern, text):
            return name
    return "Trauma and orthopaedics" if injury else DEFAULT_DEPARTMENT


def assign(complaint, level, injury=False, staff=DOCTORS):
    """Levels 1-2 go to the resuscitation team. Everyone else goes to the right department's
    least busy doctor."""
    department = "Resuscitation team" if level <= 2 else department_for(complaint, injury)
    doctor = min(staff[department], key=lambda d: d["waiting"])
    doctor["waiting"] += 1
    reason = ("Level 1 or 2: straight to the resuscitation team." if level <= 2 else
              f"The complaint points to {department.lower()}; {doctor['name']} has the shortest list there.")
    return {"department": department, "doctor": doctor["name"], "room": doctor["room"], "reason": reason}


# ---------------------------------------------------------------- follow-up questions

# Used when the local language model is switched off or too slow. Written by hand.
QUESTION_BANK = {
    "Cardiology": ["Does the pain spread to your arm, jaw or back?", "Are you short of breath or sweating?",
                   "Did it start suddenly in the last hour?"],
    "Respiratory medicine": ["Is it hard to speak a full sentence?", "Are your lips or fingers turning blue?",
                             "Did the breathing problem start suddenly?"],
    "Neurology": ["Is one side of your face, arm or leg weak or numb?", "Is your speech slurred or confused?",
                  "Is this the worst headache of your life?"],
    "Trauma and orthopaedics": ["Is there heavy bleeding that will not stop?", "Did you hit your head or pass out?",
                                "Is the limb numb, cold or out of shape?"],
    "Gastroenterology and surgery": ["Have you vomited blood or passed black stool?", "Is your belly hard and very tender?",
                                     "Have you been unable to keep fluids down all day?"],
    "Eye, ear, nose and throat": ["Did you suddenly lose vision?", "Did a chemical get into your eye?",
                                  "Are you having trouble swallowing or breathing?"],
    "General medicine": ["Do you have a high fever with a stiff neck or a rash?", "Have you fainted or felt close to fainting?",
                         "Do you have a long-term illness such as diabetes or heart disease?"],
}

_QUESTION_PROMPT = """A patient arrives at an emergency department and says: "{complaint}"

Write exactly 3 short yes/no questions a triage nurse would ask to find out if this is dangerous.
Rules: plain everyday words a worried person understands, one question per line, each under 14 words,
each must be answerable with yes or no, and a "yes" must always mean MORE danger."""

_QUESTION_SCHEMA = {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "string"},
                                                                  "minItems": 3, "maxItems": 3}},
                    "required": ["questions"]}


def questions_from_model(complaint, timeout=25):
    body = {"model": OLLAMA_MODEL, "stream": False, "format": _QUESTION_SCHEMA,
            "messages": [{"role": "user", "content": _QUESTION_PROMPT.format(complaint=complaint)}],
            "options": {"temperature": 0.2, "num_ctx": 1024}}
    request = urllib.request.Request(OLLAMA_URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        answer = json.loads(json.loads(response.read())["message"]["content"])
    questions = [q.strip() for q in answer.get("questions", []) if isinstance(q, str) and q.strip().endswith("?")]
    if len(questions) != 3:
        raise ValueError("the model did not return three questions")
    return questions


def follow_up_questions(complaint, injury=False, use_model=True):
    """Three yes/no danger-sign questions. Tries the local language model, falls back to the fixed list."""
    if use_model and complaint:
        try:
            return {"questions": questions_from_model(complaint), "source": "ai"}
        except Exception:
            pass
    return {"questions": QUESTION_BANK[department_for(complaint, injury)], "source": "list"}


# ---------------------------------------------------------------- the electronic line (plain rules)

def new_line():
    return {"clock": 0, "next": 1, "patients": []}


def place(patient, clock):
    """Sorting key, smaller first. Levels 1-2 always lead. A reported danger sign leads within a level.
    Waiting lifts a level 3-5 patient by one level every 45 minutes."""
    waited = clock - patient["arrived"]
    if patient["level"] <= 2:
        return (0, patient["level"], 0, patient["arrived"])
    return (1, patient["level"] - waited / MOVE_UP_EVERY, 0 if patient.get("danger") else 1, patient["arrived"])


def ordered(line):
    return sorted(line["patients"], key=lambda p: place(p, line["clock"]))


def join(line, who, level, danger=False, you=False):
    ticket = f"A{line['next']:03d}"
    line["patients"].append({"ticket": ticket, "who": who, "level": level, "danger": bool(danger),
                             "you": bool(you), "arrived": line["clock"]})
    line["next"] += 1
    return ticket


def call_next(line):
    queue = ordered(line)
    if not queue:
        return None
    first = queue[0]
    line["patients"] = [p for p in line["patients"] if p["ticket"] != first["ticket"]]
    return first


def view(line):
    return {"clock": line["clock"], "patients": [
        {**p, "position": i, "waited": line["clock"] - p["arrived"], "name": LEVEL_NAMES[p["level"]]}
        for i, p in enumerate(ordered(line), 1)]}


SAMPLE_PATIENTS = [("sore throat", 5), ("ankle pain", 4), ("abd pain", 3), ("headache", 4), ("dizziness", 3)]


def sample_line():
    """A waiting room that already has five made-up patients in it."""
    line = new_line()
    for minutes_ago, (who, level) in zip([40, 31, 22, 14, 6], SAMPLE_PATIENTS):
        line["clock"] = 40 - minutes_ago
        join(line, who, level)
    line["clock"] = 40
    return line
