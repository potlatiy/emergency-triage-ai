# Plan: one device per role (patient, nurse, doctor)

> **Historical note:** this is the team's first-day plan, kept to show how the project started. Everything in it
> has since been built and more; the [README](README.md) describes the finished project and [JUDGES.md](JUDGES.md)
> shows how to try it.

*Draft for the team. Nothing here is built yet; the backend for the doctor's screen, the patient's phone and the
referral is already in pull request #3.*

## What already works

- **Everything runs on one server.** `python hospital/server.py` loads both trained models (urgency level and
  department). The question writer (Gemma through Ollama) runs on the same computer. For the demo, the laptop
  is the server.
- **The department is already chosen by AI.** The assignment model, trained on the real diagnoses of 1,267
  patients, is right 70% of the time and has the right department in its top three 88% of the time. The nurse
  sees three options and chooses; a plain rule then picks the least busy doctor on duty there.
  It does not read the questionnaire answers.
- **A test hospital already exists.** 14 made-up doctors, 5 made-up patients waiting, and buttons for
  "on duty", "15 minutes pass" and "new day".

## What is missing

### 1. One screen per role

| Screen | Who | What happens |
|---|---|---|
| `/patient` | Patient's phone | Complaint → questionnaire → a big QR code: "Show this to the nurse". Then it updates by itself: "You are 3rd in line", then "Go to room 5". Never the urgency level |
| `/nurse` | Nurse | Scanning the QR with the phone camera opens this patient's form directly (no scanner needed in the page). There is also a "Waiting for the nurse" list. Vital signs, alertness, pain → AI suggestion → the nurse confirms level and department → "Done, to the waiting room" or "Send straight to a doctor" with a reason. The line and "Call next" are here too |
| `/doctor` | Doctor | Their patients with the full card (endpoint ready in #3), and a "Visit finished" button |
| `/` (`index.html`) | Video | Stays as the one-screen walk-through, so Ostap's redesign is not disturbed |

Backend needed for this:
- the patient's phone saves the questionnaire answers itself; new status "waiting for the nurse";
- a list for the nurse; the nurse's assessment reads complaint, age and sex from the database;
- `/api/me` also says "go to the nurse";
- "visit finished" for the doctor;
- the server listens on the local network, so phones on the same Wi-Fi can open it.

The QR code is drawn in the browser by one small JavaScript file kept in `static/` (nothing to install).

### 2. AI and the questionnaire answers

Proposal: the trained assignment model keeps deciding the department, because it is tested on real data, which
is our strongest point with the judges. Gemma reads the complaint and all the answers and gives the nurse a
one-line second opinion ("Looks like cardiology: the pain spreads to the arm"). The nurse decides. It is not
tested, so it is labelled as a hint.

### 3. A livelier test hospital

- 10–12 made-up patients with different complaints, vital signs and answers (no names, no real data).
- A few already with doctors, so the doctor's screen is not empty at the start.
- A "Add 5 random patients" demo button.

### 4. Tests for everything new, in `tests/test_hospital.py`.

## Things to know

- **Time.** Deadline Sunday 11:59 PM US Eastern (Monday 05:59 Paris). Feature freeze proposed Sunday 16:00 Paris.
  Rough estimate: backend 3–4 hours, pages 4–5 hours.
- **Phones** must be on the same Wi-Fi as the laptop. Hosting online with Ollama is not quick; the design plan
  keeps that for the finals. For the video, three browser windows side by side on one laptop work.
- **Ostap.** The new pages are separate files, so `index.html` is untouched. `doctor.html` was his in the spec:
  agree who builds it.
- **Order.** Backend with tests in one pull request, the pages in a second one, so each can be merged on its
  own. If the pages run late, the backend is still in `main`.
