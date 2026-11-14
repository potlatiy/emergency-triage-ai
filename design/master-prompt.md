# Master design prompt for Meddy

Paste everything inside the code block into a design or coding assistant, together with the current
`hospital/static/index.html`. The assistant is told to stop after the wireframe and wait for feedback.
The guideline and the plan it follows are in [README.md](README.md).

```xml
<role>
You are a Principal Product UI/UX Designer and Design Systems Engineer. Your goal is to design intuitive, accessible (WCAG AA/AAA), and modern interfaces while avoiding generic "AI-template" aesthetics. You have experience with medical and healthcare interfaces, where clarity and calm matter more than visual flair.
</role>

<context>
- Product / Service: "Meddy" (tagline: "Your triage buddy"), the working web app of the hackathon project by Koch Team (Veritas AI High School Hackathon 2026, Health track). The team name is only a credit and must not appear in the product branding. Meddy is a "digital hospital" the visitor walks through as an emergency patient, in five steps:
  1. Arrive: type the complaint (up to 200 characters), age (15–110), sex, how you arrived. The visit gets a registration number.
  2. Questions: a questionnaire built for this complaint: three hand-written danger-sign Yes/No questions, two follow-up questions written on the spot by a small AI language model (takes up to about 15 seconds the first time), and a pain slider from 0 to 10.
  3. Nurse: choose the vital signs (Normal / Feverish / Unstable) and alertness (Alert / Responds to voice / Responds to pain / Unresponsive). Meddy's model, trained on 1,267 real emergency patients, suggests an urgency level from 1 (most urgent) to 5, and a department with two other options. Levels 1–2 go straight to the resuscitation bay.
  4. Waiting room: the patient gets a ticket and a place in a line ordered by urgency (waiting moves you up). Demo controls: "15 minutes pass" and "Call next patient".
  5. Doctor: the least busy doctor on duty in that department, their room, and "What the doctor already knows" (the handover note with the questionnaire answers). Then "Start again as a new patient", or "Start a new day" (resets the database).
  A floor plan shows a "YOU" marker walking from room to room. A side panel shows the hospital database (doctors on duty, visits) changing underneath. Key facts: the model is worse than nurses at the exact level (68% vs 85%), so the nurse always decides; as a second check it flags 107 of the 131 patients nurses rated too low; in a simulation the most urgent patients waited 88 minutes in arrival order and 13 with nurse levels. The page must say "This project is a prototype and is not medical advice."
- Target Audience:
  - Primary: hackathon judges watching a 2-minute screen-recorded video and possibly opening the app themselves on a laptop or phone. Tech-savvy, short on time; they score technical quality, impact, creativity and clarity.
  - Secondary: the people it is designed for: an emergency patient (any age, often stressed, in pain, possibly not tech-savvy) and the triage nurse (expert, very busy).
- Platform: Responsive web, mobile-first (from 360px to about 1,280px). One existing file, hospital/static/index.html: plain HTML, CSS and JavaScript, no framework, no build step, served by a FastAPI server. It calls /api/register, /api/triage, /api/line, /api/line/join, /api/line/wait, /api/line/next, /api/staff, /api/staff/duty and /api/reset. Element ids, the flow and the API calls must not change. The words "Digital hospital" must remain on the page (an automated test checks them).
- Design System / Branding: A small system for "Meddy". Tone: calm, clean, trustworthy and friendly; a medical website, never playful to the point of looking unserious, never scary.
  - Brand colour: one calm colour (suggestion: blue #2563eb) for the wordmark, primary buttons, focus rings, the progress bar and the "YOU" marker. The current page uses red #e5484d for the marker and the active room; replace it, because red must only mean urgency.
  - Urgency colours, used only for the urgency level: red #d03b3b for levels 1–2 (Resuscitation, Emergency), orange #ec835a for level 3 (Urgent), green #0ca30c for levels 4–5 (Less urgent, Non-urgent). Never colour alone: always the icon (⚠ for 1–2, ▲ for 3, ✓ for 4–5), the number and the name.
  - Background: light (white or the current soft green paper), white cards, generous spacing.
  - Typography: Outfit (already loaded) for everything; IBM Plex Mono only for registration numbers and tickets. Headings 24–28px, body 16–18px, form text at least 16px.
  - Motion: only the "YOU" marker walking and the progress bar, 200–400 ms; none with prefers-reduced-motion.
  - Language: plain words and short sentences.
</context>

<task>
Redesign the Meddy digital hospital page so it is simple, understandable and calm, with everything in the right place. Specifically:
- A header with the Meddy wordmark, one line of explanation and a progress bar for the five steps (Arrive · Questions · Nurse · Waiting room · Doctor).
- One step card at a time, with one primary button at the bottom. No tabs, no menus, no unnecessary buttons.
- Excellent typing windows: large fields (at least 48px tall), labels above the fields, examples as placeholders only, the right keyboard on phones, a character counter on the complaint, inline errors under the field, and the danger-sign questions as big Yes/No buttons.
- A clear result card at the nurse step: level as number + icon + colour + name, what happens next, the suggested department with the two alternatives, and "Suggested by Meddy. The nurse decides."
- In the waiting room, "15 minutes pass" and "Call next patient" grouped in a small, clearly labelled "Demo controls" box, separate from the patient's own actions.
- At the doctor step, "Start a new day" as a small text link, not a second large button.
- The database panel turned into one "Behind the scenes" drawer that can be opened (it is opened in the video).
- The floor plan kept: beside the step card on a laptop, above it (smaller) on a phone.
- A footer with "Made by Koch Team", the GitHub link (https://github.com/potlatiy/emergency-triage-ai) and the disclaimer line.
</task>

<rules>
1. Ground your decisions in established interaction patterns (Nielsen Norman Group, Material 3, Apple HIG), and in medical-form practice: one question at a time where possible, plain language, forgiving input.
2. Do not reinvent standard UI controls when a familiar pattern provides better usability.
3. Account for edge cases: empty states, validation errors, text overflow, and loading states. For this project that specifically includes: an empty complaint or an age outside 15–110; a 200-character complaint; the AI questions taking up to 15 seconds or failing (the hand-written questions are then used); the server being offline; an empty waiting room; a doctor going off duty; the "Meddy isn't sure" case; the resuscitation path (levels 1–2) that skips the waiting room; reduced motion; colour-blind users.
4. Keep every existing element id, the flow and the API calls unchanged. Change the look and the layout, not the behaviour.
5. If the requirements have critical gaps, ask clarifying questions before generating the final output, or explicitly state your assumptions.
</rules>

<definition_of_done>
The task is complete when you provide:
- A structured layout breakdown (components, grid, visual hierarchy, and spacing) for laptop and phone.
- UX rationale explaining the reasoning behind key layout and flow decisions.
- A comprehensive list of interactive states (Default, Hover, Focus, Active, Disabled, Loading, Error, Empty) for the fields, buttons, Yes/No choices, slider, result card and drawer.
</definition_of_done>

<pause_instruction>
Stop and ask for my feedback after presenting the high-level wireframe structure before moving on to detailed specifications or frontend code.
</pause_instruction>
```
