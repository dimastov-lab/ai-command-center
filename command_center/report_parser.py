"""Deterministic extraction of structured fields from agent-report Markdown/text.

No LLM call, no guessing: every field is either matched by an explicit heading/label
pattern or a well-known verdict phrase, or it is left `None`/empty and reported as
"not provided" by the UI. The original report text is never modified or truncated by
this module — callers persist it separately (see `agent_runner`), and this module only
produces a derived, correctable side record.

Recognized verdict phrases (checked most-specific/negative-first so e.g. "NOT APPROVED
FOR COMMIT" is never misread as "APPROVED FOR COMMIT"):
APPROVED FOR COMMIT, NOT APPROVED FOR COMMIT, READY FOR FINAL REVIEW,
NOT READY FOR FINAL REVIEW, READY FOR COMMIT, FAILED.
"""

from __future__ import annotations

import re

from command_center import models

# --------------------------------------------------------------------------
# Section splitting
# --------------------------------------------------------------------------

_HEADING_PATTERNS = [
    re.compile(r"^#{1,6}\s+(.*)$"),
    # Numbered report sections, e.g. the "Required Final Report" template's
    # "1. Verdict or result" / "2. Scope inspected" / ... items.
    re.compile(r"^\d{1,2}\.\s+([A-Za-z][^\n]{0,78})$"),
    # A bold label alone on its own line, e.g. "**Verdict**"
    re.compile(r"^\*\*([^*\n]{1,78})\*\*:?\s*$"),
]


def _split_sections(text: str) -> list[tuple[str, str]]:
    sections: list[list] = [["", []]]
    for line in text.splitlines():
        stripped = line.strip()
        heading_text = None
        for pattern in _HEADING_PATTERNS:
            match = pattern.match(stripped)
            if match:
                heading_text = match.group(1).strip()
                break
        if heading_text is not None:
            sections.append([heading_text.lower(), []])
        else:
            sections[-1][1].append(line)
    return [(heading, "\n".join(body).strip()) for heading, body in sections]


def _find_section_body(sections: list[tuple[str, str]], *patterns: str) -> str | None:
    compiled = [re.compile(pattern, re.I) for pattern in patterns]
    for heading, body in sections:
        if not heading:
            continue
        for pattern in compiled:
            if pattern.search(heading):
                return body.strip() or None
    return None


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------

# Order matters for detection: negative ("NOT ...") variants must be checked (and
# masked, see _find_all_verdicts) before the positive phrase they contain as a
# substring, so a single "NOT APPROVED FOR COMMIT" is never double-counted as an
# independent "APPROVED FOR COMMIT" occurrence.
_VERDICT_PATTERNS: list[tuple[str, re.Pattern]] = [
    (models.VERDICT_NOT_APPROVED_FOR_COMMIT, re.compile(r"\bNOT\s+APPROVED\s+FOR\s+COMMIT\b", re.I)),
    (models.VERDICT_APPROVED_FOR_COMMIT, re.compile(r"\bAPPROVED\s+FOR\s+COMMIT\b", re.I)),
    (models.VERDICT_NOT_READY_FOR_FINAL_REVIEW, re.compile(r"\bNOT\s+READY\s+FOR\s+FINAL\s+REVIEW\b", re.I)),
    (models.VERDICT_READY_FOR_FINAL_REVIEW, re.compile(r"\bREADY\s+FOR\s+FINAL\s+REVIEW\b", re.I)),
    (models.VERDICT_READY_FOR_COMMIT, re.compile(r"\bREADY\s+FOR\s+COMMIT\b", re.I)),
    (models.VERDICT_FAILED, re.compile(r"\bFAILED\b", re.I)),
]

# Order matters for *resolution*: when a report contains more than one distinct
# verdict phrase (a genuinely contradictory/self-contradicting report, not the
# NOT-X-vs-X substring case above, which _find_all_verdicts already resolves before
# this list is consulted), the most conservative/restrictive reading wins rather than
# whichever pattern happens to be checked first or appear first in the text.
_CONSERVATIVE_VERDICT_ORDER: list[str] = [
    models.VERDICT_NOT_APPROVED_FOR_COMMIT,
    models.VERDICT_FAILED,
    models.VERDICT_NOT_READY_FOR_FINAL_REVIEW,
    models.VERDICT_READY_FOR_FINAL_REVIEW,
    models.VERDICT_READY_FOR_COMMIT,
    models.VERDICT_APPROVED_FOR_COMMIT,
]


def _find_all_verdicts(scope_text: str) -> dict[str, str]:
    """Return {verdict: evidence} for every *distinct* verdict phrase in scope_text.

    Each match's span is masked out (replaced with spaces) before the next pattern is
    checked, so a single "NOT APPROVED FOR COMMIT" occurrence can never also register
    as an independent "APPROVED FOR COMMIT" — masking depends on `_VERDICT_PATTERNS`
    checking negative variants first, same as the old single-verdict resolution did.
    """
    working_text = scope_text
    found: dict[str, str] = {}
    for verdict, pattern in _VERDICT_PATTERNS:
        match = pattern.search(working_text)
        if not match:
            continue
        start = max(0, match.start() - 40)
        end = min(len(working_text), match.end() + 40)
        found[verdict] = working_text[start:end].strip().replace("\n", " ")
        working_text = working_text[: match.start()] + (" " * (match.end() - match.start())) + working_text[match.end() :]
    return found


def _resolve_verdict(scope_text: str) -> tuple[str | None, str | None, bool]:
    """Returns (verdict, evidence, contradictory).

    `contradictory=True` means more than one distinct verdict phrase was found in the
    same scope; the most conservative one (per `_CONSERVATIVE_VERDICT_ORDER`) is
    returned, but callers must treat this as needing manual confirmation, not as a
    confident automatic result — see `workflow.suggest_next_task`.
    """
    all_found = _find_all_verdicts(scope_text)
    if not all_found:
        return None, None, False
    if len(all_found) == 1:
        verdict, evidence = next(iter(all_found.items()))
        return verdict, evidence, False
    for candidate in _CONSERVATIVE_VERDICT_ORDER:
        if candidate in all_found:
            return candidate, all_found[candidate], True
    return None, None, False  # unreachable: all_found keys are always from _VERDICT_PATTERNS


# --------------------------------------------------------------------------
# Findings by severity
# --------------------------------------------------------------------------

_FINDING_LINE_PATTERNS = [
    # Both "**Blocker:** text" and "**Blocker**: text" — real reports use either.
    re.compile(r"^[-*]\s*\*\*(Blocker|High|Medium|Low):?\*\*:?\s*(.+)$", re.I),
    re.compile(r"^[-*]\s*\[(Blocker|High|Medium|Low)\]\s*(.+)$", re.I),
    re.compile(r"^[-*]\s*(Blocker|High|Medium|Low)\s*:\s*(.+)$", re.I),
    re.compile(r"^(Blocker|High|Medium|Low)\s*:\s*(.+)$", re.I),
    re.compile(r"^\|\s*(Blocker|High|Medium|Low)\s*\|\s*(.+?)\s*\|", re.I),
]


def _strip_severity_prefix(item: str, severity: str) -> str:
    pattern = re.compile(rf"^(?:\*\*{severity}:?\*\*|\[{severity}\]|{severity})\s*:?\s*", re.I)
    return pattern.sub("", item).strip()


def _extract_findings(text: str) -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {severity: [] for severity in models.SEVERITIES}
    severity_by_lower = {severity.lower(): severity for severity in models.SEVERITIES}

    for heading, body in _split_sections(text):
        severity = severity_by_lower.get(heading.strip().lower())
        if not severity:
            continue
        for raw_line in body.splitlines():
            line = raw_line.strip()
            bullet_match = re.match(r"^[-*]\s+(.+)$", line)
            if bullet_match:
                item = _strip_severity_prefix(bullet_match.group(1).strip(), severity)
                if item and item not in findings[severity]:
                    findings[severity].append(item)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for pattern in _FINDING_LINE_PATTERNS:
            match = pattern.match(line)
            if match:
                severity = match.group(1).strip().capitalize()
                if severity in findings:
                    description = match.group(2).strip().rstrip("|").strip()
                    if description and description not in findings[severity]:
                        findings[severity].append(description)
                break

    return findings


def severity_counts(parsed: dict | None) -> dict[str, int]:
    if not parsed:
        return {severity: 0 for severity in models.SEVERITIES}
    findings = parsed.get("findings") or {}
    return {severity: len(findings.get(severity, [])) for severity in models.SEVERITIES}


# --------------------------------------------------------------------------
# Files modified/created/deleted
# --------------------------------------------------------------------------


def _extract_bulleted_or_csv(body: str | None) -> list[str]:
    if not body:
        return []
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    bullet_lines = [line for line in lines if re.match(r"^[-*]\s+", line)]
    if bullet_lines:
        items = []
        for line in bullet_lines:
            item = re.sub(r"^[-*]\s+", "", line).strip().strip("`")
            if item and item.lower() not in ("none", "н/д", "—", "-"):
                items.append(item)
        return items
    if len(lines) == 1 and "," in lines[0]:
        return [part.strip().strip("`") for part in lines[0].split(",") if part.strip()]
    return []


# --------------------------------------------------------------------------
# Single-line labeled fields
# --------------------------------------------------------------------------

_COMMIT_HASH_LABEL_RE = re.compile(r"(?im)^\**commit(?:\s*hash)?\**\s*:\s*`?([0-9a-fA-F]{7,40})`?")
_LOOSE_HASH_RE = re.compile(r"\b([0-9a-f]{7,40})\b")
_COMMIT_MESSAGE_RE = re.compile(r"(?im)^\**commit message\**\s*:\s*(.+)$")
_BRANCH_RE = re.compile(r"(?im)^\**branch\**\s*:\s*`?([^\n`]+?)`?\s*$")
_REMOTE_RE = re.compile(r"(?im)^\**remote\**\s*:\s*`?([^\n`]+?)`?\s*$")
_PR_LABEL_RE = re.compile(r"(?im)^\**(?:pull request|pr)\**\s*:\s*(\S+)")
_PR_URL_FALLBACK_RE = re.compile(r"https?://\S*(?:pull|pulls|merge_requests)\S*", re.I)

_NOT_PROVIDED_VALUES = {"n/a", "none", "—", "-", "нет", "unknown"}


def _looks_like_commit_hash(token: str) -> bool:
    """A real git SHA is effectively random hex — reject the degenerate all-digit
    case (e.g. a string of the same digit repeated), which is never a real hash but
    would otherwise match the `[0-9a-fA-F]` character class. Applied uniformly to
    both the labeled and the loose fallback match, so a labeled-but-implausible
    value (e.g. "Commit: 1111111111111111111111111111111111111111") is not accepted
    just because it had a label."""
    return bool(token) and 7 <= len(token) <= 40 and any(char in "abcdef" for char in token.lower())


def _extract_commit_hash(text: str) -> tuple[str | None, str | None]:
    match = _COMMIT_HASH_LABEL_RE.search(text)
    if match and _looks_like_commit_hash(match.group(1)):
        return match.group(1), match.group(0).strip()
    for match in _LOOSE_HASH_RE.finditer(text):
        token = match.group(1)
        if _looks_like_commit_hash(token):
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 10)
            return token, text[start:end].strip().replace("\n", " ")
    return None, None


def _extract_single_line(pattern: re.Pattern, text: str) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    value = match.group(1).strip()
    if not value or value.lower() in _NOT_PROVIDED_VALUES:
        return None
    return value


def _extract_pr_url(text: str) -> str | None:
    value = _extract_single_line(_PR_LABEL_RE, text)
    if value:
        return value.rstrip(".,)")
    match = _PR_URL_FALLBACK_RE.search(text)
    if match:
        return match.group(0).rstrip(".,)")
    return None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

CORRECTABLE_FIELDS: list[str] = [
    "verdict",
    "commit_hash",
    "commit_message",
    "branch",
    "remote",
    "pull_request_url",
    "recommended_next_action",
]


def empty_parsed_result() -> dict:
    return {
        "verdict": None,
        "verdict_evidence": None,
        "verdict_contradictory": False,
        "findings": {severity: [] for severity in models.SEVERITIES},
        "files_modified": [],
        "files_created": [],
        "files_deleted": [],
        "commit_hash": None,
        "commit_message": None,
        "branch": None,
        "remote": None,
        "pull_request_url": None,
        "validation_result": None,
        "git_status": None,
        "recommended_next_action": None,
        "evidence": {},
        "confidence": "none",
        "manual_corrections": {},
    }


def _confidence(result: dict) -> str:
    populated = 0
    if result.get("verdict"):
        populated += 1
    if any(result["findings"].values()):
        populated += 1
    if result.get("files_modified") or result.get("files_created") or result.get("files_deleted"):
        populated += 1
    if result.get("commit_hash"):
        populated += 1
    if result.get("branch"):
        populated += 1
    if result.get("validation_result"):
        populated += 1
    if result.get("git_status"):
        populated += 1
    if result.get("recommended_next_action"):
        populated += 1
    if result.get("verdict_contradictory"):
        # A report asserting more than one distinct verdict is inherently suspect,
        # regardless of how many other fields were successfully extracted.
        return "low" if populated >= 1 else "none"
    if populated >= 5:
        return "high"
    if populated >= 2:
        return "medium"
    if populated >= 1:
        return "low"
    return "none"


def parse_report(text: str) -> dict:
    """Deterministically extract structured fields from an agent report.

    Always returns the full `empty_parsed_result()` shape; unmatched fields stay
    `None`/empty rather than being omitted, so the UI can render "not provided"
    consistently.
    """
    result = empty_parsed_result()
    if not text or not text.strip():
        return result

    sections = _split_sections(text)

    verdict_body = _find_section_body(sections, r"verdict|result\b")
    verdict, evidence, contradictory = _resolve_verdict(verdict_body) if verdict_body else (None, None, False)
    if verdict is None:
        verdict, evidence, contradictory = _resolve_verdict(text)
    result["verdict"] = verdict
    result["verdict_evidence"] = evidence
    result["verdict_contradictory"] = contradictory

    result["findings"] = _extract_findings(text)

    modified_body = _find_section_body(sections, r"files?\s*modified", r"files?\s*changed", r"files?\s*reviewed")
    created_body = _find_section_body(sections, r"files?\s*created", r"files?\s*added")
    deleted_body = _find_section_body(sections, r"files?\s*deleted", r"files?\s*removed")
    result["files_modified"] = _extract_bulleted_or_csv(modified_body)
    result["files_created"] = _extract_bulleted_or_csv(created_body)
    result["files_deleted"] = _extract_bulleted_or_csv(deleted_body)

    commit_hash, commit_hash_evidence = _extract_commit_hash(text)
    result["commit_hash"] = commit_hash
    result["commit_message"] = _extract_single_line(_COMMIT_MESSAGE_RE, text)
    result["branch"] = _extract_single_line(_BRANCH_RE, text)
    result["remote"] = _extract_single_line(_REMOTE_RE, text)
    result["pull_request_url"] = _extract_pr_url(text)

    validation_body = _find_section_body(sections, r"validation")
    result["validation_result"] = validation_body

    git_status_body = _find_section_body(sections, r"git status")
    result["git_status"] = _strip_code_fences(git_status_body) if git_status_body else None

    next_action_body = _find_section_body(sections, r"recommend", r"next action")
    result["recommended_next_action"] = next_action_body

    result["evidence"] = {
        "verdict": evidence,
        "commit_hash": commit_hash_evidence,
    }
    result["confidence"] = _confidence(result)
    return result


def apply_manual_corrections(parsed: dict) -> dict:
    """Return a view of `parsed` with any manual corrections overlaid.

    Never mutates `parsed`: the original deterministic extraction and the manual
    corrections both remain available in the stored run record for audit purposes.
    """
    effective = dict(parsed)
    corrections = parsed.get("manual_corrections") or {}
    for field, value in corrections.items():
        if field in CORRECTABLE_FIELDS:
            effective[field] = value
    return effective


def set_manual_correction(parsed: dict, field: str, value: str) -> dict:
    if field not in CORRECTABLE_FIELDS:
        raise ValueError(f"Field is not correctable: {field}")
    updated = dict(parsed)
    corrections = dict(parsed.get("manual_corrections") or {})
    corrections[field] = value
    updated["manual_corrections"] = corrections
    return updated
