# Meddy: design guideline and build plan

**Product name:** Meddy · **Tagline:** Your triage buddy · **Front end:** Ostap

*This project is a prototype and is not medical advice.*

This folder is the front end's plan for the rest of the hackathon. The design prompt that goes with it is in
[master-prompt.md](master-prompt.md).

## The decision

- **Meddy is the working digital hospital** (`hospital/static/index.html`, served by `hospital/server.py`).
  We are not building a separate presentation website. Judges score what works, and the video script in
  [PRESENTATION.md](../PRESENTATION.md) is already built around the hospital.
- **The job now is to make that page simple, clear and good-looking**, not to add features.
- **Results** (68% vs 85%, 107 of 131, 88 → 13 minutes) go on 2–3 plain cards for the video, not on the page.
- **We keep the page as one HTML file with plain JavaScript.** No React, no build step: the server already
  serves it, and changing the stack a day before the deadline is a risk we don't need.

## The name

**Meddy** is the nurse's buddy: it suggests and flags, and the nurse always decides. "Koch Team" is only the
team credit. Other names we considered: Kestrel and Nudge.

## Five rules for every screen

It is a medical website, so calm and clear beats clever.

1. **One step, one job, one main button.** Each of the five stops asks only what it needs, and has one
   obvious primary button at the bottom ("Register", "Go to the nurse"…).
2. **Nothing extra.** No tabs, no menus, no icons without labels, no decoration. If a control isn't
   needed for the patient's path, it goes into the "Behind the scenes" drawer or is removed.
3. **Always show where you are.** A progress bar at the top: Arrive · Questions · Nurse · Waiting room ·
   Doctor. The floor plan shows the same thing as a picture.
4. **Plain words.** "How bad is the pain?" not "NRS score". Say what happens next after every result.
5. **The nurse decides.** Every AI result says it is a suggestion, and the nurse can change it.

## Typing windows (forms)

The questionnaire is the heart of the page, so the fields must feel easy.

- **Size:** fields at least 48px tall, text at least 16px (smaller text makes phones zoom in), full width
  on phones.
- **Labels** sit above the field, always visible. Never use the placeholder as the label; use it only for
  an example ("for example: chest pain since this morning").
- **The right keyboard:** `inputmode="numeric"` for age, a normal text field for the complaint.
- **Focus:** the cursor starts in the first field. Enter moves on. A clear focus ring in the brand colour.
- **Limits are visible:** a small "38 / 200" counter on the complaint field.
- **Errors** appear under the field, in plain words, with what to do ("Please enter an age between 15 and
  110"), and the field gets a red border plus an icon. Never only a colour.
- **Yes/No questions** (the three danger-sign questions) are two big buttons side by side, not tiny
  radio dots.
- **Pain** stays a slider, with a big thumb and the number shown next to it ("6 of 10").
- **Choices with 2–4 options** (sex, vital signs) are segmented buttons. **Longer lists** (arrival,
  alertness) are a normal dropdown.

## Screen by screen

The flow, the API calls and the element ids stay the same, so the backend and the tests keep working.

| Step | What the patient sees | Main button | Change from today |
|---|---|---|---|
| Header | "Meddy" wordmark, one line ("You are the patient. Walk through the emergency department."), the progress bar | none | Replaces the long title. Keep the words "Digital hospital" on the page: a test checks for them |
| 1. Arrive | Complaint (big text field), age, sex, how you arrived | Register and go to check-in | Bigger fields, a character counter, errors under the field |
| 2. Questions | Three danger-sign Yes/No questions, two follow-up questions, the pain slider | Go to the nurse | Yes/No as big buttons. While the AI writes the questions (up to about 15 s): "Meddy is writing two questions for you…" with a calm loading bar |
| 3. Nurse | Vital signs (Normal / Feverish / Unstable), alertness | Measure and assess | The result card: the level as number + icon + colour + name ("2 · Emergency"), what happens next, the suggested department with the other two options, and "Suggested by Meddy. The nurse decides." |
| 4. Waiting room | Your ticket, your place in line ("You are 3rd"), the line | Wait for your turn | "15 minutes pass" and "Call next patient" are demo controls. Put them in a small, clearly labelled "Demo controls" box so they don't look like patient buttons |
| 5. Doctor | Doctor's name, department and room; "What the doctor already knows" (the handover note) | Start again as a new patient | "Start a new day" becomes a small text link, not a second big button |
| Behind the scenes | The hospital database (doctors on duty, visits) | none | One drawer ("Behind the scenes: the hospital database"), closed for patients, opened in the video |
| Footer | "Made by Koch Team", GitHub link, "This project is a prototype and is not medical advice." | none | New |

**The floor plan stays.** It is the friendliest part and makes the video easy to follow. On a laptop it sits
on the left and the step card on the right. On a phone the plan goes on top (smaller) and the step card below.

## Look and feel

- **Colours:**
  - **Brand:** one calm colour for Meddy, buttons, focus rings and the "YOU" marker. Suggestion: blue
    `#2563eb`.
  - **Urgency colours only for urgency:** red `#d03b3b` (levels 1–2), orange `#ec835a` (level 3), green
    `#0ca30c` (levels 4–5). Always with the icon (⚠ ▲ ✓) and the number.
  - **Fix:** today the page uses red (`--accent: #e5484d`) for the marker and the active room, which reads
    as "emergency". Switch those to the brand colour.
- **Background:** keep the light, soft-green paper look, or move to plain white and light grey. Either way,
  white cards on a light background with lots of space.
- **Type:** keep Outfit (friendly and clear). Use the mono font only for registration numbers and tickets.
  Headings around 24–28px, body 16–18px.
- **Motion:** only the "YOU" marker walking and the progress bar filling, 200–400 ms, and none when the user
  asks for reduced motion.
- **Contrast:** text at least 4.5:1 against its background (WCAG AA). Buttons and touch targets at least 44px.

## States to design (every step)

| State | What it looks like |
|---|---|
| Default | The field or button at rest |
| Hover / focus | A slightly darker button. A brand-coloured focus ring on fields and buttons, visible when using the keyboard |
| Active / pressed | The button pressed in; a chosen segmented option filled with the brand colour |
| Disabled | The main button greyed until the required fields are filled, with a short hint why ("Enter your age to continue") |
| Loading | The button says "Registering…" and can't be pressed twice; the questionnaire shows "Meddy is writing two questions for you…" |
| Error | The message under the field. If the server is down: "Meddy can't reach the hospital right now. Check that the server is running and try again." with a Try again button |
| Empty | Waiting room with nobody in line: "Nobody is waiting. You're next." |
| Not sure | If no level is clearly most likely: "Meddy isn't sure about this patient. The nurse decides." |

## How to work

1. Run the hospital: `python hospital/server.py`, then open http://localhost:8000. Edit
   `hospital/static/index.html` and refresh. Nothing else needs to change.
2. Give [master-prompt.md](master-prompt.md) to your design or coding assistant. It stops after the
   wireframe, so you can check it before any code is written.
3. Change one step at a time, then click through the whole path again.
4. Don't rename element ids or change what is sent to `/api/...` without telling Yegor.
5. Tell the team chat before you push changes to `index.html`. Commit small, with a clear message.

## Plan until the deadline (Sunday, November 15, 11:59 PM US Eastern)

| When | What | Done when |
|---|---|---|
| Saturday evening | Colours, type, header, progress bar, footer. Steps 1 and 2 (the typing windows) | You can register and answer the questions on a laptop and a phone |
| Sunday morning | Steps 3–5, the result card, the waiting room, the "Behind the scenes" drawer, all the states above | The whole path works on a laptop and a phone with nothing unclear |
| Sunday midday | Run the video script in PRESENTATION.md once from start to finish; fix what looks wrong. Make the 2–3 result cards | The demo runs without a hitch |
| Sunday afternoon | Record the video (OBS or the Mac screen recorder, browser about 1,200px wide), upload it unlisted to YouTube | The video is under 2 minutes and the link opens in a private window |
| Before the deadline | Denys sends the form; everyone checks every link logged out | Submitted |

**Later, only if we reach the finals** (announced November 18, live on November 21): host the hospital
online and show a QR code, so judges can walk through Meddy on their own phones.
