"""Checks on the two trained parts: the urgency decision and the department answer key."""

import numpy as np
import pytest

import assignment
import triage


def test_safety_first_prefers_the_more_urgent_level():
    # Level 3 is the most likely, but under-rating costs three times as much, so level 2 is chosen.
    probabilities = np.array([[0.0, 0.30, 0.45, 0.25, 0.0]])
    assert triage.most_likely(probabilities)[0] == 3
    assert triage.safety_first(probabilities)[0] == 2


def test_safety_first_keeps_a_clear_answer():
    assert triage.safety_first(np.array([[0.0, 0.0, 0.02, 0.95, 0.03]]))[0] == 4


def test_the_data_loads_clean():
    df = triage.load()
    assert len(df) == 1267
    assert set(df["KTAS_expert"].unique()) == {1, 2, 3, 4, 5}
    assert df.loc[df["Pain"] == 0, "NRS_pain"].eq(0).all()          # no pain means a pain score of 0
    for column in triage.VITALS:
        assert df[column].dtype.kind == "f"                          # "unmeasurable" became a missing number


@pytest.mark.parametrize("diagnosis, department", [
    ("Unstable angina", "Cardiology"),
    ("Cerebral infarction, unspecified", "Neurology"),              # must not be caught by "infarction"
    ("Acute cholecystitis", "Gastroenterology and surgery"),        # must not be caught by "cystitis"
    ("Gallbladder cancer", "Gastroenterology and surgery"),         # must not be caught by "bladder"
    ("Acute cystitis", "Urology and kidney"),
    ("Torsion of testis", "Urology and kidney"),
    ("Acute renal failure, unspecified", "Urology and kidney"),
    ("Herniated disc disease of lumbar spine", "Trauma and orthopaedics"),   # must not be caught by "hernia"
    ("Dysarthria", "Neurology"),                                    # must not be caught by "arthr"
    ("Corneal abrasion", "Eye, ear, nose and throat"),              # must not be caught by "abrasion"
    ("Threatened abortion", "Obstetrics and gynaecology"),
    ("Community-acquired pneumonia", "Respiratory medicine"),
    ("Laceration of scalp", "Trauma and orthopaedics"),
    ("Fever", "General medicine"),
])
def test_diagnoses_go_to_the_right_department(diagnosis, department):
    assert assignment.department_of_diagnosis(diagnosis) == department


def test_every_patient_gets_a_known_department():
    labels = assignment.labels(triage.load())
    assert labels.notna().all()
    assert set(labels.unique()) <= set(assignment.DEPARTMENTS)
