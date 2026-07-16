import pytest

from command_center import models, report_parser

APPROVED_REPORT = """
## Verdict or result

APPROVED FOR COMMIT

## Git status

Working tree clean.
"""

NOT_APPROVED_REPORT = """
## Verdict or result

NOT APPROVED FOR COMMIT

## Findings with severity

### Blocker
- SQL injection risk in query builder (auth/db.py:42)

### High
- Missing rate limiting on login endpoint

## Files changed

- auth/db.py
"""

READY_FOR_FINAL_REVIEW_REPORT = """
1. Verdict or result

READY FOR FINAL REVIEW
"""

NOT_READY_FOR_FINAL_REVIEW_REPORT = "Verdict: NOT READY FOR FINAL REVIEW\n"

READY_FOR_COMMIT_REPORT = "Verdict: READY FOR COMMIT\n"

FAILED_REPORT = "Verdict: FAILED\n\nTests could not run due to a missing dependency.\n"


def test_parses_approved_for_commit():
    parsed = report_parser.parse_report(APPROVED_REPORT)
    assert parsed["verdict"] == models.VERDICT_APPROVED_FOR_COMMIT


def test_parses_not_approved_for_commit_not_confused_with_approved():
    parsed = report_parser.parse_report(NOT_APPROVED_REPORT)
    assert parsed["verdict"] == models.VERDICT_NOT_APPROVED_FOR_COMMIT


def test_parses_ready_for_final_review():
    parsed = report_parser.parse_report(READY_FOR_FINAL_REVIEW_REPORT)
    assert parsed["verdict"] == models.VERDICT_READY_FOR_FINAL_REVIEW


def test_parses_not_ready_for_final_review_not_confused_with_ready():
    parsed = report_parser.parse_report(NOT_READY_FOR_FINAL_REVIEW_REPORT)
    assert parsed["verdict"] == models.VERDICT_NOT_READY_FOR_FINAL_REVIEW


def test_parses_ready_for_commit():
    parsed = report_parser.parse_report(READY_FOR_COMMIT_REPORT)
    assert parsed["verdict"] == models.VERDICT_READY_FOR_COMMIT


def test_parses_failed():
    parsed = report_parser.parse_report(FAILED_REPORT)
    assert parsed["verdict"] == models.VERDICT_FAILED


def test_missing_verdict_is_none_not_invented():
    parsed = report_parser.parse_report("Just some unrelated text with no verdict.")
    assert parsed["verdict"] is None


def test_severity_counts():
    parsed = report_parser.parse_report(NOT_APPROVED_REPORT)
    counts = report_parser.severity_counts(parsed)
    assert counts == {"Blocker": 1, "High": 1, "Medium": 0, "Low": 0}


def test_findings_inline_style_without_subheadings():
    text = "- **Blocker:** hardcoded credential in config.py\n- [High] missing auth check\n"
    parsed = report_parser.parse_report(text)
    assert parsed["findings"]["Blocker"] == ["hardcoded credential in config.py"]
    assert parsed["findings"]["High"] == ["missing auth check"]


def test_commit_hash_extraction_labeled_short():
    parsed = report_parser.parse_report("Commit hash: a1b2c3d4e5f6\n")
    assert parsed["commit_hash"] == "a1b2c3d4e5f6"


def test_commit_hash_extraction_full_sha():
    sha = "abcdef1234567890abcdef1234567890abcdef12"
    parsed = report_parser.parse_report(f"Commit: {sha}\n")
    assert parsed["commit_hash"] == sha


def test_commit_hash_not_invented_when_absent():
    parsed = report_parser.parse_report("No commit information here, just prose about 2024 numbers.")
    assert parsed["commit_hash"] is None


def test_files_modified_extraction():
    parsed = report_parser.parse_report(NOT_APPROVED_REPORT)
    assert parsed["files_modified"] == ["auth/db.py"]


def test_branch_and_pull_request_extraction():
    text = "Branch: feature/example\nPull Request: https://github.com/org/repo/pull/42\n"
    parsed = report_parser.parse_report(text)
    assert parsed["branch"] == "feature/example"
    assert parsed["pull_request_url"] == "https://github.com/org/repo/pull/42"


def test_empty_report_returns_full_shape_all_unknown():
    parsed = report_parser.parse_report("")
    assert parsed["verdict"] is None
    assert parsed["findings"] == {severity: [] for severity in models.SEVERITIES}
    assert parsed["confidence"] == "none"
    assert parsed["commit_hash"] is None


def test_confidence_increases_with_populated_fields():
    sparse = report_parser.parse_report("Verdict: READY FOR COMMIT\n")
    rich = report_parser.parse_report(NOT_APPROVED_REPORT)
    order = {"none": 0, "low": 1, "medium": 2, "high": 3}
    assert order[rich["confidence"]] > order[sparse["confidence"]]


def test_manual_correction_overlay_does_not_mutate_original():
    parsed = report_parser.parse_report(APPROVED_REPORT)
    corrected = report_parser.set_manual_correction(parsed, "commit_hash", "deadbeef")
    assert parsed["manual_corrections"] == {}
    assert corrected["manual_corrections"] == {"commit_hash": "deadbeef"}
    effective = report_parser.apply_manual_corrections(corrected)
    assert effective["commit_hash"] == "deadbeef"


def test_set_manual_correction_rejects_uncorrectable_field():
    parsed = report_parser.empty_parsed_result()
    with pytest.raises(ValueError):
        report_parser.set_manual_correction(parsed, "findings", "x")


def test_original_report_text_is_never_modified_by_parsing():
    original = NOT_APPROVED_REPORT
    report_parser.parse_report(original)
    assert original == NOT_APPROVED_REPORT


# --------------------------------------------------------------------------
# F-03: contradictory verdicts (independent-review remediation)
# --------------------------------------------------------------------------


def test_not_approved_substring_is_not_flagged_contradictory():
    """A single 'NOT APPROVED FOR COMMIT' occurrence must not register as both
    NOT_APPROVED_FOR_COMMIT and APPROVED_FOR_COMMIT — regression guard for the
    masking approach in _find_all_verdicts."""
    parsed = report_parser.parse_report(NOT_APPROVED_REPORT)
    assert parsed["verdict"] == models.VERDICT_NOT_APPROVED_FOR_COMMIT
    assert parsed["verdict_contradictory"] is False


def test_not_ready_substring_is_not_flagged_contradictory():
    parsed = report_parser.parse_report(NOT_READY_FOR_FINAL_REVIEW_REPORT)
    assert parsed["verdict"] == models.VERDICT_NOT_READY_FOR_FINAL_REVIEW
    assert parsed["verdict_contradictory"] is False


def test_genuinely_contradictory_report_flagged_and_resolves_conservatively():
    text = "Verdict: FAILED\nVerdict: APPROVED FOR COMMIT\n"
    parsed = report_parser.parse_report(text)
    assert parsed["verdict_contradictory"] is True
    # FAILED is more conservative than APPROVED_FOR_COMMIT in the required order.
    assert parsed["verdict"] == models.VERDICT_FAILED


def test_contradictory_not_approved_and_approved_both_present_picks_not_approved():
    text = "APPROVED FOR COMMIT stated first.\nOn reflection: NOT APPROVED FOR COMMIT.\n"
    parsed = report_parser.parse_report(text)
    assert parsed["verdict_contradictory"] is True
    assert parsed["verdict"] == models.VERDICT_NOT_APPROVED_FOR_COMMIT


def test_contradictory_verdict_caps_confidence_at_low():
    # Both phrases must fall within the same scanned scope (here: no dedicated
    # "Verdict" heading, so the whole-text fallback scans everything) — a stray
    # mention of the other phrase outside the verdict section is correctly *not*
    # treated as contradictory (see test_not_approved_substring_is_not_flagged_
    # contradictory), matching the section-scoped-then-fallback design.
    text = "Verdict: NOT APPROVED FOR COMMIT\nVerdict: APPROVED FOR COMMIT\n"
    parsed = report_parser.parse_report(text)
    assert parsed["verdict_contradictory"] is True
    assert parsed["confidence"] == "low"


def test_conservative_order_matches_required_sequence():
    expected = [
        models.VERDICT_NOT_APPROVED_FOR_COMMIT,
        models.VERDICT_FAILED,
        models.VERDICT_NOT_READY_FOR_FINAL_REVIEW,
        models.VERDICT_READY_FOR_FINAL_REVIEW,
        models.VERDICT_READY_FOR_COMMIT,
        models.VERDICT_APPROVED_FOR_COMMIT,
    ]
    assert report_parser._CONSERVATIVE_VERDICT_ORDER == expected


def test_non_contradictory_report_unaffected_by_f03_change():
    parsed = report_parser.parse_report(APPROVED_REPORT)
    assert parsed["verdict"] == models.VERDICT_APPROVED_FOR_COMMIT
    assert parsed["verdict_contradictory"] is False


# --------------------------------------------------------------------------
# F-04: commit-hash sanity check applies to the labeled regex too
# --------------------------------------------------------------------------


def test_labeled_all_digit_commit_hash_rejected():
    text = "Commit: 1111111111111111111111111111111111111111\n"
    parsed = report_parser.parse_report(text)
    assert parsed["commit_hash"] is None


def test_labeled_real_looking_commit_hash_still_accepted():
    parsed = report_parser.parse_report("Commit hash: a1b2c3d4e5f6\n")
    assert parsed["commit_hash"] == "a1b2c3d4e5f6"
