"""VOYN-W0-AICC-SRV-07: migration volume figures must read as estimates.

The «≈1.22 млн строк» figure, and the «бэкфилл ≈6.5 минут» window derived from
it, were extrapolated from a single table of a synthetic ~35 MB fixture. They
were never measured against the live database — the one snapshot taken of that
database found its domain tables empty. Neither file is in the repo or in CI,
so the numbers cannot be re-measured here. The only property that *can* be
checked here is the one whose absence caused the error: that the figures are
never restated as measurements.

Two tiers, because the cost of a false alarm differs between them:

* Anywhere in the repo's Markdown, the two specific hearsay figures must carry
  the word «экстраполяция» / "extrapolation" in the same paragraph. These exact
  numbers have exactly one origin, so demanding the label of them cannot
  mislabel somebody else's real measurement.
* Inside the migration documents specifically, *any* millions-of-rows volume
  claim must carry it. A future plan restating the estimate as «≈1.4 млн строк»
  would slip past a literal-only gate while making the identical mistake, and
  these are the documents SRV-07/SRV-09 read for the source volume.

A gate for a documentation property is worth only as much as its ability to
fail, so the checker is a pure function over text and the negative controls
below exercise it directly rather than trusting a green run over today's docs.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.architecture.aios_boundary import EXCLUDED_DIR_NAMES

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"

# The documents SRV-07/SRV-09 read for the source volume and the transfer plan.
MIGRATION_DOCS = (
    DOCS_DIR / "srv01b-schema-map.md",
    DOCS_DIR / "postgres-foundation.md",
)

# A paragraph that is talking about the transfer window at all. Required of the
# minutes figure and of nothing else: "6.5 minutes" is an ordinary duration that
# something in this repo may one day genuinely have measured, and a gate that
# demanded the word «экстраполяция» of that would be forcing a real measurement
# to describe itself as a guess — the mirror image of the error being fixed.
_TRANSFER_WINDOW = re.compile(r"бэкфилл|backfill|окно|window|перенос", re.IGNORECASE)

# The two unmeasured figures, in the spellings a plan would use: both decimal
# separators, and the ru/en unit words, because the docs are written in both.
# Each pairs with the context its paragraph must also carry to count, or None.
_HEARSAY_FIGURES = (
    (re.compile(r"1[.,]22\s*(?:млн|миллион|million|M\b)", re.IGNORECASE), None),
    (re.compile(r"6[.,]5\s*(?:мин|min)", re.IGNORECASE), _TRANSFER_WINDOW),
)

# Any claim of the shape "<N> million rows" — the mistake is the shape, not the
# particular number. A scale word is required, so a plain observed count
# ("137 строк") is not a volume claim; the digit-group alternative catches the
# same magnitude written out ("1 220 000 строк").
_ROW_VOLUME_CLAIM = re.compile(
    r"""(?: \d+(?:[.,]\d+)? \s* (?: млн | миллион\w* | млрд | million | billion | M\b )
        |   \d{1,3} (?: [\s  ,] \d{3} ){2,}
        )
        [^\n]{0,40}?
        (?: стро[кчн] | rows? )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_ESTIMATE_MARKERS = ("экстраполяц", "extrapolat")


def _paragraphs(text: str) -> list[str]:
    return [block for block in re.split(r"\n\s*\n", text) if block.strip()]


def _unlabelled(
    text: str,
    pattern: re.Pattern[str],
    context: re.Pattern[str] | None = None,
) -> list[str]:
    """Paragraphs matching ``pattern`` that do not own up to being estimates."""

    offenders = []
    for block in _paragraphs(text):
        if not pattern.search(block):
            continue
        if context is not None and not context.search(block):
            continue
        if any(marker in block.lower() for marker in _ESTIMATE_MARKERS):
            continue
        offenders.append(" ".join(block.split())[:160])
    return offenders


def _markdown_files() -> list[Path]:
    """Every Markdown file the repo actually authors.

    Vendored trees are skipped via the exclusion set the AIOS boundary gate
    already uses, rather than a second hand-maintained list: a checkout with a
    root `.venv` carries ~55 third-party README files, and this gate has no
    business ruling on somebody else's row counts.
    """

    return sorted(
        path
        for path in REPO_ROOT.rglob("*.md")
        if not any(
            part in EXCLUDED_DIR_NAMES
            or part.startswith((".venv", "venv"))
            or part == "site-packages"
            for part in path.relative_to(REPO_ROOT).parts
        )
    )


def test_the_hearsay_figures_are_never_restated_as_measurements():
    offenders: list[str] = []
    for path in _markdown_files():
        text = path.read_text(encoding="utf-8")
        for pattern, context in _HEARSAY_FIGURES:
            offenders += [
                f"{path.relative_to(REPO_ROOT)}: {block}"
                for block in _unlabelled(text, pattern, context)
            ]

    assert not offenders, (
        "the 1.22-million-rows / 6.5-minute figures were extrapolated from one "
        "table of a synthetic fixture, never measured against the live "
        "database; stating them without the word «экстраполяция» presents "
        "hearsay as a measurement: " + "; ".join(offenders)
    )


def test_migration_docs_label_every_volume_claim_as_an_estimate():
    offenders: list[str] = []
    for path in MIGRATION_DOCS:
        offenders += [
            f"{path.relative_to(REPO_ROOT)}: {block}"
            for block in _unlabelled(
                path.read_text(encoding="utf-8"), _ROW_VOLUME_CLAIM
            )
        ]

    assert not offenders, (
        "no row-count volume of the source has ever been measured, so a volume "
        "figure in the migration documents is an estimate and must say so: "
        + "; ".join(offenders)
    )


def test_schema_map_records_that_the_volume_was_never_measured():
    text = (DOCS_DIR / "srv01b-schema-map.md").read_text(encoding="utf-8")
    assert "## Объёмы" in text, (
        "docs/srv01b-schema-map.md must keep its volumes section: SRV-07/SRV-09 "
        "read this map for the source volume, and this is where the "
        "'extrapolated, never measured' correction is recorded"
    )
    for claim in ("экстраполяц", "синтетической фикстуры", "137 строк", "count(*)"):
        assert claim in text, (
            f"the volumes section lost a load-bearing claim: {claim!r}"
        )


def test_the_repo_wide_scan_covers_authored_docs_and_skips_vendored_ones():
    """Over-exclusion is the quiet failure: a gate scanning nothing still passes."""

    scanned = _markdown_files()
    assert DOCS_DIR / "srv01b-schema-map.md" in scanned
    assert REPO_ROOT / "CHANGELOG.md" in scanned
    assert not [
        path
        for path in scanned
        if {"site-packages", "node_modules"} & set(path.parts)
        or any(part.startswith((".venv", "venv")) for part in path.parts)
    ]


# --- negative controls: the gate has to be able to fail -------------------


def test_gate_flags_an_unlabelled_hearsay_figure():
    """The exact regression: the number pasted into a plan as a fact."""

    plan = "Объём переноса — ≈1.22 млн строк, окно бэкфилла ≈6.5 минут."
    assert _unlabelled(plan, *_HEARSAY_FIGURES[0])
    assert _unlabelled(plan, *_HEARSAY_FIGURES[1])
    assert _unlabelled(plan, _ROW_VOLUME_CLAIM)


def test_gate_flags_a_restatement_that_changes_the_number():
    """A literal-only gate would miss these; the same mistake is being made."""

    for restatement in (
        "Источник несёт около 1.4 млн строк.",
        "The source holds roughly 1 220 000 rows.",
        "Ожидаем 2 миллиона строк в run_event.",
    ):
        assert _unlabelled(restatement, _ROW_VOLUME_CLAIM), restatement


def test_gate_accepts_a_figure_that_owns_up_to_being_an_estimate():
    labelled = (
        "Оценка «≈1.22 млн строк» и выведенное из неё окно бэкфилла "
        "«≈6.5 минут» получены экстраполяцией с одной таблицы синтетической "
        "фикстуры."
    )
    assert not _unlabelled(labelled, *_HEARSAY_FIGURES[0])
    assert not _unlabelled(labelled, *_HEARSAY_FIGURES[1])
    assert not _unlabelled(labelled, _ROW_VOLUME_CLAIM)


def test_gate_does_not_mistake_an_observed_count_for_a_volume_claim():
    """`137 строк` is something somebody counted, not a projection."""

    assert not _unlabelled("В файле 137 строк, все таблицы пусты.", _ROW_VOLUME_CLAIM)
    assert not _unlabelled("Файл занимает ≈35 МБ на диске.", _ROW_VOLUME_CLAIM)


def test_gate_leaves_an_unrelated_duration_alone():
    """A real 6.5-minute measurement of something else is not this figure."""

    assert not _unlabelled("Прогон CI занимает 6.5 мин.", *_HEARSAY_FIGURES[1])
