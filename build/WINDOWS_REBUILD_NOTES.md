# Windows rebuild invariants

These checks describe the bytes that must ship, not merely the Python modules
that PyInstaller says it excluded.

## Frozen layout

- SparkyBot is a one-directory PyInstaller app.
- The v2.2.1 fat main-executable topology remains in place for v2.2.2. It is
  larger than a thin bootloader, but changing topology is separate from code
  signing and must not be described as a SmartScreen fix.
- UPX is disabled in both specs.
- Dependencies are exact-pinned in `requirements-windows.lock`.
- `SparkyBotUpdater.exe` sits beside `SparkyBot.exe`.
- GW2EI and user/runtime state never ship.

## Expected absent DLLs

The `a.binaries` filter must remove these exact basenames from the final ZIP:

```text
opengl32sw.dll
qdirect2d.dll
Qt6Pdf.dll
Qt6Quick.dll
Qt6Qml.dll
Qt6QmlMeta.dll
Qt6QmlModels.dll
Qt6QmlWorkerScript.dll
```

v2.2.0 and v2.2.1 incorrectly shipped the six Qt DLLs despite older notes
claiming the check was green. They consumed about 16.9 MB uncompressed.

## Expected present runtime

At minimum the verified ZIP must contain:

```text
SparkyBot/SparkyBot.exe
SparkyBot/SparkyBotUpdater.exe
SparkyBot/_internal/assets/theme_dark.qss
SparkyBot/_internal/assets/sbtray.ico
SparkyBot/_internal/assets/sbtray.png
SparkyBot/_internal/prompts/*
SparkyBot/_internal/PySide6/avcodec-*.dll
SparkyBot/_internal/PySide6/plugins/multimedia/windowsmediaplugin.dll
```

`qtaudio_windows.dll` is not in the known-good v2.2.1 PySide6 6.11.2 bundle;
older notes incorrectly listed it as required. Do not add a requirement that
the real runtime does not satisfy.

## Mechanical verification

The release driver first rejects any tracked or untracked file in the tagged
checkout before generating build output:

```bat
py -3.12 build\verify_release.py ^
  --repo-root . --require-tag --repo-only
```

After building, it allows the new untracked `build/` and `dist/` files but
still rejects any tracked-source change:

```bat
build\venv\Scripts\python.exe build\verify_release.py ^
  --repo-root . --require-tag --allow-untracked ^
  --archive dist\release\SparkyBot-vX.Y.Z.zip ^
  --manifest dist\release\SparkyBot-vX.Y.Z.manifest.json
```

The verifier reads and hashes the ZIP itself. It checks version agreement,
single `SparkyBot/` root, forward-slash and sorted entries, required runtime,
forbidden DLLs/state, CRCs, per-file hashes, and archive SHA-256. It is not
valid to point it at `dist\SparkyBot` instead.

If a previous manifest is available, pass `--previous-manifest` and
`--diff-output`. Review and explain every added, removed, or changed file in
the build notes.
