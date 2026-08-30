"""Poison Coverage layer for the Raid Report — ported from topstats-poison-tab.

Computes per-player poison HITS (from *-Conditions-Out table) and OUTPUT/sec
(from *-Total-Condition-Output-Generation echarts source), then appends a
bubble-chart + leaderboard tab and splices it into the *-Menu tabs macro.
"""

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path


def find_session_tag(tiddlers):
    for t in tiddlers:
        m = re.match(
            r"(\d{4}-\d{2}-\d{2}-\d{2}:\d{2}:\d{2})-Conditions-Out$",
            t.get("title", ""),
        )
        if m:
            return m.group(1)
    raise ValueError("no session tag found (missing *-Conditions-Out tiddler)")


@dataclass
class PoisonRow:
    account: str
    name: str
    prof: str
    apps: int
    fight_time: float
    output: float = 0.0

    @property
    def apps_per_min(self):
        return self.apps / (self.fight_time / 60) if self.fight_time else 0.0

    @property
    def has_output(self):
        return self.output > 0


_IMG_RE = re.compile(r"\[img[^\]]*?\[([^|\]]+)\|[^\]]*?\]\]")


def _cells(line):
    line = _IMG_RE.sub(r"{\1}", line)
    return [c.strip() for c in line.split("|")]


def _prof_from_cell(cell):
    m = re.search(r"\{([A-Za-z][A-Za-z ]*?)\}", cell)
    if m:
        return m.group(1).strip()
    m = re.search(r"\[([A-Za-z][A-Za-z ]*?)\|", cell)
    return m.group(1).strip() if m else cell.strip()


def parse_applications(tiddlers):
    co = next(
        (t for t in tiddlers if t.get("title", "").endswith("-Conditions-Out")),
        None,
    )
    if not co:
        return {}
    lines = co["text"].splitlines()
    hdr = next(l for l in lines if "!Party" in l and "Prof" in l)
    hc = _cells(hdr)
    pidx = next(i for i, c in enumerate(hc) if re.search(r"\bPoison\b", c))
    ftidx = next(i for i, c in enumerate(hc) if "FightTime" in c)
    out = {}
    for l in lines:
        m = re.search(r'data-tooltip="([^"]+)"', l)
        if not m:
            continue
        c = _cells(l)
        acct = m.group(1)
        name = re.sub(r"<[^>]+>", "", c[2]) if len(c) > 2 else acct
        prof = _prof_from_cell(c[3]) if len(c) > 3 else ""
        try:
            apps = int(c[pidx].replace(",", "") or 0)
        except (ValueError, IndexError):
            apps = 0
        try:
            ft = float(c[ftidx].replace(",", ""))
        except (ValueError, IndexError):
            ft = 0.0
        if apps > 0:
            out[acct] = PoisonRow(acct, name.strip(), prof, apps, ft)
    return out


def parse_poison_output(tiddlers):
    t = next(
        (x for x in tiddlers
         if x.get("title", "").endswith("-Total-Condition-Output-Generation")),
        None,
    )
    if not t:
        return {}
    m = re.search(r"source:\s*(\[\[.*?\]\])\s*\}", t["text"], re.S)
    if not m:
        return {}
    rows = re.findall(r"\[([^\[\]]*)\]", m.group(1))
    if not rows:
        return {}
    header = [c.strip().strip("'\"") for c in rows[0].split(",")]
    try:
        pidx = header.index("Poison")
    except ValueError:
        return {}
    out = {}
    for r in rows[1:]:
        cells = [c.strip().strip("'\"") for c in r.split(",")]
        if len(cells) <= pidx:
            continue
        lm = re.match(r"\{\{\w+\}\}\s*-\s*(.+)", cells[0])
        name = lm.group(1).strip() if lm else cells[0]
        try:
            val = float(cells[pidx])
        except ValueError:
            val = 0.0
        out[name] = val
    return out


CLASS_COLORS = {
    # One shade family per core profession; the core name shares the family
    # base (same convention as Necromancer/Scourge below). Every name in
    # fight_report.PROFESSION_NAMES must have an entry — guarded by
    # tests/test_poison_class_colors.py so new elite specs can't fall
    # through to the grey _default again.
    "Necromancer": "#52A76F",
    "Scourge": "#52A76F", "Reaper": "#3E7D54", "Harbinger": "#6FB58A",
    "Guardian": "#72C1D9",
    "Firebrand": "#72C1D9", "Dragonhunter": "#5A9FB5", "Luminary": "#8FD3E8",
    "Willbender": "#3F7E94",
    "Elementalist": "#F68A87",
    "Tempest": "#F68A87", "Catalyst": "#D96E6B", "Evoker": "#FBAAA7",
    "Weaver": "#B85451",
    "Mesmer": "#B679D5",
    "Chronomancer": "#B679D5", "Mirage": "#9A5CB8", "Troubadour": "#CE9BE6",
    "Virtuoso": "#7E44A0",
    "Warrior": "#E5B84B",
    "Spellbreaker": "#E5B84B", "Berserker": "#C79A2E", "Paragon": "#F0D477",
    "Bladesworn": "#A87F22",
    "Revenant": "#D16E5A",
    "Herald": "#D16E5A", "Renegade": "#B45540", "Ritualist": "#E0917C",
    "Vindicator": "#96422F",
    "Ranger": "#8CDC82",
    "Druid": "#8CDC82", "Amalgam": "#6FB56A", "Conduit": "#A7E89E",
    "Soulbeast": "#4E9A48", "Untamed": "#C8F2C0", "Galeshot": "#B9E463",
    "Engineer": "#D09C59",
    "Scrapper": "#B07E3E", "Holosmith": "#E8B678", "Mechanist": "#F2CE9B",
    "Thief": "#C08F95",
    "Daredevil": "#A6707A", "Deadeye": "#8E5A64", "Specter": "#D5A8B0",
    "Antiquary": "#E2BFC7",
    "_default": "#9AA0A6",
}


def class_color(prof):
    return CLASS_COLORS.get((prof or "").strip(), CLASS_COLORS["_default"])


def build_model(tiddlers):
    rows = parse_applications(tiddlers)
    output = parse_poison_output(tiddlers)
    for r in rows.values():
        r.output = output.get(r.name, 0.0)
    return sorted(rows.values(), key=lambda r: r.apps, reverse=True)


def build_chart_tiddler(tag, rows):
    header = "['Name', 'Profession', 'Poison Output/sec', 'Poison Hits', "
    header += "'Apps/min', 'color']"
    body = []
    maxsize = 0.0
    for r in rows:
        maxsize = max(maxsize, r.apps_per_min)
        body.append(
            "['%s', '%s', %.3f, %d, %.2f, '%s']"
            % (r.name.replace("'", " "), r.prof, r.output, r.apps,
               r.apps_per_min, class_color(r.prof))
        )
    data = ",\n".join([header] + body)
    return {
        "title": f"{tag}-SparkyBot-Poison-Chart",
        "tags": tag,
        "xAxis": "Poison Hits",
        "xData": "Poison Hits",
        "yAxis": "Poison Output/sec",
        "yData": "Poison Output/sec",
        "min": "0.0",
        "max": str(maxsize if maxsize else 1.0),
        "data": data,
        "text": "",
    }


def build_tab_tiddler(tag, rows):
    note = (
        ",,x = poison hits (applications &mdash; coverage, keeps the &minus;33% "
        "healing debuff up); y = poison output (generation/sec); bubble size = "
        "applications/min. Relic of the Demon Queen is not tracked by the parser "
        "and is excluded.,,"
    )
    lines = [
        "__''Poison Coverage''__",
        "",
        note,
        "",
        "{{%s-SparkyBot-Poison-Chart||BubbleChart_Template}}" % tag,
        "",
        "!! Poison Coverage &mdash; all appliers",
        "|thead-dark table-hover sortable|k",
        "|!Player | !Class | !Hits | !Apps/min | !Output/sec|h",
    ]
    for r in rows:
        lines.append(
            "|%s | %s | %d | %.1f | %.3f|"
            % (r.name, r.prof, r.apps, r.apps_per_min, r.output)
        )
    return {
        "title": f"{tag}-SparkyBot",
        "caption": "Poison Coverage",
        "tags": tag,
        "text": "\n".join(lines),
    }


def splice_menu(tiddlers, tag):
    menu = next(
        (t for t in tiddlers if t.get("title", "").endswith("-Menu")), None
    )
    if not menu:
        return False
    entry = "[[%s-SparkyBot]]" % tag
    if entry in menu["text"]:
        return False
    new, n = re.subn(
        r'(<<tabs\s+"[^"]*?)(")',
        r"\1 " + entry + r"\2",
        menu["text"],
        count=1,
    )
    if n:
        menu["text"] = new
        return True
    return False


def augment(tiddlers):
    out = copy.deepcopy(tiddlers)
    tag = find_session_tag(out)
    rows = build_model(out)
    st_titles = {f"{tag}-SparkyBot", f"{tag}-SparkyBot-Poison-Chart"}
    out = [t for t in out if t.get("title") not in st_titles]
    out.append(build_chart_tiddler(tag, rows))
    out.append(build_tab_tiddler(tag, rows))
    splice_menu(out, tag)
    return out


def augment_file(json_path: Path) -> dict:
    try:
        tiddlers = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"appliers": 0, "with_output": 0, "skipped": True}

    try:
        tag = find_session_tag(tiddlers)
    except ValueError:
        return {"appliers": 0, "with_output": 0, "skipped": True}

    rows = build_model(tiddlers)
    if not rows:
        return {"appliers": 0, "with_output": 0, "skipped": True}

    out = augment(tiddlers)
    json_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    n_output = sum(1 for r in rows if r.has_output)
    return {"appliers": len(rows), "with_output": n_output, "skipped": False}
