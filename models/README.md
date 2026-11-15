# The trained models

Two models, trained on all 1,267 patients in `data/ktas_data.csv` and saved here so nobody has to train them.

| File | What it predicts | Tested on patients it had not seen |
|---|---|---|
| `urgency_model.joblib` | The urgency level, 1 (most urgent) to 5 | Exact level right for 68% (nurses: 85%) |
| `department_model.joblib` | One of nine hospital departments | Right for 70%; among its top three for 88% |
| `model_info.json` | The details: date, library versions, inputs, answers, data fingerprints, and the cut for Meddy's range of levels | Experts' level inside the range for 90.6% |

The percentages come from the same recipe tested with 5-fold cross-validation, where every patient is scored by
a model that never saw them (see `triage.ipynb`). The files here were then trained on **all** patients for the
demonstration, so they must not be tested on this data again: they have already seen every answer.

Both are logistic regression models. They read the same things: age, pain score, how busy the department is,
six vital signs, sex, how the patient arrived, injury, alertness, and the complaint in words. Neither is given
the nurse's level, the diagnosis or the outcome.

## Use them

```python
import pandas as pd
import trained_models

urgency, department = trained_models.ready()

patient = pd.DataFrame([{
    "Chief_complain": "ant. chest pain", "Age": 67, "Sex": 2, "Arrival mode": 2, "Injury": 1, "Mental": 1,
    "Pain": 1, "NRS_pain": 7, "Patients number per hour": 8, "SBP": 88, "DBP": 54, "HR": 128, "RR": 28,
    "BT": 36.4, "Saturation": 89, "unmeasurable": 0,
}])
print(urgency.predict_proba(patient))        # the probability of each level, 1 to 5
print(department.predict(patient))           # the most likely department
```

Run this from the top folder of the repository. The codes for sex, arrival and alertness are explained in
`data/ktas_codebook.csv`. The digital hospital (`hospital/`) and the demo (`app.py`) load the models the same way.

## Make them again

```bash
python trained_models.py
```

Do this after any change to the data, to `data/diagnosis_departments.csv`, or to the model code in `triage.py`
and `assignment.py`. A test (`tests/test_saved_models.py`) fails if the saved files no longer match the code.

## Three things to know

- **They only load with the same version of scikit-learn** they were saved with (written in `model_info.json`).
  With any other version, `trained_models.ready()` trains the models again (and says so in one line) from the data, which takes
  about a second, so the project still works.
- **Only load model files from this repository.** A `.joblib` file can run code when it is opened, so a file
  from an unknown source is a risk. `ready()` refuses a model file that does not match `model_info.json`.
- **These are prototype models.** One country, two hospitals, 1,267 patients. This project is a prototype and
  is not medical advice.
