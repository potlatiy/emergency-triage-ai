"""The model files in models/ must be loadable and must match what the code trains today."""

import numpy as np

import trained_models
from triage import load


def test_saved_models_are_usable_here():
    assert trained_models.why_not_usable() is None


def test_saved_models_answer_exactly_like_freshly_trained_ones():
    """If someone changes the model code or the data and forgets to re-save, this fails."""
    data = load()
    saved_urgency, saved_department = trained_models.ready(announce=False)
    fresh_urgency, fresh_department = trained_models.train(data)
    assert np.allclose(saved_urgency.predict_proba(data), fresh_urgency.predict_proba(data), atol=1e-6)
    assert np.allclose(saved_department.predict_proba(data), fresh_department.predict_proba(data), atol=1e-6)
    assert list(saved_department.classes_) == list(fresh_department.classes_)


def test_a_different_scikit_learn_version_means_training_again(monkeypatch):
    monkeypatch.setattr(trained_models.sklearn, "__version__", "0.0.0")
    assert "scikit-learn" in trained_models.why_not_usable()
    urgency, department = trained_models.ready(announce=False)      # still works, by training
    assert list(urgency.classes_) == [1, 2, 3, 4, 5]


def test_a_tampered_model_file_is_not_loaded(monkeypatch, tmp_path):
    fake = tmp_path / "urgency_model.joblib"
    fake.write_bytes(b"not the real model")
    monkeypatch.setattr(trained_models, "URGENCY_FILE", fake)
    assert "does not match" in trained_models.why_not_usable()


def test_a_changed_recipe_or_other_inputs_means_training_again(monkeypatch):
    monkeypatch.setattr(trained_models, "recipe", lambda: {"urgency": "another", "department": "another"})
    assert "built has changed" in trained_models.why_not_usable()
    monkeypatch.undo()
    monkeypatch.setattr(trained_models, "NUMBERS", trained_models.NUMBERS[:-1])
    assert "other inputs" in trained_models.why_not_usable()


def test_the_saved_range_cut_and_its_figures_are_what_the_data_gives_today():
    """The cut for Meddy's range of levels is saved next to the models. It must be what the fair probabilities
    give, never something worked out from the saved model's answers on patients it has already seen. It is saved
    unrounded: rounding it up dropped the very patient the cut was set on. The figures saved with it are the
    ones the code measures."""
    import json
    from triage import fair_probabilities, level_range_figures, load, range_cut
    data = load()
    unseen = fair_probabilities(data)
    truth = data["KTAS_expert"].values
    assert trained_models.why_not_usable() is None
    settings = trained_models.level_range_settings()
    assert settings["cut"] == range_cut(unseen, truth) == trained_models.level_range_cut()
    kept = (unseen[range(len(truth)), truth - 1] >= settings["cut"]).sum()
    assert kept >= 0.9 * (len(truth) + 1) - 1                 # the promise, on the patients the cut was set on
    figures = level_range_figures(truth, unseen)
    saved = json.loads(trained_models.INFO_FILE.read_text())["level_range"]
    assert saved["expert_level_inside_on_unseen_patients"] == round(float(figures["inside"]), 3)
    assert saved["average_levels_in_range"] == round(float(figures["width"]), 2)
    assert saved["urgent_patients_inside"] == round(float(figures["urgent_inside"]), 3)
    assert settings["inside"] == {width: round(float(share), 3) for width, share in figures["inside_by_width"].items()}
