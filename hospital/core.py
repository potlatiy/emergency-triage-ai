"""The logic behind the digital hospital. No web code here, so it can be tested on its own.

What is here, and what uses AI:
1. questionnaire - danger-sign questions from a hand-written list, plus two extra questions written
                   by a local language model for the exact complaint
2. triage        - the urgency level, from the model tested in the notebook
3. line order    - who is seen next (plain rules)
4. department    - which department a complaint belongs to (plain rules)
The doctors, the registration numbers and the line itself live in the database (db.py).
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
    ("Respiratory medicine", r"dyspnea|breath|cough|asthma|sputum|hemoptysis|wheez"),
    ("Neurology", r"headache|dizz|vertigo|weakness|mental|seizure|syncope|numb|dysarthria|faint|stroke|confus"),
    ("Trauma and orthopaedics", r"wound|fractur|lacerat|burn|injur|sprain|trauma|bite|fall|fell|broke|twist|cut |bleed|"
                                r"ankle|knee|wrist|shoulder|arm |leg |back pain|hip|finger|foot|hand "),
    ("Gastroenterology and surgery", r"abd|epigastr|stomach|belly|bowel|vomit|diarrh|nausea|melena|hemateme|stool|"
                                     r"flank|constipat|ruq|rlq"),
    ("Eye, ear, nose and throat", r"eye|ocular|visual|vision|ear |throat|nose|epistaxis|foreign body"),
]
DEFAULT_DEPARTMENT = "General medicine"

def department_for(complaint, injury=False):
    text = f" {(complaint or '').lower()} "
    for name, pattern in DEPARTMENTS:
        if re.search(pattern, text):
            return name
    return "Trauma and orthopaedics" if injury else DEFAULT_DEPARTMENT


# ---------------------------------------------------------------- the questionnaire

# Part 1 of every questionnaire: danger-sign questions, written by hand for each kind of complaint.
# For every one of them "yes" means more danger, so a yes can safely move the patient up.
DANGER_QUESTIONS = {
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

_EXTRA_PROMPT = """A patient arrives at an emergency department and says: "{complaint}"

The nurse has already asked these questions:
{asked}

Write exactly 2 more short questions that the doctor will want answered about THIS complaint
(for example when it started, what makes it worse, medicines taken, whether it happened before).
Rules: plain everyday words, each under 14 words, each answerable in a few words, do not repeat the questions above."""

_EXTRA_SCHEMA = {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "string"},
                                                               "minItems": 2, "maxItems": 2}},
                 "required": ["questions"]}


def questions_from_model(complaint, asked, timeout=25):
    """Two extra questions written for this exact complaint by the local language model."""
    prompt = _EXTRA_PROMPT.format(complaint=complaint, asked="\n".join("- " + q for q in asked))
    body = {"model": OLLAMA_MODEL, "stream": False, "format": _EXTRA_SCHEMA,
            "messages": [{"role": "user", "content": prompt}], "options": {"temperature": 0.2, "num_ctx": 1024}}
    request = urllib.request.Request(OLLAMA_URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        answer = json.loads(json.loads(response.read())["message"]["content"])
    questions = [q.strip() for q in answer.get("questions", []) if isinstance(q, str) and q.strip().endswith("?")]
    if len(questions) != 2:
        raise ValueError("the model did not return two questions")
    return questions


def questionnaire(complaint, injury=False, use_model=True):
    """Build the check-in questionnaire for one complaint.

    - "danger" questions: picked by the kind of complaint from the hand-written list. A yes counts.
    - "extra" questions: written by the AI for this exact complaint. Their answers go to the doctor
      as information only, because nobody has checked that a yes always means the same thing.
    """
    department = department_for(complaint, injury)
    danger = DANGER_QUESTIONS[department]
    extra, source = [], "list"
    if use_model and complaint:
        try:
            extra, source = questions_from_model(complaint, danger), "ai"
        except Exception:
            pass                      # model switched off or too slow: the danger questions still work
    return {"topic": department, "source": source,
            "questions": [{"question": q, "kind": "danger"} for q in danger] +
                         [{"question": q, "kind": "extra"} for q in extra]}


# ---------------------------------------------------------------- the electronic line (plain rules)

def place(patient, clock):
    """Sorting key, smaller first. Levels 1-2 always lead.
    Waiting lifts a level 3-5 patient by one level every 45 minutes."""
    waited = clock - patient["arrived"]
    if patient["level"] <= 2:
        return (0, patient["level"], patient["arrived"])
    # A yes to a danger-sign question counts as half a level more urgent.
    return (1, patient["level"] - waited / MOVE_UP_EVERY - (0.5 if patient.get("danger") else 0), patient["arrived"])
