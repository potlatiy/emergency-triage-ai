"""The demo: a second check for the triage nurse.

Run:   python app.py     then open http://localhost:7861

The model here is trained on all 1,267 patients, because the demo is for showing how the
tool behaves. Every score in the notebook comes from models tested on patients they never saw.
"""

from pathlib import Path

import gradio as gr
import numpy as np
import pandas as pd

from triage import CATEGORIES, LEVELS, NUMBERS, TEXT, load, make_model, safety_first

IMAGES = Path(__file__).parent / "images"
NAMES = {1: "Resuscitation", 2: "Emergency", 3: "Urgent", 4: "Less urgent", 5: "Non-urgent"}
QUEUE = {1: "Staff alerted now", 2: "Staff alerted now", 3: "Front of the queue",
         4: "Standard queue", 5: "Standard queue"}
COLOUR = {1: "#d03b3b", 2: "#d03b3b", 3: "#ec835a", 4: "#0ca30c", 5: "#0ca30c"}
ICON = {1: "&#9888;", 2: "&#9888;", 3: "&#9650;", 4: "&#10003;", 5: "&#10003;"}   # never colour alone

SEX = {"Female": 1, "Male": 2}
ARRIVAL = {"Walked in": 1, "Public ambulance": 2, "Private car": 3, "Private ambulance": 4}
MENTAL = {"Alert": 1, "Responds to voice": 2, "Responds to pain": 3, "Unresponsive": 4}
LABELS = {"Age": "Age", "NRS_pain": "Pain score", "Patients number per hour": "How busy the department is",
          "SBP": "Blood pressure (upper)", "DBP": "Blood pressure (lower)", "HR": "Heart rate",
          "RR": "Breathing rate", "BT": "Temperature", "Saturation": "Oxygen level",
          "unmeasurable": "Vital signs could not be measured", "Sex": "Sex", "Arrival mode": "How they arrived",
          "Injury": "Injury", "Mental": "Alertness", "Pain": "Pain", TEXT: "The complaint in words"}
SURE_ENOUGH = 0.60      # below this the tool says it is not sure

df = load()
model = make_model().fit(df, df["KTAS_expert"])
BUSY = float(df["Patients number per hour"].median())


def reasons(patient, level):
    """Which inputs pointed most strongly to this level (the model's own weights times the values)."""
    prepare, logistic = model.named_steps["prepare"], model.named_steps["model"]
    row = prepare.transform(patient)
    row = row.toarray()[0] if hasattr(row, "toarray") else np.asarray(row)[0]
    pushes = row * logistic.coef_[list(logistic.classes_).index(level)]
    grouped = {}
    for name, push in zip(prepare.get_feature_names_out(), pushes):
        part, rest = name.split("__", 1)
        key = TEXT if part == "complaint" else rest if part == "numbers" else next(
            c for c in CATEGORIES if rest.startswith(c))
        grouped[key] = grouped.get(key, 0) + push
    strong = [(LABELS[k], v) for k, v in grouped.items() if v > 0.15]
    return sorted(strong, key=lambda item: -item[1])[:4]


def card(level, probabilities, why, nurse_level):
    colour, confidence = COLOUR[level], probabilities.max()
    bars = ""
    for l, p in zip(LEVELS, probabilities):
        mark = " hr-chosen" if l == level else ""
        bars += f"""<div class="hr-row{mark}"><div class="hr-name">Level {l} · {NAMES[l]}</div>
            <div class="hr-track"><div class="hr-fill" style="width:{max(1, round(p * 100))}%"></div></div>
            <div class="hr-dir">{p:.0%}</div></div>"""
    notes = ""
    if nurse_level and level < nurse_level:
        notes += f"""<div class="hr-flag"><b>&#9873; Second check:</b> the AI rates this patient more urgent than
        the nurse's level {nurse_level}. Please look again before they join the queue.</div>"""
    elif nurse_level:
        notes += f'<div class="hr-note hr-pad">No flag: the AI does not rate this patient more urgent than level {nurse_level}.</div>'
    if confidence < SURE_ENOUGH:
        notes += """<div class="hr-note hr-pad"><b>The AI is not sure about this patient.</b>
        No level has more than 60% probability, so the nurse's own judgement should decide.</div>"""
    why_rows = "".join(f"<li>{label}</li>" for label, _ in why) or "<li>No single input stood out.</li>"
    return f"""
<div class="hr-card">
  <div class="hr-verdict" style="border-color:{colour}">
    <span class="hr-icon" style="color:{colour}">{ICON[level]}</span>
    <div><div class="hr-verdict-text">Level {level} · {NAMES[level]}</div>
    <div class="hr-note">{QUEUE[level]}</div></div>
    <div class="hr-risk">{level}</div>
  </div>
  {notes}
  <div class="hr-heading">How likely each level is</div>
  {bars}
  <div class="hr-heading">What pointed to this level</div>
  <ul class="hr-why">{why_rows}</ul>
  <div class="hr-note hr-foot">The suggestion leans towards the more urgent level on purpose: rating a patient too
  low is the dangerous mistake. In testing the AI matched the expert panel less often than nurses did (68% against
  85%), so it is a second check and not a replacement. Prototype, not medical advice.</div>
</div>"""


def check(complaint, age, sex, arrival, injury, mental, pain, sbp, dbp, hr, rr, temp, oxygen, nurse_level):
    """Returns the result card and a short record of the patient that can be added to the queue."""
    if not complaint or age is None:
        return '<div class="hr-card hr-empty">Please enter the complaint and the age.</div>', None
    patient = pd.DataFrame([{
        TEXT: complaint.lower(), "Age": age, "Sex": SEX[sex], "Arrival mode": ARRIVAL[arrival],
        "Injury": 2 if injury else 1, "Mental": MENTAL[mental], "Pain": int((pain or 0) > 0), "NRS_pain": pain or 0,
        "Patients number per hour": BUSY, "SBP": sbp, "DBP": dbp, "HR": hr, "RR": rr, "BT": temp,
        "Saturation": oxygen if oxygen else np.nan, "unmeasurable": 0,
    }]).astype({c: float for c in NUMBERS})
    probabilities = model.predict_proba(patient)
    level = int(safety_first(probabilities)[0])
    nurse = int(nurse_level[-1]) if nurse_level and nurse_level != "Not given yet" else None
    record = {"who": f"{complaint}, {int(age)}, {sex[0]}", "level": level,
              "flag": bool(nurse and level < nurse), "unsure": bool(probabilities.max() < SURE_ENOUGH)}
    return card(level, probabilities[0], reasons(patient, level), nurse), record


# ---------------------------------------------------------------- the electronic queue
# Plain rules, not AI: the AI supplies each patient's level, the rules below order the line.

MOVE_UP_EVERY = 45      # a level 3-5 patient gains one level of priority per 45 minutes waited


def new_queue():
    return {"clock": 0, "next": 1, "patients": []}


def place(patient, clock):
    """Sorting key: smaller goes first. Levels 1-2 always come before everyone else."""
    waited = clock - patient["arrived"]
    if patient["level"] <= 2:
        return (0, patient["level"], patient["arrived"])
    # Waiting moves a patient up among the non-emergency patients, never ahead of an emergency.
    return (1, patient["level"] - waited / MOVE_UP_EVERY, patient["arrived"])


def ordered(queue):
    return sorted(queue["patients"], key=lambda p: place(p, queue["clock"]))


def phone_message(position, patient):
    if patient["level"] <= 2:
        return "Staff are coming to you now"
    ending = {1: "st", 2: "nd", 3: "rd"}.get(position if position < 20 else position % 10, "th")
    return f"You are {position}{ending} in line"


def board(queue, note=""):
    line = ordered(queue)
    hours, minutes = divmod(queue["clock"], 60)
    top = f"""<div class="q-top"><b>{len(line)} waiting</b><span>Demo clock: {hours}h {minutes:02d}m</span></div>"""
    if note:
        top += f'<div class="q-note">{note}</div>'
    if not line:
        return f"""<div class="hr-card">{top}<div class="hr-empty">The line is empty. Check a patient and press
        <b>Add to the line</b>, or load the sample patients.</div></div>"""
    rows = ""
    for position, p in enumerate(line, 1):
        waited = queue["clock"] - p["arrived"]
        tags = ""
        if p["flag"]:
            tags += '<span class="q-tag q-red">&#9873; re-check asked</span>'
        if p["unsure"]:
            tags += '<span class="q-tag">AI not sure</span>'
        if p["level"] >= 3 and waited >= MOVE_UP_EVERY:
            tags += '<span class="q-tag">&#9650; moved up for waiting</span>'
        rows += f"""<div class="q-row" style="border-left-color:{COLOUR[p['level']]}">
          <div class="q-pos">{position}</div>
          <div><div class="q-who">{p['ticket']} · {p['who']}</div>
               <div class="hr-note">{ICON[p['level']]} Level {p['level']} · {NAMES[p['level']]} · waited {waited} min {tags}</div></div>
          <div class="q-phone"><span class="hr-note">Patient's phone shows</span><br>{phone_message(position, p)}</div>
        </div>"""
    return f'<div class="hr-card">{top}{rows}</div>'


def add_to_line(record, queue):
    if not record:
        return queue, board(queue, "Check a patient first, then add them to the line.")
    ticket = f"A{queue['next']:03d}"
    queue["patients"].append({**record, "ticket": ticket, "arrived": queue["clock"]})
    queue["next"] += 1
    return queue, board(queue, f"{ticket} joined the line at level {record['level']}.")


def call_next(queue):
    line = ordered(queue)
    if not line:
        return queue, board(queue)
    first = line[0]
    queue["patients"] = [p for p in queue["patients"] if p["ticket"] != first["ticket"]]
    return queue, board(queue, f"{first['ticket']} ({first['who']}) was called to a doctor.")


def pass_time(queue):
    queue["clock"] += 15
    return queue, board(queue, "15 minutes passed.")


SAMPLE_LINE = [      # made-up patients, already given a level, arriving a few minutes apart
    ("sore throat, 31, F", 5, False, 0), ("ankle pain, 24, F", 4, False, 4), ("abd pain, 45, M", 3, False, 9),
    ("headache, 52, F", 4, True, 12), ("dizziness, 58, F", 3, False, 20), ("ant. chest pain, 67, M", 2, True, 26),
]


def load_sample(queue):
    queue = new_queue()
    for who, level, flag, arrived in SAMPLE_LINE:
        queue["patients"].append({"who": who, "level": level, "flag": flag, "unsure": False,
                                  "ticket": f"A{queue['next']:03d}", "arrived": arrived})
        queue["next"] += 1
    queue["clock"] = 30
    return queue, board(queue, "Six sample patients loaded. The chest pain patient arrived last and is first in line.")


def clear_line(queue):
    queue = new_queue()
    return queue, board(queue)


CSS = """
.gradio-container {max-width: 1080px !important; margin: 0 auto !important;}
#hr-title h1 {font-size: 1.7rem; margin-bottom: 2px;}
#hr-title p {color: var(--body-text-color-subdued); font-size: 1.02rem; margin-top: 0;}
.hr-card {border: 1px solid var(--border-color-primary); border-radius: 12px; padding: 20px;
          background: var(--background-fill-secondary); color: var(--body-text-color);}
.hr-empty {color: var(--body-text-color-subdued); text-align: center; padding: 56px 24px; line-height: 1.6;}
.hr-verdict {display: flex; align-items: center; gap: 14px; border-left: 6px solid; padding-left: 14px;}
.hr-icon {font-size: 1.8rem; line-height: 1;}
.hr-verdict-text {font-size: 1.25rem; font-weight: 650;}
.hr-risk {margin-left: auto; font-size: 2.6rem; font-weight: 700;}
.hr-note {color: var(--body-text-color-subdued); font-size: 0.88rem;}
.hr-pad {margin-top: 14px; line-height: 1.5;}
.hr-flag {margin-top: 16px; padding: 12px 14px; border: 1.5px solid #d03b3b; border-radius: 8px; line-height: 1.5;}
.hr-heading {font-weight: 650; margin: 22px 0 10px;}
.hr-row {display: grid; grid-template-columns: 44% 1fr 48px; align-items: center; gap: 10px; margin: 7px 0;
         font-size: 0.93rem; color: var(--body-text-color-subdued);}
.hr-chosen {color: var(--body-text-color); font-weight: 650;}
.hr-track {height: 10px; border-radius: 5px; background: var(--border-color-primary);}
.hr-fill {height: 100%; border-radius: 5px; background: #2a78d6;}
.hr-dir {font-size: 0.85rem; text-align: right; font-variant-numeric: tabular-nums;}
.hr-why {margin: 0; padding-left: 20px; font-size: 0.93rem; line-height: 1.7;}
.hr-foot {margin-top: 18px; line-height: 1.5;}
.q-top {display: flex; justify-content: space-between; margin-bottom: 6px;}
.q-top span {color: var(--body-text-color-subdued); font-size: 0.9rem;}
.q-note {font-size: 0.9rem; margin-bottom: 10px; color: var(--body-text-color-subdued);}
.q-row {display: grid; grid-template-columns: 40px 1fr 190px; align-items: center; gap: 12px; padding: 10px 12px;
        margin-top: 8px; border: 1px solid var(--border-color-primary); border-left: 6px solid; border-radius: 0 8px 8px 0;
        background: var(--background-fill-primary);}
.q-pos {font-size: 1.5rem; font-weight: 700; text-align: center;}
.q-who {font-weight: 650;}
.q-phone {font-size: 0.92rem; text-align: right;}
.q-tag {display: inline-block; margin-left: 8px; padding: 0 7px; border: 1px solid var(--border-color-primary);
        border-radius: 10px; font-size: 0.78rem; white-space: nowrap;}
.q-red {border-color: #d03b3b;}
"""

EMPTY = """<div class="hr-card hr-empty">Enter what the nurse records at the door and press
<b>Check urgency</b>, or pick one of the example patients below.</div>"""

# Made-up patients. Order matches the inputs below.
EXAMPLES = [
    ["ant. chest pain", 67, "Male", "Public ambulance", False, "Alert", 6, 95, 60, 118, 26, 36.8, 91, "Level 3"],
    ["ankle pain", 24, "Female", "Walked in", True, "Alert", 3, 122, 78, 76, 16, 36.5, 99, "Level 4"],
    ["dizziness", 58, "Female", "Private car", False, "Alert", 0, 138, 85, 88, 18, 36.6, None, "Not given yet"],
]

with gr.Blocks(title="Meddy: second check") as demo:
    gr.Markdown("# A second check for emergency triage\n"
                "The nurse records the complaint and vital signs at the door. The AI suggests an urgency level, "
                "warns when a patient looks more urgent than the level they were given, and the electronic line "
                "orders everyone by urgency.", elem_id="hr-title")
    queue = gr.State(new_queue())
    last_patient = gr.State(None)

    with gr.Tab("1. Check a patient"):
        with gr.Row(equal_height=False):
            with gr.Column(scale=5):
                gr.Markdown("### At the door")
                complaint = gr.Textbox(label="Complaint, in a few words", placeholder="for example: chest pain")
                with gr.Row():
                    age = gr.Number(label="Age", value=50, minimum=15, maximum=110)
                    sex = gr.Radio(list(SEX), label="Sex", value="Female")
                with gr.Row():
                    arrival = gr.Dropdown(list(ARRIVAL), label="How they arrived", value="Walked in")
                    mental = gr.Dropdown(list(MENTAL), label="Alertness", value="Alert")
                with gr.Row():
                    pain = gr.Slider(0, 10, step=1, value=0, label="Pain score (0 = none)")
                    injury = gr.Checkbox(label="Caused by an injury")
                gr.Markdown("### Vital signs")
                with gr.Row():
                    sbp = gr.Number(label="Blood pressure, upper", value=125)
                    dbp = gr.Number(label="Blood pressure, lower", value=80)
                with gr.Row():
                    hr = gr.Number(label="Heart rate", value=80)
                    rr = gr.Number(label="Breathing rate", value=18)
                with gr.Row():
                    temp = gr.Number(label="Temperature (°C)", value=36.6)
                    oxygen = gr.Number(label="Oxygen level (%)", info="Leave empty if not measured", value=None)
                nurse_level = gr.Dropdown(["Not given yet"] + [f"Level {l}" for l in LEVELS], value="Not given yet",
                                          label="Level the nurse gave", info="Optional. Used for the second check.")
                button = gr.Button("Check urgency", variant="primary", size="lg")
            with gr.Column(scale=6):
                gr.Markdown("### Suggestion")
                result = gr.HTML(EMPTY)
                add_button = gr.Button("Add to the line", size="lg")
                added = gr.Markdown("")
                examples_slot = gr.Column()

    with gr.Tab("2. Electronic line"):
        gr.Markdown("Everyone waiting, ordered by urgency. Levels 1 and 2 always go first. A less urgent patient moves "
                    f"up one level for every {MOVE_UP_EVERY} minutes waited, so nobody is stuck at the back. "
                    "Patients see their place on their phone, never a promised time. "
                    "The order is set by plain rules; the AI only supplies each level.")
        with gr.Row():
            sample_button = gr.Button("Load sample patients")
            time_button = gr.Button("15 minutes pass")
            next_button = gr.Button("Call next patient", variant="primary")
            clear_button = gr.Button("Clear the line")
        line_view = gr.HTML(board(new_queue()))

    inputs = [complaint, age, sex, arrival, injury, mental, pain, sbp, dbp, hr, rr, temp, oxygen, nurse_level]
    button.click(check, inputs, [result, last_patient])
    add_button.click(add_to_line, [last_patient, queue], [queue, line_view]).then(
        lambda record: "Added. Open **2. Electronic line** to see the order." if record else
        "Check a patient first.", last_patient, added)
    sample_button.click(load_sample, queue, [queue, line_view])
    time_button.click(pass_time, queue, [queue, line_view])
    next_button.click(call_next, queue, [queue, line_view])
    clear_button.click(clear_line, queue, [queue, line_view])
    with examples_slot:
        gr.Examples(EXAMPLES, inputs, [result, last_patient], fn=check, run_on_click=True,
                    label="Example patients (made up)",
                    example_labels=["Chest pain, nurse said level 3", "Ankle injury, nurse said level 4",
                                    "Dizziness, no nurse level yet"])

    with gr.Accordion("How well does this work? (the honest numbers)", open=False):
        for image in ["second_check.png", "waiting.png"]:
            if (IMAGES / image).exists():
                gr.Image(str(IMAGES / image), show_label=False, container=False, interactive=False)
        gr.Markdown(
            "Tested on 1,267 real emergency patients from two Korean hospitals, each scored by a model that never "
            "saw them.\n\n"
            "| | Exact level | Rated too low | Rated too high |\n|---|---|---|---|\n"
            "| Always level 3 (baseline) | 38% | 19% | 42% |\n| AI, safety-first | 60% | 8% | 32% |\n"
            "| Nurses | 85% | 10% | 4% |\n\n"
            "The AI is worse than nurses at giving the level, so it should not replace them. Used as a second check, "
            "it flags 107 of the 131 patients nurses rated too low, and asks for a re-check on about a third of all "
            "patients. With time to re-check only 20% of patients, it reaches 67% of the under-rated ones, against "
            "34 to 38% for simple rules. The waiting times are simulated. This is a prototype and is not medical advice."
        )

if __name__ == "__main__":
    demo.launch(server_port=7861, css=CSS, theme=gr.themes.Default(primary_hue="blue"))
