# SparkyBot Windows Build Kit

How to build and distribute the SparkyBot `.exe` from this repository.

## Prerequisites (on the Windows build machine)

- Windows 10 or 11 (64-bit)
- Python 3.11 or newer on PATH (`py -3.12` preferred, `python` fallback)
- Internet access for `pip install`
- ~500 MB free disk space for the virtual environment and build artifacts

### Runtime dependency for end users (not the builder)

GW2EI (Guild Wars 2 Elite Insights Parser) is a .NET 8 application. Users must
have the **.NET 8 Desktop Runtime** installed on their PC to run GW2EI.
Alternatively, you can swap the `GW2EI/` folder in this repo with a
**self-contained** GW2EI build (which bundles the runtime), so users need no
separate .NET install. The self-contained build is provided on
[GW2EI's releases page](https://github.com/baaron4/GW2-Elite-Insights-Parser/releases).

## Build steps

1. Clone this repository on a Windows machine.
2. Double-click `build/build_windows.bat` **from the repo root**.
3. Wait for the build to complete (~2–5 minutes).
4. The compiled app is at `dist/SparkyBot/SparkyBot.exe`.

### What the batch file does

```
[1/3] Creates a Python venv at build/venv/
[2/3] Installs requirements.txt + PyInstaller into the venv
[3/3] Runs pyinstaller --clean --noconfirm build/sparkybot.spec
```

### Manual build (if you prefer the command line)

```
py -3.12 -m venv build\venv
build\venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm build\sparkybot.spec
```

## Output layout

```
dist/
  SparkyBot/
    SparkyBot.exe            <-- launch this
    _internal/               <-- bundled Python + deps + data
      core/
      prompts/
      assets/
      GW2EI/
      ...
```

The one-directory layout is deliberate (never `--onefile`). It starts faster,
generates fewer AV false positives, and lets users inspect or replace the
bundled GW2EI folder.

**GW2EI placement:** The `GW2EI/` folder is placed at `dist/SparkyBot/GW2EI/`
(next to `SparkyBot.exe`), not inside `_internal/`. This makes it writable so
the EI self-updater can replace files at runtime. The `gw2ei_dir()` helper in
`core/apppaths.py` resolves to this location when frozen.

## Installer

SparkyBot ships an Inno Setup 6 installer (`build/sparkybot.iss`) that produces
a single `SparkyBot-vX.Y.Z-setup.exe` for end users.

### Prerequisites for building the installer

- Inno Setup 6 installed on the Windows build machine.
  - `winget install --id=JRSoftware.InnoSetup -e` (recommended)
  - Or download `innosetup-6.*.exe` from https://jrsoftware.org/isdl.php and
    run with `/VERYSILENT`.

### Build command

From the repo root, after `build_windows.bat` has completed:

```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\sparkybot.iss
```

Output: `dist\SparkyBot-v2.0.0-setup.exe`

### Smoke checklist additions (installer)

- [ ] Run `SparkyBot-v2.0.0-setup.exe /VERYSILENT /DIR=C:\Temp\SparkyBotTest`
      — installs without UI to a temp directory.
- [ ] Run `C:\Temp\SparkyBotTest\SparkyBot.exe` — app launches.
- [ ] Uninstall via `"C:\Temp\SparkyBotTest\unins000.exe" /VERYSILENT` — temp
      directory is removed.

### Packaging for distribution

Zip the entire `dist/SparkyBot/` directory:

```
zip -r SparkyBot-vX.Y.Z.zip dist/SparkyBot/
```

Users extract the zip anywhere and run `SparkyBot.exe`. No installer or admin
rights needed.

**Important:** `config.properties` is created next to `SparkyBot.exe` on first
run (not inside `_internal/`). When zipping for distribution, do NOT include a
`config.properties` — let each user generate their own.

## Qt multimedia plugins

PySide6's QtMultimedia module (used for TTS audio playback) requires platform
media service plugins. These DLLs live under `_internal/PySide6/plugins/`

If audio playback fails on a target machine, confirm the following files exist
in the dist directory:

```
_internal/PySide6/plugins/multimedia/windowsmediaplugin.dll
_internal/PySide6/plugins/audio/qtaudio_windows.dll
```

## Smoke checklist

Run **every item** on a **clean machine** (no Python installed, no repo checkout)
after every build:

- [ ] Launch `SparkyBot.exe` — splash/setup wizard appears within ~3 seconds.
- [ ] The Settings window opens from the tray icon.
- [ ] GW2EI download works (Settings → Elite Insights → Download/Update).
- [ ] TTS test plays audio (Settings → TTS → Test).
- [ ] Drop a sample `.evtc` log into the watched folder — watcher picks it up
      and a fight report appears.
- [ ] Raid Report page generates without error.
- [ ] Close the app — tray icon disappears, process exits cleanly.
- [ ] Relaunch — `config.properties` is found and settings are preserved.

## Known issues

- **AV false positives:** PyInstaller executables (even one-directory builds)
  are occasionally flagged by antivirus software. This is a well-known
  industry-wide issue with Python-packaged executables, not specific to
  SparkyBot. A future code-signing certificate will eliminate most of these.
- **First-run delay:** The setup wizard downloads GW2EI on first launch
  (~50 MB). Release builds intentionally leave the writable `GW2EI/` folder
  empty so every new install fetches the current parser.
- **Qt Multimedia DLLs:** On some Windows editions the required media
  plugin DLLs may be stripped by PyInstaller. If TTS audio playback fails,
  check for the files listed in the "Qt multimedia plugins" section above.
