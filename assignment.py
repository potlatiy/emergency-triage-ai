"""Doctor assignment: which department should a patient be sent to?

The answer key comes from real data. Each of the 1,267 patients has a final diagnosis made in the
emergency department. We match every diagnosis to a department (the table is saved in
data/diagnosis_departments.csv so anyone can review it), then train a model to predict that
department from what is known at the door: the complaint and the triage measurements.

Run:  python assignment.py
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline

from triage import SEED, TEXT, load, make_model

TABLE = Path(__file__).parent / "data" / "diagnosis_departments.csv"
GENERAL = "General medicine"

# Which words in a DIAGNOSIS put it in which department. The first match wins, so the order matters:
# for example "cerebral infarction" must reach neurology before "infarction" reaches cardiology.
DIAGNOSIS_RULES = [
    ("Obstetrics and gynaecology",
     r"vagin|uter|ovar|cervix|cervical incompetence|pregnan|\blabou?r\b|abortion|dysmenorr|pelvic inflammatory|"
     r"pelvic pain|postpartum|contracepti|rupture of membranes|menstr"),
    ("Eye, ear, nose and throat",
     r"cornea|conjunctiv|\beye|ocular|retina|vitreous|hyphema|hyphaema|uveitis|iritis|glaucoma|kerat|eyelid|orbit|"
     r"diplopia|optic neuritis|epistaxis|otitis|otalgia|tonsil|pharyng|laryng|sinusitis|rhinitis|nasal|\bnose|"
     r"\bear\b|hearing|tinnitus|vestibular|paroxysmal vertigo|positional vertigo|meniere|tooth|dental|dento|"
     r"odontogenic|pulpitis|gingiv|periodont|mouth|stomatitis|parotid|salivary|submandibular|epiglott|"
     r"peritonsillar|sore throat|hoarseness|lump, neck|"
     r"foreign body in (ear|nose|throat|pharynx|cornea|conjunctiv)"),
    ("Neurology",
     r"cerebr|intracranial|subarachnoid|subdural|epidural h|stroke|transient isch|seizure|epilep|convuls|"
     r"headache|migraine|dizziness|giddiness|vertigo|neuropath|neuralgia|palsy|paralysis|hemiplegia|"
     r"meningitis|encephal|parkinson|dementia|myasthenia|multiple sclerosis|syncope|altered mental|"
     r"somnolence|stupor|coma\b|numbness|paresthesia|tremor|ataxia|amnesia|hydrocephalus|guillain|meningioma|"
     r"neoplasm of brain|cancer to the brain|confusion|involuntary movement|carotid|muscle weakness|cord compression"),
    ("Cardiology",
     r"angina|myocardial|infarction|\bheart|cardiac|cardio|atrial|ventricular|fibrillation|flutter|tachycardia|"
     r"bradycardia|arrhythm|chest pain|palpitation|hypertens|hypotension|aortic|aneurysm|pericard|endocard|"
     r"atrioventricular|heart block|coronary|valve|brugada|vein thrombosis|atherosclerosis"),
    ("Respiratory medicine",
     r"pneumonia|asthma|\bcopd|chronic obstructive|pulmonary|bronch|dyspn|pneumothorax|pleural|pleurisy|"
     r"respiratory|hemoptysis|haemoptysis|hyperventilation|influenza|\blung|cough|tuberculosis|common cold"),
    ("Urology and kidney",
     r"ureter|\\brenal|kidney|urinary|(?<!chole)cystitis|pyelonephritis|hematuria|haematuria|prostat|urethr|\\btesti|"
     r"epididym|orchitis|priapism|retention of urine|nephr|(?<!gall)bladder|percutaneous catheter|hydronephrosis|"
     r"urolith|calculus of (kidney|ureter)|dysuria|urinoma|anuria|oliguria"),
    ("Gastroenterology and surgery",
     r"gastro|gastri|abdominal|abdomen|abdomino|appendic|cholecyst|cholang|cholelith|gallstone|gallbladder|biliary|"
     r"bile duct|pancrea|\bliver|hepat|cirrhosis|ascites|ileus|bowel|intestin|colitis|colon|hernia|"
     r"hemorrhoid|haemorrhoid|\banal\b|anus|rectal|rectum|peptic|duodenal|gastric|stomach|ulcer of|"
     r"diverticul|peritonitis|constipation|vomiting|nausea|melena|hematemesis|haematemesis|dyspepsia|"
     r"esophag|oesophag|diarrh|enteritis|jaundice|varices|variceal|hematochezia|crohn|hirschsprung|intussusception|"
     r"fecal impaction|mallory|ampulla of vater|klatskin"),
    ("Trauma and orthopaedics",
     r"wound|fractur|contusion|sprain|strain|lacerat|burn|dislocat|injur|trauma|bite|crush|amputat|abrasion|"
     r"concussion|haematoma|hematoma|rupture of (tendon|ligament|muscle)|tendon|ligament|lumbago|sciatica|"
     r"back pain|low back|cervicalgia|neck pain|myalgia|arthr|joint|pain in limb|pain in (leg|arm|knee|"
     r"shoulder|hip|ankle|foot|hand|wrist|elbow)|(hip|knee|shoulder|leg|arm|ankle|foot|wrist|elbow) pain|"
     r"disc (disorder|herniation)|spondyl|spinal stenosis|bursitis|gout|regional pain syndrome|chondromalacia|"
     r"frozen shoulder|foreign body|splinter|superficial|whiplash|fall"),
]
DEPARTMENTS = [name for name, _ in DIAGNOSIS_RULES] + [GENERAL]


def department_of_diagnosis(diagnosis):
    text = (diagnosis or "").lower()
    for name, pattern in DIAGNOSIS_RULES:
        if re.search(pattern, text):
            return name
    return GENERAL


def build_table(df):
    """One row per distinct diagnosis, with its department, how many patients have it, and whether a
    rule matched. Unmatched diagnoses fall into general medicine and most need a human look."""
    counts = df["Diagnosis in ED"].fillna("").str.strip().value_counts()
    rows = []
    for diagnosis, n in counts.items():
        department = department_of_diagnosis(diagnosis)
        matched = any(re.search(p, diagnosis.lower()) for _, p in DIAGNOSIS_RULES)
        rows.append({"diagnosis": diagnosis, "patients": int(n), "department": department,
                     "matched_by_rule": "yes" if matched else "no, default"})
    return pd.DataFrame(rows)


def labels(df):
    """The department for every patient. A hand-edited table on disk wins over the rules."""
    table = pd.read_csv(TABLE, keep_default_na=False) if TABLE.exists() else build_table(df)
    lookup = dict(zip(table["diagnosis"], table["department"]))
    return df["Diagnosis in ED"].fillna("").str.strip().map(lookup).fillna(GENERAL)


# ---------------------------------------------------------------- the model

def make_assignment_model():
    """Same inputs and preparation as the urgency model, predicting a department instead of a level."""
    prepare = make_model().named_steps["prepare"]
    # We tried four versions (with and without balancing the departments, with and without the
    # key-word rule as an extra input). This plain one scored best and is also the simplest.
    return Pipeline([("prepare", prepare), ("model", LogisticRegression(max_iter=3000, C=3.0))])


def fair_predictions(df, target):
    """Every patient's department is predicted by a model that never saw them (5-fold cross-validation)."""
    folds = StratifiedKFold(5, shuffle=True, random_state=SEED)
    probabilities = cross_val_predict(make_assignment_model(), df, target, cv=folds, method="predict_proba")
    classes = np.array(sorted(target.unique()))
    return classes[probabilities.argmax(axis=1)], probabilities.max(axis=1)


def complaint_rule(df):
    """The key-word rule used so far: pick the department from words in the COMPLAINT."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "hospital"))
    from core import department_for
    return np.array([department_for(c, injury == 2) for c, injury in zip(df[TEXT], df["Injury"])])


def interval(correct, rounds=1000):
    rng = np.random.default_rng(SEED)
    values = [correct[rng.integers(0, len(correct), len(correct))].mean() for _ in range(rounds)]
    low, high = np.percentile(values, [2.5, 97.5])
    return f"{low:.0%}-{high:.0%}"


def main():
    df = load()
    if not TABLE.exists():
        build_table(df).to_csv(TABLE, index=False)
        print(f"wrote {TABLE.name}")
    truth = labels(df)
    table = pd.read_csv(TABLE, keep_default_na=False)
    unmatched = table[table["matched_by_rule"] != "yes"]
    print(f"{len(table)} distinct diagnoses. {len(unmatched)} matched no rule "
          f"({unmatched['patients'].sum()} patients) and default to general medicine.")
    print("\nPatients per department (from the real diagnosis):")
    print(truth.value_counts().to_string())

    predicted, confidence = fair_predictions(df, truth)
    rule = complaint_rule(df)
    common = truth.mode()[0]
    print("\nHOW OFTEN EACH METHOD PICKS THE DEPARTMENT OF THE REAL DIAGNOSIS")
    for name, guess in [(f"Baseline: always {common.lower()}", np.full(len(df), common)),
                        ("Key-word rule on the complaint", rule),
                        ("Trained model", predicted)]:
        correct = (guess == truth.values)
        print(f"  {name:36} {correct.mean():.0%}  (95% range {interval(correct)})")

    # Is the model really better than the rule, or is the gap luck? Re-draw the patients 1,000 times.
    rng = np.random.default_rng(SEED)
    model_right, rule_right = (predicted == truth.values), (rule == truth.values)
    gaps = []
    for _ in range(1000):
        pick = rng.integers(0, len(df), len(df))
        gaps.append(model_right[pick].mean() - rule_right[pick].mean())
    low, high = np.percentile(gaps, [2.5, 97.5])
    print(f"  Model minus rule: {model_right.mean() - rule_right.mean():+.1%}  (95% range {low:+.1%} to {high:+.1%})")

    print("\nTrained model, by department:")
    for department in DEPARTMENTS:
        mask = (truth == department).values
        if mask.any():
            print(f"  {department:30} {mask.sum():4} patients   found {(predicted[mask] == department).mean():.0%}"
                  f"   key-word rule {(rule[mask] == department).mean():.0%}")

    print("\nWhat the digital hospital uses: the model when it is sure enough, the key-word rule otherwise")
    for cut in [0.5, 0.7]:
        mixed = np.where(confidence >= cut, predicted, rule)
        print(f"  model when {cut:.0%}+ sure: right {(mixed == truth.values).mean():.0%}, "
              f"model decides for {(confidence >= cut).mean():.0%} of patients")

    print("\nWhen the model is confident:")
    for cut in [0.0, 0.5, 0.7]:
        keep = confidence >= cut
        print(f"  confidence {cut:.0%}+: covers {keep.mean():.0%} of patients, right {(predicted[keep] == truth.values[keep]).mean():.0%}")


if __name__ == "__main__":
    main()
