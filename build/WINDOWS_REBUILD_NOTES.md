# Windows Rebuild Notes — Installer Diet (PyInstaller spec fix)

Branch: `fnfat-remi/installer-diet` (off `ui/welcome-subtle-guild-import` @ ef6dfd9).
Changed file: `build/sparkybot.spec` (+ this doc, + README-BUILD.md pointer).
`build/build_windows.bat` and `build/sparkybot_updater.spec` are unchanged
(the updater is a deliberate one-file stdlib-only helper; embedding its
binaries in its exe is correct).

## What was wrong (audit findings)

1. **Qt duplicated.** The old spec passed `a.binaries`/`a.datas` positionally
   to `EXE(...)` AND again to `COLLECT(...)`. That embedded a full copy of
   every collected DLL inside `SparkyBot.exe` (audit saw opengl32sw.dll
   20.6 MB, avcodec-61 14 MB, Qt6Core 10.4 MB, Qt6Quick 6.6 MB, Qt6Qml
   5.3 MB, Qt6Pdf 4.6 MB inside the exe) while the same DLLs shipped again
   in `_internal/PySide6/`. Real app code (PYZ) is only 5.6 MB.
2. **Unneeded Qt payload.** QML/Quick/Pdf are never imported (Widgets-only
   app), and the software-OpenGL fallback plus the Direct2D platform plugin
   are dead weight.

## What the spec now does

- `EXE(pyz, a.scripts, exclude_binaries=True, ...)` — exe is a thin
  bootloader; every binary lives only in `_internal/` via `COLLECT`.
- `Analysis.excludes` adds: `PySide6.QtQuick`, `PySide6.QtQml`,
  `PySide6.QtQmlModels`, `PySide6.QtPdf`.
- Post-Analysis `a.binaries` filter drops: `opengl32sw.dll`, `qdirect2d`
  (loose DLLs collected by the PySide6 binary hooks regardless of module
  excludes).
- **QtMultimedia verdict: KEEP.** `core/tts.py:32` imports
  `QAudioOutput, QMediaPlayer` from `PySide6.QtMultimedia`, and
  `core/setup_wizard.py:2361` does the same for the TTS test button.
  `QtMultimedia`, `QtMultimediaWidgets`, `QtTextToSpeech` hiddenimports
  stay, and the FFmpeg DLLs (`avcodec-61`, `avformat`, `avutil`,
  `swresample`, `swscale`) stay — QMediaPlayer's Windows media plugin
  loads them. Do NOT exclude them.

## How to build (Windows build box)

From the repo root, either:

```
build\build_windows.bat
```

or manually:

```
py -3.12 -m venv build\venv
build\venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm build\sparkybot.spec
pyinstaller --clean --noconfirm build\sparkybot_updater.spec
copy /Y dist\SparkyBotUpdater.exe dist\SparkyBot\SparkyBotUpdater.exe
xcopy GW2EI dist\SparkyBot\GW2EI\ /E /I /Q /H
```

## Sizes to expect

| Artifact | Before (audit) | Expected after |
|---|---|---|
| `dist\SparkyBot\SparkyBot.exe` | fat — contained the full Qt DLL set (≥60 MB of DLLs seen inside) + 5.6 MB PYZ | thin bootloader only: **~4–10 MB** (console=False, icon, no UPX) |
| `_internal\base_library.dat` | n/a (PYZ was inside exe) | ~5–6 MB (the real app code) |
| `_internal\PySide6\` DLLs | duplicated vs exe | single copy only |
| `opengl32sw.dll` (20.6 MB) | present | **gone** |
| `qdirect2d*.dll` platform plugin | present | **gone** |
| `Qt6Quick` (6.6 MB) / `Qt6Qml` (5.3 MB) / `Qt6Pdf` (4.6 MB) | present | **gone** (~16.5 MB saved in `_internal` too) |
| `avcodec-61` (14 MB) / `avformat` / `avutil` FFmpeg DLLs | present | **still present** (required by QtMultimedia) |

Rule of thumb: `SparkyBot.exe` must no longer be the largest file in
`dist\SparkyBot\`; the whole dist folder should shrink by at least the
size of the duplicated DLL set plus ~40 MB of removed dead DLLs. If
`SparkyBot.exe` is still tens of MB, the fix did not take — check the
`exclude_binaries=True` kwarg.

## Post-build verification (build box, before smoke checklist)

```
dir dist\SparkyBot\SparkyBot.exe                       REM expect single-digit MB
dir /s /b dist\SparkyBot\_internal | findstr /i "opengl32sw qdirect2d Qt6Pdf Qt6Quick Qt6Qml"   REM expect NO hits
dir /s /b dist\SparkyBot\_internal | findstr /i "avcodec avformat avutil windowsmediaplugin qtaudio_windows" REM expect hits
```

Then run the full smoke checklist in `README-BUILD.md` — the TTS item
(Settings → TTS → Test) is the critical one for this change.

## Revert candidates if something regresses

1. **TTS plays no audio:** confirm `_internal\PySide6\plugins\multimedia\windowsmediaplugin.dll`
   and `_internal\PySide6\plugins\audio\qtaudio_windows.dll` exist (see
   README-BUILD.md "Qt multimedia plugins"). If a FFmpeg DLL is missing,
   the `a.binaries` filter is too aggressive — loosen `_DEAD_BINARIES` first.
2. **App won't launch / blank window:** temporarily drop `opengl32sw.dll`
   and `qdirect2d` from `_DEAD_BINARIES` and rebuild to bisect; then drop
   the four `PySide6.*` excludes to bisect the module list. Report the
   culprit DLL instead of shipping it back in.

— fnfat-remi (opencode), ticket 5649acaa-55b3-4bf5-9c2c-77f49f248df6
