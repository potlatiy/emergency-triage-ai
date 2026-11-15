"""Checks on the two trained parts: the urgency decision and the department answer key."""

import numpy as np
import pytest

import assignment
import similar
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


# ---------------------------------------------------------------- a range of levels with a promise

def test_the_range_cut_lets_the_promised_share_of_patients_through():
    """Ten patients, promise 80%: (10 + 1) x 0.2 = 2.2, so two may be missed. The cut is the second-smallest
    probability the model gave to the right level, and exactly one patient falls below it."""
    right = np.array([0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90])
    probabilities = np.zeros((10, 5))
    probabilities[:, 2] = right                     # every patient is truly level 3
    probabilities[:, 3] = 1 - right
    assert triage.range_cut(probabilities, np.full(10, 3), coverage=0.8) == 0.10
    assert (right >= 0.10).mean() == 0.9
    assert triage.range_cut(probabilities, np.full(10, 3), coverage=0.99) == 0.0        # too few patients: keep every level


def test_a_range_has_no_holes_and_always_holds_the_most_likely_level():
    odds = np.array([[0.30, 0.02, 0.28, 0.02, 0.38],        # levels 1, 3 and 5 reach the cut: the range is 1 to 5
                     [0.02, 0.90, 0.04, 0.02, 0.02],        # one clear level
                     [0.05, 0.05, 0.05, 0.05, 0.80]])
    low, high = triage.level_range(odds, cut=0.2)
    assert list(low) == [1, 2, 5] and list(high) == [5, 2, 5]
    low, high = triage.level_range(odds, cut=0.95)          # nothing reaches the cut: the most likely level stays
    assert list(low) == list(high) == [5, 2, 5]
    assert odds[0, 1] == 0.02                               # the probabilities themselves are left alone


def test_the_range_keeps_its_promise_on_patients_it_was_not_set_on(tested):
    df, probabilities = tested
    figures = triage.level_range_figures(df["KTAS_expert"].values, probabilities)
    assert 0.89 <= figures["inside"] <= 0.92                # promised 90%, measured 90.6%
    assert 1.9 <= figures["width"] <= 2.1 and 0.25 <= figures["one_level"] <= 0.30
    # The weak spot we report: the promise is an average. The most urgent patients are inside less often.
    assert 0.77 <= figures["urgent_inside"] <= 0.83 and figures["by_level"][5] < 0.65
    assert abs(figures["more_urgent_than_range"] + figures["less_urgent_than_range"] + figures["inside"] - 1) < 1e-9
    likely = triage.most_likely(probabilities)              # the baseline: one level either side of the most likely
    truth = df["KTAS_expert"].values
    low, high = np.maximum(likely - 1, 1), np.minimum(likely + 1, 5)
    assert round(float(((low <= truth) & (truth <= high)).mean()), 3) == 0.957
    assert round(float((high - low + 1).mean()), 1) == 2.9


# ---------------------------------------------------------------- patients like this one

def test_similar_patients_are_never_found_among_the_patients_own_fold(tested):
    """The fair test: a patient is looked up among patients of the other four folds, so never finds themselves."""
    df, _ = tested
    found, _ = similar.fair_neighbours(df, kinds=similar.kinds_of(df))          # as the hospital does it
    folds = np.zeros(len(df), dtype=int)
    from sklearn.model_selection import StratifiedKFold
    for number, (_, held_out) in enumerate(StratifiedKFold(5, shuffle=True, random_state=triage.SEED)
                                           .split(df, df["KTAS_expert"])):
        folds[held_out] = number
    assert found.shape == (len(df), similar.NEIGHBOURS)
    assert not (folds[found] == folds[:, None]).any()
    truth = df["KTAS_expert"].to_numpy()
    assert round(float((similar.vote(truth[found]) == truth).mean()), 2) == 0.61      # weaker than the model's 68%
    assert round(float((truth[found] == truth[:, None]).any(axis=1).mean()), 2) == 0.89


def test_a_patient_in_the_data_is_most_like_themselves_and_no_diagnosis_is_shown(tested):
    df, _ = tested
    index = similar.Similar(df)
    found = index.nearest(df.iloc[[100]])
    assert len(found) == similar.NEIGHBOURS and found[0]["alike"] == 1.0
    assert found[0]["complaint"] == df.iloc[100]["Chief_complain"]
    assert [p["alike"] for p in found] == sorted((p["alike"] for p in found), reverse=True)
    assert all(set(p) == {"complaint", "age", "sex", "nurse_level", "expert_level", "alike", "complaint_alike", "same_kind"}
               for p in found)


def test_patients_with_the_same_kind_of_complaint_come_first(tested):
    """The kind is read from the complaint by the key-word rule, never from the diagnosis. A patient whose kind
    is not recognised is looked up by the mix alone."""
    df, _ = tested
    kinds = similar.kinds_of(df)
    assert 0.78 <= np.mean([kind is not None for kind in kinds]) <= 0.82
    index = similar.Similar(df, kinds)
    chest = df.iloc[[int(np.flatnonzero(kinds == "Cardiology")[0])]]
    assert all(p["same_kind"] for p in index.nearest(chest, "Cardiology"))
    assert not any(p["same_kind"] for p in index.nearest(chest, None))


def test_a_tie_among_the_similar_patients_goes_to_the_more_urgent_level():
    assert list(similar.vote(np.array([[2, 2, 4, 4, 5], [3, 3, 3, 1, 1], [5, 4, 3, 2, 1]]))) == [2, 3, 1]

