# Security: what Meddy is protected against, and what it is not

Meddy is open to the internet, and the patient's side has no sign-in: anybody with the link can register a visit.
That is on purpose (a patient in an emergency department should not need an account), and it means the server
must expect spam and abuse. This page lists what we tried against our own site, what the server does about it now,
and what it still does not do.

All of it is plain code in [`hospital/guard.py`](hospital/guard.py), a few lines of
[`hospital/server.py`](hospital/server.py) and the sealed log in [`hospital/db.py`](hospital/db.py). No outside
security service is used. The tests are in [`tests/test_guard.py`](tests/test_guard.py),
[`tests/test_guard_advanced.py`](tests/test_guard_advanced.py) and [`tests/test_guard_review.py`](tests/test_guard_review.py).

**The graded audit** is in [SECURITY_AUDIT.md](SECURITY_AUDIT.md). `python security_audit.py` starts a copy of the
server, attacks it and grades what holds; the things on the "not protected" list below count against the grade.

This is a prototype. It is not a medical device and it must not hold real patients' data.

## The five layers

1. **Limits.** How often one address, and everybody together, may call the open addresses.
2. **Blocking.** An address that keeps being refused is shut out for a while, longer each time.
3. **Only the visit's own phone** may change a visit.
4. **The browser runs only Meddy's own scripts and style sheets** and talks only to Meddy's own server.
5. **A sealed log.** A line of a visit's record cannot be changed or removed without it showing.

The chief doctor's panel (`/chief`) has a "Security" card that shows what was refused, who is blocked now and
whether the log is unchanged.

## What we tried, and what happens now

| What somebody could try | What happens now |
|---|---|
| Fill the nurse's list with made-up patients from a script | One address may register 10 visits a minute and 40 an hour, and everybody together 90 in ten minutes. After that the server answers "429 Too many requests" and says how many seconds to wait. |
| Close the site for everybody with requests that do nothing (a registration with no complaint, "I feel worse" for a number that does not exist) | Only requests the hospital accepted count towards the limit for everybody. "I feel worse" and check-in have no limit for everybody at all: a patient who is getting worse is never refused because of what other people sent. A reviewer showed that 430 such requests closed registration, check-in, "I feel worse" and the staff sign-in; this is closed now. |
| Keep trying after being refused | An address refused 30 times within five minutes is blocked from registering, check-in, "I feel worse" and follow-up questions for 10 minutes. The next block lasts 20 minutes, then 40, at most an hour. The phone is told to speak to the staff at the desk. Being refused because the site is full is nobody's fault and does not count. |
| Spam from many addresses at once | When everybody together has used half of a limit, each single address gets a third of its usual share (3 registrations a minute instead of 10). A real patient registers once and does not notice. |
| Press "I feel worse" for every waiting patient (registration numbers follow one another), so the nurse is called for nothing | Only the phone that registered a visit may press it. The phone gets a secret key when it registers and must show it. Any other caller is refused (403), the nurse is not called, and each try counts towards a block. |
| Finish somebody else's check-in with invented answers | The same key is needed. Any other caller is refused (403). |
| Read the doctor's note of another patient | The note is sent only to the phone that holds the visit's key. |
| Read another patient's follow-up questions, which the language model words with their complaint ("When did the crushing chest pain start?") | Only the phone that holds the visit's key gets them, and only until check-in is done. A reviewer found this open; it is closed now. |
| Pretend to be somebody else: a made-up address in `X-Forwarded-For`, a second header line, a port or brackets around the address, a new address out of the same IPv6 block at every try | The server reads every line of the header from the right, steps only over a proxy's own addresses, and stops at anything that is not an address. A whole IPv6 block (/64) counts as one visitor. On a laptop only the connection itself is believed. |
| Guess the staff PIN from one address | Five wrong tries lock the PIN for that address for a minute, and it may try 10 times a minute. |
| Send a huge request so the server runs out of memory | A request larger than 64 KB is refused (413) before it is read. The largest real request is under 10 KB. A request that does not say its size is refused too. |
| Keep the language model busy so real patients wait | The model has two seats. A third request does not wait in a queue: that patient gets the standard questions at once, as when the model is off. Patients' phones may hold one seat at most, so the nurse's summary and the doctor's note always have one. |
| Find somebody's visit by typing their insurance card number | The answer says that the card has an open visit and names its ticket, not the registration number. |
| Put a script into a name, a complaint or a note, to run on the nurse's or the doctor's screen | Two walls. First, every page writes patients' text as text: we tried `<img src=x onerror=...>` in the complaint, the patient's own words, the answers, the nurse's note and the referral reason, and nothing ran. Second, the browser is told the fingerprint (SHA-256) of each script the page itself contains, and runs no other. We put a script and an `onerror` handler into a live page by hand: the browser refused both. |
| Load a library from the QR code's host that runs code written in a page's text | The policy names the one file of the QR library, not its host. Another library from the same host was refused in the browser. |
| Put a style sheet into a page, to hide a danger warning on the nurse's screen | A page's own style sheet is listed by fingerprint too; one put into the page was not used. |
| If a script did run: send the patients' names to another site | The browser lets a page talk only to Meddy's own server, and no form may be sent anywhere. A test request to another site was blocked. |
| Show Meddy inside an invisible frame on another site, to trick staff into clicking | Only huggingface.co, whose page shows the Space, may show Meddy in a frame. Pages may hold no frames themselves. |
| A form on another website (or slipped into a page) pressing "Start a new day" for a nurse who is signed in | A request that changes something is refused when the browser says another site started it, or when it is the kind a plain form sends. Meddy's own pages always send JSON. |
| Text that reads differently than it is stored (invisible characters that turn the direction around) | Such characters and control characters are taken out of everything a patient or the staff types. |
| Put text into a database query (SQL injection) | Every query passes its values separately from the query text. |
| A staff action started from another website while a nurse is signed in | The staff cookie is `SameSite=Lax` and `HttpOnly`: the browser does not send it with a request another site starts, and a script cannot read it. |
| Change the log of a visit afterwards, for example the line that says which level the nurse set | Every line of the log carries a seal: a keyed fingerprint of the line and of the seal of the visit's line before it. A changed line, a line taken out, a line added, or the end cut off all make the seals stop fitting. The chief's panel then shows "Changed", the visit and what happened. |
| Hide such a change: take the seals off and call the lines "too old to check", or wait for the visit's next step to seal the log afresh | Every line must have a seal (a file from before sealing is sealed once, as found). A log that does not fit is never repaired by the next step: the alarm stays until "Erase everything". A reviewer found both tricks working; both are closed now. |
| Put a log saved before "Erase everything" (or from another copy of Meddy) under a new visit with the same number | Every database has a number of its own, new at every erase, and it is part of every seal. |
| Look up every address of the server | `/docs` and `/openapi.json` are switched off. (The code is public anyway.) |

Every answer also tells the browser not to guess file types, not to tell other sites which Meddy address the
user came from, and not to keep answers about patients in its cache.

## The limits

| Address | One address | Everybody together |
|---|---|---|
| Register a visit | 10 a minute, 40 an hour | 90 in ten minutes |
| Finish check-in | 20 a minute | no limit (one per visit) |
| "I feel worse" | 6 a minute | no limit (needs the visit's key) |
| Ask the model for follow-up questions | 20 a minute | 180 in ten minutes |
| Staff PIN | 10 tries a minute | 120 in ten minutes |
| Spelling help while typing | 240 a minute | 3,000 in ten minutes |
| A phone asking about its visit | 1,200 a minute | no limit |
| The waiting-room board | 300 a minute | no limit |

"One address" is generous on purpose: a whole waiting room can sit behind one Wi-Fi router. The numbers are
our choice for a demonstration, not measured on a real hospital. "Everybody together" counts only requests the
hospital accepted.

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

After the second layer went live we opened all six pages there: each ran its own scripts under the new policy,
and a script and an `onerror` handler that we put into the live patient page by hand were refused by the browser.

Blocking, the tighter limits under pressure and the sealed log were tested on a laptop and by the automatic
tests, not on the live site. The staff screens on the live site were only seen locked (we do not type the PIN
there from the test tools).

## Independent review

Three separate AI reviewers (Claude sessions that were given the code and SECURITY.md, not how it was written)
attacked the limits and blocking, the sealed log, and the pages. They were told to reproduce what they claimed.

- **No way to run a script on a staff screen was found.** Every place where a patient's text, a note or the
  language model's words reach a page writes them as text.
- **Found and fixed:**
  - Requests that did nothing used up the limit for everybody and closed "I feel worse", registration and the
    staff sign-in for all (the most serious finding).
  - Another patient's follow-up questions were readable by anyone.
  - The sealed log could be fooled in several ways (see the table above), and one damaged seal crashed the
    check, which left the old "Unchanged" on the screen.
  - The caller's address could be chosen with a second header line, a port, or addresses out of one IPv6 block.
  - A block for spamming also shut the nurse on the same Wi-Fi out of signing in.
  - The policy trusted the whole host of the QR library; a plain form could press body-less staff buttons.
  - One odd letter in a cookie crashed the request; the card-number answer named the registration number.
- **Found and not fixed** (all in the list below): the four-digit shared PIN and what follows from it; a card
  number can be used by a stranger; the registration answer names a department; what any phone can read about a
  visit by its number; the limits of the sealed log.

Each fixed finding has a test in `tests/test_guard_review.py` or `tests/test_guard_advanced.py`, and an attack
in `security_audit.py`.

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
- **A four-digit PIN can be guessed.** One address is slowed to five tries a minute, but a script that uses a
  new address for every try is not: a reviewer worked out that all 10,000 PINs fall in about 14 hours, and
  PINs like the published ones much sooner. Whoever has the PIN is staff: not limited, and able to do everything.
- **A phone on the hospital's Wi-Fi can keep the nurse from signing in** by sending five wrong PINs a minute,
  because the lock counts by address and they share one. Staff who are already signed in are not affected.
- **The staff cookie is the same for every sign-in and does not end on the server.** "Lock this screen" removes
  it from that browser, but a copy would keep working until the PIN is changed.
- **A stranger who knows somebody's insurance card number can register with it.** The card's owner is then told
  the card has an open visit and must go to the desk, where staff close the stranger's visit.
- **One address can still add 40 made-up patients an hour** to the nurse's list, each with "danger signs". The
  nurse can remove a visit and must give the reason.
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
- **The seal covers the log of steps, not the visit's own row.** The screens read the level, the status and the
  notes from the row. A level changed there would disagree with the log, but nothing compares the two.
- **The sealed log does not notice** a visit removed together with all of its lines; a log put back exactly as
  it was at an earlier moment, by somebody who kept a copy of the file from then; or a change in the order of
  lines between two visits. Somebody who can write to the file and then have the server restarted could also
  make it look like a file from before sealing, which the server seals afresh as it finds it.
- **An alarm about the log cannot be cleared** except by "Erase everything". Running an older version of the
  code on the same database for a while causes one, because that version writes lines without seals.
- **The registration answer names the department the key-word rule guessed** (for example "Cardiology"). The
  page never shows it and uses it only to choose the comfort tips, but it is in what the phone receives. The
  patient is never sent the urgency level. Changing this is a decision about the tips, which we left as it is.
- **Anybody who knows or guesses a registration number can see that visit's ticket, place in line, doctor and
  room, whether the patient said they feel worse, and how the visit ended** (`/api/me`; this is what lets a
  patient bring the visit back on another phone with the four-digit code). No name, complaint or level is
  shown, but the doctor's name tells the department, and "Resus bay" tells that the case is urgent. The wall
  board shows tickets and rooms too, as boards in hospitals do.
- **Somebody who types another person's insurance card number at registration** is told that the card has an
  open visit, and its ticket (not the registration number).
- **A phone that brings a visit back with its four-digit code has no key.** It can follow the visit, but it
  cannot press "I feel worse": the page tells the patient to speak to the staff.
- **Made-up patients of the simulation have no key,** so anybody may press "I feel worse" for them (within the
  limits).
- **Rude or nonsense text cannot be filtered.** A complaint is free text. The nurse can remove a visit and must
  give the reason.
- **A style written inside a single tag is still allowed,** because the pages use them. If somebody did get
  markup into a page, such a tag could cover part of the screen with a false message, or send the screen to
  another address. No policy can stop a page from being sent elsewhere; the first wall is that pages write
  patients' text as text.
- **Numbers from the server are written into the pages without the text-escaping function.** They are numbers
  today (the server refuses anything else), so nothing can be put in; it is a place to harden.
- **The QR library is loaded from its host without a fingerprint of the file.** If that host served a changed
  file, it would run on the patient's page.
- **Whoever may push to the `main` branch runs code on the live site,** because the site updates itself from
  GitHub.
- **The libraries we use were not audited by us, and their versions are not fixed** in the build. Nobody outside
  the team has tested the site's security.
- **On a laptop the site runs without HTTPS.** Online, Hugging Face provides it.

## If you find a problem

Open an issue at https://github.com/potlatiy/emergency-triage-ai/issues. Please use made-up data when you test.
