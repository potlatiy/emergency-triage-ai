"""Train the two models on all 1,267 patients and save them in models/, so anyone can use them without training.

    python trained_models.py        trains both models, saves them, and writes models/model_info.json

Other programs call ready(). It loads the saved files when they match this computer's version of
scikit-learn and the data they were trained on; otherwise it trains again, which takes about a second.

The saved files are "joblib" files. Loading one runs code stored inside it, so only ever load model files
that come from this repository.
"""

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import sklearn

from assignment import TABLE, fair_predictions, labels as department_labels, make_assignment_model
from triage import (CATEGORIES, DATA, LEVELS, NUMBERS, RANGE_COVERAGE, TEXT, fair_probabilities, level_range_figures,
                    load, make_model, most_likely, range_cut)

FOLDER = Path(__file__).parent / "models"
URGENCY_FILE = FOLDER / "urgency_model.joblib"
DEPARTMENT_FILE = FOLDER / "department_model.joblib"
INFO_FILE = FOLDER / "model_info.json"


def fingerprint(path):
    """A short code that changes whenever the file changes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def recipe():
    """A short code for how the two models are built (their steps and settings). It changes when someone
    changes the recipe in triage.py or assignment.py, and not when they only edit a comment."""
    return {"urgency": hashlib.sha256(repr(make_model()).encode()).hexdigest()[:16],
            "department": hashlib.sha256(repr(make_assignment_model()).encode()).hexdigest()[:16]}


def train(data=None):
    """Both models, each trained on every patient in the data."""
    data = load() if data is None else data
    return (make_model().fit(data, data["KTAS_expert"]),
            make_assignment_model().fit(data, department_labels(data)))


def save():
    data = load()
    urgency, department = train(data)
    FOLDER.mkdir(exist_ok=True)
    joblib.dump(urgency, URGENCY_FILE, compress=3)
    joblib.dump(department, DEPARTMENT_FILE, compress=3)

    # The saved models have seen every patient, so they cannot be tested on this data.
    # The fair figures come from the same recipe tested on patients it had not seen.
    unseen = fair_probabilities(data)
    level_right = float((most_likely(unseen) == data["KTAS_expert"]).mean())
    # The range of levels: the cut comes from those same fair probabilities, never from the saved model's own
    # answers on patients it has seen (it is too sure of those, and the range would come out too narrow).
    range_figures = level_range_figures(data["KTAS_expert"].values, unseen)
    target = department_labels(data)
    department_right = float((fair_predictions(data, target)[0] == target).mean())
    in_top_three = float((fair_predictions.ranked[:, :3] == target.to_numpy()[:, None]).any(axis=1).mean())

    info = {
        "trained_on": str(date.today()),
        "patients": len(data),
        "made_with": {"python": sys.version.split()[0], "scikit-learn": sklearn.__version__, "numpy": np.__version__},
        "inputs": {"numbers": NUMBERS, "categories": CATEGORIES, "text": TEXT},
        "recipe": recipe(),
        "data": {"data/ktas_data.csv": fingerprint(DATA), "data/diagnosis_departments.csv": fingerprint(TABLE)},
        "models": {
            URGENCY_FILE.name: {
                "predicts": "urgency level, 1 (most urgent) to 5",
                "answers": [int(c) for c in urgency.classes_],
                "kind": "logistic regression on scaled numbers, one-hot categories and letter patterns of the complaint",
                "exact_level_on_unseen_patients": round(level_right, 3),
                "fingerprint": fingerprint(URGENCY_FILE),
            },
            DEPARTMENT_FILE.name: {
                "predicts": "hospital department",
                "answers": [str(c) for c in department.classes_],
                "kind": "the same preparation and model type as the urgency model",
                "right_department_on_unseen_patients": round(department_right, 3),
                "right_department_in_top_three": round(in_top_three, 3),
                "fingerprint": fingerprint(DEPARTMENT_FILE),
            },
        },
        "level_range": {
            "promise": RANGE_COVERAGE,
            "keep_a_level_when_its_probability_is_at_least": round(range_cut(unseen, data["KTAS_expert"].values), 4),
            "expert_level_inside_on_unseen_patients": round(float(range_figures["inside"]), 3),
            "average_levels_in_range": round(float(range_figures["width"]), 2),
            "urgent_patients_inside": round(float(range_figures["urgent_inside"]), 3),
        },
        "note": "The figures were measured with 5-fold cross-validation: every patient scored by a model that "
                "never saw them. The saved files were then trained on all patients, for the demonstration.",
    }
    INFO_FILE.write_text(json.dumps(info, indent=2) + "\n")
    return info


def why_not_usable():
    """None when the saved models can be loaded safely, otherwise the reason they cannot."""
    if not (INFO_FILE.exists() and URGENCY_FILE.exists() and DEPARTMENT_FILE.exists()):
        return "no saved models found"
    info = json.loads(INFO_FILE.read_text())
    if info["made_with"]["scikit-learn"] != sklearn.__version__:
        return (f"saved with scikit-learn {info['made_with']['scikit-learn']}, "
                f"this computer has {sklearn.__version__}")
    if info.get("inputs") != {"numbers": NUMBERS, "categories": CATEGORIES, "text": TEXT}:
        return "the models were saved with other inputs than the code uses now"
    if info.get("recipe") != recipe():
        return "the way the models are built has changed since they were saved"
    for name, saved in info["data"].items():
        if fingerprint(Path(__file__).parent / name) != saved:
            return f"{name} has changed since the models were saved"
    for file in (URGENCY_FILE, DEPARTMENT_FILE):
        if fingerprint(file) != info["models"][file.name]["fingerprint"]:
            return f"{file.name} does not match model_info.json"
    return None


def level_range_cut():
    """How small a level's probability may be for the level to stay in Meddy's range (see triage.range_cut).
    Read from model_info.json when the saved models are usable, worked out from the data otherwise."""
    if why_not_usable() is None:
        saved = json.loads(INFO_FILE.read_text()).get("level_range")
        if saved and saved.get("promise") == RANGE_COVERAGE:
            return saved["keep_a_level_when_its_probability_is_at_least"]
    data = load()
    return range_cut(fair_probabilities(data), data["KTAS_expert"].values)


def ready(announce=True):
    """The urgency model and the department model, loaded from models/ when possible."""
    reason = why_not_usable()
    if reason is None:
        return joblib.load(URGENCY_FILE), joblib.load(DEPARTMENT_FILE)
    if announce:
        print(f"Training the models now ({reason}).")
    return train()


if __name__ == "__main__":
    info = save()
    for name, details in info["models"].items():
        size = (FOLDER / name).stat().st_size / 1024
        print(f"Saved models/{name}  ({size:.0f} KB)  predicts: {details['predicts']}")
    print(f"Urgency: exact level right for {info['models'][URGENCY_FILE.name]['exact_level_on_unseen_patients']:.0%} "
          f"of patients the model had not seen.")
    print(f"Department: right for {info['models'][DEPARTMENT_FILE.name]['right_department_on_unseen_patients']:.0%}, "
          f"in the top three for {info['models'][DEPARTMENT_FILE.name]['right_department_in_top_three']:.0%}.")
