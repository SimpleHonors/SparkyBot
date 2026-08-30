# Settings: Application

SparkyBot's look, startup behavior, tray behavior, and the AI master switch.

## What's on this page

### Interface Theme

- **Theme** — the color scheme for SparkyBot's windows (Workbench Dark, JADE, Obsidian (AMOLED), Daylight, Midnight Blue, Mist Teal, Crimson, Sunset, Legendary Gold, Bubblegum). Changes apply instantly so you can preview, and are kept when you save.

### Startup & tray

- **Start with Windows** — launch SparkyBot when Windows starts. This is a Windows setting, applied when you click OK or Apply.
- **Start Minimized** — open hidden in the system tray instead of showing the window.
- **Start Watcher on Startup** — begin watching the log folder as soon as SparkyBot opens.
- **Minimize to System Tray** — the minimize button hides to the tray instead of the taskbar.
- **Close to System Tray** — the X button hides to the tray instead of quitting.
- **Hide Console Window (use pythonw.exe)** — hide the black console window (source installs). Console and Windows-startup changes take effect the next time SparkyBot is launched.
- **Check for updates on launch** — automatically check for SparkyBot and Elite Insights updates when the app starts.

### AI features

- **Enable AI features (fight commentary and voice)** — the one AI switch. SparkyBot writes short AI commentary about each fight and can read it aloud. Fully optional — everything else works without it. Turning it on adds the **AI Commentary**, **Voice**, and **Vocabulary** pages to this dialog and the **Calibration** page to the sidebar, immediately on save. Turning it off removes them just as completely.

## Common problems

- **Turned AI on but see no AI pages** — click **Apply** (or OK); the pages appear the moment the change saves.
- **SparkyBot won't quit from the X button** — that's **Close to System Tray** doing its job. Use **File → Exit** or the tray icon's Quit.
- **"Start with Windows" seems ignored** — it's applied when you click OK/Apply and takes effect from the next Windows sign-in.
- **Console window still visible after hiding it** — restart SparkyBot; the change applies on the next launch.
