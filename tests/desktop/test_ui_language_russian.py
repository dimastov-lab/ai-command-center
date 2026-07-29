"""Automated linguistic gate: every user-visible string in the shell is Russian.

The master requirement is a fully Russian UI. This walks the live widget tree of
the assembled shell and asserts each user-facing string (button/label text,
placeholders, tooltips, accessible names/descriptions, the window title) either
contains Cyrillic or is composed only of allowed proper nouns / abbreviations
(the product name and the conventional technical tokens that stay Latin, e.g. CI,
PR, SHA, API, URL, JSON, CPU, macOS). Object names (widget identifiers) are not
user-visible and are intentionally not checked.
"""

from __future__ import annotations

import re

from PySide6.QtWidgets import QWidget

# Latin tokens allowed to appear without Cyrillic (proper noun + conventional
# abbreviations kept Latin per the UI-language policy).
_ALLOWED_LATIN = {
    "AI", "Command", "Center",  # the product name "AI Command Center"
    "CI", "PR", "SHA", "API", "URL", "JSON", "CPU", "macOS", "QR", "PDF",
    "ID", "HTTP", "HTTPS", "Git", "GitHub",
    # Git domain terms kept in their conventional Latin form (like "Git"),
    # clearer to a developer operator than a literal translation.
    "worktree", "Worktree",
}

_LATIN_WORD = re.compile(r"[A-Za-z]+")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")

# The user-visible getters we audit on every widget. objectName() is excluded on
# purpose — it is an identifier, not shown to users.
_TEXT_GETTERS = (
    "windowTitle",
    "title",  # QGroupBox et al. — a visible title, not exposed via text()
    "text",
    "placeholderText",
    "toolTip",
    "accessibleName",
    "accessibleDescription",
    "whatsThis",
)


def _is_russian_or_allowed(value: str) -> bool:
    latin_words = _LATIN_WORD.findall(value)
    if not latin_words:
        return True  # pure Cyrillic / digits / punctuation
    if _CYRILLIC.search(value):
        # mixed: every Latin run must be an allowed token (e.g. "Проект в PR")
        return all(w in _ALLOWED_LATIN for w in latin_words)
    # no Cyrillic at all: allowed only if it is purely an allowed proper noun
    return all(w in _ALLOWED_LATIN for w in latin_words)


def _collect_user_visible_strings(root: QWidget) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    widgets = [root, *root.findChildren(QWidget)]
    for w in widgets:
        for getter in _TEXT_GETTERS:
            fn = getattr(w, getter, None)
            if not callable(fn):
                continue
            try:
                value = fn()
            except TypeError:
                continue
            if isinstance(value, str) and value.strip():
                seen.append((f"{type(w).__name__}.{getter}()", value))
    return seen


def test_all_shell_strings_are_russian(shell):
    offenders = [
        (where, value)
        for where, value in _collect_user_visible_strings(shell)
        if not _is_russian_or_allowed(value)
    ]
    assert not offenders, "Непереведённые (английские) строки в UI:\n" + "\n".join(
        f"  {where}: {value!r}" for where, value in offenders
    )
