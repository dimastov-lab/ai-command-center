"""Package-resource paths shared by source and frozen desktop builds."""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path


def resource(package_path: str = "") -> Traversable:
    """Return a traversable path inside the bundled ``command_center`` package."""
    root = files("command_center")
    return root.joinpath(package_path) if package_path else root


def resource_path(package_path: str = "") -> Path:
    """Return a filesystem path for an unpacked bundled resource."""
    return Path(str(resource(package_path)))
