# Guild setup files and other log tools

The **File** menu can share SparkyBot's setup with guildmates and trade settings with neighboring WvW log tools. The **Help** menu's **Other WvW Log Tools & Credits...** entry opens a catalog of those tools.

## Guild setup files

A guild setup file is a small `.json` file containing only Discord destinations — which channels reports go to and the bot's display name. It never includes AI keys, voice settings, Twitch, or anything about your computer's files.

- **File → Create Guild Setup File...** — for guild admins. A warning titled **Create a Guild Setup File?** first shows exactly what will be shared; click **Create Setup File** to save it (default name suggested, in Documents). Share it only with guild members you trust — the file can post messages to your Discord channels.
- **File → Use Guild Setup File...** — for members. Pick the file an admin sent you; a confirmation titled **Use This Guild Setup?** lists the destinations that will replace your current ones. Click **Use Guild Setup** to apply. Nothing else on your computer is changed.

New members can also load the file on the setup wizard's [Welcome screen](welcome-setup.md).

## Other log tools

- **File → Import from Another Log Tool...** — reuse a neighboring tool's documented settings (log folder, parser path, Discord routes where available) without changing that tool's files. You see a preview and approve what's taken. Your existing SparkyBot AI, voice, and Twitch choices are preserved.
- **File → Create Setup for Another Log Tool...** — the reverse: writes a setup the other tool can use, reversibly.
- **Help → Other WvW Log Tools & Credits...** — the catalog of supported neighboring tools, with credits and links.

## Common problems

- **"Setup File Not Used"** — the file isn't a readable SparkyBot guild setup. Ask the admin for a fresh export.
- **"Discord posting is turned off in this setup file."** — the admin exported with posting disabled; they need to enable posting and export again.
- **"Settings Were Not Imported"** — the other tool's settings file couldn't be read or was missing the needed entries. Set the same values by hand in **Settings** instead.
- **Worried about what a file shares** — read the confirmation dialog; it lists everything, and both directions exclude secrets like AI keys.
