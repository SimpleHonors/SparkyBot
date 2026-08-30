# Setup: Startup Behavior

A setup-wizard screen. It configures how SparkyBot behaves when it starts and how it interacts with your system tray (the little icon area by the clock).

## What's on this screen

- **Start watching for logs automatically on launch** — SparkyBot begins monitoring your log folder the moment it opens, without you clicking **Start Watcher**.
- **Start minimized to system tray** — SparkyBot launches silently in the background; open its window from the tray icon.
- **Close to system tray instead of quitting** — clicking the window's X hides SparkyBot to the tray instead of exiting, so it keeps working in the background.
- **Minimize to system tray** — the minimize button sends SparkyBot to the tray instead of the taskbar.
- **Check for updates on launch** — automatically checks GitHub for new SparkyBot and Elite Insights versions at startup. When an update exists, a quiet button appears in the status bar — nothing installs without your click.

All of these can be changed later in **Settings → Application** (updates: **Settings → Updates**).

## Common problems

- **SparkyBot "disappeared" when you closed it** — with **Close to system tray instead of quitting** on, it's still running. Find the tray icon by the clock; right-click it to open or quit.
- **Nothing posts after launch** — if you left **Start watching for logs automatically on launch** off, click **Start Watcher** (or **Start Run**) on the Home page when you begin playing.
