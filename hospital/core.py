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
import trained_models  # noqa: E402
from triage import LEVELS, NUMBERS, TEXT, load, safety_first  # noqa: E402

LEVEL_NAMES = {1: "Resuscitation", 2: "Emergency", 3: "Urgent", 4: "Less urgent", 5: "Non-urgent"}
SURE_ENOUGH = 0.60
MOVE_UP_EVERY = 45          # minutes of waiting that lift a level 3-5 patient by one level
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma3:4b"

# ---------------------------------------------------------------- the urgency model

_data = load()
# Both models were trained on all 1,267 patients and saved in models/ (see trained_models.py).
# The saved files are loaded when they fit this computer; otherwise the models are trained again here.
# The assignment model learned from each real patient's final diagnosis (see assignment.py).
_model, _assignment = trained_models.ready()
_BUSY = float(_data["Patients number per hour"].median())
# The model was trained on complaints as nurses write them ("ant. chest pain"). Patients typing their own
# words are less familiar to it, and at middling confidence it made odd choices in our trials. So it
# decides only when it is at least 70% sure; otherwise the key-word rule decides.
ASSIGN_SURE_ENOUGH = 0.70

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
    # Which department? The trained model answers when it is sure enough, the key-word rule otherwise.
    department_odds = _assignment.predict_proba(row)[0]
    best = int(department_odds.argmax())
    if department_odds[best] >= ASSIGN_SURE_ENOUGH:
        department = str(_assignment.classes_[best])
        how = f"trained model, {department_odds[best]:.0%} sure"
    else:
        department = department_for(complaint, injury)
        how = f"key-word rule (the model was only {department_odds[best]:.0%} sure)"
    # Three departments for the nurse to choose from: the one picked above, then the model's next most likely.
    names = [str(c) for c in _assignment.classes_]
    order = [names.index(department)] + [i for i in np.argsort(-department_odds) if names[i] != department]
    return {
        "department": department,
        "department_how": how,
        "department_options": [{"department": names[i], "chance": round(float(department_odds[i]), 3)}
                               for i in order[:3]],
        "level": level,
        "name": LEVEL_NAMES[level],
        "probabilities": {str(l): round(float(p), 3) for l, p in zip(LEVELS, probabilities)},
        "unsure": bool(probabilities.max() < SURE_ENOUGH),
    }


# ---------------------------------------------------------------- department and doctor (plain rules)

# Each department: the words in a complaint that send a patient there. First match wins.
DEPARTMENTS = [
    ("Obstetrics and gynaecology", r"vagin|pregnan|menstr|period pain|pelvic|labou?r pain|miscarr"),
    ("Urology and kidney", r"urin|flank|kidney|dysuria|hematuria|testic|scrot|voiding|pee|bladder"),
    ("Cardiology", r"chest|palpitat|heart|흉통"),
    ("Respiratory medicine", r"dyspnea|breath|cough|asthma|sputum|hemoptysis|wheez"),
    ("Neurology", r"headache|dizz|vertigo|weakness|mental|seizure|syncope|numb|dysarthria|faint|stroke|confus"),
    ("Trauma and orthopaedics", r"wound|fractur|lacerat|burn|injur|sprain|trauma|bite|fall|fell|broke|twist|cut |bleed|"
                                r"ankle|knee|wrist|shoulder|arm |leg |back pain|hip|finger|foot|hand "),
    ("Gastroenterology and surgery", r"abd|epigastr|stomach|belly|bowel|vomit|diarrh|nausea|melena|hemateme|stool|"
                                     r"constipat|ruq|rlq"),
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
    "Urology and kidney": ["Are you unable to pass urine at all?", "Is there blood in your urine with a fever or shaking chills?",
                           "Is there sudden severe pain in a testicle?"],
    "Obstetrics and gynaecology": ["Are you pregnant, or could you be?", "Is the bleeding soaking a pad every hour?",
                                   "Do you have severe one-sided pain low in your belly?"],
    "General medicine": ["Do you have a high fever with a stiff neck or a rash?", "Have you fainted or felt close to fainting?",
                         "Do you have a long-term illness such as diabetes or heart disease?"],
}

# The question writer is a general language model. We do not retrain it. We steer it with this
# system prompt: fixed instructions sent before every request (its role, the rules, and examples).
QUESTION_SYSTEM_PROMPT = """You are helping at the check-in desk of a hospital emergency department.
A patient has typed what is wrong. A nurse has already asked the danger-sign questions.
Your only job is to write 2 extra questions whose answers will save the doctor time.

Rules for every question:
- It must be about THIS patient's complaint, using the patient's own words where possible.
- Plain everyday words. No medical terms. Under 14 words. One thing per question.
- It must be answerable in a few words by a worried person with no medical training.
- Ask about facts the patient knows: when it started, how it changed, what was taken for it, whether it happened before.
- Never repeat or rephrase a question the nurse already asked.
- Never suggest a diagnosis, never give advice, never tell the patient what to do, never say how serious it is.
- Never ask for a name, address, phone number or any identity detail.

Examples of good questions:
Complaint: "chest pain since this morning" -> "What were you doing when the pain started?" / "Have you had this pain before?"
Complaint: "i twisted my ankle playing football" -> "Can you put any weight on that foot?" / "How long ago did it happen?"
Complaint: "fever and cough for three days" -> "What is the highest temperature you measured?" / "Have you taken any medicine for it?"

Reply with exactly 2 questions."""

_EXTRA_PROMPT = """Complaint: "{complaint}"

The nurse has already asked:
{asked}"""

_EXTRA_SCHEMA = {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "string"},
                                                               "minItems": 2, "maxItems": 2}},
                 "required": ["questions"]}
_BANNED = re.compile(r"\b(you (should|must|need to)|diagnos|name|address|phone|call an ambulance)\b", re.I)


def questions_from_model(complaint, asked, timeout=25):
    """Two extra questions written for this exact complaint by the local language model."""
    body = {"model": OLLAMA_MODEL, "stream": False, "format": _EXTRA_SCHEMA,
            "messages": [{"role": "system", "content": QUESTION_SYSTEM_PROMPT},
                         {"role": "user", "content": _EXTRA_PROMPT.format(
                             complaint=complaint, asked="\n".join("- " + q for q in asked))}],
            "options": {"temperature": 0.2, "num_ctx": 2048}}
    request = urllib.request.Request(OLLAMA_URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        answer = json.loads(json.loads(response.read())["message"]["content"])
    questions = [q.strip() for q in answer.get("questions", []) if isinstance(q, str) and q.strip().endswith("?")]
    # A second safety net in plain code: drop anything that breaks the rules the prompt sets.
    questions = [q for q in questions if len(q.split()) <= 16 and not _BANNED.search(q)]
    if len(questions) != 2:
        raise ValueError("the model did not return two usable questions")
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
