# Meddy: demo video script (about 1:55)

Three of us on camera, about 18 seconds each, one stat each. A short screen recording of the demo in the middle,
and the three of us together for the last line. It keeps the brief's four parts: the problem, the demo, how it
works and the next steps. The video can be up to 2 minutes.

| Time | Who | Part | The stat |
|---|---|---|---|
| 0:00–0:18 | Denys, on camera | The problem: why we chose it | One extra death for every 82 patients delayed 6 to 8 hours |
| 0:18–0:36 | Ostap, on camera | Who it's for | Nurses rate about 1 patient in 10 as less urgent than they are |
| 0:36–1:21 | Screen recording, Ostap's voice | The demo | |
| 1:21–1:41 | Yegor, on camera | How it works and the baseline | Meddy catches 107 of the 131 patients nurses rated too low |
| 1:41–1:55 | All three, one shot | Where it fails, what's next | |

Swap the speakers if you like; each part works with any of you. Speak calmly, about 150 words a minute. Each
on-camera part is 45 to 49 words.

## 1. Denys: why we chose this problem (0:00–0:18)

**On screen:** Denys, to camera. Text on screen with the stat:
*"1 extra death for every 82 patients delayed 6–8 hours. Jones et al., Emergency Medicine Journal, 2022"*

> "For every 82 patients stuck in an English emergency department for six to eight hours before getting a bed, one
> extra person dies. That comes from a study of over five million patients. We chose this problem because waiting
> in the ER isn't just annoying. It can be deadly."

*(49 words)*

## 2. Ostap: who it's for (0:18–0:36)

**On screen:** Ostap, to camera. Text on screen: *"Nurses rated 1 patient in 10 less urgent than they were.
1,267 real patients, Moon et al., PLOS ONE, 2019"*

> "Who gets seen first is decided by one triage nurse, in a few minutes, under pressure. In the real hospital data
> we used, nurses rated about one patient in ten as less urgent than they were. So we built Meddy, a second pair
> of eyes for that nurse."

*(48 words)*

## 3. The demo, Ostap's voice over the screen (0:36–1:21)

Three browser windows side by side: the patient's phone, the nurse's screen and the doctor's screen. Leave short
pauses so the screen can catch up.

**0:36–0:48, the patient's phone.** "Make up a name", complaint `bad stomach pain and vomiting`, age `40`, Female,
"No insurance", register. **No, none of these**, **Cramping**, one quick answer, **Go to the nurse**: a code
appears.

> "The patient checks in on their own phone: what's wrong, a few danger signs, and follow-up questions an AI
> picks for this complaint."

**0:48–1:08, the nurse's screen.** Tap the patient. **Feverish**, then **Measure and get Meddy's report**: level 3.
**I want to check the patient myself**, choose **level 4**: the amber note **"▲ Look again: Meddy thinks this
patient is more urgent."** Change to **3**.

> "The nurse measures the vital signs and Meddy suggests level 3. If she rates the patient a 4, Meddy flags it:
> look again, this patient is probably more urgent. She corrects it."

**1:08–1:21, nurse, doctor, phone.** Pick **Dr. Baptiste**, send to the waiting room. Doctor's screen: **Yes, let
them come in**. The phone shows the room.

> "Meddy suggests a free doctor. The patient sees their place in line, never the level, and the doctor already
> has the whole story."

*(78 words, slower than the talking parts, with pauses)*

## 4. Yegor: how it works and the baseline (1:21–1:41)

**On screen:** Yegor, to camera. Text on screen: *"Catches 107 of 131 under-rated patients. Guessing 38% · Meddy
68% · Nurses 85%"*. Optionally cut to the README's second-check chart for the last sentence.

> "Meddy learned from 1,267 real emergency patients. Honestly, it's worse than a nurse: 68% right against 85%,
> while guessing gets 38%. So the nurse always decides. But as a second check, it catches 107 of the 131 patients
> nurses rated too low. It misses 24."

*(45 words)*

## 5. All three: where it fails and what's next (1:41–1:55)

**On screen:** all three in one shot, one sentence each.

> **Denys:** "It still misses urgent patients whose danger doesn't show in the vital signs."
> **Ostap:** "Next, we'll test it with real triage nurses."
> **Yegor:** "This project is a prototype and is not medical advice."

*(28 words)*

## Before you record

**The talking parts**
- Film landscape, at eye level, the phone propped up, in a quiet room with a window in front of you rather than
  behind. Leave a second of silence before and after each take, so it cuts cleanly.
- Learn your lines rather than reading them; a few words different is fine, but keep the numbers exact.
- Put the stat on screen as text while you say it, with its source. The judges will want to see where a number
  this strong comes from.

**The demo**
1. Open https://darkshdws-meddy.hf.space (narrow, like a phone), `/nurse` and `/doctor` side by side. Type the staff
   PIN `0070` on the nurse's and the doctor's windows. On the doctor's window choose **Dr. Baptiste**.
2. Add the Space variable `MEDDY_AUTO_UPDATE` = `0`, so a merge can't restart the site mid-recording. Delete it
   afterwards.
3. Run the patient through once before recording: the language model is slow the first time.
4. Nurse's screen, "Behind the scenes", **Start a new day**, then reload the phone window.

## Where the numbers come from

| Stat | Source |
|---|---|
| One extra death for every 82 patients delayed 6–8 hours, from over 5 million patients | Jones S et al. (2022), Emergency Medicine Journal, https://pubmed.ncbi.nlm.nih.gov/35042695/ (quoted in `PROJECT_GUIDE.md`) |
| Nurses rated 131 of 1,267 patients (about 1 in 10) too low | Our data: Moon et al. (2019), PLOS ONE, https://doi.org/10.1371/journal.pone.0216972 |
| Guessing level 3: 38%, Meddy: 68%, nurses: 85%; Meddy flags 107 of the 131, misses 24 | Our own tests, `triage.ipynb` (README, Results 2 and 3) |
