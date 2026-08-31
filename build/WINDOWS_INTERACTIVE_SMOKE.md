# Packaged Windows interactive smoke gate

Use a disposable, logged-in standard-user desktop. Test the exact archive read
back from the release share; never substitute the source tree or `dist` folder.
Use synthetic fixture paths/data only.

## 1. Identity and Windows trust

- Match the ZIP, manifest, and installer SHA-256 values to `SHA256SUMS` at
  build, share-write, and share-read locations.
- Confirm `Get-AuthenticodeSignature` is `Valid` for the app, updater, and
  installer before publication.
- Exercise an Internet-marked download. A source-widget screenshot or locally
  built file cannot test SmartScreen reputation.

## 2. First run

- Launch `SparkyBot.exe` from the extracted archive.
- Capture every wizard page from the actual packaged process.
- Exercise automatic setup, GW2EI opt-in install, valid/invalid WvW log paths,
  Discord skip, Twitch skip, AI opt-in/out, startup behavior, and Finish.
- Reject any stray `?`, overlap, clipped label, dead button, or 404 link.

## 3. Main application

- Capture Home, Fight Summary, Process Files, and Settings at the shipped
  default window size and maximized.
- Open every Settings category. Change a theme, save it, and confirm the live
  UI changes.
- Use a synthetic `.evtc` fixture to exercise the file picker and processing
  path; do not use real player data.
- Exit completely, relaunch, and confirm the wizard stays complete and saved
  settings persist.

## 4. Installer round-trip

- Install `SparkyBot-vX.Y.Z-Setup.exe` silently and confirm its installed app
  launches in the logged-in desktop.
- Confirm version, shortcuts, writable runtime state, and updater location.
- Uninstall silently and confirm the application files are removed without
  deleting unrelated user files.

## 5. Evidence verdict

Store screenshots, command results, hashes, and a `PASS`/`FAIL`/`SKIPPED`
checklist beside the candidate on the shared release path. State explicitly
`NOT USER-VERIFIED` until the operator exercises the real client path.
