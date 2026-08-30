# Setup: Find fight logs on this computer

A setup-wizard screen. SparkyBot watches the folder where ArcDPS (the game add-on that records your fights) saves WvW fight logs. This screen finds that folder for you.

## What's on this screen

### The detected locations

At the top, SparkyBot shows what it found:

- If it found an ArcDPS setup, it lists where Guild Wars 2 is installed, where ArcDPS is, and the exact folder ArcDPS says WvW fight logs go. Click **Yes — Use This ArcDPS Setup** to accept it.
- If ArcDPS settings weren't found, it shows the standard location instead (inside your Documents folder, under `Guild Wars 2\addons\arcdps\arcdps.cbtlogs`). Click **Yes — Use This Folder** to accept it. SparkyBot selects the WvW subfolder automatically.
- **More than one ArcDPS setup was found** — if you have several game installs, a drop-down lists each one; pick the setup you actually play on, then accept it.

### If the guess is wrong

- **ArcDPS Is Somewhere Else...** — point SparkyBot at your `arcdps.ini` settings file; it reads the log location from there.
- **Choose a Different Folder (advanced)** — type or **Browse...** to the fight-log folder yourself. Use this only when the detected locations are wrong.

## Common problems

- **"Base folder found but no WvW subfolder yet."** — ArcDPS hasn't recorded a WvW fight on this computer yet. Play one WvW fight with ArcDPS logging on, then come back (or browse to the folder manually).
- **"Default folder does not exist yet."** — ArcDPS isn't installed or isn't logging. Install ArcDPS, enable WvW logging, play a fight, then finish setup.
- **"ArcDPS points to this folder, but it is not available right now."** — the folder is on a drive that isn't connected (for example an external or network drive). Connect it, or choose a different folder.
- **"SparkyBot needs a real fight-log folder before setup can finish."** — the folder you entered doesn't exist. Play one WvW fight with logging enabled, use the recommended location again, or pick the folder by hand.
- **"That file is not readable ArcDPS settings. Choose arcdps.ini."** — when using **ArcDPS Is Somewhere Else...**, pick the file named `arcdps.ini`, which sits next to the game's `Gw2-64.exe`.
