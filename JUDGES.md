# For the judges: try Meddy in three minutes

**Live:** https://darkshdws-meddy.hf.space

| Screen | Link | PIN |
|---|---|---|
| The patient's phone | https://darkshdws-meddy.hf.space | none |
| The nurse's screen | https://darkshdws-meddy.hf.space/nurse | `0070` |
| The doctor's screen | https://darkshdws-meddy.hf.space/doctor | `0070` |
| The waiting room's board | https://darkshdws-meddy.hf.space/board | none |
| The chief doctor's panel | https://darkshdws-meddy.hf.space/chief | `0070` (the chief doctor's own PIN when one is set) |

**Why a PIN at all.** The lock is part of what we built: the staff screens show patients' names and complaints, and
their buttons can close visits, so a passer-by with the link sees nothing. The patient's phone and the board never
need it. One shared PIN is a lock on the door, not a sign-in: a real hospital would give every nurse and doctor
their own account. We put the PIN here, in the repository, rather than on the site itself.

The site is a free server: the first visit after a quiet day takes about a minute to wake up. Open the screens side
by side (the phone narrow, like a phone).

## A three-minute tour

1. **Phone.** Make up a name, type a complaint in your own words (a typo is fine: "breating problems" is understood),
   age, "No insurance", register. Tap the danger signs that are true, say whether there is pain, press "Go to the
   nurse". The phone shows a code.
2. **Nurse** (PIN `0070`). Tap your code in "New from check-in". Read the AI summary of what you said, press
   "Random values" (or type the measurements), then "Measure and get Meddy's report". Meddy suggests an urgency level
   and a doctor, marked free or busy. Choose a doctor (open "Choose another doctor" for the short list) and send the
   patient to the waiting room.
3. **Phone.** Your place in line, your doctor, and an estimated wait from how busy the doctors are.
4. **Doctor** (choose the doctor the nurse chose). A window asks: "New patient for you. Are you free now?" Press
   "Yes, let them come in": the phone shows the room. Read the patient summary, press "Visit finished", write a short
   note for the patient ("Tidy with Meddy" fixes spelling and punctuation), "Close the visit". The phone says thank
   you and shows the note.
5. **Board and chief doctor.** The board on the waiting room's wall shows the called tickets; the chief doctor's
   panel shows the whole department today.

A chest pain in an adult is usually rated level 2 and goes straight to the resuscitation team, with no doctor to
choose: to see the choice of doctor, try a sore throat, a twisted ankle or "it burns when I pee".

## What is real and what is made up

- **Real:** the urgency model and the department model, trained and tested on 1,267 real emergency patients (see the
  README for every figure and its limits).
- **Made up:** the doctors and the other patients. Made-up patients from "other nurses" keep the doctors busy and
  free again, so the screens look like a working department; they never touch a real patient.
- Meddy never gives a diagnosis, and the nurse always decides. This is a prototype and not medical advice.
