"""Keeps the Space on the latest main of GitHub, so nobody has to rebuild it by hand.

It runs next to the server (see start.sh) and does two things:
1. At start: when the Space files on main (deploy/huggingface) differ from the ones this Space was built with,
   it copies them to the Space. That commit makes Hugging Face rebuild with the new Dockerfile.
2. Then every two minutes: when main has a new commit, it starts a factory rebuild, which downloads it.

Needs the Space secret HF_TOKEN (a Hugging Face token with write access to this Space).
Set the Space variable MEDDY_AUTO_UPDATE=0 to pause it, for example during a live demo: a rebuild takes the
site down for a few minutes and starts a fresh day.
"""

import os
import subprocess
import time
from pathlib import Path

from huggingface_hub import HfApi

REPO = "https://github.com/potlatiy/emergency-triage-ai"
SPACE = os.environ.get("SPACE_ID", "darkshdws/meddy")      # Hugging Face sets SPACE_ID in every Space
EVERY = 120                                                 # seconds between two looks at GitHub
BUILT = Path("/home/user/space")                            # the Space's own files, as this copy was built
LATEST = Path("/home/user/app/deploy/huggingface")          # the same files on main
SPACE_FILES = ["Dockerfile", "start.sh", "watch.py", "README.md"]


def say(text):
    print(f"[auto-update] {text}", flush=True)


def main_commit():
    """The newest commit on main. git ls-remote is not limited like GitHub's API, which the Space shares."""
    try:
        out = subprocess.run(["git", "ls-remote", REPO, "refs/heads/main"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.split()[0] if out.returncode == 0 and out.stdout.strip() else None


def built_commit():
    """The commit this copy was built from, read from GitHub's download while building (see Dockerfile)."""
    path = Path("/home/user/built_commit")
    return path.read_text().strip() if path.exists() else ""


def changed_space_files():
    return [name for name in SPACE_FILES if (LATEST / name).exists()
            and (not (BUILT / name).exists() or (LATEST / name).read_bytes() != (BUILT / name).read_bytes())]


def run():
    if os.environ.get("MEDDY_AUTO_UPDATE") == "0":
        return say("paused (MEDDY_AUTO_UPDATE=0): the Space stays on this version.")
    token = os.environ.get("HF_TOKEN")
    if not token:
        return say("no HF_TOKEN secret, so the Space does not update itself. Rebuild it by hand instead.")
    api = HfApi(token=token)

    changed = changed_space_files()
    if changed:
        say(f"{', '.join(changed)} changed on main: copying to the Space, which then rebuilds.")
        api.upload_folder(folder_path=LATEST, repo_id=SPACE, repo_type="space", allow_patterns=SPACE_FILES,
                          commit_message="Space files from GitHub main")
        return

    built = built_commit() or main_commit()
    say(f"running {built[:7] if built else 'an unknown commit'}; looking at GitHub every {EVERY} s.")
    while True:
        time.sleep(EVERY)
        latest = main_commit()
        if latest and built and latest != built:
            say(f"main moved to {latest[:7]}: rebuilding the Space with it.")
            api.restart_space(SPACE, factory_reboot=True)
            return


if __name__ == "__main__":
    try:
        run()
    except Exception as error:          # never take the hospital down with it
        say(f"stopped: {error}")
