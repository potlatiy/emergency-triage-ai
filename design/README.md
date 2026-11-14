# Meddy: the website brief

**Product name:** Meddy
**Tagline:** Your triage buddy.
**Front end:** Ostap

*This project is a prototype and is not medical advice.*

This folder holds the plan for the project's public website. The website does not exist yet; the
ready-to-use design prompt is in [master-prompt.md](master-prompt.md).

## The name

**Meddy** is a friendly medical helper: the nurse's buddy. It suggests and flags; the nurse always decides.
That matches the project's main finding: the model is worse than nurses at picking a level (68% against
85%), but as a second check it catches 107 of the 131 patients nurses rated too low.

- "Koch Team" is the team's name and only appears as a credit. It is not part of the product branding.
- Other names we considered: Kestrel and Nudge (final three), and before that Second Check, Meddly,
  ReMed, MedQ, DoubleTake, Amber, NextUp, TriageTwice and Lantern.
- A simple Meddy mascot is optional. If we use one, it guides visitors (especially in the game) and never
  looks like a doctor making decisions.

## What the website must do

A judge should understand the project within 30 seconds and try it within one minute. The page tells one
story in four steps:

1. **The problem:** nurses rate about 1 patient in 10 as less urgent than they really are.
2. **Try it:** you play the triage nurse.
3. **Meddy as a second check:** it catches 107 of those 131 patients.
4. **Why it matters:** in the simulation, urgent patients wait 88 minutes under first come, first served,
   and 24 minutes with Meddy's levels.

## Sections (one scrolling page)

| # | Section | What the visitor sees or does |
|---|---|---|
| 1 | Hero | "1 in 10 emergency patients waits longer than they should." One button: *Try being the triage nurse*. The disclaimer line underneath. |
| 2 | "You be the triage nurse" game | Real cases from the dataset. The player picks a level 1–5, then the real nurse's, Meddy's and the expert panel's levels are revealed. A score at the end. |
| 3 | Second check | 131 dots (the under-rated patients). A toggle compares random choice, a simple rule and Meddy at a 20% re-check budget: 20%, 38%, 67% caught. |
| 4 | Queue simulator | An animated waiting room with 3 doctor desks. Switch the queue order (first come / Meddy / nurse / expert), set how busy it is, and watch the urgent patients' waiting time. |
| 5 | Live tool | The form (complaint, age, sex, arrival, alertness, pain, injury, vital signs, optional nurse level) and a result card: level, probability bars, reasons, second-check warning, "Meddy is not sure". |
| 6 | Electronic line and digital hospital | The line ordered by urgency (levels 1–2 first, everyone else moves up a level every 45 minutes), and a link to or embed of the digital hospital walkthrough. |
| 7 | Honest limits | The 7 urgent patients sent to the slow queue, the drop at another hospital (68% to 55–58%), men vs women, and the note that waiting times are simulated. |
| 8 | Footer | Made by Koch Team (Yegor, Denys, Ostap), sources, GitHub link, and the disclaimer line. |

## Design rules

- **Urgency colours** (from the existing demo): red `#d03b3b` for levels 1–2, orange `#ec835a` for 3,
  green `#0ca30c` for 4–5. Never colour alone: always add the icon (⚠ for 1–2, ▲ for 3, ✓ for 4–5) and
  the number.
- **Brand accent:** one friendly colour that is not red, orange or green, so it never reads as urgency.
- **Tone:** warm and approachable, still calm and trustworthy; playful in the interactive parts, never
  scary or childish.
- **Type:** one clean, friendly sans-serif; big bold numbers for the headline results.
- **Motion:** 200–400 ms, only when it explains something. Respect `prefers-reduced-motion`.
- **Mobile first:** works from 360px wide; touch targets at least 44px.
- **Words:** plain language, as in the main README ("rated too low", not "false negative").
- **Honesty:** never suggest Meddy replaces nurses, and show the failures as clearly as the successes.

## Building it

- **Stack:** Vite + React + Tailwind CSS, Framer Motion for animation, hand-made SVG charts.
- **Story sections (2–4):** static JSON exported once from the notebook (about 30 sample cases with
  nurse, Meddy and expert levels; the second-check results; the waiting times). No server needed.
- **Queue simulator:** a JavaScript copy of `simulate_wait` in `triage.py`.
- **Live tool and line:** the API in `hospital/server.py` already exists:
  - `POST /api/triage` gives the suggested level. It takes a vital-signs preset, not raw numbers, so
    raw vital signs in the form need a small change on the backend.
  - `/api/line`, `/api/line/join`, `/api/line/wait` and `/api/line/next` run the electronic line.
  - If the API is offline, the site falls back to precomputed example patients.
- **Hosting:** the frontend on Vercel, Netlify or GitHub Pages; the API on Render or Hugging Face Spaces.

**Build order** (each step leaves a working site):

1. Static page with all sections and the real text and numbers.
2. The game.
3. The queue simulator.
4. The second-check dots.
5. The live tool connected to the API.
6. Polish: mobile, colour-blind check, reduced motion, loading and error states.

## What we need from each other

- **Yegor (backend):** the JSON export, and raw vital signs accepted by `/api/triage` (or a new endpoint).
- **Denys (project decisions):** final headline numbers, so the website and the main README never disagree.
- **Ostap (front end):** the website, following the rules above.
