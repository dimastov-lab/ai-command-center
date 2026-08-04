# -*- mode: python ; coding: utf-8 -*-
"""Unsigned Windows 11 x64 development build for Desktop D4B."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

SPEC_DIR = Path(SPEC).resolve().parent
ROOT = SPEC_DIR.parents[1]
datas = collect_data_files("command_center")

a = Analysis(
    [str(SPEC_DIR / "entrypoint.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["streamlit", "fastapi", "uvicorn", "flask"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI Command Center",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AI Command Center",
)
