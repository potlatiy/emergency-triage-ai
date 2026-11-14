# Presentation notes

Everything needed to record the video, answer the judges and fill in the submission form.
All numbers here come from `triage.ipynb` and `assignment.py`. If you change the code, re-check them.

## The one sentence

Our AI is worse than a triage nurse at giving an urgency level, so the nurse always decides; but it catches most
of the patients nurses rate too low, and a line sorted by urgency gets the most urgent patients seen far sooner.

## Two-minute video script

Record the screen with your voice over it. No editing needed.

| Time | What is on screen | What to say |
|---|---|---|
| 0:00–0:15 | The digital hospital, entrance step | "In a crowded emergency department, the person with chest pain can end up waiting behind five sprained ankles. A nurse sorts the line, but under pressure about one patient in ten is rated less urgent than they really are." |
| 0:15–0:35 | Type "crushing chest pain and sweating", age 67, male. Tap "Make up a number" for the insurance card. At check-in tap one danger sign, what the pain feels like and the answers for the doctor | "You are the patient. You say what is wrong and get a quick check built for your complaint: danger signs to tap, and three history questions that an AI model on this laptop chose for you. A few taps, and no waiting." |
| 0:35–0:55 | Nurse station: choose Unstable, press "Measure and get Meddy's report". Show the report, then press "Everything is correct" | "The nurse measures the vital signs, and Meddy writes its report: the patient at a glance, a level, and the likely doctor. Our model, trained on 1,267 real emergency patients, says level 2. The nurse reads it and presses one button: everything is correct. The patient goes straight to the resuscitation bay." |
| 0:55–1:15 | Start again: "bad stomach pain and vomiting", feverish. Meddy advises a personal check: press "I want to check the patient myself", tap level 3, send. Show the line, press 15 minutes pass, then Call next | "For the next patient Meddy is less sure, so it advises the nurse to check in person. She gives her own level and the patient joins the electronic line. The patient's phone shows a ticket and a place in line, never the level. The line is ordered by urgency, and waiting moves you up so nobody is stuck. When called, a second model suggests the department and the database picks the least busy doctor on duty." |
| 1:15–1:45 | The second-check chart from the README | "Does it work? On the exact level the model is right 68% of the time and nurses 85%, so it cannot replace a nurse. As a second check it flags 107 of the 131 patients nurses rated too low. If a nurse can re-check one patient in five, simple rules find about a third of them. Our model finds two thirds." |
| 1:45–2:00 | The limits section of the README | "The waiting times are simulated, the data is from one country, and the questionnaire is untested. Next we would try it with real nurses." |

## Demo checklist before recording

1. Start the hospital: `python hospital/server.py`, then open http://localhost:8000
2. Run both patients of the script through once first. The AI model needs a few seconds to load the first time,
   and a complaint it has already seen is answered at once, so the recording will not wait.
3. Press "Start a new day" on the last screen so the recording begins with a clean waiting room.
4. Keep the browser window around 1,200 pixels wide so the floor plan and the panel sit side by side.

## Numbers to know by heart

| What | Figure |
|---|---|
| Patients in the data | 1,267, from two emergency departments in South Korea |
| Exact level: always guessing level 3 / our model / nurses | 38% / 68% / 85% |
| Patients nurses rated too low | 131 (about 1 in 10) |
| Of those, flagged by the second check | 107 (82%; plausible range 75–88%) |
| Share of all patients the nurse must re-check | 36% |
| The stricter flag the hospital shows: share of patients flagged / under-rated patients reached | 21% / 91 of 131 (69%) |
| Reports where a quick review is offered / AI level too low there / nurses too low there | 26% of patients / 7% / 7% |
| Reports where a personal check is advised: AI level right / nurses right | 63% / 84% |
| Under-rated patients reached when re-checking 1 in 5: simple rules / our model | 34–38% / 67% |
| Simulated wait for the most urgent: arrival order / AI levels / nurse levels / expert levels | 88 / 24 / 13 / 7 minutes |
| Right department: most common / key-word rule / our model | 21% / 67% / 70% |
| Right department among the model's top three | 88% |
| Exact level when the model moves to the other hospital | 55–58% (down from 68%) |

## Questions judges may ask

**Doesn't the "Everything is correct" button let the AI decide?**
The nurse still decides: she reads the report and can always check the patient herself. Meddy offers a quick review
only where its level held up in our tests (about 1 patient in 4: at least 80% sure, level 3 to 5, no warning sign).
There its level was too low for 7% of patients, the same as the nurses. For everyone else it advises a personal check.
We have not tested how real nurses use the button, and reading a report first may make them agree too easily. That is
why the app counts every verdict.

**Why is your AI worse than nurses?**
It reads a few words and seven numbers. A nurse sees and hears the patient. That is why we did not build a
replacement; we built a second check and a way to sort the line.

**If it is worse, why use it at all?**
Because it makes different mistakes. It flags 82% of the patients nurses under-rated. And in our simulation,
sorting the line by even a rough level cuts the wait of the most urgent patients from 88 minutes to 24.
Everyone else waits longer for it (about 107 minutes instead of 88): sorting moves waiting time, it does not remove it.

**How do you know the test is fair?**
Every patient was scored by a model that never saw them. The nurse's level, the diagnosis and the outcome were
never given to the model. We compare against always guessing the most common answer, against two simple rules,
and against real nurses.

**Does it really save time?**
Sorting the line does not: it moves waiting from the most urgent patients to everyone else, and the average stays
about the same. A second simulation of the whole chain shows a saving (104 down to 55 minutes) with our assumed
timings, but most of it rests on one assumption, that the doctor saves a minute per patient by already having the
answers. If the doctor saves nothing, the saving is 7 minutes; if the report only costs the nurse time, the
department gets slower. So our honest claim is that the right patients are seen sooner and the nurse is
double-checked. Whether the department gets faster has to be measured.

**Are the waiting times real?**
No. The data has no arrival times, so we simulated a busy department with three doctors. We say so wherever the
figures appear.

**Why does the department model only reach 70%?**
We tried eleven versions and none passed 71%. The same complaint leads to different departments: of the patients
who arrived with "dyspnea", 31 had a lung diagnosis, 15 a heart diagnosis and 8 a general medical one. So the
model offers three suggestions, the right one is among them 88% of the time, and the nurse chooses.

**Did you train the AI that writes the questions?**
No. It is a general language model that we steer with written instructions, called a system prompt. It chooses
three of six history topics for the complaint and words the questions; plain code decides which topics are allowed,
supplies the answers to tap and checks the result. Only the urgency model and the department model are trained on
real data. The questions are untested, so their answers are passed to the doctor and never change the level.

**Where does it fail?**
It sent seven level 1–2 patients to the slow queue; nurses got five of those right. It is worse at a hospital it
was not trained on. It asks for a re-check on about a third of patients.

**Is any patient data stored?**
No names are asked for. The insurance card only makes sure one person has one open visit: we keep a one-way
fingerprint of the number and its last four characters, never the number, and it never reaches the models.
The demonstration database is wiped every time the server starts.

**Which parts are AI and which are plain rules?**
AI: the urgency level, the department suggestion, the choice and wording of the three history questions. Plain
rules: the order of the line, the choice of doctor, the danger-sign questions, the doctor's overview.

## Submission form

- **Track:** Health
- **Code:** https://github.com/potlatiy/emergency-triage-ai
- **Write-up:** the same link (the README is the write-up)
- **What we added:** "Our own idea."
- **Disclaimer line (already in the README):** "This project is a prototype and is not medical advice."
- **Credits:** copy the Sources section at the end of the README. It lists the dataset, the libraries, the
  language model and the AI tools used.

Before submitting: open every link in a private window, check all three names and emails, and send a first
version by Sunday afternoon. The deadline is Sunday, November 15 at 11:59 PM US Eastern time.
