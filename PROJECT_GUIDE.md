# Project guide: cutting the wait in the emergency department

This document explains the problem we are working on, what we built to address it, and what each team
member does next. The results and the technical write-up are in [README.md](README.md).

*This project is a prototype and is not medical advice.*

## 1. The problem: people wait too long, and the wrong people wait longest

### How long the wait is

Emergency departments in many countries can no longer see patients on time.

- **England.** The official standard is that 95% of patients are admitted, transferred or discharged within
  four hours. It was last met nationally in 2013-14. In 2025-26 the figure was 74.9%, and in the major
  emergency departments only 60.9%
  ([National Audit Office](https://www.nao.org.uk/reports/managing-the-flow-of-patients-through-hospital-from-ae/),
  [Nuffield Trust](https://www.nuffieldtrust.org.uk/resource/a-e-waiting-times)).

### Why the wait is dangerous

- **Waiting is linked to deaths.** A study of more than 5 million patients admitted through English emergency
  departments found that the risk of dying within 30 days rose steadily once the wait passed five hours. For
  every 82 patients whose admission was delayed beyond six to eight hours, there was one extra death
  ([Jones and colleagues, Emergency Medicine Journal, 2022](https://pubmed.ncbi.nlm.nih.gov/35042695/)).
- **It harms people who go home, too.** A study of 13.9 million discharged patients in Ontario found that those
  seen during shifts with average stays of six hours or more were more likely to die or be admitted within a
  week than those seen during shifts with stays under one hour
  ([Guttmann and colleagues, BMJ, 2011](https://pubmed.ncbi.nlm.nih.gov/21632665/)).

Both are observational studies. They show a link between long waits and worse outcomes, not proof of cause.

### Why the wait happens inside the department

Some of the delay comes from outside the department (no free beds in the hospital). We cannot fix that.
The part we can address is how a patient moves through the front door:

1. **Too many hand-offs before the right doctor.** A patient is registered by one person, asked what is wrong
   by a second, and assigned by a third. Each step has its own small queue, and the patient repeats the same
   story each time.
2. **Urgent and minor cases wait in the same line.** Triage exists to prevent this: a nurse gives each patient
   an urgency level from 1 (most urgent) to 5. But triage is done in minutes, under pressure, and it is
   sometimes wrong.
3. **A wrong level costs the patient who can least afford it.** In the real data we use (1,267 patients from
   two Korean emergency departments), trained nurses rated about 1 patient in 10 as less urgent than an expert
   panel later decided. Those patients were sent to the slow part of the queue.
4. **Doctors and patients are matched by hand.** One specialist can have a full list while another has none.

### The problem in one sentence

Patients lose time at every hand-off before reaching a doctor, and the patients rated too low at triage lose
the most, even though they are the ones for whom waiting is most dangerous.

## 2. What we are doing about it

We cannot remove waiting: if ten people arrive and three doctors are free, seven wait. What we can change is
**who waits, for how long, and how many times they are handed over**. We work on three points.

| Point | What we built | Is AI used? | Tested on real data? |
|---|---|---|---|
| The level is sometimes wrong | A **second check**: a model reads the same facts as the nurse and flags patients it rates more urgent than she did | Yes | Yes. It flags 107 of the 131 under-rated patients (82%) |
| Urgent and minor cases share a line | An **electronic line** ordered by urgency, where waiting slowly moves a patient up | No, plain rules | Simulated only |
| Too many hand-offs | A **questionnaire at check-in** built for the complaint, and **direct assignment** to a doctor who is on duty, with the answers passed on so the story is told once | Partly | Assignment: being trained and tested now. Questionnaire: not tested |

### The patient's path in our system

1. **Entrance.** The patient says what is wrong. The visit gets a registration number in the hospital database.
2. **Check-in.** The system reads the complaint and builds a questionnaire: three danger-sign questions for
   that kind of problem, and two follow-up questions written for the exact complaint.
3. **Nurse station.** The nurse measures vital signs. The model suggests an urgency level and says when it is
   not sure. The nurse decides.
4. **Waiting room.** The electronic line orders everyone by urgency, then by waiting time.
5. **Doctor.** When called, the patient is matched to the right department and to the least busy doctor on
   duty there. The doctor receives the complaint, level, vital signs and questionnaire answers.

### What we do not claim

- **We do not claim minutes saved.** No public data records real queues, so our waiting-time figures come from
  a simulation and are labelled as one.
- **We do not claim the AI can replace a triage nurse.** On the exact level it is right 68% of the time and
  nurses 85%. That is why it is built as a second check.
- **We do not claim the questionnaire is validated.** Nobody has checked the AI-written questions, so their
  answers never change a patient's level.

## 3. How the AI in the system thinks

There are three AI parts. Each has one narrow job, and each hands its result to a person or a plain rule.

### Part A: the urgency model (trained on real data)

- **What it reads:** the complaint in words, age, sex, how the patient arrived, alertness, pain score and
  vital signs. It never sees the diagnosis, the outcome or the nurse's level.
- **How it reasons:** it turns the complaint into short letter patterns, gives every input a weight for each of
  the five levels, and adds the weights into five probabilities. The weights were learned from 1,267 real
  patients whose correct level was set by experts.
- **How it decides:** rating a patient too low is the dangerous mistake, so it does not simply pick the most
  likely level. It picks the level with the lowest expected cost, where one level too low costs three times as
  much as one level too high.
- **What it outputs:** a level, the probability of each level, and "not sure" when no level reaches 60%.

### Part B: the assignment model (being trained on real data)

- **What it reads:** the same facts as Part A.
- **What it learns from:** each real patient's final diagnosis, mapped to a department. The model learns which
  complaints and vital signs lead to which department.
- **What it outputs:** a department. A plain rule then picks the least busy doctor on duty there from the database.

### Part C: the question writer (a language model steered by a system prompt)

- **What it is:** a small general-purpose language model (Gemma 3) running on the same computer.
- **How we steer it:** with a **system prompt**. This is a fixed set of written instructions sent to the model
  before every request: its role, the rules for a good question, what it must never do, and worked examples.

**A note on "fine-tuning with a system prompt".** These are two different things, and it matters for what we
can claim.

| | Fine-tuning | System prompt |
|---|---|---|
| What changes | The model's internal weights | Nothing inside the model; only the instructions it is given |
| What it needs | Thousands of example question sets, a graphics card and hours of training | A carefully written text |
| Is it "trained on real data"? | Yes | No |
| Can we do it by the deadline? | No. No public real dataset of triage questions exists, and our laptops cannot train a language model | Yes |

A system prompt is the right tool for us, and it is a normal, respected technique. We describe it accurately:
the question writer is **instructed**, not **trained**. The real-data claim belongs to Parts A and B.

### Where people stay in charge

- The nurse confirms or changes every level.
- Only the hand-written danger-sign questions can move a patient up the line.
- The doctor receives the AI-written questions and answers as information.

## 4. Our plan of action

### Done

- [x] Real data found, downloaded and published (`data/`)
- [x] Urgency model trained and tested against nurses and simple rules (`triage.py`, `triage.ipynb`)
- [x] Second-check demo and electronic line (`app.py`)
- [x] Digital hospital with a floor plan, questionnaire, database and doctor assignment (`hospital/`)
- [x] Interactive mind map of the project (`docs/`)

### In progress

- [ ] Train the assignment model on real diagnoses and compare it with the key-word rule
- [ ] Steer the question writer with a system prompt
- [ ] Review the diagnosis-to-department table (`data/diagnosis_departments.csv`) by hand

### Before submitting (deadline: Sunday, November 15, 11:59 PM US Eastern time)

- [ ] Everyone reads the notebook and can explain every section
- [ ] README rewritten in our own words, every number checked against the notebook
- [ ] One real person tries the digital hospital; their reaction goes into the README
- [ ] Two-minute video recorded
- [ ] Every link opened in a private window
- [ ] Submission form sent (Health track), a first version by Sunday afternoon

## 5. Who does what

| Person | Role | Owns | Next actions |
|---|---|---|---|
| **Yegor** | Backend | `hospital/core.py`, `hospital/db.py`, `hospital/server.py`, `triage.py` | Check the assignment model's results. Check the database rules. Confirm the notebook runs in Colab |
| **Ostap** | Front end | `hospital/static/index.html`, `docs/index.html`, `app.py` | Try the digital hospital on a phone and a laptop. Fix anything unclear on screen. Record the video |
| **Denys** | Project decisions | `README.md`, this guide | Review the diagnosis-to-department table. Decide what the video says. Rewrite the README. Send the submission form |

Everyone, whatever their role, must be able to answer three questions:

1. Why is the AI worse than nurses at the level, and why is that acceptable?
2. Why is the test fair?
3. Which parts are tested on real data and which are demonstrations?

## 6. How to run everything

Install once:

```bash
pip install -r requirements.txt
```

| What | Command | Then open |
|---|---|---|
| All results | `python triage.py` | (prints to the screen) |
| Notebook | open `triage.ipynb` | |
| Second-check demo | `python app.py` | http://localhost:7861 |
| Digital hospital | `python hospital/server.py` | http://localhost:8000 |

For AI-written questions in the digital hospital, install [Ollama](https://ollama.com) and run
`ollama pull gemma3:4b` once. Without it, the hospital still works with the hand-written questions.

## 7. How we work together on GitHub

1. Get the latest version before you start: `git pull`
2. Make your change and try it.
3. Save it with a message that says what changed: `git add -A`, then `git commit -m "what changed"`
4. Share it: `git push`

If two people edit the same file at the same time, the second `git push` is refused. Run `git pull`, fix what
it reports, and push again. To avoid this, say in the team chat which file you are editing.

## 8. Data we use

| File | What it is | Source and licence |
|---|---|---|
| `data/ktas_data.csv` (and `.xlsx`) | 1,267 real emergency patients: complaint, vital signs, nurse level, expert level, final diagnosis | Moon and colleagues, PLOS ONE, 2019, https://doi.org/10.1371/journal.pone.0216972, CC BY 4.0 |
| `data/ktas_codebook.csv` (and `.xlsx`) | What each column means | Same |
| `data/diagnosis_departments.csv` | Our own table matching each diagnosis in the data to a department | Made by us from the data above |

`hospital/hospital.db` is not training data. It is the demonstration database of made-up doctors and test
visits, rebuilt each time the server starts, and it is not stored on GitHub.
