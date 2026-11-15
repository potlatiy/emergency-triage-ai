# Master prompt for the chain: from the patient's questionnaire to the summary on the doctor's screen

> **Status: built.** The link this prompt asks for was built from it on 15 November 2026, in
> [pull request #14](https://github.com/potlatiy/emergency-triage-ai/pull/14). The doctor's card now opens with
> the patient summary. The text below is kept as the brief that was used: where it says the last link "does not"
> work or names "the gap", that was true before #14 and is not true now. Do not run this prompt again as it is.
> To use it as a pattern for another piece of work, rewrite `<context>` and `<task>` from the code first.

Paste everything inside the code block into a coding assistant that can read and run this repository.
It is told to stop after the plan and wait for feedback before it writes code.

**What it is for.** The chain has three people and three screens. The patient fills in the questionnaire on
their phone, the nurse reviews it on her screen, and the doctor should then see one patient summary on theirs.
The first two links work. The last one does not: the summary the nurse sees is thrown away, and the doctor's
screen builds its own overview from the raw answers. This prompt asks for that last link, and for nothing else.

**Check before you use it.** The code changes several times a day. The facts under `<context>` were true on
Sunday 15 November 2026 at commit `b461d93` (176 tests passing). If `main` has moved on, the assistant is told
to trust the code over this text and to say where they differ.

```xml
<role>
You are a careful full-stack engineer finishing one feature in a small, working healthcare prototype built by
three high-school students for a hackathon. You change as little as possible, you test what you change, and you
never make a claim the code cannot back. Safety and honesty matter more here than polish: this software sorts
(made-up) emergency patients, and its judges penalise results that cannot be explained.
</role>

<context>
PROJECT. "Meddy" is a digital emergency department (Veritas AI High School Hackathon 2026, Health track).
Repository: https://github.com/potlatiy/emergency-triage-ai . Live copy: https://darkshdws-meddy.hf.space .
Every screen carries the line "This project is a prototype and is not medical advice."

STACK. Python, FastAPI, SQLite from the standard library (one connection, one lock), scikit-learn, plain HTML,
CSS and JavaScript with no framework and no build step. A small local language model (Gemma 3 through Ollama)
is optional: everything must work when it is off. Tests: `python -m pytest tests` (176 pass today).

FILES THAT MATTER FOR THIS TASK.
- hospital/core.py    the logic: assess() (level and department), review_advice(), second_check(),
                      follow_up() and checked_questions() (the questionnaire), nurse_summary() and
                      checked_summary() (two AI sentences for the nurse), pain_word(), vital_warnings().
- hospital/db.py      the database: register(), check_in(), nurse_patients(), nurse_card(), save_assessment(),
                      admit(), refer(), call_next(), call_for_doctor(), doctor_patients(), finish_visit().
- hospital/server.py  the API (FastAPI). Sync handlers; every state check happens inside the database lock.
- hospital/static/index.html   the PATIENT's phone.   hospital/static/nurse.html   the NURSE's screen.
- hospital/static/doctor.html  the DOCTOR's screen.   hospital/static/meddy.css    shared styles.
- tests/test_hospital.py       one test per behaviour, with plain-English names.

THE CHAIN AS IT IS BUILT. A visit moves through five states: registered, nurse, waiting, with doctor, done.
1. Patient's phone. POST /api/register (complaint, age, sex, insurance) gives a registration number. The
   questionnaire: three danger signs to tap (hand-written, chosen by the kind of complaint), what the pain feels
   like, whether it was an injury, three history questions chosen from six topics by the language model with
   fixed answers to tap (GET /api/follow-up), and one open field "in your own words". POST /api/checkin saves
   the answers, sets the state to "nurse" and gives the phone a short code to show.
2. Nurse's screen. GET /api/nurse/patients lists who waits for her (danger signs first). GET
   /api/nurse/patient/{reg_no} is one patient's card with every answer. GET /api/nurse/summary/{reg_no} returns
   two sentences written by the language model from the answers, or {"source": "none"}; it is computed on
   request, kept only in memory, and shown only here. She measures the vital signs; POST /api/triage returns
   Meddy's report (most likely level with its probabilities, department with the likely doctor, advice on
   whether a quick review is reasonable). She presses "Everything is correct" or checks the patient herself
   (her own level, Meddy's second-check flag, department, or "send straight to a doctor" with a required reason).
   POST /api/line/join or POST /api/refer records her decision; levels 1 and 2 go to the resuscitation team at once.
3. Doctor's screen. It polls GET /api/doctor/{doctor_reg}/patients every 5 seconds. "Call my next patient"
   (POST /api/doctor/call) brings in the first patient in line for the department. Each patient card shows the
   final level, Meddy's level and flag, vital signs, and the raw answers. An overview at the top is put together
   in the browser by glance() in doctor.html. "Visit finished" (POST /api/doctor/finish) closes the visit.

THE GAP THIS TASK CLOSES.
- The doctor never receives a patient summary. The nurse's two AI sentences are not stored and never leave her
  screen; the doctor's overview is rebuilt in the browser from raw answers by separate code.
- The nurse cannot say that the AI's sentences are wrong, and cannot add a note of her own for the doctor.
- Nothing on the doctor's card says who each fact comes from: the patient, the nurse, or Meddy.
- Meddy's own doubts (not sure of the level, could not read the complaint, warning signs) stop at the nurse.
</context>

<task>
Build the last link of the chain: one patient summary, written once on the server when the nurse sends the
patient on, stored with the visit, and shown at the top of the doctor's card as soon as the patient is called.

1. ONE SUMMARY, BUILT ON THE SERVER. Add a function in core.py that builds the summary of one visit from the
   visit, its answers and the nurse's assessment. It has three parts, and every line belongs to exactly one:
   - "The patient said": the two checked AI sentences when there are any, otherwise the same facts as one or two
     plain sentences made by a template in code; the patient's own words, quoted; the danger signs answered yes;
     the labelled history answers ("Started: under an hour ago").
   - "The nurse found": the measurements, the warning signs among them, her final level, whether she accepted
     Meddy's report or checked the patient herself, her reason if she sent the patient straight in, and her note.
   - "Meddy suggested": its most likely level and how sure it was, "not sure" and "could not read the complaint"
     when they apply, its flag ("strong", "mild" or none) against the nurse's level, and the department with how
     it was chosen.
   The summary must exist for every patient, with or without the language model, including a patient sent
   straight in before any measurement.

2. THE NURSE HAS THE LAST WORD ON IT. On the nurse's screen, next to the AI sentences that are already there:
   - one control to say "this is not right: do not pass it on" (the template sentences are then used instead);
   - one optional note for the doctor, at most 300 characters.
   Both travel with her decision (POST /api/line/join and POST /api/refer) and are optional, so older pages and
   the existing tests keep working.

3. FROZEN WHEN THE PATIENT LEAVES THE NURSE. Store the summary with the visit at that moment, so that the
   doctor reads exactly what the nurse saw and nothing is written again later by a model that may answer
   differently. A patient the nurse sees again before they are called gets a new summary; after that it never changes.

4. THE DOCTOR SEES IT FIRST. GET /api/doctor/{doctor_reg}/patients returns the stored summary with each
   patient. The doctor's card opens with a "Patient summary" block in the three parts above, each under its own
   label; the AI sentences are marked "written by a language model, checked by the nurse" or "put together from
   the patient's answers" as the case is. The full answers and measurements stay below, unchanged. The block
   appears by itself within one polling interval of the patient being called. Remove the browser-side glance()
   once the server's summary replaces it, and keep a fallback for a card that has no stored summary.

5. PROVE IT END TO END. One test walks a patient through the whole chain with the API (register, check in,
   nurse's card, triage, join with a note, doctor's call) and checks the summary on the doctor's card.
</task>

<contract>
Agree this shape with me before writing code. Field names may change; the three parts and the sources may not.

visits: two new columns, added to SCHEMA and to the stale-schema check at the bottom of db.py.
  summary TEXT        the JSON below, written when the nurse sends the patient on
  nurse_note TEXT     her note, or NULL

POST /api/line/join and POST /api/refer: two new optional fields
  "note": "Lives alone, daughter is on her way"        at most 300 characters, trimmed, may be absent
  "summary_ok": false                                  absent or true: pass the AI sentences on

GET /api/doctor/{doctor_reg}/patients: each patient gains
  "summary": {
    "patient_said":    {"text": "The patient reports ...", "written_by": "ai" | "code",
                        "own_words": "..." | null, "danger_yes": ["..."],
                        "facts": [{"label": "Started", "value": "Under an hour ago"}]},
    "nurse_found":     {"measured": {...} | null, "warnings": ["high fever"], "level": 3,
                        "review": "approved" | "checked" | null, "referral_reason": "..." | null,
                        "note": "..." | null},
    "meddy_suggested": {"level": 3, "sure": 0.57, "unsure": true, "unread": false,
                        "flag": "strong" | "mild" | null, "department": "...", "how": "..."} | null
  }
A visit from before this change, or one of the made-up patients, has "summary": null or a summary built on the
spot from what is stored; say which you chose and why.
</contract>

<rules>
These already hold in the code. Break none of them, and add a test for each one your change touches.
1. MEDDY NEVER GIVES A DIAGNOSIS. No text produced by this feature may name a condition the patient did not
   name, guess a cause, give advice, or say how serious something is. AI text passes checked_summary() before it
   is stored; text made by the template uses only words the patient or the nurse supplied.
2. THE SUMMARY REPEATS, IT DOES NOT ADD. Every body part, symptom or fact in it was reported by the patient,
   measured by the nurse, or computed by the tested model. Nothing is inferred.
3. THE NURSE DECIDES. The summary never changes the level, the department or the place in line. Meddy's parts
   are labelled as Meddy's.
4. THE PATIENT NEVER SEES A LEVEL, A LEVEL COLOUR OR A DEPARTMENT. GET /api/me/{reg_no} stays as it is. Do not
   send the summary to the patient's phone.
5. IT WORKS WITHOUT THE LANGUAGE MODEL, and nobody waits for the model: no request that the nurse or the doctor
   needs may block on it.
6. NO NAMES, NO CARD NUMBERS. The nurse's note is free text: say on the screen that it must not contain a name.
7. ONE CHECK, ONE LOCK. A state check and the write it guards happen in the same locked transaction in db.py
   (see _still_at_the_desk). Sending the same patient twice must not write two summaries or count twice.
8. EVERYTHING THAT REACHES innerHTML GOES THROUGH esc(), including the nurse's note and the AI sentences.
9. ADD, DO NOT RENAME. Existing endpoints, fields and element ids keep their names. New fields are optional.
10. NO NEW DEPENDENCY, no framework, no build step. Plain words in comments and on screen, short sentences.
11. NO NEW NUMBERS. Do not quote an accuracy, a percentage or a time saving that `python triage.py` does not
    print. This feature is not tested with real nurses or doctors: say so wherever it is described.
</rules>

<how_to_work>
1. `git pull origin main` first, then read the files named above. If the code differs from <context>, list the
   differences and follow the code.
2. Work on a branch. Never push to main. Open a pull request and say plainly what was and was not checked.
3. Small commits, each with its tests. Run `python -m pytest tests` before every commit; all tests must pass.
4. Check the three screens in a browser at laptop width and at 375 px: a patient with danger signs and a note,
   a patient with the language model off, a patient sent straight in with no measurement, and a made-up patient.
5. Three pages are edited by three people. Touch only the lines you need in index.html and nurse.html, and say
   in the pull request which ids you added.
</how_to_work>

<tests_to_add>
In tests/test_hospital.py, each with a name that states the behaviour:
1. The whole chain: what the patient tapped and wrote appears under "patient_said" on the doctor's card.
2. Without the language model the summary is still there, written_by is "code", and it contains no word the
   patient did not use apart from the template's own.
3. An AI sentence that guesses a cause, names a condition or judges seriousness never reaches the doctor.
4. "summary_ok": false replaces the AI sentences with the template's; the nurse's note is stored and shown.
5. A note longer than 300 characters, or made only of spaces, is refused or dropped; say which and test it.
6. The summary is written once: after the patient is called, a second look by the model cannot change it.
7. A patient sent straight in before any measurement has a summary with "measured": null and the reason.
8. GET /api/me/{reg_no} still has exactly the keys it has today.
9. Nothing in the summary is called a diagnosis, and its keys contain no "diagnos".
</tests_to_add>

<definition_of_done>
- With three windows open (patient, nurse, doctor), a patient who fills in the questionnaire, is reviewed by the
  nurse and is called by the doctor appears on the doctor's screen with a "Patient summary" in three labelled
  parts within 5 seconds, and nobody typed or clicked anything twice.
- The same works with Ollama stopped.
- All earlier tests pass unchanged, the nine new ones pass, and the browser check in <how_to_work> is described
  in the pull request with what was seen.
- README.md (the row for the doctor's note), PROJECT_GUIDE.md (step 5 of the patient's path) and the video
  script in PRESENTATION.md say what the doctor now sees, in one or two sentences each, with no new claim.
- The pull request lists what is still not done or not verified.
</definition_of_done>

<out_of_scope>
Retraining or changing either model; new accuracy figures; sign-in or access control; a redesign of any screen;
the deployment; the questionnaire itself; the line's order. If one of these seems necessary, stop and ask.
</out_of_scope>

<pause_instruction>
Before writing any code, reply with: (1) the differences you found between <context> and the code, (2) the
exact JSON you propose for "summary", (3) the template sentences you would use when there is no AI text, with
two worked examples, and (4) the list of files and functions you will touch. Then stop and wait for my answer.
</pause_instruction>
```

## Why the prompt is shaped this way

- **It names what exists.** Most of the chain was built in the last day by three people. An assistant that is
  only told the goal rebuilds things that work. The context lists the endpoints and functions to reuse.
- **It asks for one summary, written once.** Today three pages each put an overview together in their own way.
  A summary stored when the nurse sends the patient on is the same for everyone and cannot change behind the
  doctor's back.
- **Every line has a source.** The doctor should be able to tell at a glance what the patient said, what the
  nurse measured and what Meddy suggested. That is also the honest way to show AI text.
- **The rules are the project's own.** No diagnosis, the nurse decides, the patient sees no level, it works
  without the language model. Each is already tested, and the prompt asks for a test wherever the change touches one.
- **It stops before the code.** The JSON shape and the template sentences are the two decisions worth a
  person's five minutes. Everything after them is mechanical.

*This project is a prototype and is not medical advice.*
