# Privacy and cookies

The same text is on the site: https://darkshdws-meddy.hf.space/privacy (every screen links to it in its footer).

**The short version**

- **Meddy is a hackathon prototype, not a real hospital service.** Please use made-up names and details, never real
  health or insurance information. A note on the first visit says so and links here.
- **No advertising, analytics or tracking.** The only cookies are the two that keep staff signed in after they type
  a PIN. Patients get no cookies at all.
- **The AI runs on our own server.** Meddy's urgency model and the Gemma 3 language model both run inside it; what
  you type is not sent to any AI company.
- **Nothing is sold or shared,** and the demonstration is erased regularly.

## What Meddy keeps, and why

- **From the patient's phone:** first and last name, age, sex, how they arrived, the complaint in their own words,
  their answers and pain score. For insurance only a one-way fingerprint of the card number and its last four
  characters, never the number.
- **From the staff:** vital signs, the urgency level, the nurse's and the doctor's notes, which doctor and room, and
  the time of each step.
- **Why:** only to run the visit (the line, the right doctor, the phone saying what happens next) and the chief
  doctor's summary of the day. The waiting-room board shows tickets and rooms only. Nothing typed into Meddy is used
  to train its models.

It is kept in one SQLite file on the Hugging Face Space, with a copy in the Space's private storage, until the chief
doctor presses "Erase everything". "Start a new day" closes open visits but does not delete them.

## Cookies (strictly necessary only, so no consent banner is needed)

| Name | What it does | Lasts |
|---|---|---|
| `meddy_staff` | Remembers that the nurse or doctor typed the staff PIN (a signed token, never the PIN; `HttpOnly`) | 12 hours, or until "Lock this screen" |
| `meddy_chief` | The same for the chief doctor's panel | 12 hours, or until "Lock this screen" |

## Items saved in the browser (never sent anywhere)

| Name | What it does |
|---|---|
| `meddy-visit` | The patient's own visit number and step, so the phone carries on after a reload |
| `meddy-doctor` | Which doctor's screen is open on this computer |
| `meddy-offered-…` | Which new patients this doctor was already asked about |
| `meddy-large` | Larger text on or off |
| `meddy-curtain` | The caption while moving between screens (removed at once) |
| `meddy-privacy-seen` | The privacy note was closed |

## Other services

- **Hugging Face** hosts the site and sees IP addresses in its logs ([privacy policy](https://huggingface.co/privacy)).
- **Google Fonts** serves the typeface, so Google sees the visitor's IP address ([privacy policy](https://policies.google.com/privacy)).

## In a real hospital

Health information is a special category of personal data (GDPR Article 9). A hospital using Meddy would be
responsible for the data, run it on its own systems, and need a legal basis for health data, a data protection
impact assessment, real staff accounts instead of a shared PIN, and its own retention rules. This demonstration has
none of that, which is why it asks for made-up details.

A test (`test_the_privacy_page_names_every_cookie_and_browser_item_the_site_sets`) checks that the privacy page names
every cookie the server sets and every item the pages save, and that every screen links to it.
