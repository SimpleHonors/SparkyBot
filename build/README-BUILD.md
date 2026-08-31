# SparkyBot Windows release build

`build\release_windows.bat` is the only supported release path. It builds the
app and updater, runs the exact tagged source suite, creates a deterministic
ZIP plus SHA-256 manifest, builds the installer, verifies the archive, and
checks all three executables for Authenticode signatures.

## Prerequisites

- 64-bit CPython 3.12 (`py -3.12`); v2.2.2 uses Python 3.12.8.
- Inno Setup 6 with `iscc.exe` on `PATH`.
- Internet access for the first locked dependency install.
- A clean checkout whose `HEAD` is tagged `v<core/version.py VERSION>`.
- A code-signing certificate and signing step before a public release.

Do not install UPX. Both specs explicitly disable it so the same source and
lock file cannot silently produce different bytes on different build hosts.

## Build

From a Windows command prompt at the repository root:

```bat
build\release_windows.bat
```

The dependency environment is recreated from
`build\requirements-windows.lock`. The command fails if tests, tag/version
agreement, ZIP contents, installer construction, or signatures fail.

Outputs are written to `dist\release\`:

- `SparkyBot-vX.Y.Z.zip`
- `SparkyBot-vX.Y.Z.manifest.json`
- `SparkyBot-vX.Y.Z-Setup.exe`
- `SHA256SUMS`

`GW2EI` is downloaded by SparkyBot after the user opts in. It is never copied
into a release. Config files, logs, `.evtc`/`.zevtc` files, caches, and other
runtime state are also excluded.

## Internal unsigned candidate

Signing is the only durable fix for SmartScreen's "Unknown publisher" warning.
Changing PyInstaller's fat/thin layout does not establish a publisher identity.
The release driver therefore blocks unsigned publication by default.

For a clearly labelled internal test candidate only:

```bat
set SPARKYBOT_ALLOW_UNSIGNED_CANDIDATE=1
build\release_windows.bat
```

That override does not make the files safe to publish and does not count as a
SmartScreen pass.

## Required operator-layer smoke

After the scripted gate passes, follow
[`WINDOWS_INTERACTIVE_SMOKE.md`](WINDOWS_INTERACTIVE_SMOKE.md) using the exact
ZIP copied back from the release share. Source-driven/offscreen widget grabs,
unit tests, hashes, and a process-alive check are not substitutes for packaged
pixels in a logged-in Windows desktop.

The Setup installer must also complete a silent install/launch/uninstall
round-trip before publication:

```bat
SparkyBot-vX.Y.Z-Setup.exe /VERYSILENT /NORESTART /SUPPRESSMSGBOXES
```

Record every gate as `PASS`, `FAIL`, or `SKIPPED — reason` in the candidate's
build notes. A skipped operator-layer check means the candidate is not
user-verified.
