"""``WorktreeRow`` — one worktree of an ``ok`` project (D2C, §4).

Shows path, branch, and short HEAD. The branch string is rendered exactly as the
snapshot provides it (from `git_info`), including any literal detached-HEAD
marker — the desktop page invents no alternative label, keeping a single source of
truth for that state. Missing keys degrade to empty strings rather than raising.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from .. import tokens


class WorktreeRow(QFrame):
    def __init__(self, worktree: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WorktreeRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            tokens.SPACE_MD, tokens.SPACE_XS, tokens.SPACE_MD, tokens.SPACE_XS
        )
        layout.setSpacing(tokens.SPACE_MD)

        self._path = QLabel(worktree.get("path", ""))
        self._path.setObjectName("WorktreePath")
        self._branch = QLabel(worktree.get("branch", ""))
        self._branch.setObjectName("WorktreeBranch")
        self._head = QLabel(worktree.get("head", ""))
        self._head.setObjectName("WorktreeHead")

        layout.addWidget(self._path)
        layout.addStretch(1)
        layout.addWidget(self._branch)
        layout.addWidget(self._head)

    def path_text(self) -> str:
        return self._path.text()

    def branch_text(self) -> str:
        return self._branch.text()

    def head_text(self) -> str:
        return self._head.text()
