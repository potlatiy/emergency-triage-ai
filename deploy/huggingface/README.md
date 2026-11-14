---
title: Meddy
emoji: 🏥
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: A digital emergency department with AI triage (prototype)
---

# Meddy online

**Live:** https://darkshdws-meddy.hf.space (the doctor's screen: https://darkshdws-meddy.hf.space/doctor)

This folder is the whole Hugging Face Space (https://huggingface.co/spaces/darkshdws/meddy): `Dockerfile`,
`start.sh`, `watch.py` and this README (the block at the top tells Hugging Face to build the Dockerfile).
The Space downloads the code from the `main` branch of https://github.com/potlatiy/emergency-triage-ai while
it builds, so the Space never holds a copy of it.

Everything runs in one container: the hospital server, both trained models, Ollama with Gemma 3 1B for
the history questions, and the SQLite database. The database starts a fresh demonstration day at every
start, as it does on a laptop. This is a prototype and not medical advice; it holds no real patients.

## Putting the latest `main` online

It happens by itself. `watch.py` runs inside the Space and looks at GitHub every two minutes; when `main`
has a new commit, it rebuilds the Space, which downloads it. The site is back about five minutes later.
A change to the files in this folder reaches the Space the same way: after the rebuild, `watch.py` sees
that they differ, copies them to the Space and Hugging Face builds again with them.

Its messages start with `[auto-update]` in the Space's logs (Space page → Logs → Container).

It needs the Space secret `HF_TOKEN`: a Hugging Face token with write access to this Space (Space →
Settings → Variables and secrets → New secret). Without it the Space simply stays on the version it was
built with. By hand at any time: Space → Settings → **Factory rebuild**.

**Before a live demo**, add the Space variable `MEDDY_AUTO_UPDATE` = `0`: otherwise a merge during the demo
takes the site down for a few minutes and starts a fresh day. Delete the variable afterwards.

A new Python library must also be added to the `pip install` line of the `Dockerfile`, which does not read
`requirements.txt`; otherwise the Space stops at start.

## Settings

| Where | Name | Default | What it does |
|---|---|---|---|
| Space secret | `HF_TOKEN` | none | Lets `watch.py` rebuild the Space |
| Space variable | `MEDDY_AUTO_UPDATE` | on | `0` pauses the automatic updates |
| `Dockerfile` | `MEDDY_OLLAMA_MODEL` | `gemma3:1b` | The Gemma that writes the questions, downloaded while building. `gemma3:4b` is better but too slow without a graphics card |
| Space variable | `MEDDY_OLLAMA_TIMEOUT` | `45` | Seconds to wait for Gemma before the standard questions are used |

## Good to know

- A free Space falls asleep after 48 hours without visitors. The first visit then takes about a minute.
  Open it before showing it to anyone.
- Phones open the same address; the QR code on the entrance step points to it.
