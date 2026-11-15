"""Keeps the Space on the latest main of GitHub, so nobody has to rebuild it by hand.

It runs next to the server (see start.sh) and looks at GitHub every two minutes. When main has a new commit:

1. It downloads that commit's code.
2. If the Space's own files changed (deploy/huggingface: Dockerfile, start.sh, watch.py, README.md), it copies
   them to the Space. That commit makes Hugging Face build the Space again with the new Dockerfile. This is the
   only case that needs a rebuild, and it takes the site down for about five minutes.
3. Otherwise it puts the new code in place and restarts only the hospital server, which start.sh runs in a loop.
   The site is away for a few seconds, not five minutes: Ollama keeps running with Gemma loaded and the database
   stays where it is. Open pages notice the new version and offer to reload.
4. If the server does not answer after that (for example the new code needs a library the image does not have),
   it falls back to a rebuild, as before.

Before this, every commit on main rebuilt the whole Space. On a day with 45 commits the site was away for a large
part of the afternoon, and a browser showed its own error page in its place (a black one in dark mode).

Step 2 and the fallback need the Space secret HF_TOKEN (a Hugging Face token with write access to this Space).
Step 3 needs nothing. Set the Space variable MEDDY_AUTO_UPDATE=0 to pause it all, for example during a recording.
"""

import os
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = "https://github.com/potlatiy/emergency-triage-ai"
SPACE = os.environ.get("SPACE_ID", "darkshdws/meddy")      # Hugging Face sets SPACE_ID in every Space
EVERY = 120                                                 # seconds between two looks at GitHub
APP = Path("/home/user/app")                                # the code the server runs, and its database
BUILT = Path("/home/user/space")                            # the Space's own files, as this copy was built
BUILT_COMMIT = Path("/home/user/built_commit")              # the commit the running code comes from
BROUGHT = Path("/home/user/app_files")                      # the files the last version of the code brought
SPACE_FILES = ["Dockerfile", "start.sh", "watch.py", "README.md"]
UPDATING = Path("/tmp/meddy-updating")                      # while it exists, start.sh does not start the server
SERVER_PID = Path("/tmp/meddy-server.pid")                  # written by start.sh
PORT = os.environ.get("MEDDY_PORT", "7860")
BACK_WITHIN = 90                                            # seconds the restarted server has to answer


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
    """The commit the running code comes from: written while building (see Dockerfile) and after every update."""
    return BUILT_COMMIT.read_text().strip() if BUILT_COMMIT.exists() else ""


def download(commit, into):
    """The code of one commit, unpacked into the folder `into`. Returns the folder that holds it."""
    packed = Path(into) / "code.tar.gz"
    with urllib.request.urlopen(f"{REPO}/archive/{commit}.tar.gz", timeout=60) as answer, open(packed, "wb") as file:
        shutil.copyfileobj(answer, file)
    unpacked = Path(into) / "code"
    with tarfile.open(packed) as archive:
        archive.extractall(unpacked, filter="data")       # "data": nothing may be written outside the folder
    inside = [p for p in unpacked.iterdir() if p.is_dir()]
    return inside[0] if len(inside) == 1 else unpacked     # GitHub puts everything in one folder named after the commit


def files_of(tree):
    """Every file of a code folder, as paths from its top."""
    tree = Path(tree)
    return sorted(str(p.relative_to(tree)) for p in tree.rglob("*") if p.is_file())


def changed_space_files(tree, built=None):
    """The Space's own files that are different in this code from the ones the Space was built with."""
    built = BUILT if built is None else Path(built)
    latest = Path(tree) / "deploy" / "huggingface"
    return [name for name in SPACE_FILES if (latest / name).exists()
            and (not (built / name).exists() or (latest / name).read_bytes() != (built / name).read_bytes())]


def put_in_place(tree, app=None, brought=None):
    """Copy the new code over the running one. A file the last version brought and this one no longer has is
    removed. Nothing else is touched: the database, and whatever the server wrote next to the code, stay."""
    app = APP if app is None else Path(app)
    brought = BROUGHT if brought is None else Path(brought)
    new = files_of(tree)
    old = brought.read_text().splitlines() if brought.exists() else []
    for name in new:
        target = app / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(tree) / name, target)
    for name in set(old) - set(new):
        (app / name).unlink(missing_ok=True)
    brought.write_text("\n".join(new) + "\n")
    return len(new)


def stop_server(wait=15):
    """Ask the hospital server to stop and wait until it has. start.sh starts it again once UPDATING is gone."""
    try:
        pid = int(SERVER_PID.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    until = time.monotonic() + wait
    while time.monotonic() < until:
        try:
            os.kill(pid, 0)                # still there?
        except ProcessLookupError:
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)       # it would not go: start.sh still starts the new one
    except ProcessLookupError:
        pass
    return True


def server_answers(within=BACK_WITHIN):
    """Does the hospital answer again? Asked every second for up to `within` seconds."""
    until = time.monotonic() + within
    while time.monotonic() < until:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/version", timeout=3) as answer:
                if answer.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(1)
    return False


def update_in_place(tree, commit):
    """Put the new code in place and restart only the hospital server. True when it answers again."""
    UPDATING.touch()                        # start.sh waits: the server must not start on half-copied files
    try:
        if not stop_server():
            return False                    # no server to restart (an older start.sh): rebuild instead
        count = put_in_place(tree)
        BUILT_COMMIT.write_text(commit + "\n")
    finally:
        UPDATING.unlink(missing_ok=True)
    say(f"{count} files in place; waiting for the hospital to answer.")
    return server_answers()


def hugging_face(token):
    from huggingface_hub import HfApi       # only needed for a rebuild
    return HfApi(token=token)


def rebuild(token, why):
    if not token:
        return say(f"{why}, and there is no HF_TOKEN secret: rebuild the Space by hand (Settings, Factory rebuild).")
    say(f"{why}: rebuilding the Space.")
    hugging_face(token).restart_space(SPACE, factory_reboot=True)


def run():
    if os.environ.get("MEDDY_AUTO_UPDATE") == "0":
        return say("paused (MEDDY_AUTO_UPDATE=0): the Space stays on this version.")
    token = os.environ.get("HF_TOKEN")

    # Right after a build: the Space's own files on main may be newer than the ones this Space was built with.
    changed = changed_space_files(APP)
    if changed and token:
        say(f"{', '.join(changed)} changed on main: copying to the Space, which then rebuilds.")
        hugging_face(token).upload_folder(folder_path=APP / "deploy" / "huggingface", repo_id=SPACE, repo_type="space",
                                          allow_patterns=SPACE_FILES, commit_message="Space files from GitHub main")
        return

    built = built_commit() or main_commit()
    say(f"running {built[:7] if built else 'an unknown commit'}; looking at GitHub every {EVERY} s.")
    while True:
        time.sleep(EVERY)
        latest = main_commit()
        if not latest or not built or latest == built:
            continue
        say(f"main moved to {latest[:7]}.")
        with tempfile.TemporaryDirectory() as room:
            try:
                tree = download(latest, room)
            except Exception as error:      # GitHub did not answer: try again at the next look
                say(f"could not download it ({error}); trying again in {EVERY} s.")
                continue
            changed = changed_space_files(tree)
            if changed:
                if not token:
                    return say(f"{', '.join(changed)} changed and there is no HF_TOKEN secret: update the Space by hand.")
                say(f"{', '.join(changed)} changed: copying to the Space, which then rebuilds.")
                hugging_face(token).upload_folder(folder_path=tree / "deploy" / "huggingface", repo_id=SPACE,
                                                  repo_type="space", allow_patterns=SPACE_FILES,
                                                  commit_message="Space files from GitHub main")
                return
            if update_in_place(tree, latest):
                built = latest
                say(f"now running {latest[:7]}. The site was away for a few seconds.")
                continue
        return rebuild(token, "the hospital did not answer after the update")


if __name__ == "__main__":
    try:
        run()
    except Exception as error:          # never take the hospital down with it
        say(f"stopped: {error}")
