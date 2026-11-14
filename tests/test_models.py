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


# ---------------------------------------------------------------- the figures the write-up quotes

@pytest.fixture(scope="module")
def tested():
    df = triage.load()
    return df, triage.fair_probabilities(df)


def test_the_headline_figures_are_what_the_code_gives(tested):
    """If the data or a model changes, these fail, and every document that quotes them has to be re-read."""
    df, probabilities = tested
    truth, nurse = df["KTAS_expert"].to_numpy(), df["KTAS_RN"].to_numpy()
    assert len(df) == 1267
    assert round(float((triage.most_likely(probabilities) == truth).mean()), 2) == 0.68
    assert round(float((nurse == truth).mean()), 2) == 0.85
    under = nurse > truth
    assert under.sum() == 131
    strength = triage.flag_strength(probabilities, nurse)
    assert ((strength != "") & under).sum() == 107 and (strength != "").sum() == 461
    assert ((strength == "strong") & under).sum() == 91 and (strength == "strong").sum() == 260
    quick = triage.quick_review(probabilities, triage.vital_sign_rule(df))
    assert quick.sum() == 325


def test_a_group_of_equal_scores_is_counted_by_its_share():
    score = np.array([3, 1, 1, 1, 1, 0, 0, 0, 0, 0])         # the cut-off after 3 patients falls among four equals
    under = np.array([1, 1, 1, 0, 0, 0, 0, 0, 0, 0], dtype=bool)
    # the first patient is surely re-checked; two of the four equals fit, so half of their two under-rated count
    assert triage.caught_within_budget(score, under, 0.3) == pytest.approx((1 + 0.5 * 2) / 3)
    assert triage.caught_within_budget(score, under, 0.3) == triage.caught_within_budget(score[::-1], under[::-1], 0.3)


def test_the_simple_baselines_do_not_depend_on_luck(tested):
    df, probabilities = tested
    nurse = df["KTAS_RN"].to_numpy(); under = nurse > df["KTAS_expert"].to_numpy()
    least_urgent_first = triage.caught_within_budget(nurse.astype(float), under, 0.30)
    model = triage.caught_within_budget(triage.urgency_gap_score(probabilities, nurse), under, 0.30)
    assert round(least_urgent_first, 2) == 0.49 and round(model, 2) == 0.77

