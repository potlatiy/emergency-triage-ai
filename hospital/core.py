"""The logic behind the digital hospital. No web code here, so it can be tested on its own.

What is here, and what uses AI:
1. questionnaire - danger-sign questions from a hand-written list, plus optional history questions that a
                   local language model chooses and words for the exact complaint
2. triage        - the urgency level, from the model tested in the notebook
3. line order    - who is seen next (plain rules)
4. department    - which department a complaint belongs to (a trained model when it is sure, plain rules otherwise)
The doctors, the registration numbers and the line itself live in the database (db.py).
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import trained_models  # noqa: E402
from triage import (LEVELS, NUMBERS, QUICK_SURE, TEXT, flag_strength, load, safety_first,  # noqa: E402
                    urgency_gap_score, warning_signs)

LEVEL_NAMES = {1: "Resuscitation", 2: "Emergency", 3: "Urgent", 4: "Less urgent", 5: "Non-urgent"}
SURE_ENOUGH = 0.60
MOVE_UP_EVERY = 45          # minutes of waiting that lift a level 3-5 patient by one level
OLLAMA_URL = "http://localhost:11434/api/chat"
# The online copy runs on a plain processor, so it can choose a smaller Gemma and wait longer for it.
OLLAMA_MODEL = os.environ.get("MEDDY_OLLAMA_MODEL", "gemma3:4b")
OLLAMA_TIMEOUT = int(os.environ.get("MEDDY_OLLAMA_TIMEOUT", 20))

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


# ---------------------------------------------------------------- can Meddy read this complaint?
# The model reads letter patterns, so it gives a confident answer even to a complaint it cannot understand
# (another language, typed nonsense, or no symptom named). To warn the nurse we look for at least one word
# Meddy knows as a symptom or a body part: a word nurses wrote in the training complaints, a word from the
# department rules below, or an everyday word from this list.
_EVERYDAY = set("""
pain hurt ache sore burn sting itch rash swollen swelling bleed blood bruise
breath breathe cough wheeze choke chest heart stomach belly tummy head throat nose ear eye mouth tooth teeth
neck back arm leg hand foot feet finger toe knee ankle wrist shoulder elbow hip skin bone
fever temperature shiver chill sweat dizzy faint weak tired numb tingling
sick vomit nausea diarrhea diarrhoea constipated pee urine poop stool period pregnant
fell fall hit cut twist broke broken sprain injure injury accident crash bit bite bitten stung
seizure shaking confused unconscious collapse vision blurry blind hearing swallow sleep
allergy allergic lump wound infection infected pressure tight cramp""".split())
_NOT_A_SYMPTOM = set("""a an and are as at be both but by can for from had has have her him his how if in into is it its
me my no not of on or our out she since so than that the their them then there these they this to too up was we
were what when where which who why will with you your after before about more very still some any just only also
day days week weeks hour hours month months year years today yesterday morning night ago last left right side old
both area pt am do go he us ok hi oh im ive id did get got feel feeling felt bad really
type like free open high low status site problem general wall nos state light near post whole upper lower
change changes finding film copy lab idea sense sudden adult attempt activity""".split())


def _words(text):
    """The words of a complaint, in any alphabet, two letters or more (nurses write "ha" for headache)."""
    return [w for w in re.findall(r"[^\W\d_]+", (text or "").lower()) if len(w) >= 2]


_NURSE_WORDS = {w for text in _data[TEXT].fillna("") for w in _words(text)} - _NOT_A_SYMPTOM


def known_words(complaint):
    """The words of a complaint that Meddy knows as a symptom or a body part.

    A word needs three letters to count. Shorter ones ("ha" for headache) count only when the whole
    complaint is written in nurses' shorthand, so that "di" in an Italian sentence does not pass."""
    def known(word):
        forms = {word, word.removesuffix("s"), word.removesuffix("es"), word.removesuffix("ed"),
                 word.removesuffix("ing"), word.removesuffix("d"), word.removesuffix("y"), word.removesuffix("ness")}
        if word.endswith("ing"):
            forms.add(word.removesuffix("ing") + "e")          # "breathing" -> "breathe"
        forms = {form for form in forms if len(form) >= 3}     # "died" must not become the shorthand "di"
        return bool(forms & _NURSE_WORDS or forms & _EVERYDAY or _SYMPTOM_RULES.search(word))
    words = [w for w in _words(complaint) if w not in _NOT_A_SYMPTOM]
    found = [w for w in words if len(w) >= 3 and known(w)]
    if not found and words and all(w in _NURSE_WORDS for w in words):
        return words
    return found


def vital_warnings(vitals, alert="Alert", pain=0):
    """The warning signs in one patient's measurements, by name. The same rule as in the tests (triage.py)."""
    missing = any(value is None for value in vitals.values())
    row = pd.DataFrame([{**vitals, "Mental": {"Alert": 1}.get(alert, 2), "NRS_pain": pain, "unmeasurable": int(missing)}])
    table = warning_signs(row.astype(float))
    return [name for name in table.columns if bool(table[name].iloc[0])]


def review_advice(result, danger=False):
    """Can the nurse accept Meddy's report after a quick look, or should she check the patient in person?

    A quick review is advised only where Meddy's level held up in our tests: Meddy is at least 80% sure,
    the level is 3 to 5 and nothing in the measurements or the answers is a warning. On such patients its
    level was too low as rarely as the nurses' (7%). A suggested level 1 or 2 is also quick: accepting it
    sends the patient in at once, which is the safe direction. The nurse decides either way."""
    sure = result.get("sure", max(result["probabilities"].values()))
    reasons = []
    if result["unread"]:
        reasons.append("Meddy found no symptom it knows in the complaint")
    if result["level"] <= 2:
        return {"quick": not reasons, "emergency": True, "reasons": reasons}
    if sure < QUICK_SURE:
        reasons.append(f"Meddy is only {sure:.0%} sure of the level")
    if danger:
        reasons.append("the patient answered yes to a danger sign")
    if result["warnings"]:
        reasons.append("warning signs: " + ", ".join(result["warnings"]))
    return {"quick": not reasons, "emergency": False, "reasons": reasons}


def second_check(probabilities, nurse_level):
    """The flag for one patient once the nurse has given her level: "strong", "mild" or None.
    The rule itself is in triage.py (flag_strength), where it is tested on the 1,267 patients."""
    if probabilities is None or nurse_level is None:
        return None
    return str(flag_strength(np.asarray(probabilities, dtype=float)[None, :], [nurse_level])[0]) or None


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
        "unmeasurable": int(any(value is None for value in vitals.values())),
    }]).astype({c: float for c in NUMBERS})
    probabilities = _model.predict_proba(row)[0]
    # Meddy shows its most likely level (right 68% of the time in our tests). The safety-first level, which
    # rates higher when in doubt, is kept for the second check: shown as the suggestion it rated 1 patient
    # in 3 too high.
    level = int(LEVELS[int(probabilities.argmax())])
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
        "sure": float(probabilities.max()),          # unrounded: the 80% rule is decided on this
        "unsure": bool(probabilities.max() < SURE_ENOUGH),
        "careful_level": int(safety_first(probabilities[None, :])[0]),
        # For every level the nurse could give: how sure Meddy is that the patient is more urgent than that,
        # and the flag it would raise ("strong", "mild" or none).
        "more_urgent": {str(l): round(float(urgency_gap_score(probabilities[None, :], [l])[0]), 3) for l in LEVELS},
        "second_check": {str(l): second_check(probabilities, l) for l in LEVELS},
        "unread": not known_words(complaint),
        "warnings": vital_warnings(vitals, alert, pain),
    }


# ---------------------------------------------------------------- department and doctor (plain rules)

# Each department: the words in a complaint that send a patient there. First match wins.
# Short stems are tied to the start of a word (\b) so they cannot match inside another one: "urin" used to
# match "during", "pee" matched "speech", "arm " matched "warm ", "ear " matched "year ", and a body part
# followed by a full stop or written in the plural ("my legs.") was missed.
DEPARTMENTS = [
    ("Obstetrics and gynaecology", r"vagin|pregnan|menstr|period pain|pelvic|labou?r pain|miscarr"),
    ("Urology and kidney", r"\burin|flank|kidney|dysuria|hematuria|testic|scrot|\bvoiding|\bpee(s|d|ing)?\b|(?<!gall)bladder"),
    ("Cardiology", r"chest|palpitat|\bheart(?!burn)|흉통"),
    ("Respiratory medicine", r"dyspnea|breath|cough|asthma|sputum|hemoptysis|wheez"),
    ("Neurology", r"headache|dizz|vertigo|weakness|mental|seizure|syncope|\bnumb(ness|ing)?\b|dysarthria|faint|\bstroke|confus|"
                  r"speech|slurr|droop"),
    ("Trauma and orthopaedics", r"wound|fractur|lacerat|\bburn|injur|sprain|trauma|\bbit(e|es|ten)\b|\bfall(s|en)?\b|"
                                r"\bfell\b|\bbroke|twist|\bcuts?\b|bleed|ankle|knee|wrist|shoulder|\b(fore)?arms?\b|"
                                r"\blegs?\b|back pain|\bhips?\b|finger|\bfoot\b|\bfeet\b|\bhands?\b"),
    ("Gastroenterology and surgery", r"abd|epigastr|stomach|belly|bowel|vomit|diarrh|nausea|melena|hemateme|stool|"
                                     r"constipat|ruq|rlq|heartburn|reflux|gall ?bladder"),
    ("Eye, ear, nose and throat", r"\beye|ocular|visual|vision|\bear(s|ache)?\b|throat|\bnose|epistaxis|foreign body"),
]
DEFAULT_DEPARTMENT = "General medicine"
DEPARTMENT_NAMES = [name for name, _ in DEPARTMENTS] + [DEFAULT_DEPARTMENT]
_SYMPTOM_RULES = re.compile("|".join(pattern for _, pattern in DEPARTMENTS))

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

# The question writer is a general language model. We do not retrain it. We steer it with a system
# prompt: fixed instructions sent before every request (its role, the rules, and an example).
#
# To give the doctor a useful first picture from only three questions, the model does not ask whatever it
# likes. It chooses three of six history topics (the things a doctor asks first), skips a topic the patient
# has already answered in the complaint, and words each question with the patient's own words.
# The answers to tap are ours, one fixed list per topic, so they always fit and the model stays quick.
TOPICS = {
    "onset": {"label": "Started", "ask": "When did it start?",
              "choices": ["Under an hour ago", "A few hours ago", "Yesterday", "Longer ago"]},
    "course": {"label": "Since then", "ask": "Is it getting better or worse?",
               "choices": ["Getting worse", "About the same", "Getting better", "Comes and goes"]},
    "trigger": {"label": "Was doing", "ask": "What were you doing when it started?",
                "choices": ["Resting", "Moving or working", "Eating or drinking", "Nothing special"]},
    "before": {"label": "Had it before", "ask": "Have you had this before?",
               "choices": ["Never", "Once or twice", "Many times"]},
    "medicine": {"label": "Taken for it", "ask": "Have you taken anything for it?",
                 "choices": ["Nothing", "A painkiller", "Another medicine"]},
    "background": {"label": "Background", "ask": "Do you have a long-term illness or take medicine every day?",
                   "choices": ["No", "A long-term illness", "Daily medicine", "Both"]},
}
FOLLOW_UPS = 3      # all optional and answered with one tap, so check-in stays quick

# Pain: we only ask what it feels like (or "No pain"). We do not ask how bad it is. The urgency model was
# trained with a 0-10 pain score, so a patient with pain gets the typical score of the patients who had pain
# in our data (their median), and a patient without pain gets 0.
PAIN_NONE = "No pain"
PAIN_TYPICAL = int(_data.loc[_data["NRS_pain"] > 0, "NRS_pain"].median())
# The answer is information for the doctor only, like the history questions.
PAIN_FEEL = {"question": "What does the pain feel like?", "kind": "extra", "topic": "feel", "label": "Pain",
             "choices": ["Sharp or stabbing", "Dull or aching", "Burning", "Pressure or tightness", "Cramping"]}


def pain_word(score):
    """What the doctor's screen says about pain. How bad it is was not asked, so only yes or no."""
    if score is None:
        return None
    return "Has pain" if score > 0 else PAIN_NONE


QUESTION_SYSTEM_PROMPT = """You help at the check-in desk of a hospital emergency department. A patient has typed what is wrong.
Choose the 3 history questions whose answers give the doctor the best first picture of THIS patient.

Each question has a topic and a fixed shape. Put the patient's own words in place of "...":
- onset: "When did ... start?"
- course: "Is ... getting better or worse?"
- trigger: "What were you doing when ... started?"
- before: "Have you had ... before?"
- medicine: "Have you taken anything for ...?"
- background: "Do you have a long-term illness or take medicine every day?"

Rules:
- Use 3 different topics. Choose what a doctor would ask first for this complaint.
- Skip a topic the complaint already answers. If it says "since this morning" or "for three days", do not use onset.
- Plain everyday words, under 14 words, no medical terms.
- Never suggest a diagnosis, never give advice, never say how serious it is, never ask for a name, address or phone number.

- Use only the allowed topics given with the complaint.
- The question must read as correct English. For "dizzy and weak" write "When did you start feeling dizzy and weak?"

Example. Complaint: "chest pain since this morning". Allowed topics: course, trigger, before, medicine, background
{"questions":[{"topic":"trigger","question":"What were you doing when the chest pain started?"},{"topic":"before","question":"Have you had this chest pain before?"},{"topic":"medicine","question":"Have you taken anything for the chest pain?"}]}"""



def _extra_schema(allowed):
    """The shape the model's answer must have. The topic can only be one of the allowed ones."""
    return {"type": "object", "required": ["questions"], "properties": {"questions": {
        "type": "array", "minItems": FOLLOW_UPS, "maxItems": FOLLOW_UPS,
        "items": {"type": "object", "required": ["topic", "question"],
                  "properties": {"topic": {"type": "string", "enum": allowed}, "question": {"type": "string"}}}}}}


# Meddy never gives a diagnosis. It suggests an urgency level and a department, and it asks questions; saying
# what is wrong with the patient is the doctor's job. The prompt tells the model so, and this check enforces
# it in plain code: a question that speaks of a diagnosis or names a condition is thrown away.
_BANNED = re.compile(r"\b(you (should|must|need to)\w*|diagnos\w*|(sur)?names?|address|(tele)?phone|call an ambulance)", re.I)
_NAMES_A_CONDITION = re.compile(
    r"\b(heart attack|heart failure|stroke|cancer|tumou?r|infection|infected|appendicitis|pneumonia|asthma\w*|"
    r"diabet\w*|covid|flu|migraine|epilep\w*|fracture|broken (bone|arm|leg|rib|wrist|ankle)|blood clot|embolism|"
    r"sepsis|meningitis|kidney stone|gallstone|ulcer|hernia|concussion|angina|arrhythmia|bronchitis)(s|es|d)?\b", re.I)
QUESTION_MAX_LETTERS = 120
# Words in a complaint that already answer a topic, so that topic is not asked again.
_SAYS_WHEN = re.compile(
    r"\b(since|ago|yesterday|today|tonight|this (morning|afternoon|evening)|last (night|week|month)|"
    r"for ((a|an|\w+|\d+) )?(minute|hour|day|week|month|year)s?)\b", re.I)
_SAYS_COURSE = re.compile(r"\b(getting (\w+ )?(worse|better)|worsening|comes and goes|on and off)\b", re.I)
# Words of a complaint that is an event (an injury or an accident) rather than an illness.
_AN_EVENT = re.compile(r"\b(fell|falls?|fallen|cut|burn(ed|t)?|scald\w*|swallow\w*|bit(ten|e)|stung|sting|crash\w*|accident|injur\w*|twist\w*|sprain\w*|welding|stabbed|wound|hit by|knocked)\b", re.I)
_written = {}       # complaints the model has already answered, so a repeated complaint is instant


# "In your own words": the one open question at check-in, typed by the patient. Information only, like the others.
OWN_WORDS = {"question": "Tell the nurse in your own words what happened", "kind": "extra", "topic": "story",
             "label": "In their own words"}


def topic_label(topic):
    if topic == PAIN_FEEL["topic"]:
        return PAIN_FEEL["label"]
    if topic == OWN_WORDS["topic"]:
        return OWN_WORDS["label"]
    return TOPICS[topic]["label"] if topic in TOPICS else None


def checked_questions(written, skip=(), complaint=""):
    """A second safety net in plain code: keep only what follows the rules the prompt sets.

    A question is dropped when its topic is unknown, repeated or already covered, or when it is not a
    short plain question. Too few usable questions is an error, and the standard ones are used."""
    kept, used = [], set(skip)
    for item in written if isinstance(written, list) else []:
        if not isinstance(item, dict):
            continue
        topic, question = item.get("topic"), str(item.get("question", "")).strip()
        if topic not in TOPICS or topic in used:
            continue
        if not question.endswith("?") or len(question.split()) > 16 or _BANNED.search(question):
            continue
        # One short question and nothing else. A statement in front of it ("This is serious. When did it
        # start?") is where advice or an opinion would hide, so any other sentence mark throws it away.
        if len(question) > QUESTION_MAX_LETTERS or re.search(r"[.!\n]|\?.", question):
            continue
        # "Could it be a heart attack?" is a diagnosis in disguise. A condition the patient named themselves
        # ("asthma attack") may be repeated back: those are the patient's words, not Meddy's opinion. It has to
        # be the same whole word: "flu" is not in "reflux".
        said = lambda name: re.search(rf"\b{re.escape(name)}\b", complaint, re.I)       # noqa: E731
        if any(not said(found.group(0)) for found in _NAMES_A_CONDITION.finditer(question)):
            continue
        kept.append({"question": question, "topic": topic})
        used.add(topic)
    if len(kept) < FOLLOW_UPS:
        raise ValueError("the model did not return enough usable questions")
    return kept[:FOLLOW_UPS]


def questions_from_model(complaint, skip=(), timeout=OLLAMA_TIMEOUT):
    """History questions for this exact complaint, chosen and worded by the local language model."""
    allowed = [t for t in TOPICS if t not in skip]
    body = {"model": OLLAMA_MODEL, "stream": False, "format": _extra_schema(allowed), "keep_alive": "30m",
            "messages": [{"role": "system", "content": QUESTION_SYSTEM_PROMPT},
                         {"role": "user", "content": f'Complaint: "{complaint}". Allowed topics: {", ".join(allowed)}'}],
            "options": {"temperature": 0.2, "num_ctx": 2048}}
    request = urllib.request.Request(OLLAMA_URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        answer = json.loads(json.loads(response.read())["message"]["content"])
    return checked_questions(answer.get("questions"), skip, complaint)


def standard_questions(complaint, skip=()):
    """The same kind of questions without the language model: fixed wording, chosen by a plain rule."""
    wanted = ["onset", "course", "medicine", "before", "trigger"]
    topics = [t for t in wanted if t not in skip][:FOLLOW_UPS]
    return [{"question": TOPICS[t]["ask"], "topic": t} for t in topics]


def already_answered(complaint, injury=False):
    """Topics not worth asking: the complaint says it already, or a danger question covers it."""
    skip = set()
    if _SAYS_WHEN.search(complaint):
        skip.add("onset")
    if _SAYS_COURSE.search(complaint):
        skip.add("course")
    if department_for(complaint, injury) == DEFAULT_DEPARTMENT:
        skip.add("background")          # general medicine's danger questions ask about long-term illness
    if injury or _AN_EVENT.search(complaint):
        # A fall, a cut, a burn or something swallowed happened once: "Have you had this cut finger before?" and
        # "Have you taken anything for the coin?" make no sense. Gemma 3 1B ignored this rule in the prompt
        # (tried on 20 complaints), so plain code keeps the two topics away from it.
        skip |= {"before", "medicine"}
    return skip


def follow_up(complaint, injury=False, use_model=True):
    """The optional history questions for the doctor, each with a topic and answers to tap.

    Written by the AI for this exact complaint when it answers in time, and the standard wording otherwise,
    so the doctor always gets the same kind of overview. The answers are information only: they never
    change the level or the place in line, because nobody has tested them."""
    skip = already_answered(complaint, injury)
    text = complaint.strip().lower()
    key = (text, frozenset(skip))             # the same words with other allowed topics need other questions
    source, questions = "list", None
    if use_model and text:
        try:
            if key not in _written:
                if len(_written) >= 500:
                    _written.clear()          # a demo never gets here; a long-running copy must not grow for ever
                _written[key] = questions_from_model(complaint, skip)
            source, questions = "ai", _written[key]
        except Exception:
            pass                      # model switched off, too slow, or broke the rules
    questions = questions or standard_questions(complaint, skip)
    return {"source": source, "questions": [
        {**q, "kind": "extra", "label": TOPICS[q["topic"]]["label"], "choices": TOPICS[q["topic"]]["choices"]}
        for q in questions]}


def questionnaire(complaint, injury=False, use_model=True):
    """Build the check-in questionnaire for one complaint.

    - "danger" questions: picked by the kind of complaint from the hand-written list. A yes counts.
    - "extra" questions: optional history questions for the doctor (see follow_up). The page asks for
      them separately, with use_model=False here, so that registration never waits for the language model.
    - "pain": what the pain feels like, the "no pain" answer, and the score the model gets when there is pain.
    """
    department = department_for(complaint, injury)
    extra = follow_up(complaint, injury) if use_model else {"source": "list", "questions": []}
    return {"topic": department, "source": extra["source"],
            "questions": [{"question": q, "kind": "danger"} for q in DANGER_QUESTIONS[department]] + extra["questions"],
            "pain": {"feel": PAIN_FEEL, "none": PAIN_NONE, "score": PAIN_TYPICAL}}


# ---------------------------------------------------------------- the nurse's summary (language model)
# When the nurse opens a patient, the language model sums up what the patient tapped at check-in in two short
# sentences. It is information only, shown next to the patient's own answers: it never sets the level, the
# department or the place in line. The same rules as for the questions hold, checked again in plain code.

SUMMARY_SYSTEM_PROMPT = """You help a triage nurse in a hospital emergency department. You get what one patient reported at check-in.
Write a summary for the nurse: exactly two short sentences, at most 40 words, in the third person ("The patient ...").

Rules:
- Use only the facts given. Add nothing. If the patient wrote something in their own words, use it.
- Never guess the cause: no "likely", "probably", "possibly", "due to", "suggests". Never name a disease or a diagnosis.
- Never give advice and never say how serious it is.
- When a question offered several things joined by "or", keep the "or": the patient said yes to one of them, not to all.
- Plain English, no lists, no headings."""
SUMMARY_MAX_WORDS = 50
# A sentence that guesses a cause is dropped: the summary repeats what the patient said, it does not explain it.
# Tried on 20 made-up patients with Gemma 3 1B: the first prompt guessed causes ("likely due to a recent injury")
# and wrote three sentences, which threw the whole summary away; this prompt and these checks kept all 20.
_GUESSES = re.compile(r"\b(likely|probably|possibly|perhaps|due to|suggest\w*|indicat\w*|may be|might be|could be)\b", re.I)
# A small model copies words from its instructions or invents them: with an example in the prompt, Gemma 3 1B told
# the nurse that a patient with knee pain had pain "spreading to the arm, jaw and back". A sentence that names a
# part of the body or a symptom the patient never mentioned is dropped.
_BODY_AND_SYMPTOMS = re.compile(r"\b(arms?|jaw|back|chest|head|neck|legs?|knees?|ankles?|feet|foot|hands?|fingers?|wrists?|hips?|shoulders?|stomach|belly|abdom\w*|eyes?|ears?|nose|throat|skin|breath\w*|bleed\w*|blood|vomit\w*|fever|faint\w*|dizz\w*|numb\w*|rash|swell\w*|swollen|cough\w*|sweat\w*|nause\w*|seiz\w*|speech|slurred|confus\w*)\b", re.I)


# How serious it is, is the nurse's call: "with no signs of a serious condition" (seen on the live site) is dropped.
_JUDGES = re.compile(r"\b(serious\w*|severity|critical|urgent\w*|emergenc\w*|stable|concern\w*|worr\w*|signs? of|nothing to|life.threatening|alarming|reassur\w*)\b", re.I)


def _without_guess(sentence):
    """The sentence with a guessed cause cut off: "Knee pain after a fall, likely due to an injury." becomes
    "Knee pain after a fall." Nothing is left when the guess was the whole sentence ("It is probably nothing.")."""
    if not _GUESSES.search(sentence):
        return sentence
    kept = re.sub(r"[,;]?\s*\b(likely|probably|possibly|perhaps|due to|suggest\w*|indicat\w*|may be|might be|could be)\b[^.!?]*",
                  "", sentence, flags=re.I).strip().rstrip(".!?,; ")
    return kept + "." if len(kept.split()) >= 3 else ""


def _grounded(sentence, report):
    """True when every body part or symptom the sentence names was in what the patient reported."""
    said = report.lower()
    return all(re.search(r"\b" + re.escape(found.group(0).lower().rstrip("s")[:4]), said)
               for found in _BODY_AND_SYMPTOMS.finditer(sentence))


_summaries = {}        # one summary per visit and set of answers, so the nurse's screen can ask again for free


def checked_summary(text, complaint="", report=None):
    """The summary as it may be shown, or ValueError. Too long, advice, a diagnosis or a condition the patient
    did not name themselves: thrown away, and the nurse sees the patient's own answers only."""
    text = " ".join(str(text or "").split())
    # Keep the first two sentences that do not guess. A third sentence is cut off rather than costing the whole summary.
    sentences = [_without_guess(s) for s in re.split(r"(?<=[.!?])\s+", text)]
    sentences = [s for s in sentences if s and not _JUDGES.search(s) and (report is None or _grounded(s, report))][:2]
    text = " ".join(sentences)
    if not text or len(text.split()) > SUMMARY_MAX_WORDS:
        raise ValueError("not a short summary")
    if _BANNED.search(text):
        raise ValueError("advice or personal data")
    said = lambda name: re.search(rf"\b{re.escape(name)}\b", complaint, re.I)       # noqa: E731
    if any(not said(found.group(0)) for found in _NAMES_A_CONDITION.finditer(text)):
        raise ValueError("names a condition")
    return text


def _report_for_summary(complaint, age, sex, answers, injury=False):
    """What the patient reported, as plain lines for the model. Only answers the patient actually gave."""
    lines = [f'Complaint, in the patient\'s words: "{complaint}"', f"Age {age:g}, {str(sex).lower()}"]
    if injury:
        lines.append("Caused by an injury: yes")
    danger = [a["question"] for a in answers if a.get("kind") == "danger" and a.get("answer") == "Yes"]
    lines += [f"Danger sign, answered yes: {q}" for q in danger] or ["Danger signs: answered no to all"]
    story = [a["answer"] for a in answers if a.get("topic") == OWN_WORDS["topic"] and a.get("answer")]
    lines += [f'The patient wrote, in their own words: "{text}"' for text in story]
    lines += [f"{a['question']} {a['answer']}" for a in answers
              if a.get("kind") == "extra" and a.get("answer") and a.get("topic") != OWN_WORDS["topic"]]
    return "\n".join(lines)


def nurse_summary(key, complaint, age, sex, answers, injury=False, use_model=True, timeout=OLLAMA_TIMEOUT):
    """Two sentences for the nurse, written by the language model: {"source": "ai", "text": ...}.
    {"source": "none", "text": None} when the model is off, too slow, or broke the rules."""
    report = _report_for_summary(complaint, age, sex, answers, injury)
    cache = (key, report)
    if cache in _summaries:
        return _summaries[cache]
    if not use_model:
        return {"source": "none", "text": None}
    body = {"model": OLLAMA_MODEL, "stream": False, "keep_alive": "30m",
            "messages": [{"role": "system", "content": SUMMARY_SYSTEM_PROMPT}, {"role": "user", "content": report}],
            "options": {"temperature": 0.2, "num_ctx": 2048, "num_predict": 120}}
    request = urllib.request.Request(OLLAMA_URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            written = json.loads(response.read())["message"]["content"]
        result = {"source": "ai", "text": checked_summary(written, complaint, report)}
    except Exception:
        return {"source": "none", "text": None}          # not kept: the next look may find the model ready
    if len(_summaries) >= 500:
        _summaries.clear()
    _summaries[cache] = result
    return result


# ---------------------------------------------------------------- the electronic line (plain rules)

def place(patient, clock):
    """Sorting key, smaller first. Levels 1-2 always lead.
    Waiting lifts a level 3-5 patient by one level every 45 minutes."""
    waited = clock - patient["arrived"]
    if patient["level"] <= 2:
        return (0, patient["level"], patient["arrived"])
    # A yes to a danger-sign question counts as half a level more urgent.
    return (1, patient["level"] - waited / MOVE_UP_EVERY - (0.5 if patient.get("danger") else 0), patient["arrived"])
