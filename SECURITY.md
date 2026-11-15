# Security: what Meddy is protected against, and what it is not

Meddy is open to the internet, and the patient's side has no sign-in: anybody with the link can register a visit.
That is on purpose (a patient in an emergency department should not need an account), and it means the server
must expect spam and abuse. This page lists what we tried against our own site, what the server does about it now,
and what it still does not do.

All of it is plain code in [`hospital/guard.py`](hospital/guard.py), a few lines of
[`hospital/server.py`](hospital/server.py) and the sealed log in [`hospital/db.py`](hospital/db.py). No outside
security service is used. The tests are in [`tests/test_guard.py`](tests/test_guard.py) and
[`tests/test_guard_advanced.py`](tests/test_guard_advanced.py).

This is a prototype. It is not a medical device and it must not hold real patients' data.

## The five layers

1. **Limits.** How often one address, and everybody together, may call the open addresses.
2. **Blocking.** An address that keeps being refused is shut out for a while, longer each time.
3. **Only the visit's own phone** may change a visit.
4. **The browser runs only Meddy's own scripts** and talks only to Meddy's own server.
5. **A sealed log.** A line of a visit's record cannot be changed or removed without it showing.

The chief doctor's panel (`/chief`) has a "Security" card that shows what was refused, who is blocked now and
whether the log is unchanged.

## What we tried, and what happens now

| What somebody could try | What happens now |
|---|---|
| Fill the nurse's list with made-up patients from a script | One address may register 10 visits a minute, and everybody together 90 in ten minutes. After that the server answers "429 Too many requests" and says how many seconds to wait. |
| Keep trying after being refused | An address refused 30 times within five minutes is blocked from registering, check-in, "I feel worse", follow-up questions and the PIN for 10 minutes. The next block lasts 20 minutes, then 40, at most an hour. The phone is told to speak to the staff at the desk. |
| Spam from many addresses at once | When everybody together has used half of a limit, each single address gets a third of its usual share (3 registrations a minute instead of 10). A real patient registers once and does not notice. |
| Press "I feel worse" for every waiting patient (registration numbers follow one another), so the nurse is called for nothing | Only the phone that registered a visit may press it. The phone gets a secret key when it registers and must show it. Any other caller is refused (403), the nurse is not called, and each try counts towards a block. |
| Finish somebody else's check-in with invented answers | The same key is needed. Any other caller is refused (403). |
| Read the doctor's note of another patient | The note is sent only to the phone that holds the visit's key. |
| Guess the staff PIN, and change the `X-Forwarded-For` header at every try so the lock never counts | The server uses the address written by Hugging Face's proxy, and on a laptop the connection itself. Five wrong tries lock the PIN for a minute, one address may try 10 times a minute, and 30 refusals block it. |
| Send a huge request so the server runs out of memory | A request larger than 64 KB is refused (413) before it is read. The largest real request is under 10 KB. A request that does not say its size is refused too. |
| Keep the language model busy so real patients wait | The model has two seats. A third request does not wait in a queue: that patient gets the standard questions at once, as when the model is off. |
| Put a script into a name, a complaint or a note, to run on the nurse's or the doctor's screen | Two walls. First, every page writes patients' text as text: we tried `<img src=x onerror=...>` in the complaint, the patient's own words, the answers, the nurse's note and the referral reason, and nothing ran. Second, the browser is told the fingerprint (SHA-256) of each script the page itself contains, and runs no other. We put a script and an `onerror` handler into a live page by hand: the browser refused both. |
| If a script did run: send the patients' names to another site | The browser lets a page talk only to Meddy's own server. A test request to another site was blocked. |
| Show Meddy inside an invisible frame on another site, to trick staff into clicking | Only Hugging Face (where the site lives) may show Meddy in a frame. |
| Put text into a database query (SQL injection) | Every query passes its values separately from the query text. |
| A staff action started from another website while a nurse is signed in | The staff cookie is `SameSite=Lax` and `HttpOnly`: the browser does not send it with a request another site starts, and a script cannot read it. |
| Change a visit's record afterwards, for example the level the nurse set | Every line of the log carries a seal: a keyed fingerprint of the line and of the seal of the visit's line before it. A changed line, a line taken out, a line added, or the end cut off all make the seals stop fitting. The chief's panel then shows "Changed", the visit and what happened. |
| Look up every address of the server | `/docs` and `/openapi.json` are switched off. (The code is public anyway.) |

Every answer also tells the browser not to guess file types, not to tell other sites which Meddy address the
user came from, and not to keep answers about patients in its cache.

## The limits

| Address | One address | Everybody together |
|---|---|---|
| Register a visit | 10 a minute | 90 in ten minutes |
| Finish check-in | 20 a minute | 180 in ten minutes |
| "I feel worse" | 6 a minute | 40 in ten minutes |
| Ask the model for follow-up questions | 20 a minute | 180 in ten minutes |
| Staff PIN | 10 tries a minute | 120 in ten minutes |
| Spelling help while typing | 240 a minute | 3,000 in ten minutes |
| A phone asking about its visit | 1,200 a minute | no limit |
| The waiting-room board | 300 a minute | no limit |

"One address" is generous on purpose: a whole waiting room can sit behind one Wi-Fi router. The numbers are
our choice for a demonstration, not measured on a real hospital.

Reading (a phone asking about its visit, the board) is limited but never leads to a block. Staff who signed in
with the PIN are not limited or blocked at all: a nurse may share the hospital's Wi-Fi, and so its address, with
the phone that spams.

## Checked on the live site

After the first version went live we tried it there:

- 330 quick requests for the waiting-room figures: 299 answered, 31 refused (the limit is 300 a minute, and one
  earlier request counted).
- "I feel worse" for a visit that does not exist: refused after the sixth press in a minute.
- The same with a made-up `X-Forwarded-For` address in front: still refused.
- A 70 KB request: refused.

Blocking, the tighter limits under pressure, the script fingerprints and the sealed log were tested on a
laptop and by the automatic tests, not yet on the live site.

## Settings

| Name | What it does |
|---|---|
| `MEDDY_LIMITS=off` | Switches the limits and the blocking off (for example for a recording). The tests run with it. |
| `MEDDY_STRICT_SCRIPTS=off` | Goes back to letting a page run every script written inside it. |
| `MEDDY_LOG_KEY` | The secret key for the seals of the log. See below before setting it. |
| `MEDDY_PROXY=1` | Says that a proxy in front writes the visitor's address (Hugging Face is recognised by itself). |

## What is not protected

We want this list to be as clear as the one above.

- **The staff PIN is one shared PIN, and for the judges it is published** in [JUDGES.md](JUDGES.md). Anyone who
  reads it can open the staff screens, and with the chief's PIN press "Erase everything". A real hospital would
  give each nurse and doctor an account of their own.
- **We could not measure whether the live site tells visitors apart.** We could not look at Hugging Face's proxy
  from the inside, and we tested from one connection only. If all visitors reached the server from one address,
  the limits for one address would count for everybody together, and one spammer would get everybody blocked
  from registering for ten minutes. The numbers are chosen so that a demonstration still works in that case.
- **People behind one router share one address.** If one phone on the hospital's Wi-Fi gets blocked, the other
  patients on that Wi-Fi cannot register on their phones either until the block ends. They register at the desk.
- **A spammer with a great many addresses is only held by the limit for everybody.** When that limit is reached,
  real patients must wait too (up to ten minutes). A real service would put a paid anti-bot service in front. We
  chose not to add a CAPTCHA: a patient in pain should not solve puzzles.
- **Limits, blocks and the security figures are kept in memory.** A restart of the server forgets them, and
  every update restarts it.
- **The seals of the log use a key that is written in the code, unless `MEDDY_LOG_KEY` is set.** With the built-in
  key the seals show damage and careless changes, but somebody who has read the code and can write to the
  database file could make new seals that fit. With an own key they could not. Changing the key later makes
  every older line look changed, so it must be set before the first day, or followed by "Erase everything".
- **The sealed log does not notice a visit removed together with all of its lines.** Lines written before the
  log was sealed cannot be checked; the panel says how many there are.
- **Anybody who knows or guesses a registration number can see that visit's ticket, place in line and room**
  (`/api/me`). No name, complaint or level is shown there.
- **A phone that brings a visit back with its four-digit code has no key.** It can follow the visit, but it
  cannot press "I feel worse": the page tells the patient to speak to the staff.
- **Made-up patients of the simulation have no key,** so anybody may press "I feel worse" for them (within the
  limits).
- **Rude or nonsense text cannot be filtered.** A complaint is free text. The nurse can remove a visit and must
  give the reason.
- **Styles written inside a page are still allowed** (`'unsafe-inline'` for styles only), because the pages use
  them. An injected style could change how a page looks, not run code.
- **Whoever may push to the `main` branch runs code on the live site,** because the site updates itself from
  GitHub.
- **The libraries we use were not audited by us,** and nobody outside the team has tested the site's security.
- **On a laptop the site runs without HTTPS.** Online, Hugging Face provides it.

## If you find a problem

Open an issue at https://github.com/potlatiy/emergency-triage-ai/issues. Please use made-up data when you test.
