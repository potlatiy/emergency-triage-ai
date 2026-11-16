# Security audit

Made by `python security_audit.py --write` on 16 November 2026, on the code this file is committed with. Run it again after any change: the grade is only true for the code it ran on.

The tool starts its own copy of the hospital server with a throwaway database and attacks it over real HTTP:
floods, guessing, other patients' visits, oversized and malformed requests, and changes made straight in the
database file. Every check is a sentence that should be true of a safe site, and it passes or fails.
The checks marked *known gap* are things Meddy does not do (see [SECURITY.md](SECURITY.md)). They count
against the grade.

## Result: B (111 of 132 points, 84%)

48 of 60 checks passed. 12 failed as expected (known gaps). None failed unexpectedly.

| Area | Points | Share | Grade |
|---|---|---|---|
| Spam and flooding | 27 of 29 | 93% | A |
| Other patients' visits | 17 of 23 | 74% | C |
| Staff screens | 17 of 24 | 71% | C |
| The browser | 18 of 19 | 95% | A |
| Bad input | 8 of 8 | 100% | A |
| The record of a visit | 24 of 29 | 83% | B |
| **Whole site** | **111 of 132** | **84%** | **B** |

Points: 3 = a patient could be harmed or their data exposed, 2 = the service could be disturbed or the staff
misled, 1 = extra hardening. Grade: A from 90%, B from 80%, C from 70%, D from 60%, else F. The weights and
the scale are our own judgement, not a standard.

## Spam and flooding

| | Points | What should be true | What the audit saw |
|---|---|---|---|
| pass | 3 | One connection cannot fill the nurse's list: the 11th registration in a minute is refused | 13 registrations: 10 accepted, 3 refused |
| pass | 3 | Writing a false address in front of the request does not make the caller somebody new | with two made-up addresses in front: 429 |
| pass | 2 | A second address line in the request does not make the caller somebody new either | 13 registrations, each with its own made-up first line: 10 accepted |
| pass | 3 | Requests that do nothing cannot close registration or "I feel worse" for everybody | after 300 such requests: a real patient's "I feel worse": 200 (nurse told: True); a new patient registers: 200 |
| pass | 2 | A refused caller is told how long to wait, and another patient is not affected | refused with Retry-After 60 s; a patient at another address: 200 |
| pass | 2 | A request far larger than any questionnaire is refused before it is read | 70,000 bytes: 413 |
| pass | 3 | A connection that keeps being refused is shut out of registering, and told to go to the desk | after 40 refusals: registering gives 429, wait 600 s |
| pass | 2 | A blocked phone can still see its own place in line, and other patients are not blocked | blocked phone registers: 429; reads its place: 200; another patient: 200 |
| pass | 2 | Signed-in staff are never limited, even on an address that is blocked | a phone on that address: 429; the nurse, 12 registrations: 12 accepted |
| pass | 2 | Under a flood from many addresses, each single address gets a smaller share | once half of the limit for everybody was used: 3 registrations a minute for one address instead of 10 |
| pass | 2 | A flood from a great many addresses is held to the limit for everybody | registrations from ever new addresses stopped being accepted after 17 more |
| **FAIL** (known gap) | 2 | A real patient can still register on the phone while such a flood lasts | a new patient during the flood: 429 (they must register at the desk) |
| pass | 1 | The language model takes two questions at a time; a third does not wait in a queue | the third caller is turned away at once and gets the standard questions |

## Other patients' visits

| | Points | What should be true | What the audit saw |
|---|---|---|---|
| pass | 3 | Nobody but the patient's own phone can press "I feel worse" for a visit | a stranger: 403; a guessed key: 403; nurse called for nothing: False; the own phone: 200 |
| pass | 3 | Nobody but the patient's own phone can finish their check-in | a stranger: 403; the visit stayed 'registered'; the own phone: 200 |
| pass | 3 | The doctor's note reaches only the phone that registered the visit | a stranger gets None, a guessed key None, the own phone the note |
| pass | 3 | What any phone can read about a visit holds no name, no complaint and no urgency level | nothing of the kind in /api/me, the board, the waiting room or the registration answer |
| pass | 3 | Nobody else can read the follow-up questions, which repeat the patient's complaint | a stranger: 403; a guessed key: 403; the own phone: 200 |
| pass | 2 | Typing somebody's insurance card number does not give away their visit | the answer says the card has an open visit (409) and names no registration number |
| **FAIL** (known gap) | 2 | A stranger who knows a card number cannot register with it and keep its owner out | the stranger registered first (200); the card's owner then gets 409 until staff close that visit |
| **FAIL** (known gap) | 2 | The patient's phone is never sent a department | the registration answer names the department the key-word rule guessed (Cardiology); the page uses it only to choose the comfort tips and never shows it |
| **FAIL** (known gap) | 2 | Somebody who guesses a registration number learns nothing about that visit | a stranger sees the visit's ticket, position (no name, complaint or level) |

## Staff screens

| | Points | What should be true | What the audit saw |
|---|---|---|---|
| pass | 3 | Without the staff PIN, no staff address gives any data or accepts any action | 17 staff addresses tried: all refused |
| pass | 3 | The chief doctor's panel and "Erase everything" need the chief's own PIN | without a PIN: [401, 401]; with the nurse's PIN: [401, 401, 401] |
| pass | 3 | Guessing the PIN is slowed down: after a few wrong tries the connection must wait | 14 guesses: 5 were looked at, then refused; even the right PIN waits (429) |
| pass | 2 | Somebody guessing the PIN does not lock the real staff out | a nurse at another address signs in as usual |
| pass | 2 | A connection that was blocked for spamming can still be used by a nurse to sign in | a phone on that connection registers: 429; the nurse signs in: yes |
| **FAIL** (known gap) | 2 | A phone on the hospital's Wi-Fi cannot keep the nurse from signing in | after five wrong PINs from the shared address, the right PIN gets 429 for a minute, and again every minute |
| **FAIL** (known gap) | 2 | A four-digit PIN cannot be found by trying from many addresses | 30 of 30 guesses from 30 addresses were looked at; nothing but the number of addresses limits this |
| pass | 2 | The staff cookie cannot be read by a script and is not sent with requests another site starts | HttpOnly and SameSite=Lax are set |
| pass | 2 | Another website, or a form slipped into a page, cannot press a staff button for a signed-in nurse | a plain form on "Start a new day" and "next": [403, 403, 403, 403]; a request from another site: 403; the nurse's own page: 200 |
| **FAIL** (known gap) | 2 | Every nurse and doctor signs in with an account of their own | one shared staff PIN and one chief PIN; for the judges both are published |
| **FAIL** (known gap) | 1 | A copied staff cookie stops working when the screen is locked | the old cookie after "Lock this screen": 200 (the cookie is the same for every sign-in) |

## The browser

| | Points | What should be true | What the audit saw |
|---|---|---|---|
| pass | 3 | A page runs only its own scripts: each one is listed by fingerprint, nothing else written into a page runs | 6 pages: every script-src lists exactly the page's own scripts |
| pass | 3 | A page may send data only to Meddy's own server | on every page: connect-src 'self', no form may be sent anywhere, base-uri 'self' |
| pass | 2 | From outside, a page may load one script file only (the QR library), not everything its host serves | script-src names only qrcode.min.js on every page |
| pass | 2 | A style sheet put into a page is not used (it could hide a danger warning on the nurse's screen) | every page lists exactly its own style sheet by fingerprint |
| pass | 2 | No other site can show Meddy inside a hidden frame | may be framed by: 'self' https://huggingface.co; frames inside a page: 'none' |
| pass | 1 | Answers about patients are not cached, file types are not guessed, the address is not passed on | no-store / nosniff / no-referrer |
| pass | 2 | No page depends on a handler written inside a tag (the policy would not run it) | none in the six pages and two script files |
| pass | 2 | A name with markup in it is refused at registration | four such names: [422, 422, 422, 422] |
| pass | 1 | The list of every address of the server is not served | /docs, /redoc, /openapi.json: [404, 404, 404] |
| **FAIL** (known gap) | 1 | A style written inside a single tag is refused too | still allowed, because the pages use them; such a style can only change the tag it is written in |

## Bad input

| | Points | What should be true | What the audit saw |
|---|---|---|---|
| pass | 3 | Text meant to break a database query is stored as plain text and breaks nothing | registered as text: True; odd registration numbers: [404, 404, 404]; the table of visits still answers |
| pass | 2 | Malformed requests to the open addresses are refused cleanly, never with a server error | 118 malformed requests: none gave a server error |
| pass | 1 | Characters that cannot be seen but turn the text around are taken out of what a patient types | stored as 'no chest pain at all' |
| pass | 2 | A request that does not say how large it is cannot be used to send an endless body | HTTP/1.1 413 Content Too Large |

## The record of a visit

| | Points | What should be true | What the audit saw |
|---|---|---|---|
| pass | 3 | A changed line of a visit's log is noticed (for example the line that says which level was set) | a line was changed or added, or one before it was removed |
| pass | 3 | A line taken out of a visit's log is noticed | a line was changed or added, or one before it was removed |
| pass | 2 | The last line of a log cut off is noticed | counted among the 9 visits whose log was changed |
| pass | 2 | A line added without the key is noticed | a line was changed or added, or one before it was removed |
| pass | 3 | Lines cannot be passed off as "too old to check" by taking their seals off | a line was changed or added, or one before it was removed |
| pass | 3 | A cut log stays an alarm when the visit goes on: the next step does not repair it | after a new step on each: cut log: counted among the 9 visits whose log was changed; emptied log: the beginning of the log was removed |
| pass | 2 | A damaged seal is an alarm, not a crash that leaves the old "Unchanged" on the screen | the check answered 200; counted among the 9 visits whose log was changed |
| pass | 2 | A log whose visit was deleted is noticed | counted among the 9 visits whose log was changed |
| pass | 2 | An untouched log is not blamed: only the visits that were changed are counted | 163 lines checked; 9 visits counted as changed, 9 expected |
| **FAIL** (known gap) | 2 | Somebody who has read the code cannot forge a seal | a line changed and sealed again with the key written in the code was accepted (MEDDY_LOG_KEY is not set, as on the live site) |
| **FAIL** (known gap) | 2 | A level changed in the visit's own row, not in its log, is noticed | not noticed: the seals cover the log of steps, and nothing compares the row with it |
| **FAIL** (known gap) | 1 | A visit removed together with all of its lines is noticed | not noticed: nothing is left that a seal could be checked against |
| pass | 2 | The chief doctor sees what was refused, and no visitor's address is shown | refused so far: {'too_many': 79, 'blocked': 36, 'wrong_phone': 13, 'wrong_pin': 45, 'too_large': 112, 'other_site': 5}; addresses in the answer: 0 |

## What this audit does not cover

- **It runs on a laptop, against a copy.** It does not show how the live site behaves behind Hugging Face's
  proxy. What we tried on the live site by hand is in [SECURITY.md](SECURITY.md).
- **It has no browser.** That a script hidden in a complaint or a note does not run on the staff screens was
  tried by hand in a browser, not by this tool. The tool checks the policy the server sends.
- **It was written by the team that wrote the code.** Nobody outside the team has tested the site.
- **The language model is off** in the audit's server, so only its two seats are checked.
- A passed check means this one attack failed, not that no attack of that kind can work.

