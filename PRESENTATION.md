# Presentation notes

Everything needed to record the video, answer the judges and fill in the submission form.
All numbers here come from `triage.ipynb` and `assignment.py`. If you change the code, re-check them.

## The one sentence

Our AI is worse than a triage nurse at giving an urgency level, so the nurse always decides; but it catches most
of the patients nurses rate too low, and a line sorted by urgency gets the most urgent patients seen far sooner.

## Two-minute video script

Record the screen with your voice over it. No editing needed. Three browser windows side by side: the patient's
phone on the left (https://darkshdws-meddy.hf.space, narrow like a phone), the nurse's screen in the middle
(`/nurse`) and the doctor's screen on the right (`/doctor`, choose Dr. Baptiste, Gastroenterology and surgery).

| Time | What is on screen | What to say |
|---|---|---|
| 0:00–0:15 | The three windows, the phone at the entrance | "In a crowded emergency department, the person with chest pain can end up waiting behind five sprained ankles. A nurse sorts the line, but under pressure about one patient in ten is rated less urgent than they really are." |
| 0:15–0:35 | Phone: type "crushing chest pain and sweating", age 67, male, "No insurance", register. Tap the first danger sign and "Pressure or tightness", type "I was carrying shopping upstairs when my chest went tight" in "In your own words", press "Go to the nurse". The phone shows a code | "This is the patient's phone. You say what is wrong and get a quick check built for your complaint: danger signs to tap, questions an AI model chose for you, and room to say it in your own words. Then the phone shows a code for the nurse." |
| 0:35–0:55 | Nurse: tap the code in her list. Show what the patient said and the AI summary. Choose Unstable, press "Measure and get Meddy's report", then "Everything is correct: send straight to the resuscitation bay". The phone switches by itself | "The nurse opens the patient by that code and reads what they said, summed up by the AI in two sentences. She measures the vital signs, and Meddy, trained on 1,267 real emergency patients, suggests level 2, an emergency. One button, and the patient's phone says: go to the resuscitation bay. It never shows the level." |
| 0:55–1:15 | Phone: "Start again as a new patient", "bad stomach pain and vomiting", age 40, "No insurance", "No, none of these", "Cramping". Nurse: Feverish, "I want to check the patient myself", level 3, to the waiting room. Phone: place in line. Doctor: "Call my next patient" until the phone shows the room | "Here Meddy is less sure, so it advises a personal check. The nurse gives her own level and the patient joins a line ordered by urgency; the phone shows only a ticket and a place. The doctor calls the next patient of their department, and the phone tells the patient which room to go to. The doctor's screen opens with a patient summary: what the patient said, what the nurse found, and what Meddy suggested, each under its own label." |
| 1:15–1:45 | The second-check chart from the README | "Does it work? On the exact level the model is right 68% of the time and nurses 85%, so it cannot replace a nurse. As a second check it flags 107 of the 131 patients nurses rated too low. If a nurse can re-check one patient in five, simple rules find about a third of them. Our model finds two thirds." |
| 1:45–2:00 | The limits section of the README | "The waiting times are simulated, the data is from one country, and the questionnaire is untested. Next we would try it with real nurses." |

## Demo checklist before recording

1. Open the three windows: https://darkshdws-meddy.hf.space, `/nurse` and `/doctor`. (Or locally:
   `python hospital/server.py` and http://localhost:8000.) A free Space that has been quiet for two days takes
   about a minute to wake up.
2. Add the Space variable `MEDDY_AUTO_UPDATE` = `0` (Space → Settings → Variables), so a teammate's merge cannot
   restart the site during the recording. Delete it afterwards.
3. Run both patients of the script through once first. The AI model needs a few seconds to load the first time,
   and a complaint it has already seen is answered at once, so the recording will not wait.
4. On the nurse's screen open "Behind the scenes" and press "Start a new day", so the recording begins with a
   clean waiting room. Then reload the patient's window.

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
| Under-rated patients reached when re-checking 1 in 5: simple rules / our model | 34–36% / 67% |
| Simulated wait for the most urgent: arrival order / AI levels / nurse levels / expert levels | 88 / 24 / 13 / 7 minutes |
| Right department: most common / key-word rule / our model | 21% / 67% / 70% |
| Right department among the model's top three | 88% |
| Exact level when the model moves to the other hospital | 55–58% (down from 68%) |
| Meddy's range of levels: experts' level inside / levels wide / most urgent patients inside | 90.6% / 2.0 / 80% |
| The same for the simple baseline (most likely level, one either side) | 95.7% / 2.9 / 92% |
| Range inside when it is one level / two levels / three or more | 82% / 93% / 96% |
| Range with model and cut from the other hospital: inside / urgent patients inside | 87% / 64% |
| Five similar patients: their most common level was the experts' / at least one had it | 61% / 89% (five random patients: 33% / 79%; the model alone: 68%) |
| Re-check while waiting: level 3 / 4 / 5 | every 30 / 60 / 120 minutes (the Canadian scale's times) |

## Questions judges may ask

**Doesn't the "Everything is correct" button let the AI decide?**
The nurse still decides: she reads the report and can always check the patient herself. Meddy offers a quick review
only where its level held up in our tests (about 1 patient in 4: at least 80% sure, level 3 to 5, no warning sign).
There its level was too low for about 7% of patients, about the same as the nurses (with other random splits of the data,
8% against 7%). For everyone else it advises a personal check. One exception: when Meddy suggests level 1 or 2, accepting
sends the patient to the resuscitation team at once, which is the safe direction, so that is one press too.
We have not tested how real nurses use the button, and reading a report first may make them agree too easily. That is
why the app counts every verdict.

**What is the "likely range" on the nurse's screen?**
One level is right for 68% of patients, so Meddy also says "level 3 to level 4". A level is in the range when its
probability is at least about 10%. We chose that cut so that the experts' level stays inside for 9 patients in 10,
then checked it on patients the cut had not seen: 90.6%. The method is called conformal prediction. The weak spots:
for the most urgent patients it is only 8 in 10, a miss on them is nearly always too low, and there the simple "one
level either side" does better. It also does not travel: with the other hospital's model and cut it is 87%. The
screen says the first two next to every range.

**What are the "patients like this one"?**
Five real patients from the 1,267 who look most like the new one: same kind of complaint first, then closest in the
complaint words, the numbers and the tick-box answers. The nurse sees the level their nurse gave and the level the
experts gave, never the diagnosis. Tested on patients they had not seen, the most common level among the five was
the experts' level 61% of the time (five random patients: 33%). That is weaker than the model (68%), so we show them
as evidence to look at, not as a second opinion.

**What if a patient gets worse while waiting?**
The phone has an "I feel worse" button. In the waiting room it does not move the patient up the line; it puts them
on top of the nurse's list.
She measures again, Meddy writes a new report, and her new level decides. The patient keeps the ticket and the
minutes already waited. The nurse's screen also marks patients who have waited longer than their level allows
without a second look. We could not test this on our data: it has one measurement per patient.

**Why is your AI worse than nurses?**
It reads a few words, the age and the vital signs. A nurse sees and hears the patient. That is why we did not build a
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
Its most likely level put 19 patients whom experts rated level 1 or 2 at level 4 or 5; nurses got 14 of those right.
(With the safety-first setting it is seven, and nurses got five right.) It is worse at a hospital it
was not trained on. It asks for a re-check on about a third of patients.

**Is any patient data stored?**
The patient gives a first and last name so the nurse and the doctor can call them; the models and the language
model never see it, and the patient's phone never gets it back. In the demonstration we use made-up names. The
insurance card only makes sure one person has one open visit: we keep a one-way fingerprint of the number and its
last four characters, never the number, and it never reaches the models. The database keeps every visit (a new day
closes the open ones), with a copy in the Space's private storage so a restart loses nothing.

**Which parts are AI and which are plain rules?**
AI: the urgency level, the department suggestion, the choice and wording of the three history questions. Plain
rules: the order of the line, the choice of doctor, the danger-sign questions, and how the doctor's patient summary is
put together (the only AI text in it is the two checked sentences, and the nurse can hold them back).

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
