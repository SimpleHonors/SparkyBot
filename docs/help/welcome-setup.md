# Setup: Welcome

The first screen of the setup wizard, titled **Set up SparkyBot**. It appears the first time you run SparkyBot and walks you through everything the app needs: where your fight logs live and which Discord channels get your reports.

Most people can simply click **Next** and follow the steps.

## What's on this screen

### The easy path

If you have nothing to import, the screen just says SparkyBot will walk you through setup step by step. Click **Next** to begin.

### "SparkyBot found … already set up" (only if another log tool is detected)

If you already use another fight-log tool on this computer (for example PlenBot), SparkyBot may find its settings and offer to reuse them:

- **Found [tool] — set me up from it** — one click reuses that tool's choices (log folder, parser, Discord destinations where available). Before anything is applied you see a preview and must approve it. The other tool's own files are never changed.
- If more than one tool is found, the button reads **Set me up from my log tools** and SparkyBot combines them. A small dialog titled **Combine Your Log-Tool Setups** asks one question: which tool you use most — that one wins if any settings disagree.
- **Choose a settings file instead** — pick a tool and its settings file by hand rather than using what was detected.

### Use a Guild Setup File...

Only relevant if a guild admin sent you a SparkyBot setup file (a small `.json` file). Most people won't have one.

- Click **Use a Guild Setup File...** and pick the file (the picker starts in your Downloads folder).
- A confirmation titled **Use This Guild Setup?** shows exactly which Discord channels the file will post to. Click **Set Up SparkyBot** to accept or **Cancel** to back out.
- Only use a setup file sent by a guild admin you trust — it controls where your reports are posted.

After a guild file loads, the wizard skips ahead and only checks the things that are unique to your computer: the parser and your fight-log folder.

### Advanced

A small **Advanced** button at the bottom (hidden while a detected tool offer is showing). It opens one extra option:

- **Choose an App and Its File** — for when you use a fight-report app SparkyBot did not find automatically. You pick the app and its settings file yourself.

## Common problems

- **"Setup File Not Used"** — the file could not be read or is not a SparkyBot guild setup file. Ask your guild admin to send a fresh one.
- **"Setup File Not Ready" / "Discord posting is turned off in this setup file"** — the admin exported the file with posting disabled. Ask them to create a new setup file with posting enabled.
- **The detected-tool offer doesn't appear** — that's normal if no supported tool is installed, or if its settings could not be read. Just click **Next** and set things up by hand; it only takes a few screens.
- **You imported the wrong thing** — nothing is final until you click Finish on the last page. You can also re-run the import from the main window later: **File → Use Guild Setup File...** or **File → Import from Another Log Tool...** (see [Guild setup files and other log tools](guild-setup-files.md)).
