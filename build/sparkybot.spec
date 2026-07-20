# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for SparkyBot one-directory build.

Run from the repo root:
    pyinstaller --clean --noconfirm build/sparkybot.spec

Output: dist/SparkyBot/SparkyBot.exe
"""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

# SPEC resolves relative to build/ — anchor everything to the repo root.
_repo_root = os.path.abspath(os.path.join(SPECPATH, os.pardir))

a = Analysis(
    [os.path.join(_repo_root, 'bootstrap.py')],
    pathex=[],
    binaries=[],
    datas=[
        (os.path.join(_repo_root, 'prompts'), 'prompts'),
        (os.path.join(_repo_root, 'assets'), 'assets'),
        # GW2EI is NOT bundled in _internal.  It must be placed next to
        # SparkyBot.exe (dist/SparkyBot/GW2EI/) so it is writable for the
        # self-updater.  The build_windows.bat script copies it after the
        # PyInstaller run, and the gw2ei_dir() helper in core/apppaths.py
        # prefers that location when frozen.
    ],
    hiddenimports=[
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtTextToSpeech',
        'configparser',
    ] + collect_submodules('edge_tts'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'tests',
        'unittest',
        'test',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SparkyBot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(_repo_root, 'assets', 'sbtray.ico')],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SparkyBot',
)
