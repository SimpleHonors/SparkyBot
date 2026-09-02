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
        (os.path.join(_repo_root, 'core', 'trait_evidence_catalog.json'), 'core'),
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
        # SparkyBot is a Widgets-only app: QML/Quick and Pdf are never
        # imported, but PySide6 hooks pull Qt6Quick/Qt6Qml/Qt6Pdf DLLs in
        # if the modules stay analyzable.  Exclude them explicitly.
        # Keep PySide6.QtMultimedia/QtMultimediaWidgets/QtTextToSpeech:
        # core/tts.py and core/setup_wizard.py play TTS audio via
        # QMediaPlayer/QAudioOutput (which also keeps the FFmpeg
        # avcodec/avformat/avutil DLLs legitimately required).
        'PySide6.QtQuick',
        'PySide6.QtQml',
        'PySide6.QtQmlModels',
        'PySide6.QtPdf',
    ],
    noarchive=False,
    optimize=0,
)

# Drop binaries that Qt's PySide6 hooks collect but that this app can never
# use. Module excludes above stop Python imports, but these loose DLLs can still
# be collected unconditionally, so strip them from the collected binary list.
# COLLECT reads a.binaries, so filtering here removes them from the bundle.
_DEAD_BINARIES = (
    'opengl32sw.dll',          # software OpenGL fallback; ANGLE/DX is used
    'qdirect2d.dll',           # unused Direct2D platform plugin
    'qt6pdf.dll',
    'qt6quick.dll',
    'qt6qml.dll',
    'qt6qmlmeta.dll',
    'qt6qmlmodels.dll',
    'qt6qmlworkerscript.dll',
)
a.binaries = [
    b for b in a.binaries
    if not any(d in str(part).lower() for part in b[:2] for d in _DEAD_BINARIES)
]

pyz = PYZ(a.pure)

# Onedir build: the EXE must be a thin bootloader only.  Passing a.binaries
# / a.datas positionally to EXE embeds a second copy of every DLL (full Qt
# set included) inside SparkyBot.exe while COLLECT ships them again in
# _internal/.  exclude_binaries=True keeps binaries solely in COLLECT's
# _internal/ output.
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name='SparkyBot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name='SparkyBot',
)
