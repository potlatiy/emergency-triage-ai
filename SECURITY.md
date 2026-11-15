# Security: what Meddy is protected against, and what it is not

Meddy is open to the internet, and the patient's side has no sign-in: anybody with the link can register a visit.
That is on purpose (a patient in an emergency department should not need an account), and it means the server
must expect spam and abuse. This page lists what we tried against our own site, what the server does about it now,
and what it still does not do.

All of it is plain code in [`hospital/guard.py`](hospital/guard.py) and a few lines of
[`hospital/server.py`](hospital/server.py). No outside security service is used. The tests are in
[`tests/test_guard.py`](tests/test_guard.py).

This is a prototype. It is not a medical device and it must not hold real patients' data.

## What we tried, and what happens now

| What somebody could try | What happens now |
|---|---|
| Fill the nurse's list with made-up patients from a script | One address may register 10 visits a minute, and everybody together 90 in ten minutes. After that the server answers "429 Too many requests" and says how many seconds to wait. |
| Press "I feel worse" for every waiting patient (registration numbers follow one another), so the nurse is called for nothing | Only the phone that registered a visit may press it. The phone gets a secret key when it registers and must show it. Any other caller is refused (403), and the nurse is not called. |
| Finish somebody else's check-in with invented answers | The same key is needed. Any other caller is refused (403). |
| Read the doctor's note of another patient | The note is sent only to the phone that holds the visit's key (this was already so). |
| Guess the staff PIN, and change the `X-Forwarded-For` header at every try so the lock never counts | The lock used to believe the first address in that header, which the caller writes. Now the server uses the address written by Hugging Face's proxy, and on a laptop the connection itself. On top of the existing lock, one address may try 10 times a minute. |
| Send a huge request so the server runs out of memory | A request larger than 64 KB is refused (413) before it is read. The largest real request is under 10 KB. A request that does not say its size is refused too. |
| Keep the language model busy so real patients wait | The model has two seats. A third request does not wait in a queue: that patient gets the standard questions at once, as when the model is off. |
| Put a script into a name, a complaint or a note, to run on the nurse's or the doctor's screen | We tried `<img src=x onerror=...>` in the complaint, the patient's own words, the answers, the nurse's note and the referral reason, and opened the nurse's, doctor's, chief's and board screens. Nothing ran: every page writes patients' text as text. A name with such characters is refused at registration. |
| If a script did get in: send the patients' names to another site | Every page now carries a content policy. The browser lets a page talk only to Meddy's own server, and load scripts only from Meddy and from the one host of the QR-code library. We checked it in the browser: a request to another site was blocked. |
| Show Meddy inside an invisible frame on another site, to trick staff into clicking | Only Hugging Face (where the site lives) may show Meddy in a frame. |
| Put text into a database query (SQL injection) | Every query passes its values separately from the query text. |
| A staff action started from another website while a nurse is signed in | The staff cookie is `SameSite=Lax` and `HttpOnly`: the browser does not send it with a request another site starts, and a script cannot read it. |

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

The limits can be switched off with the setting `MEDDY_LIMITS=off` (for example if they get in the way of a
recording). The tests run with them off and test them separately.

## What is not protected

We want this list to be as clear as the one above.

- **The staff PIN is one shared PIN, and for the judges it is published** in [JUDGES.md](JUDGES.md). Anyone who
  reads it can open the staff screens, and with the chief's PIN press "Erase everything". A real hospital would
  give each nurse and doctor an account of their own.
- **A spammer with many addresses is only held by the limit for everybody.** When that limit is reached, real
  patients must wait too (up to ten minutes) and register at the desk. A real service would put a paid anti-bot
  service in front. We chose not to add a CAPTCHA: a patient in pain should not solve puzzles.
- **The limits are kept in memory.** A restart of the server forgets the counts, and every update restarts it.
- **We could not look at Hugging Face's proxy from the inside.** If it turned out that all visitors reach the
  server from one address, the limits for one address would count for everybody together. The numbers are
  chosen so that a demonstration still works in that case. This has not been measured on the live site yet.
- **Anybody who knows or guesses a registration number can see that visit's ticket, place in line and room**
  (`/api/me`). No name, complaint or level is shown there.
- **A phone that brings a visit back with its four-digit code has no key.** It can follow the visit, but it
  cannot press "I feel worse": the page tells the patient to speak to the staff.
- **Made-up patients of the simulation have no key,** so anybody may press "I feel worse" for them (within the
  limits).
- **Rude or nonsense text cannot be filtered.** A complaint is free text. The nurse can remove a visit and must
  give the reason.
- **The content policy allows scripts written inside the pages** (`'unsafe-inline'`), because that is how the
  pages are built. So it does not stop an injected script from running. It stops such a script from loading
  code from, or sending data to, another site. The first wall is that pages write patients' text as text.
- **Whoever may push to the `main` branch runs code on the live site,** because the site updates itself from
  GitHub.
- **The libraries we use were not audited by us,** and nobody outside the team has tested the site's security.
- **On a laptop the site runs without HTTPS.** Online, Hugging Face provides it.

## If you find a problem

Open an issue at https://github.com/potlatiy/emergency-triage-ai/issues. Please use made-up data when you test.
