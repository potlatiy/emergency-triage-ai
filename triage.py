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
    random, and each visit takes a random time averaging `service_minutes`. Within one simulated
    day only the ORDER in which waiting patients are called depends on `priority`.
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
    """Each queue order is run on its own 200 random days (not on the same days), so a difference of a
    minute or two between two orders is within the luck of the draw. The large differences are not."""
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
    # How far to trust "too low as rarely as the nurses": we picked the 80% cut-off on these same patients,
    # so we look at the range and at other random splits of the data.
    n = len(df)
    low, high = plausible_range(lambda pick: np.mean(ai[pick][quick[pick]] > truth[pick][quick[pick]]), n)
    print(f"   The AI's too-low share where a quick review is offered has a 95% range of {low:.0%} to {high:.0%}.")
    shares = []
    for seed in range(10):
        folds = StratifiedKFold(5, shuffle=True, random_state=seed)
        other = cross_val_predict(make_model(), df, df["KTAS_expert"], cv=folds, method="predict_proba")
        pick = quick_review(other, vital_sign_rule(df))
        shares.append((np.mean(most_likely(other)[pick] > truth[pick]), np.mean(nurse[pick] > truth[pick])))
    ai_share, nurse_share = np.mean(shares, axis=0)
    print(f"   With ten other random splits of the data: AI too low {ai_share:.1%} on average, nurses {nurse_share:.1%}. "
          f"So the AI is about as good as the nurses there, or slightly worse, not better.")
    unmeasured = df["Saturation"].isna().to_numpy()
    print(f"   {int((quick & unmeasured).sum())} of the {quick.sum()} had no oxygen measurement at all (one of the two hospitals "
          f"never recorded it), so 'no warning sign' means no warning sign among what was measured.")
    return quick


# ---------------------------------------------------------------- a range of levels with a promise

RANGE_COVERAGE = 0.90   # the promise: the expert's level is inside the range for 9 patients in 10


def range_cut(probabilities, truth, coverage=RANGE_COVERAGE):
    """How small a level's probability may be for the level to stay in the range.

    For every patient, look at the probability the model gave to the level the experts chose. Sort those from
    smallest to largest and put the cut just low enough that `coverage` of the patients keep the experts'
    level. On new patients the experts' level then stays in the range about that often. The method is called
    conformal prediction; no number is tuned by hand."""
    given = np.sort(np.asarray(probabilities)[np.arange(len(truth)), np.asarray(truth) - 1])
    may_miss = int(np.floor((len(given) + 1) * (1 - coverage)))          # how many patients the promise lets us miss
    return float(given[may_miss - 1]) if may_miss else 0.0


def level_range(probabilities, cut):
    """The most urgent and the least urgent level of each patient's range.

    A level is in the range when its probability reaches the cut. The most likely level is always in, and so
    is every level between two levels that are in: a range has no holes."""
    probabilities = np.asarray(probabilities)
    keep = probabilities >= cut
    keep[np.arange(len(probabilities)), probabilities.argmax(axis=1)] = True
    return keep.argmax(axis=1) + 1, 5 - keep[:, ::-1].argmax(axis=1)


def level_range_figures(truth, probabilities, coverage=RANGE_COVERAGE, rounds=500):
    """Is the promise kept? The cut is found on one random half of the patients and tried on the other half,
    `rounds` times. Every probability already comes from a model that never saw the patient."""
    truth, n = np.asarray(truth), len(truth)
    rng = np.random.default_rng(SEED)
    rows, by_level = [], []
    for _ in range(rounds):
        order = rng.permutation(n)
        first, second = order[: n // 2], order[n // 2:]
        low, high = level_range(probabilities[second], range_cut(probabilities[first], truth[first], coverage))
        t = truth[second]
        inside = (low <= t) & (t <= high)
        width, urgent = high - low + 1, t <= 2
        rows.append([inside.mean(), width.mean(), (high == low).mean(), inside[urgent].mean(),
                     (t < low).mean(), (t > high).mean(),
                     (t[urgent] < low[urgent]).mean(),                  # urgent patients whose whole range is less urgent
                     inside[width == 1].mean(), inside[width == 2].mean(), inside[width >= 3].mean(),
                     (width == 1).mean(), (width == 2).mean(), (width >= 3).mean()])
        by_level.append([inside[t == level].mean() for level in LEVELS])
    rows = np.array(rows)
    mean = rows.mean(axis=0)
    return {"inside": mean[0], "inside_low": np.percentile(rows[:, 0], 2.5), "inside_high": np.percentile(rows[:, 0], 97.5),
            "width": mean[1], "one_level": mean[2], "urgent_inside": mean[3], "more_urgent_than_range": mean[4],
            "less_urgent_than_range": mean[5], "urgent_above_range": mean[6],
            # How often the experts' level was inside, for ranges of one level, two levels, three or more.
            "inside_by_width": {1: mean[7], 2: mean[8], 3: mean[9]}, "share_by_width": {1: mean[10], 2: mean[11], 3: mean[12]},
            "by_level": dict(zip(LEVELS, np.mean(by_level, axis=0)))}


def range_across_hospitals(df, coverage=RANGE_COVERAGE):
    """Does the promise survive a move? The model and the cut come from one hospital (the cut from that hospital's
    own fair probabilities) and are tried on the patients of the other. Returns [(from, to, inside, urgent inside)]."""
    rows = []
    for home, away in [(1, 2), (2, 1)]:
        train, test = df[df["Group"] == home], df[df["Group"] == away]
        cut = range_cut(fair_probabilities(train), train["KTAS_expert"].values, coverage)
        model = make_model().fit(train, train["KTAS_expert"])
        p = np.zeros((len(test), 5))
        p[:, [LEVELS.index(c) for c in model.classes_]] = model.predict_proba(test)
        low, high = level_range(p, cut)
        t = test["KTAS_expert"].values
        inside = (low <= t) & (t <= high)
        rows.append((home, away, float(inside.mean()), float(inside[t <= 2].mean())))
    return rows


def level_range_report(df, probabilities):
    truth = df["KTAS_expert"].values
    print("\n9. A RANGE OF LEVELS WITH A PROMISE (the cut is found on one half of the patients, tried on the other, 500 times)")
    for coverage in (RANGE_COVERAGE, 0.95):
        f = level_range_figures(truth, probabilities, coverage)
        print(f"   Promise {coverage:.0%}: the expert's level was inside the range for {f['inside']:.1%} of patients "
              f"(from {f['inside_low']:.0%} to {f['inside_high']:.0%} over the 500 tries).")
        print(f"      The range is {f['width']:.1f} levels wide on average; it is a single level for {f['one_level']:.0%} of patients.")
        print(f"      Missed: the patient was more urgent than the range for {f['more_urgent_than_range']:.1%}, "
              f"less urgent for {f['less_urgent_than_range']:.1%}.")
        print(f"      Of the urgent patients (levels 1-2), {f['urgent_above_range']:.0%} got a range that was entirely less urgent "
              f"than they were: for them a miss is nearly always the dangerous way.")
        print("      Inside, by how wide the range is: " + ", ".join(
            f"{name} {f['inside_by_width'][w]:.0%} ({f['share_by_width'][w]:.0%} of patients)"
            for w, name in [(1, "one level"), (2, "two levels"), (3, "three or more")]) + ".")
        print("      By expert level: " + ", ".join(f"L{level} {share:.0%}" for level, share in f["by_level"].items())
              + f".  Urgent patients (levels 1-2): {f['urgent_inside']:.0%}.")
    likely = most_likely(probabilities)
    low, high = np.maximum(likely - 1, 1), np.minimum(likely + 1, 5)
    inside = (low <= truth) & (truth <= high)
    print(f"   Baseline, the most likely level and one level either side: inside for {inside.mean():.1%}, "
          f"{(high - low + 1).mean():.1f} levels wide; urgent patients {inside[truth <= 2].mean():.0%}.")
    print(f"   The single most likely level: right for {(likely == truth).mean():.0%}.")
    print("   Weak spot: the promise holds on average, not for every group. Levels 1, 2 and 5 are inside less often.")
    for home, away, inside_away, urgent_away in range_across_hospitals(df):
        print(f"   Model and cut from hospital {home}, tried at hospital {away}: inside for {inside_away:.0%}, "
              f"urgent patients {urgent_away:.0%}.")
    print("   So the promise is for patients like the ones in the data. It does not travel to another hospital.")


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
    what share of the under-rated patients do they reach?

    A simple rule gives many patients the same score, so the cut-off can fall inside a group of equals,
    and which of them are re-checked is luck. That group is counted by its share: if 40 of 100 equal
    patients fit in the budget, 40% of the under-rated ones among them count as reached. (Breaking the
    tie with one random draw made the simple rules look up to 6 points better or worse than they are.)"""
    score, under = np.asarray(score, dtype=float), np.asarray(under, dtype=bool)
    take = int(round(budget * len(score)))
    cut = np.sort(score)[::-1][take - 1]                  # the lowest score that still gets a re-check
    surely, maybe = score > cut, score == cut
    share = (take - surely.sum()) / maybe.sum()           # of the group at the cut-off, the part that fits
    return (under[surely].sum() + share * under[maybe].sum()) / under.sum()


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
    level_range_report(df, probabilities)


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
