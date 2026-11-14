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

This folder is the whole Hugging Face Space: `Dockerfile`, `start.sh` and this README (the block at the
top tells Hugging Face to build the Dockerfile). The Space downloads the code from the `main` branch of
https://github.com/potlatiy/emergency-triage-ai while it builds, so the Space never holds a copy of it.

Everything runs in one container: the hospital server, both trained models, Ollama with Gemma 3 1B for
the history questions, and the SQLite database. The database starts a fresh demonstration day at every
start, as it does on a laptop. This is a prototype and not medical advice; it holds no real patients.

## Putting the latest `main` online

Space page → Settings → **Factory rebuild**. Building takes about 5–10 minutes.

## Settings (Space → Settings → Variables)

| Variable | Default here | What it does |
|---|---|---|
| `MEDDY_OLLAMA_MODEL` | `gemma3:1b` | The Gemma that writes the questions. `gemma3:4b` is better but too slow without a graphics card. Changing it needs a factory rebuild, because the model is downloaded while building |
| `MEDDY_OLLAMA_TIMEOUT` | `45` | Seconds to wait for Gemma before the standard questions are used |

## Good to know

- A free Space falls asleep after 48 hours without visitors. The first visit then takes about a minute.
  Open it before showing it to anyone.
- Phones open the same address; the QR code on the entrance step points to it.
