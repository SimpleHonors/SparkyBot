# SparkyBot

### The unhinged Guild Wars 2 WvW log analyst for Discord, Twitch, and complete end-of-night reports.

ArcDPS writes the log. SparkyBot parses the fight, posts the useful numbers,
and says what your guildies were about to say—only faster and with receipts.

Not affiliated with ArenaNet. Barely affiliated with good taste. Extremely good
at its job.

> Sparky's optional AI commentary talks trash by default. The numbers work
> without the mouth. Your call.

![SparkyBot desktop app in Workbench Dark](docs/screenshots/v2-home.png)

## The report got a glow-up. Sparky kept the receipts.

- **Less squinting, more answers.** Refreshed Simple and Pro views put totals
  beside rates, make damage and support rankings easier to read, and keep the
  important numbers visible on smaller screens.
- **Wall of Fame. Yes, even that guy.** See who Sparky called out, what for,
  and the original commentary from fights in the report. It uses saved AI
  comments—not newly invented memories of your raid.
- **Enemy Intel still has receipts.** Compare your squad with each enemy color,
  inspect observed professions and incoming skill pressure, then drill into
  estimated subgroups, role candidates, strips, CC, and build fingerprints.
- **One file, three levels of nerd.** Pro gives the detailed analysis,
  Simple keeps the highlights, and Classic remains for the no-fun league.
- **Player Compare steals the homework.** Put two squad players side by side for
  damage, support, healing, weapons, rotations, skill shares, consumables, and
  observed trait evidence when the logs include it.
- **It has outfits.** Reports include Graphite, Midnight, Blackout, and Studio
  Light skins. The desktop app has its own themes and remembers your choice.

![Sparky Pro Enemy Intel comparing an anonymized squad with one enemy color](docs/screenshots/v222-enemy-intel.png)

![SparkyBot Raid Report settings in Workbench Dark](docs/screenshots/v2-settings-raid-reports.png)

## What it does

- Watches ArcDPS logs and processes completed fights automatically.
- Posts quick KDR, downs, kills, deaths, damage, and optional AI commentary.
- Builds a local HTML night report with sortable fights, detailed player stats,
  skill-share charts, boon/support/healing tables, high scores, and Enemy Intel.
- Supports Discord webhooks, Twitch chat, optional voice, manual file processing,
  raid sessions, optional dps.report links, and guild setup files.
- Keeps AI optional. Fight parsing and reports work without it.

## Install

For Windows, you'll need [ArcDPS](https://www.deltaconnected.com/arcdps/) with
combat logging enabled and the
[.NET 8 Desktop Runtime](https://dotnet.microsoft.com/en-us/download/dotnet/8.0)
used by the Guild Wars 2 Elite Insights parser.

1. Download the Windows installer (the file ending in `-Setup.exe`) from the
   [latest release](https://github.com/SimpleHonors/SparkyBot/releases/latest).
2. Double-click it.
3. Follow the first-run setup.

No Python scavenger hunt. No command prompt audition.

## Your first raid

1. **Point Sparky at your ArcDPS log folder** in the setup wizard or Settings.
2. **Choose where to post.** For Discord, create a webhook under **Channel
   Settings → Integrations → Webhooks**, then paste its URL into SparkyBot.
   Individual fights and the night report can go to different destinations.
3. **Want commentary? Enable AI before the fights are processed.** Choose a
   provider or a compatible local server in Settings. Leave it off for stats
   without the heckling.
4. **Click Start Run before the raid.** After the last fight, let processing
   and any AI commentary finish, then click **End Run** to build the report.
   You can also select logs manually from the report page.
5. **Open the HTML in your browser or post it to Discord.** When posting,
   Sparky sends the overview followed by the report file; large reports may
   arrive as a ZIP—extract it first.

Guild leaders can share Discord routing through **File → Create Guild Setup
File**. Members load it with **File → Use Guild Setup File**.

## Reading the report

**Simple**, **Pro**, and **Classic** are views of the same night in one HTML
file. Simple gets you to the highlights; Pro adds the detailed comparisons
and drilldowns. Report data and the Simple/Pro interface are embedded for
local viewing; Classic may still need an internet connection for external
assets.

Enemy composition and skill pressure come from observed combat data; enemy
subgroups and roles are estimates. Trait evidence means an observed signal,
not a complete equipment inspection. Confident nonsense is still nonsense.

**Wall of Fame** appears when AI is enabled for report generation and draws
from locally saved final fight comments. Only comments matching the report's
fights are included. Old logs without saved commentary won't grow quotes just
because you generate a report. If you built it before the last AI response
finished, regenerate it to pick up that comment.

High Scores describe the selected night. Long-term leaderboards, when present,
are labeled separately and use accumulated history across raids.

## Data and sharing

Parsing and report generation run locally. Shared reports include player names,
account identifiers, combat stats, and any included AI comments or leaderboards.
Guild setup exports include Discord webhooks, but not AI keys, Twitch credentials,
player history, or local paths.

Optional integrations send their inputs to the services you select: posts to
Discord/Twitch, fight summaries and prompt context to AI, spoken text to voice,
and combat logs to dps.report when uploads are enabled. AI and voice can use
compatible local endpoints. Updates and tool downloads use GitHub; Classic
report assets may also load from the web.

## Help and source use

- [In-app help pages](docs/help/README.md)
- [Log-tool interoperability and credits](docs/WVW_LOG_TOOL_INTEROPERABILITY.md)
- Source launch: install `requirements.txt` in a Python virtual environment,
  then run `python bootstrap.py` from the repository folder.
- Bugs and requests: [GitHub Issues](https://github.com/SimpleHonors/SparkyBot/issues)

## Credits

SparkyBot uses the
[GW2 Elite Insights Parser](https://github.com/baaron4/GW2-Elite-Insights-Parser)
and interoperates with several community WvW log tools. Exact relationships and
license boundaries are documented in the interoperability guide above.

## License

[MIT](LICENSE). Third-party components retain their own licenses under
`THIRD_PARTY_LICENSES/`.
