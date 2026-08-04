# -*- mode: python ; coding: utf-8 -*-
"""Unsigned Apple Silicon development bundle for Desktop D4A."""

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
    argv_emulation=False,
    target_arch="arm64",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AI Command Center",
)
app = BUNDLE(
    coll,
    name="AI Command Center.app",
    icon=None,
    bundle_identifier="com.aicommandcenter.desktop",
    info_plist={
        "CFBundleDisplayName": "AI Command Center",
        "CFBundleName": "AI Command Center",
        "CFBundleShortVersionString": "0.1.0-dev",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
    },
)
