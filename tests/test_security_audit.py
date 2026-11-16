"""The security audit (security_audit.py) attacks a real copy of the server over HTTP and grades what holds.
This test runs it, so a change that opens a hole fails the tests, and a known gap that gets closed is noticed."""

import security_audit


def test_the_audit_finds_no_new_hole_and_its_known_gaps_are_still_the_known_ones():
    results = security_audit.run()
    assert len(results) == len(security_audit.CHECKS) >= 40                     # every check ran: none is skipped
    surprises = [(r["title"], r["evidence"]) for r in results if not r["passed"] and not r["known_gap"]]
    assert surprises == []
    closed = [r["title"] for r in results if r["passed"] and r["known_gap"]]
    assert closed == [], "a known gap now passes: take the mark off in security_audit.py and update SECURITY.md"
    areas, whole = security_audit.summary(results)
    assert whole[1] == sum(r["weight"] for r in results if r["passed"]) and whole[2] == sum(r["weight"] for r in results)
    assert [name for name, *_ in areas] == ["Spam and flooding", "Other patients' visits", "Staff screens", "The browser",
                                             "Bad input", "The record of a visit"]
    text = security_audit.report(results)
    assert f"## Result: {whole[4]} ({whole[1]} of {whole[2]} points" in text and "known gap" in text
    # The report in the repository must be the one this code gives: the same grade, the same table, the same checks.
    saved = (security_audit.ROOT / "SECURITY_AUDIT.md").read_text(encoding="utf-8")

    def figures(report):
        return [line.split("|")[1:4] if line.startswith("| pass") or line.startswith("| **FAIL") else line
                for line in report.splitlines() if line.startswith(("## Result", "| ")) or " checks passed." in line]
    assert figures(saved) == figures(text), "run `python security_audit.py --write` and commit SECURITY_AUDIT.md"


def test_the_grades_are_worked_out_as_the_report_says():
    assert [security_audit.grade(share) for share in (1, 0.9, 0.899, 0.8, 0.7, 0.6, 0.599, 0)] == list("AABBCDFF")
    results = [{"area": "X", "weight": 3, "title": "a", "known_gap": False, "passed": True, "evidence": ""},
               {"area": "X", "weight": 1, "title": "b", "known_gap": True, "passed": False, "evidence": ""},
               {"area": "Y", "weight": 2, "title": "c", "known_gap": False, "passed": False, "evidence": ""}]
    areas, whole = security_audit.summary(results)
    assert areas == [("X", 3, 4, 0.75, "C"), ("Y", 0, 2, 0.0, "F")] and whole == ("Whole site", 3, 6, 0.5, "F")
    assert "**1 failed unexpectedly.**" in security_audit.report(results)
