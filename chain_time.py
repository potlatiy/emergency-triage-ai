"""Does the whole chain save time? A before-and-after SIMULATION of one emergency department.

    python chain_time.py        prints the tables and saves images/chain_time.png

Read this first. Our data has no times in it at all: no arrival times, no minutes at the nurse station, no
minutes with a doctor. So every timing below is an ASSUMPTION, written in one place (Timings) so that anyone
can change it. What does come from the real 1,267 patients:
  - how urgent each patient really was (the experts' level) and the level the nurse gave;
  - which patients would be offered a quick review of the AI's report (the tested rule, on the model's real
    answers to patients it had not seen), and the AI's level for them;
  - which patients the strong second-check flag would be raised for.

The department is two steps, each with a line in front of it:
  1. the nurse station: first come, first served;
  2. the doctors: most urgent level first, then first come.

BEFORE: the nurse does a full assessment of everyone and asks the history questions herself; the doctor asks
        them again. The line is ordered by the nurse's level.
AFTER:  the patient answers the questions on the phone, so neither the nurse nor the doctor has to ask them.
        The nurse reads the AI's report. Where a quick review is offered she accepts it, and the line then
        uses the AI's level; everyone else gets a personal check, and a strong flag costs her a re-check.

Because the timings are assumed, the result is not "the app saves N minutes". The result is which assumptions
the answer depends on, and how much. That is what the second table shows.
"""

import heapq
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from triage import SEED, fair_probabilities, flag_strength, load, most_likely, quick_review, vital_sign_rule


@dataclass(frozen=True)
class Timings:
    """Every number here is our assumption, in minutes unless it says otherwise. None is measured."""
    # the department (the same before and after)
    doctors: int = 3
    nurses: int = 1
    load: float = 0.95                  # how busy the doctors are BEFORE: 0.95 means nearly always occupied
    doctor_minutes: float = 20.0        # an average consultation
    nurse_minutes: float = 5.0          # an average full assessment at the nurse station
    # what the chain changes
    history_at_nurse: float = 1.5       # of the nurse's minutes: asking the questions the phone now asks
    history_at_doctor: float = 1.0      # of the doctor's minutes: asking them again
    reading_report: float = 0.5         # new work for the nurse: reading the AI's report
    quick_review_minutes: float = 1.5   # reading a report and pressing "Everything is correct"
    recheck_minutes: float = 1.0        # new work for the nurse: looking again when the strong flag is raised
    accept_share: float = 1.0           # of the quick reviews offered, how many the nurse accepts
    fix_share: float = 0.5              # of the truly under-rated patients she re-checks, how many she corrects


BEFORE = "Before"
AFTER = "After"


def patients():
    """What the simulation knows about each of the 1,267 real patients."""
    df = load()
    probabilities = fair_probabilities(df)            # every patient scored by a model that never saw them
    nurse = df["KTAS_RN"].to_numpy()
    return {
        "truth": df["KTAS_expert"].to_numpy(),
        "nurse": nurse,
        "ai": most_likely(probabilities),
        "quick": quick_review(probabilities, vital_sign_rule(df)),
        "flagged": flag_strength(probabilities, nurse) == "strong",
    }


def draws(n, timings, rng):
    """The luck of one simulated day: who arrives when, and how long each visit happens to take.
    The same luck is used before and after, so the two are compared on the same day."""
    gaps = rng.exponential(timings.doctor_minutes / (timings.doctors * timings.load), n)
    return {"order": rng.permutation(n), "arrive": np.cumsum(gaps), "nurse_luck": rng.exponential(1.0, n),
            "doctor_luck": rng.exponential(1.0, n), "accepts": rng.random(n), "fixes": rng.random(n)}


def one_day(people, luck, timings, chain):
    """Run one day. Returns, for every patient, the minutes spent waiting for the nurse, with the nurse and
    waiting for a doctor, plus the level the line used for them."""
    n = len(people["truth"])
    who, arrive = luck["order"], luck["arrive"]
    truth, nurse_level = people["truth"][who], people["nurse"][who]

    # ---- what the nurse does with each patient, and the level the line will use
    minutes = np.full(n, timings.nurse_minutes)
    level = nurse_level.copy()
    if chain:
        accepted = people["quick"][who] & (luck["accepts"] < timings.accept_share)
        flagged = people["flagged"][who] & ~accepted
        minutes = np.where(accepted, timings.quick_review_minutes,
                           timings.nurse_minutes - timings.history_at_nurse + timings.reading_report
                           + flagged * timings.recheck_minutes)
        level = np.where(accepted, people["ai"][who], level)
        corrected = flagged & (nurse_level > truth) & (luck["fixes"] < timings.fix_share)
        level = np.where(corrected, truth, level)          # a re-check that finds the under-rating fixes it
    minutes = minutes * luck["nurse_luck"]

    # ---- step 1: the nurse station, first come, first served
    free = [0.0] * timings.nurses
    heapq.heapify(free)
    nurse_wait, ready = np.zeros(n), np.zeros(n)
    for i in range(n):
        start = max(arrive[i], heapq.heappop(free))
        nurse_wait[i] = start - arrive[i]
        ready[i] = start + minutes[i]
        heapq.heappush(free, ready[i])

    # ---- step 2: the doctors, most urgent level first
    consult = (timings.doctor_minutes - (timings.history_at_doctor if chain else 0.0)) * luck["doctor_luck"]
    queue_order = np.argsort(ready, kind="stable")          # the order in which patients leave the nurse
    free = [0.0] * timings.doctors
    heapq.heapify(free)
    waiting, doctor_wait, clock, k = [], np.zeros(n), 0.0, 0
    while k < n or waiting:
        clock = max(clock, heapq.heappop(free))
        if not waiting and k < n and ready[queue_order[k]] > clock:
            clock = ready[queue_order[k]]
        while k < n and ready[queue_order[k]] <= clock:
            p = queue_order[k]
            heapq.heappush(waiting, (level[p], ready[p], p))
            k += 1
        _, _, p = heapq.heappop(waiting)
        doctor_wait[p] = clock - ready[p]
        heapq.heappush(free, clock + consult[p])
    return {"truth": truth, "level": level, "nurse_wait": nurse_wait, "nurse_minutes": minutes, "doctor_wait": doctor_wait}


PARTS = ["nurse_wait", "nurse_minutes", "doctor_wait"]


def summary(day):
    """Average minutes from the door to the doctor, in its three parts, for the most urgent and for everyone."""
    urgent = day["truth"] <= 2
    out = {}
    for name, pick in (("urgent", urgent), ("everyone", np.ones(len(urgent), bool))):
        parts = [day[part][pick].mean() for part in PARTS]
        out[name] = parts + [sum(parts)]
    out["urgent queued as level 3-5"] = float((urgent & (day["level"] >= 3)).sum())
    return out


def compare(people, timings=Timings(), rounds=100, seed=SEED):
    """Before and after on the same simulated days. Returns {scenario: {group: [average of each part, total]}}
    and, for every day, the minutes the chain saved for everyone and for the most urgent."""
    rng = np.random.default_rng(seed)
    totals = {BEFORE: [], AFTER: []}
    for _ in range(rounds):
        luck = draws(len(people["truth"]), timings, rng)
        for name, chain in ((BEFORE, False), (AFTER, True)):
            totals[name].append(summary(one_day(people, luck, timings, chain)))
    average = {name: {key: np.mean([np.atleast_1d(day[key]) for day in days], axis=0) for key in days[0]}
               for name, days in totals.items()}
    saved = {group: np.array([b[group][3] - a[group][3] for b, a in zip(totals[BEFORE], totals[AFTER])])
             for group in ("urgent", "everyone")}
    return average, saved


# What we vary, one thing at a time, to see how much the answer depends on each assumption.
WHAT_IFS = [
    ("Our assumptions", {}),
    ("The doctor saves nothing (still asks everything)", {"history_at_doctor": 0.0}),
    ("The doctor saves 2 minutes", {"history_at_doctor": 2.0}),
    ("The doctor saves 3 minutes", {"history_at_doctor": 3.0}),
    ("The nurse saves nothing on history", {"history_at_nurse": 0.0}),
    ("The nurse accepts no quick review", {"accept_share": 0.0}),
    ("Reading the report takes 2 minutes", {"reading_report": 2.0}),
    ("Two nurses", {"nurses": 2}),
    ("A quieter department (doctors 85% busy)", {"load": 0.85}),
    ("Pessimistic: nobody saves anything, the report takes 2 minutes",
     {"history_at_doctor": 0.0, "history_at_nurse": 0.0, "reading_report": 2.0, "accept_share": 0.0}),
]


def what_ifs(people, rounds=60):
    rows = []
    for name, change in WHAT_IFS:
        average, saved = compare(people, replace(Timings(), **change), rounds)
        rows.append((name, average, saved))
    return rows


def main():
    people = patients()
    base = Timings()
    average, saved = compare(people, base)
    urgent_n = int((people["truth"] <= 2).sum())
    print("DOES THE WHOLE CHAIN SAVE TIME? A simulation. Every timing is an assumption (see Timings in chain_time.py).")
    print(f"One nurse, {base.doctors} doctors who are busy {base.load:.0%} of the time before, 1,267 real patients per run, 100 runs.\n")
    print("1. FROM THE DOOR TO THE DOCTOR, WITH OUR ASSUMED TIMINGS (average minutes)")
    print(f"   {'':34}{'waiting for':>13}{'with the':>10}{'waiting for':>13}{'door to':>9}")
    print(f"   {'':34}{'the nurse':>13}{'nurse':>10}{'a doctor':>13}{'doctor':>9}")
    for group, label in (("urgent", f"Most urgent (levels 1-2, {urgent_n})"), ("everyone", "Everyone")):
        for name in (BEFORE, AFTER):
            a, b, c, total = average[name][group]
            print(f"   {label + ', ' + name.lower():34}{a:13.1f}{b:10.1f}{c:13.1f}{total:9.1f}")
    for group, label in (("urgent", "the most urgent"), ("everyone", "everyone")):
        low, high = np.percentile(saved[group], [2.5, 97.5])
        print(f"   Saved for {label}: {saved[group].mean():.0f} minutes on average (95% of simulated days: {low:.0f} to {high:.0f}).")
    print(f"   Urgent patients queued as level 3-5: {average[BEFORE]['urgent queued as level 3-5'][0]:.0f} before, "
          f"{average[AFTER]['urgent queued as level 3-5'][0]:.0f} after.")

    rows = what_ifs(people)
    print("\n2. HOW MUCH THE ANSWER DEPENDS ON EACH ASSUMPTION (average minutes from the door to the doctor)")
    print(f"   {'':64}{'everyone':>20}{'most urgent':>22}")
    print(f"   {'':64}{'before -> after':>20}{'before -> after':>22}")
    for name, a, _ in rows:
        e0, e1 = a[BEFORE]["everyone"][3], a[AFTER]["everyone"][3]
        u0, u1 = a[BEFORE]["urgent"][3], a[AFTER]["urgent"][3]
        print(f"   {name:64}{e0:8.0f} -> {e1:<6.0f}{'':3}{u0:8.0f} -> {u1:<6.0f}")
    return average, saved, rows


if __name__ == "__main__":
    average, saved, rows = main()
    try:
        import charts
        figure = charts.chain_time_chart(average, rows)
        figure.savefig(Path(__file__).parent / "images" / "chain_time.png", bbox_inches="tight")
        print("\nSaved images/chain_time.png")
    except AttributeError:
        pass
