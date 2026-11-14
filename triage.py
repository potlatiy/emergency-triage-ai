"""Emergency triage: can a model suggest an urgency level at the door, and act as a second check on nurses?

Data: 1,267 real emergency patients from two Korean hospitals (Moon et al., PLOS ONE 2019).
Each has the nurse's urgency level and the correct level decided afterwards by an expert panel.
Level 1 = most urgent, level 5 = least urgent.

Run:  python triage.py
"""

import heapq
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

DATA = Path(__file__).parent / "data" / "ktas_data.csv"
SEED = 42
LEVELS = [1, 2, 3, 4, 5]

# Only what is known at the door. The nurse's level, the diagnosis, the outcome and the
# length of stay are NOT inputs: they are either the thing we compare against or known only later.
VITALS = ["SBP", "DBP", "HR", "RR", "BT", "Saturation"]
NUMBERS = ["Age", "NRS_pain", "Patients number per hour"] + VITALS + ["unmeasurable"]
CATEGORIES = ["Sex", "Arrival mode", "Injury", "Mental", "Pain"]
TEXT = "Chief_complain"

# Under-rating an urgent patient is worse than over-rating a minor one.
# This is our design choice: each level of under-rating costs 3 times a level of over-rating.
UNDER_COST = 3.0


def load():
    df = pd.read_csv(DATA)
    # Vital signs written as "unmeasurable" (in Korean) become missing, and we keep the fact
    # that they could not be measured, because that itself is a warning sign.
    raw = df[VITALS].astype(str)
    df["unmeasurable"] = raw.apply(lambda col: col.str.contains("측불")).any(axis=1).astype(int)
    for col in VITALS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Patients with no pain have no pain score: record it as 0.
    df.loc[df["Pain"] == 0, "NRS_pain"] = 0
    df[TEXT] = df[TEXT].fillna("").str.lower()
    return df


def make_model():
    prepare = ColumnTransformer([
        ("numbers", Pipeline([("fill", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), NUMBERS),
        ("categories", OneHotEncoder(handle_unknown="ignore"), CATEGORIES),
        # The complaint is free text ("abd pain", "pain, chest"). Short letter patterns cope with
        # abbreviations, word order and spelling better than whole words do.
        ("complaint", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=2, sublinear_tf=True), TEXT),
    ])
    return Pipeline([("prepare", prepare), ("model", LogisticRegression(max_iter=3000, C=3.0))])


def fair_probabilities(df):
    """Every patient is scored by a model that never saw them (5-fold cross-validation)."""
    folds = StratifiedKFold(5, shuffle=True, random_state=SEED)
    return cross_val_predict(make_model(), df, df["KTAS_expert"], cv=folds, method="predict_proba")


def most_likely(probabilities):
    return np.array(LEVELS)[probabilities.argmax(axis=1)]


def safety_first(probabilities, under_cost=UNDER_COST):
    """Pick the level with the lowest expected cost, where under-rating costs more than over-rating."""
    levels = np.array(LEVELS)
    # cost[true, chosen]: chosen > true means we rated the patient LESS urgent than they are.
    gap = levels[None, :] - levels[:, None]
    cost = np.where(gap > 0, gap * under_cost, -gap)
    return levels[(probabilities @ cost).argmin(axis=1)]


def summary(truth, given):
    truth, given = np.asarray(truth), np.asarray(given)
    return {
        "exact": (given == truth).mean(),
        "under": (given > truth).mean(),          # rated less urgent than the experts: dangerous
        "over": (given < truth).mean(),           # rated more urgent than the experts: wasteful
        "under_n": int((given > truth).sum()),
        "over_n": int((given < truth).sum()),
        "urgent_missed": int(((truth <= 2) & (given >= 4)).sum()),   # level 1-2 patient sent to the slow queue
    }


def line(name, s, n):
    return (f"  {name:34} exact {s['exact']:.0%}   rated too low {s['under_n']:4} ({s['under']:.0%})   "
            f"rated too high {s['over_n']:4} ({s['over']:.0%})   level 1-2 sent to slow queue: {s['urgent_missed']}")


# ---------------------------------------------------------------- the queue simulation

def simulate_wait(priority, truth, rng, doctors=3, service_minutes=20.0, load=0.95):
    """Average minutes until first seen, for the truly urgent (expert level 1-2) and for everyone.

    This is a SIMULATION. The data has no arrival times, so we invent them: patients arrive at
    random, and each visit takes a random time averaging `service_minutes`. Only the ORDER in
    which waiting patients are called differs between runs.
    """
    n = len(truth)
    order = rng.permutation(n)
    gaps = rng.exponential(service_minutes / (doctors * load), n)
    arrivals = np.cumsum(gaps)
    service = rng.exponential(service_minutes, n)
    free_at = [0.0] * doctors
    heapq.heapify(free_at)
    waiting, waits, i = [], np.zeros(n), 0
    clock = 0.0
    while i < n or waiting:
        doctor_free = heapq.heappop(free_at)
        clock = max(clock, doctor_free)
        if not waiting and i < n and arrivals[i] > clock:
            clock = arrivals[i]
        while i < n and arrivals[i] <= clock:
            patient = order[i]
            heapq.heappush(waiting, (priority[patient], arrivals[i], patient, i))
            i += 1
        _, arrived, patient, k = heapq.heappop(waiting)
        waits[patient] = clock - arrived
        heapq.heappush(free_at, clock + service[k])
    urgent = np.asarray(truth) <= 2
    return waits[urgent].mean(), waits.mean()


def compare_queues(df, ai_levels, rounds=200):
    rng = np.random.default_rng(SEED)
    truth = df["KTAS_expert"].values
    orderings = {
        "First come, first served": np.zeros(len(df)),
        "Nurse levels": df["KTAS_RN"].values,
        "AI levels (safety-first)": ai_levels,
        "Expert levels (the ideal)": truth,
    }
    rows = {}
    for name, priority in orderings.items():
        results = np.array([simulate_wait(priority, truth, np.random.default_rng(rng.integers(1 << 31)))
                            for _ in range(rounds)])
        rows[name] = results.mean(axis=0)
    return rows


# ---------------------------------------------------------------- the second check, tested harder

def urgency_gap_score(probabilities, nurse):
    """How strongly the level model disagrees with the nurse: the probability that the
    correct level is MORE urgent than the one the nurse gave."""
    levels = np.array(LEVELS)
    return (probabilities * (levels[None, :] < np.asarray(nurse)[:, None])).sum(axis=1)


STRONG_FLAG = 0.50      # "more likely than not": no number was tuned to get this


def flag_strength(probabilities, nurse):
    """The second check in two strengths, for each patient: "strong", "mild" or "".

    strong: the model thinks it more likely than not that the right level is more urgent than the nurse's.
    mild:   not that sure, but the safety-first level is still more urgent than the nurse's."""
    gap = urgency_gap_score(probabilities, nurse)
    careful = safety_first(probabilities) < np.asarray(nurse)
    return np.where(gap >= STRONG_FLAG, "strong", np.where(careful, "mild", ""))


def warning_signs(df):
    """One column per warning sign any nurse would recognise, true where a patient shows it."""
    return pd.DataFrame({
        "fast or slow pulse": (df["HR"] > 110) | (df["HR"] < 50),
        "fast breathing": df["RR"] > 24,
        "low blood pressure": df["SBP"] < 90,
        "low oxygen": df["Saturation"] < 92,
        "high fever": df["BT"] >= 38.5,
        "not fully alert": df["Mental"] > 1,
        "severe pain": df["NRS_pain"] >= 8,
        "unmeasurable vital signs": df["unmeasurable"] == 1,
    })


def vital_sign_rule(df):
    """The simple rule to beat: count the warning signs."""
    return warning_signs(df).sum(axis=1).values


QUICK_SURE = 0.80       # how sure the model must be before a quick review of its report is reasonable


def quick_review(probabilities, signs):
    """Which patients have a report a nurse could reasonably accept after a quick look: the model is at least
    80% sure, suggests level 3 to 5, and the patient shows no warning sign. Everyone else is checked in person.
    A suggested level 1 or 2 is not in here because it needs no weighing: that patient goes in at once."""
    probabilities, signs = np.asarray(probabilities), np.asarray(signs)
    return (probabilities.max(axis=1) >= QUICK_SURE) & (most_likely(probabilities) >= 3) & (signs == 0)


def quick_review_report(df, probabilities):
    """Is the model's level good enough on the patients where a quick review would be offered?"""
    truth, nurse = df["KTAS_expert"].values, df["KTAS_RN"].values
    ai, quick = most_likely(probabilities), quick_review(probabilities, vital_sign_rule(df))
    print("\n8. WHEN IS A QUICK REVIEW OF THE AI'S REPORT REASONABLE?")
    print(f"   Rule: the model is at least {QUICK_SURE:.0%} sure, suggests level 3 to 5, and there is no warning sign.")
    for name, pick in (("Quick review offered", quick), ("Personal check advised", ~quick)):
        print(f"   {name:24} {pick.sum():4d} patients ({pick.mean():.0%}):  AI right {np.mean(ai[pick] == truth[pick]):.0%}, "
              f"too low {np.mean(ai[pick] > truth[pick]):.0%}   |   nurses right {np.mean(nurse[pick] == truth[pick]):.0%}, "
              f"too low {np.mean(nurse[pick] > truth[pick]):.0%}")
    urgent = truth <= 2
    print(f"   Of the {urgent.sum()} patients experts rated level 1 or 2, {int((quick & urgent).sum())} would be offered a quick review.")
    return quick


def make_flag_model():
    """A model built for the second check itself: it sees everything known at the door AND the
    nurse's level, and learns to predict 'the nurse rated this patient too low'."""
    prepare = ColumnTransformer([
        ("numbers", Pipeline([("fill", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), NUMBERS),
        ("categories", OneHotEncoder(handle_unknown="ignore"), CATEGORIES + ["KTAS_RN"]),
        ("complaint", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=2, sublinear_tf=True), TEXT),
    ])
    return Pipeline([("prepare", prepare),
                     ("model", LogisticRegression(max_iter=3000, C=1.0, class_weight="balanced"))])


def flag_model_score(df):
    """Each patient scored by a flag model that never saw them (5-fold cross-validation)."""
    under = (df["KTAS_RN"] > df["KTAS_expert"]).astype(int)
    folds = StratifiedKFold(5, shuffle=True, random_state=SEED)
    return cross_val_predict(make_flag_model(), df, under, cv=folds, method="predict_proba")[:, 1]


def caught_within_budget(score, under, budget, rng=None):
    """If nurses can re-check only `budget` of all patients, taking the highest scores first,
    what share of the under-rated patients do they reach? Ties are broken at random."""
    rng = rng or np.random.default_rng(SEED)
    order = np.lexsort((rng.random(len(score)), -np.asarray(score, dtype=float)))
    chosen = order[: int(round(budget * len(score)))]
    return under[chosen].sum() / under.sum()


def plausible_range(statistic, n, rounds=1000):
    """95% range from re-drawing the patients with replacement (bootstrap)."""
    rng = np.random.default_rng(SEED)
    values = [statistic(rng.integers(0, n, n)) for _ in range(rounds)]
    return np.percentile(values, [2.5, 97.5])


# ---------------------------------------------------------------- report

def main():
    df = load()
    truth = df["KTAS_expert"].values
    nurse = df["KTAS_RN"].values
    n = len(df)
    print(f"{n} patients. Expert levels: " + ", ".join(f"L{l}: {(truth == l).sum()}" for l in LEVELS))

    probabilities = fair_probabilities(df)
    plain, safe = most_likely(probabilities), safety_first(probabilities)
    common = pd.Series(truth).mode()[0]

    print("\n1. HOW OFTEN EACH ONE MATCHES THE EXPERT PANEL")
    print(line(f"Baseline: always level {common}", summary(truth, np.full(n, common)), n))
    print(line("AI, most likely level", summary(truth, plain), n))
    print(line("AI, safety-first", summary(truth, safe), n))
    print(line("Nurses", summary(truth, nurse), n))

    print("\n2. THE AI AS A SECOND CHECK ON THE NURSE")
    print("   Flag a patient when the AI's safety-first level is MORE urgent than the nurse's.")
    under = nurse > truth                       # the nurse rated the patient too low
    flag = safe < nurse
    print(f"   Patients the nurse rated too low: {under.sum()}.  The AI flags {int((flag & under).sum())} of them "
          f"({(flag & under).sum() / under.sum():.0%}).")
    fine = ~under
    print(f"   Patients the nurse did not rate too low: {fine.sum()}.  The AI wrongly flags {int((flag & fine).sum())} "
          f"({(flag & fine).sum() / fine.sum():.0%}).")
    print(f"   Total flags a nurse would have to re-check: {int(flag.sum())} of {n} ({flag.mean():.0%}).")

    print("\n3. WHEN THE AI SAYS 'NOT SURE'")
    confidence = probabilities.max(axis=1)
    for cut in [0.0, 0.5, 0.6, 0.7, 0.8]:
        keep = confidence >= cut
        s = summary(truth[keep], plain[keep])
        print(f"   confident at {cut:.0%}+: handles {keep.mean():4.0%} of patients, exact {s['exact']:.0%}, "
              f"rated too low {s['under']:.0%}")

    print("\n4. SIMULATED WAITING TIME (invented arrivals, 3 doctors, busy department)")
    for name, (urgent_wait, all_wait) in compare_queues(df, safe).items():
        print(f"   {name:28} urgent patients wait {urgent_wait:5.1f} min    everyone waits {all_wait:5.1f} min")

    print("\n5. IS IT FAIR?  Share of patients rated too low")
    groups = {"Women": df["Sex"] == 1, "Men": df["Sex"] == 2, "Under 65": df["Age"] < 65, "65 and over": df["Age"] >= 65}
    for name, mask in groups.items():
        m = mask.values
        print(f"   {name:12} n={m.sum():4}   AI safety-first {(safe[m] > truth[m]).mean():.0%}   "
              f"nurses {(nurse[m] > truth[m]).mean():.0%}")

    print("\n6. TRAINED AT ONE HOSPITAL, TESTED AT THE OTHER (most likely level)")
    for train_group, test_group in [(1, 2), (2, 1)]:
        train, test = df[df["Group"] == train_group], df[df["Group"] == test_group]
        model = make_model().fit(train, train["KTAS_expert"])
        p = np.zeros((len(test), 5))
        p[:, [LEVELS.index(c) for c in model.classes_]] = model.predict_proba(test)
        print(line(f"Train hospital {train_group}, test hospital {test_group}",
                   summary(test["KTAS_expert"].values, most_likely(p)), len(test)))
    for group in [1, 2]:
        m = (df["Group"] == group).values
        print(line(f"Same-data check, hospital {group} patients", summary(truth[m], plain[m]), m.sum()))

    second_check_report(df, probabilities)
    quick_review_report(df, probabilities)


def second_check_report(df, probabilities):
    truth, nurse = df["KTAS_expert"].values, df["KTAS_RN"].values
    under = nurse > truth
    n = len(df)
    scores = {
        "Vital-sign rule (simple baseline)": vital_sign_rule(df),
        "Least urgent first (simple baseline)": nurse.astype(float),   # re-check whoever the nurse rated lowest
        "AI level model vs nurse": urgency_gap_score(probabilities, nurse),
        "AI flag model (sees nurse level)": flag_model_score(df),
    }
    print("\n7. THE SECOND CHECK, TESTED HARDER")
    print(f"   {under.sum()} of {n} patients were rated too low by the nurse. If nurses can re-check only part")
    print("   of the patients, highest scores first, what share of those under-rated patients is reached?")
    budgets = [0.10, 0.20, 0.30]
    print(f"   {'':36}" + "".join(f"re-check {b:.0%}        " for b in budgets))
    print(f"   {'Random choice':36}" + "".join(f"{b:.0%}                 " for b in budgets))
    table = {}
    for name, score in scores.items():
        cells = []
        for b in budgets:
            value = caught_within_budget(score, under, b)
            low, high = plausible_range(
                lambda pick, s=score, b=b: caught_within_budget(s[pick], under[pick], b) if under[pick].any() else np.nan, n)
            cells.append((value, low, high))
        table[name] = cells
        print(f"   {name:36}" + "".join(f"{v:.0%} ({lo:.0%}-{hi:.0%})      " for v, lo, hi in cells))
    rule = scores["Vital-sign rule (simple baseline)"] >= 1
    print(f"\n   Vital-sign rule as a yes/no flag (any warning sign): flags {rule.sum()} patients ({rule.mean():.0%}), "
          f"reaches {(rule & under).sum()} of {under.sum()} ({(rule & under).sum() / under.sum():.0%}).")
    safe = safety_first(probabilities)
    flag = safe < nurse
    low, high = plausible_range(lambda pick: (flag[pick] & under[pick]).sum() / max(1, under[pick].sum()), n)
    print(f"   Safety-first flag from before: flags {flag.sum()} patients ({flag.mean():.0%}), "
          f"reaches {(flag & under).sum()} of {under.sum()} ({(flag & under).sum() / under.sum():.0%}, range {low:.0%}-{high:.0%}).")
    strength = flag_strength(probabilities, nurse)
    strong, any_flag = strength == "strong", strength != ""
    low, high = plausible_range(lambda pick: (strong[pick] & under[pick]).sum() / max(1, under[pick].sum()), n)
    print(f"   Strong flag (the model thinks it more likely than not that the patient is more urgent): flags "
          f"{strong.sum()} patients ({strong.mean():.0%}), reaches {(strong & under).sum()} of {under.sum()} "
          f"({(strong & under).sum() / under.sum():.0%}, range {low:.0%}-{high:.0%}). "
          f"{(strong & under).sum() / strong.sum():.0%} of these flags are right, against "
          f"{(flag & under).sum() / flag.sum():.0%} for the safety-first flag.")
    print(f"   Strong or mild flag together: flags {any_flag.sum()} patients ({any_flag.mean():.0%}), "
          f"reaches {(any_flag & under).sum()} of {under.sum()} ({(any_flag & under).sum() / under.sum():.0%}).")
    return scores, table


if __name__ == "__main__":
    main()
