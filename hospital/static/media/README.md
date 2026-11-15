# Pictures made with AI tools

Everything in this folder is an illustration made with AI tools on 15 November 2026. None of it shows a real
hospital or real people, and the patient's page says so next to each picture.

## The clips on the nurse step

Two short silent clips shown on the patient's screen while they wait for the nurse (step 3, `index.html`).
They are illustrations, made by AI tools on 15 November 2026. They do not show a real hospital or real people.

| File | What it shows | How it was made |
|---|---|---|
| `nurse-scan.mp4`, `nurse-scan.jpg` | The nurse scans the code on the patient's phone | Key frame: GPT Image 2.6 (`gpt_image_2_6`), with the row of pictures from our own drawing of the patient's path as the style reference. Motion: Kling 3.0 (`kling3_0`), 5 seconds, the key frame as first and last frame so the clip loops. Both through Higgsfield. |
| `nurse-check.mp4`, `nurse-check.jpg` | The nurse measures the seated patient's blood pressure | The same two steps. |

The clips were scaled to 640 pixels wide and saved without sound with `ffmpeg`. The `.jpg` is the first frame, shown
before the clip loads and to anyone who asks for reduced motion.

The code on the phone in `nurse-scan` is a drawing of a QR code. It is not a real one and opens nothing.

## The furnished floor plan

`plan.webp` (1024 by 688) lies under the floor plan on the patient's screen (`meddy-plan.js`): the six rooms seen
from straight above, with their furniture, on white.

- Made with GPT Image 2.6 (`gpt_image_2_6`) through Higgsfield. The reference picture was our own drawing of the
  six empty rooms, so the rooms, their colours and their doors are where the page's plan has them. The prompt was
  the team's style block and furnished-hospital prompt, asking for a strict view from above, no people, no words
  and no arrows.
- Two changes by hand: the rope between the first two queue posts at check-in was painted out, so the way in
  does not cross a rope; and the off-white around the rooms was turned to white, so the picture sits on the
  card without a box around it.
- Nothing live is in the picture. The names of the rooms, the way through them, "YOU" and the people on the
  waiting-room seats are drawn by the page on top of it, from the visit and from the real number of people
  waiting. The people are drawn by code, not generated.
- If the picture does not load, the page draws plain coloured rooms instead and leaves out the note about the
  AI tool.
