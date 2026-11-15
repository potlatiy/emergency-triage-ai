"""Patients like this one: the most similar patients among the 1,267 in the data, and what happened to them.

    python similar.py        prints how good the "similar patients" are, tested on patients they had not seen

Meddy shows a nurse five real patients from the data who look most like the one in front of her: what they
complained of, the level the nurse gave them and the level the expert panel gave them. It is evidence to look
at, next to the model's level. It is never a diagnosis: the diagnosis column of the data is not used here.

"Alike" is a mix of three things the urgency model also reads:
    40%  the words of the complaint (the same letter patterns the model uses)
    40%  the numbers: age, pain score, how busy the department is, the six vital signs
    20%  the tick-box answers: sex, how the patient arrived, injury, alertness, pain yes or no

Patients with the same kind of complaint come first. The kind is what the hospital's key-word rule reads in the
complaint ("pee" and "dysuria" are both urology), so a patient who writes in everyday words still finds the
patients the data describes in clinical words. A complaint the rule does not recognise has no kind.
"""

import numpy as np
from scipy import sparse
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import StratifiedKFold

from triage import CATEGORIES, NUMBERS, SEED, fair_probabilities, load, make_model, most_likely

SHARES = {"complaint": 0.4, "numbers": 0.4, "answers": 0.2}
NEIGHBOURS = 5          # how many similar patients are shown


class Similar:
    """Finds, for any patient, the most similar patients in `data`."""

    def __init__(self, data, kinds=None):
        self.data = data.reset_index(drop=True)
        self.kinds = None if kinds is None else np.asarray(kinds)       # the kind of complaint of every patient in the data
        # The same preparation as the urgency model: numbers filled in and scaled, tick-boxes one-hot, complaint as
        # letter patterns. It is fitted on `data` only, so nothing leaks from a patient who is being looked up.
        self.prepare = make_model().named_steps["prepare"].fit(self.data)
        self.parts = self._parts(self.data)

    def _parts(self, patients):
        """The prepared patients, cut into the three parts: numbers, tick-box answers, complaint words."""
        prepared = sparse.csr_matrix(self.prepare.transform(patients))
        numbers = len(NUMBERS)
        words = len(self.prepare.named_transformers_["complaint"].vocabulary_)
        answers = prepared.shape[1] - words
        return prepared[:, :numbers].toarray(), prepared[:, numbers:answers].toarray(), prepared[:, answers:]

    def order(self, patients, kinds=None, shares=SHARES):
        """For each of `patients`, the patients in the data from most to least alike. Those with the same kind of
        complaint come first (kinds: one per patient, None for a complaint the key-word rule did not recognise)."""
        total, complaint = self.alike(patients, shares)
        rank = total.copy()
        if kinds is not None and self.kinds is not None:
            for i, kind in enumerate(kinds):
                if kind is not None:
                    rank[i] += self.kinds == kind                # 1 for the same kind: always ahead of every other patient
        return np.argsort(-rank, axis=1, kind="stable"), total, complaint

    def alike(self, patients, shares=SHARES):
        """How alike each of `patients` is to each patient in the data, from 0 (nothing in common) to 1 (the same).
        Also returns the complaint part on its own, to tell when no complaint in the data resembles this one."""
        numbers, answers, words = self._parts(patients)
        known_numbers, known_answers, known_words = self.parts
        complaint = cosine_similarity(words, known_words)
        # Numbers: the average squared gap, in standard deviations, turned into 1 (no gap) down to 0 (far apart).
        gap = ((numbers[:, None, :] - known_numbers[None, :, :]) ** 2).mean(axis=2)
        same_answers = (answers @ known_answers.T) / len(CATEGORIES)         # the share of tick-boxes that agree
        total = shares["complaint"] * complaint + shares["numbers"] * np.exp(-gap / 2) + shares["answers"] * same_answers
        return total, complaint

    def nearest(self, patient, kind=None, k=NEIGHBOURS):
        """The k patients in the data most like this one (a one-row table with the model's columns), most alike first.
        kind: this patient's kind of complaint, or None."""
        order, total, complaint = self.order(patient, [kind])
        order = order[0, :k]
        found = self.data.iloc[order]
        return [{"complaint": str(row["Chief_complain"]), "age": float(row["Age"]),
                 "sex": "Female" if row["Sex"] == 1 else "Male",
                 "nurse_level": int(row["KTAS_RN"]), "expert_level": int(row["KTAS_expert"]),
                 "alike": round(float(total[0, i]), 2), "complaint_alike": round(float(complaint[0, i]), 2),
                 "same_kind": bool(kind is not None and self.kinds is not None and self.kinds[i] == kind)}
                for i, (_, row) in zip(order, found.iterrows())]


def fair_neighbours(data, k=NEIGHBOURS, shares=SHARES, kinds=None):
    """For every patient, the k most similar patients among those in the other four folds: the patient is
    never compared with themselves, and the preparation never saw them. Returns their positions in `data`
    and how alike their complaints were. kinds: the kind of complaint of every patient (None: not used)."""
    data = data.reset_index(drop=True)
    kinds = None if kinds is None else np.asarray(kinds, dtype=object)
    folds = StratifiedKFold(5, shuffle=True, random_state=SEED)          # the same folds as the urgency model's test
    found = np.zeros((len(data), k), dtype=int)
    words_alike = np.zeros((len(data), k))
    for others, held_out in folds.split(data, data["KTAS_expert"]):
        index = Similar(data.iloc[others], None if kinds is None else kinds[others])
        order, _, complaint = index.order(data.iloc[held_out], None if kinds is None else kinds[held_out], shares)
        order = order[:, :k]
        found[held_out] = others[order]
        words_alike[held_out] = np.take_along_axis(complaint, order, axis=1)
    return found, words_alike


def kinds_of(data):
    """The kind of complaint of every patient: the department the hospital's key-word rule reads in the complaint
    (never the diagnosis), or None when the rule finds no word it knows."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent / "hospital"))
    from core import kind_of_complaint
    return np.array([kind_of_complaint(c, injury == 2) for c, injury in zip(data["Chief_complain"], data["Injury"])],
                    dtype=object)


def vote(levels):
    """The level most of the similar patients had. A tie goes to the more urgent level."""
    counts = np.stack([(levels == level).sum(axis=1) for level in range(1, 6)], axis=1)
    return counts.argmax(axis=1) + 1


def report(data=None, k=NEIGHBOURS):
    """How much the similar patients tell us, measured on patients they had not seen."""
    data = load() if data is None else data.reset_index(drop=True)
    truth = data["KTAS_expert"].to_numpy()
    figures = {"patients": len(data), "shown": k,
               "baseline": float((truth == np.bincount(truth).argmax()).mean()),
               "model": float((most_likely(fair_probabilities(data)) == truth).mean())}
    print(f"PATIENTS LIKE THIS ONE: the {k} most similar patients, found among patients the looked-up patient was not part of")
    kinds = kinds_of(data)
    for name, shares, use_kinds in [("same kind first, then the 40/40/20 mix (used)", SHARES, kinds),
                                    ("the 40/40/20 mix without the kind", SHARES, None),
                                    ("complaint words only", {"complaint": 1, "numbers": 0, "answers": 0}, None),
                                    ("numbers only", {"complaint": 0, "numbers": 1, "answers": 0}, None)]:
        found, words_alike = fair_neighbours(data, k, shares, use_kinds)
        levels = truth[found]
        majority = float((vote(levels) == truth).mean())
        among = float((levels == truth[:, None]).any(axis=1).mean())
        print(f"   {name:48} most of them had the expert's level: {majority:.0%}   "
              f"at least one of them had it: {among:.0%}")
        if use_kinds is not None:
            figures.update(majority=majority, among=among, complaint_alike=float(words_alike.mean()),
                           recognised=float(np.mean([kind is not None for kind in kinds])))
    print(f"   The key-word rule recognised the complaint of {figures['recognised']:.0%} of the patients.")
    print(f"   For comparison: always the most common level {figures['baseline']:.0%}, the urgency model {figures['model']:.0%}.")
    print("   So the similar patients are weaker than the model. They are shown as evidence to look at, not as a second opinion.")
    return figures


if __name__ == "__main__":
    report()
