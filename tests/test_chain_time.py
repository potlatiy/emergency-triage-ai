"""Checks on the whole-chain simulation (chain_time.py). They test that the simulation behaves like a
department should; they cannot test the assumed timings, which nobody has measured."""

from dataclasses import replace

import numpy as np
import pytest

import chain_time
from chain_time import AFTER, BEFORE, Timings

NOTHING_CHANGES = dict(history_at_nurse=0.0, history_at_doctor=0.0, reading_report=0.0, recheck_minutes=0.0,
                       accept_share=0.0, fix_share=0.0)


@pytest.fixture(scope="module")
def people():
    return chain_time.patients()


def test_the_patients_come_from_the_real_data(people):
    assert len(people["truth"]) == 1267 and set(np.unique(people["truth"])) == {1, 2, 3, 4, 5}
    assert 0.20 < people["quick"].mean() < 0.32               # about 1 patient in 4 is offered a quick review
    assert 0.15 < people["flagged"].mean() < 0.27              # about 1 patient in 5 gets the strong flag
    assert not (people["quick"] & (people["ai"] <= 2)).any()   # an emergency level is never a "quick review"


def test_every_patient_is_seen_once_and_nobody_waits_a_negative_time(people):
    luck = chain_time.draws(len(people["truth"]), Timings(), np.random.default_rng(1))
    for chain in (False, True):
        day = chain_time.one_day(people, luck, Timings(), chain)
        assert len(day["doctor_wait"]) == 1267
        assert (day["nurse_wait"] >= 0).all() and (day["doctor_wait"] >= 0).all() and (day["nurse_minutes"] > 0).all()
        assert sorted(day["truth"]) == sorted(people["truth"])


def test_a_chain_that_changes_nothing_gives_exactly_the_same_day(people):
    average, saved = chain_time.compare(people, replace(Timings(), **NOTHING_CHANGES), rounds=5)
    assert np.allclose(average[BEFORE]["everyone"], average[AFTER]["everyone"])
    assert np.allclose(average[BEFORE]["urgent"], average[AFTER]["urgent"])
    assert np.allclose(saved["everyone"], 0) and np.allclose(saved["urgent"], 0)


def test_the_most_urgent_wait_less_for_a_doctor_than_everyone(people):
    average, _ = chain_time.compare(people, rounds=10)
    for name in (BEFORE, AFTER):
        assert average[name]["urgent"][2] < average[name]["everyone"][2] / 3


def test_each_minute_the_doctor_saves_shortens_the_wait(people):
    door_to_doctor = [chain_time.compare(people, replace(Timings(), history_at_doctor=minutes), rounds=15)[0][AFTER]["everyone"][3]
                      for minutes in (0.0, 1.0, 2.0)]
    assert door_to_doctor[0] > door_to_doctor[1] > door_to_doctor[2]


def test_a_report_that_only_costs_time_makes_the_department_slower(people):
    """The warning in the write-up: if nobody saves anything and the nurse needs longer, the chain hurts."""
    costly = replace(Timings(), **{**NOTHING_CHANGES, "reading_report": 2.0})
    average, saved = chain_time.compare(people, costly, rounds=10)
    assert average[AFTER]["everyone"][3] > average[BEFORE]["everyone"][3]
    assert saved["everyone"].mean() < 0


def test_the_same_seed_gives_the_same_numbers(people):
    first, _ = chain_time.compare(people, rounds=4, seed=7)
    again, _ = chain_time.compare(people, rounds=4, seed=7)
    assert np.allclose(first[AFTER]["everyone"], again[AFTER]["everyone"])
