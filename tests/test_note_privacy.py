"""The doctor's note for the patient is health information. It reaches only the phone that registered the visit."""

import db
from test_hospital import client, doctors_view, fresh_day, in_the_waiting_room, server  # noqa: F401  (fresh_day: a new day before every test)


def test_the_doctors_note_is_given_only_to_the_phone_that_registered():
    arrival = client.post("/api/register", json={"complaint": "sore throat", "age": 30, "use_model": False,
                                                 "first_name": "Test", "last_name": "Person"}).json()
    reg, key = arrival["reg_no"], arrival["key"]
    assert len(key) >= 20 and key == db.phone_key(reg)
    client.post("/api/triage", json={"reg_no": reg})
    client.post("/api/refer", json={"reg_no": reg, "level": 3, "reason": "Looks unwell", "doctor_reg": "DR-1001"})
    note = "Sore throat, no fever. Rest and fluids."
    assert client.post("/api/doctor/finish", json={"reg_no": reg, "note": note}).status_code == 200

    stranger = client.get("/api/me/" + reg).json()                          # somebody counting up registration numbers
    assert stranger["status"] == "finished" and stranger["note"] is None
    assert client.get("/api/me/" + reg, headers={"X-Meddy-Visit": "a-guess"}).json()["note"] is None
    assert client.get("/api/me/" + reg, headers={"X-Meddy-Visit": key}).json()["note"] == note
    assert key not in client.get("/api/me/" + reg, headers={"X-Meddy-Visit": key}).text       # never sent back


def test_two_visits_never_share_a_key_and_the_page_sends_it_in_a_header():
    keys = {client.post("/api/register", json={"complaint": "cough", "age": 30 + i, "use_model": False,
                                               "first_name": "Test", "last_name": "Person"}).json()["key"] for i in range(5)}
    assert len(keys) == 5
    page = (server.STATIC / "index.html").read_text(encoding="utf-8")
    assert "X-Meddy-Visit" in page and "key=" not in page.replace("phone_key=", "")


def test_a_visit_from_before_keys_still_shows_its_note():
    reg = in_the_waiting_room()
    with db._lock, db._conn:
        db._conn.execute("UPDATE visits SET phone_key = NULL WHERE reg_no = ?", (reg,))
    db.call_next(reg)
    client.post("/api/doctor/finish", json={"reg_no": reg, "note": "All fine."})
    assert client.get("/api/me/" + reg).json()["note"] == "All fine."


def test_the_privacy_page_names_every_outside_service_the_pages_load_from():
    """A page that loads a font or a script from somewhere else tells that somewhere the visitor's address. The
    privacy page must name each one: the QR library from Cloudflare was loaded and not listed."""
    import re
    names = {"fonts.googleapis.com": "Google Fonts", "fonts.gstatic.com": "Google Fonts", "cdnjs.cloudflare.com": "Cloudflare"}
    privacy = (server.STATIC / "privacy.html").read_text(encoding="utf-8")
    loaded = set()
    for page in server.STATIC.iterdir():
        if page.suffix in (".html", ".js", ".css"):
            text = page.read_text(encoding="utf-8")
            loaded |= set(re.findall(r'(?:src|href)\s*=\s*"https://([^/"]+)[^"]*\.(?:js|css)', text))       # scripts and styles
            loaded |= set(re.findall(r'<link[^>]+href="https://([^/"]+)', text))                              # fonts and preconnects
            loaded |= set(re.findall(r'\.src\s*=\s*"https://([^/"]+)', text))                                # scripts added later
    assert "cdnjs.cloudflare.com" in loaded and "fonts.googleapis.com" in loaded
    for host in loaded:
        assert host in names, f"{host} is loaded by a page and is not known to this test: name it on the privacy page"
        assert names[host] in privacy, host
    # what the phone keeps: the row says the truth about the key
    assert "key" in privacy and "sent back to Meddy" in privacy

