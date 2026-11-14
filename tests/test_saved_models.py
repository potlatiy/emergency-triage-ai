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
