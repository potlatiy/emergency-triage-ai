"""The live site updates itself (deploy/huggingface/watch.py). A new commit used to rebuild the whole Space, which
took the site down for about five minutes; now the new code is put in place and only the server restarts."""

import importlib.util
import io
import os
import subprocess
import sys
import tarfile
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("watch", ROOT / "deploy" / "huggingface" / "watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def tree(folder, files):
    for name, text in files.items():
        path = Path(folder) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return Path(folder)


def test_new_code_is_put_in_place_and_the_database_is_left_alone(tmp_path):
    app = tree(tmp_path / "app", {"hospital/server.py": "old", "hospital/old_page.html": "gone soon",
                                  "hospital/hospital.db": "every visit", "hospital/__pycache__/x.pyc": "cache"})
    brought = tmp_path / "app_files"
    brought.write_text("hospital/server.py\nhospital/old_page.html\n")           # what the last version brought
    new = tree(tmp_path / "new", {"hospital/server.py": "new", "hospital/static/board.html": "a new page"})
    assert watch.put_in_place(new, app, brought) == 2
    assert (app / "hospital/server.py").read_text() == "new"                       # changed
    assert (app / "hospital/static/board.html").read_text() == "a new page"        # added
    assert not (app / "hospital/old_page.html").exists()                           # no longer in the code: removed
    assert (app / "hospital/hospital.db").read_text() == "every visit"             # never part of the code: untouched
    assert (app / "hospital/__pycache__/x.pyc").exists()
    assert brought.read_text().splitlines() == ["hospital/server.py", "hospital/static/board.html"]


def test_the_first_update_removes_nothing_it_does_not_know(tmp_path):
    app = tree(tmp_path / "app", {"a.py": "old", "left_by_the_build.txt": "x"})
    new = tree(tmp_path / "new", {"a.py": "new"})
    watch.put_in_place(new, app, tmp_path / "app_files")                           # no list from an earlier version yet
    assert (app / "left_by_the_build.txt").exists() and (app / "a.py").read_text() == "new"


def test_only_a_change_to_the_spaces_own_files_needs_a_rebuild(tmp_path):
    built = tree(tmp_path / "space", {"Dockerfile": "FROM python", "start.sh": "run", "watch.py": "w", "README.md": "r"})
    same = tree(tmp_path / "same", {f"deploy/huggingface/{n}": (built / n).read_text() for n in watch.SPACE_FILES}
                | {"hospital/core.py": "changed code"})
    assert watch.changed_space_files(same, built) == []                            # code only: update in place
    other = tree(tmp_path / "other", {f"deploy/huggingface/{n}": (built / n).read_text() for n in watch.SPACE_FILES}
                 | {"deploy/huggingface/Dockerfile": "FROM python\nRUN pip install something"})
    assert watch.changed_space_files(other, built) == ["Dockerfile"]


def test_a_download_is_unpacked_inside_its_folder_only(tmp_path, monkeypatch):
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:gz") as archive:
        for name, text in {"repo-abc123/triage.py": "code", "repo-abc123/hospital/server.py": "server"}.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    asked = []

    def fake_open(url, timeout=None):
        asked.append(url)
        return io.BytesIO(packed.getvalue())
    monkeypatch.setattr(watch.urllib.request, "urlopen", fake_open)
    folder = watch.download("abc123", tmp_path)
    assert asked == [f"{watch.REPO}/archive/abc123.tar.gz"]
    assert watch.files_of(folder) == ["hospital/server.py", "triage.py"]


@pytest.mark.skipif(sys.platform == "win32", reason="the Space runs Linux; the loop is a shell script")
def test_the_server_loop_restarts_with_the_new_code_within_seconds(tmp_path, monkeypatch):
    """The whole hand-over on a small scale: start.sh's loop runs a stand-in server, the updater stops it, swaps
    the code and lets the loop start the new one. The stand-in "database" file is still there afterwards."""
    app = tree(tmp_path / "app", {"hospital/server.py": textwrap.dedent("""
        import pathlib, time
        pathlib.Path("answers.txt").write_text("version 1")
        time.sleep(60)"""), "hospital/hospital.db": "every visit"})
    new = tree(tmp_path / "new", {"hospital/server.py": textwrap.dedent("""
        import pathlib, time
        pathlib.Path("answers.txt").write_text("version 2")
        time.sleep(60)""")})
    updating, pid_file = tmp_path / "updating", tmp_path / "server.pid"
    loop = textwrap.dedent(f"""
        cd "{app}"
        trap 'kill "$(cat "{pid_file}" 2>/dev/null)" 2>/dev/null; exit 0' TERM INT
        while true; do
            while [ -e "{updating}" ]; do sleep 0.1; done
            "{sys.executable}" hospital/server.py &
            echo $! > "{pid_file}"
            wait $!
            sleep 0.2
        done""")
    start = (ROOT / "deploy" / "huggingface" / "start.sh").read_text()
    assert "while [ -e /tmp/meddy-updating ]; do sleep 0.2; done" in start and "echo $! > /tmp/meddy-server.pid" in start
    assert "exec python hospital/server.py" not in start                         # the loop, not a single run

    for name, value in (("APP", app), ("BROUGHT", tmp_path / "app_files"), ("BUILT_COMMIT", tmp_path / "built_commit"),
                        ("UPDATING", updating), ("SERVER_PID", pid_file)):
        monkeypatch.setattr(watch, name, value)
    monkeypatch.setattr(watch, "server_answers", lambda within=0: True)
    runner = subprocess.Popen(["sh", "-c", loop])
    try:
        wait_for(lambda: (app / "answers.txt").exists() and (app / "answers.txt").read_text() == "version 1")
        began = time.monotonic()
        assert watch.update_in_place(new, "abc123") is True
        wait_for(lambda: (app / "answers.txt").read_text() == "version 2")
        assert time.monotonic() - began < 10                                      # seconds, not minutes
        assert (app / "hospital/hospital.db").read_text() == "every visit"
        assert (tmp_path / "built_commit").read_text().strip() == "abc123" and not updating.exists()
    finally:
        runner.terminate()
        try:
            os.kill(int(pid_file.read_text()), 15)
        except (OSError, ValueError):
            pass
        runner.wait(timeout=10)


def wait_for(ready, seconds=15):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            if ready():
                return
        except OSError:
            pass
        time.sleep(0.1)
    raise AssertionError("did not happen in time")


def test_without_a_running_server_the_updater_does_not_pretend(tmp_path, monkeypatch):
    for name, value in (("APP", tmp_path / "app"), ("BROUGHT", tmp_path / "app_files"), ("BUILT_COMMIT", tmp_path / "built"),
                        ("UPDATING", tmp_path / "updating"), ("SERVER_PID", tmp_path / "no.pid")):
        monkeypatch.setattr(watch, name, value)
    (tmp_path / "app").mkdir()
    assert watch.update_in_place(tree(tmp_path / "new", {"a.py": "x"}), "abc") is False     # falls back to a rebuild
    assert not (tmp_path / "updating").exists() and not (tmp_path / "app" / "a.py").exists()


def test_the_site_says_which_commit_it_runs(tmp_path, monkeypatch):
    """After an update the watcher writes the commit down, and /api/version reports it: that is how we check that
    the live site really runs the latest main."""
    sys.path.insert(0, str(ROOT / "hospital"))
    import server
    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    monkeypatch.setenv("MEDDY_COMMIT_FILE", str(tmp_path / "nowhere"))
    assert client.get("/api/version").json()["commit"] is None                 # on a laptop
    written = tmp_path / "built_commit"
    written.write_text("5ae6e55c0ffee0000000000000000000000000000\n")
    monkeypatch.setenv("MEDDY_COMMIT_FILE", str(written))
    answer = client.get("/api/version").json()
    assert answer["commit"] == "5ae6e55" and len(answer["version"]) == 12

