"""Night model extractor: combiner tiddler store -> one typed JSON night model.

Parses a GW2-EI log-combiner night store (the JSON list of tiddler dicts
behind the night summary) into a single clean, typed, skin-ready dict.
Never raises for a bad section: every section is guarded and failures are
collected in ``warnings``.

Schema of ``build_night_model(tiddlers) -> dict``::

    {
      "schema_version": 2,
      "session": {                     # session meta
        "tag": str | None,             # e.g. "2026-08-10-20:27:05"
        "date": str | None,            # "YYYY-MM-DD" part of the tag
        "commander": str | None,       # most fights tagged; combat-time tie
        "commander_account": str|None, # account from winning Tag_Stats row
        "total_duration": str | None,  # from the Overview totals row
      },
      "totals": {                      # Tag_Stats Totals row
        "fights": int, "enemy_downs": int, "enemy_kills": int,
        "ally_downs": int, "ally_deaths": int, "kdr": float,
      } | None,
      "fights": [                      # per-fight rows from the Overview
        {"index": int, "time_label": str, "duration": str,
         "squad": int|None, "allies": int|None, "enemy": int|None,
         "rgb": {"r":int,"g":int,"b":int} | None,
         "downs": int|None, "kills": int|None, "rallies": int|None,
         "damage_out": int|None, "damage_in": int|None,
         "barrier_out": int|None, "barrier_out_pct": float|None,
         "shield_out": int|None, "shield_out_pct": float|None,
         "chart": str|None}           # fight-chart tiddler target
      ],
      "leaderboards": [                # ONLY "{tag}-{stat}-Leaderboard"
        {"stat": str,                  # tiddlers; empty is valid when the
                                       # night has none (no warning)
         "rows": [{"rank": int|None, "name": str, "account": str|None,
                   "profession": str|None, "value": float|None,
                   "cells": [int|float]}]}
      ],
      "stat_tables": [                 # per-night combiner stat tables
        {"stat": str,                  # (Damage, Heal-Stats, Uptimes, ...)
         "value_label": str|None,      # honest metric used for ranking
         "rows": [{"rank": int|None, "name": str, "account": str|None,
                   "profession": str|None, "value": float|None,
                   "cells": [int|float], "metrics": {str:int|float}}]}
      ],
      "high_scores": {"blocks": [      # High-Scores flex-col blocks
         {"caption": str|None,
          "rows": [{"cells": [str], "score": float|None}]}
      ]} | None,
      "squad_composition": {"squads": [ # Squad-Composition per-fight squads
         {"fight": int,
          "players": [{"profession": str|None, "name": str}]}
      ]} | None,
      "poison": [                      # core.poison_tab.build_model rows
        {"account": str, "name": str, "prof": str, "apps": int,
         "fight_time": float, "output": float, "apps_per_min": float}
      ],
      "pro_navigation": [...],         # Pro views and their subviews
      "enemy_intel": {...},            # observed evidence + explicitly
                                        # inferred enemy subgroup estimates
      "warnings": [str]                # one entry per section that failed
    }

Wikitext table conventions handled here (see core/poison_tab.py for the
original parsing helpers): rows start with ``|``; header rows end ``|h``,
class rows end ``|k``, caption rows end ``|c``, footer rows end ``|f``.
Cells may carry ``{{Prof}}`` transclusions, ``<span ...>`` tooltips,
``[img ...]`` icons and ``[[link|target]]`` links -- stripped to plain
text; numbers may carry commas and a trailing percent.
"""

import copy
import re
from dataclasses import asdict

from core import poison_tab
from core.enemy_intel import PRO_NAVIGATION, build_enemy_intel

SCHEMA_VERSION = 2

_TAG_RE = re.compile(r"(\d{4}-\d{2}-\d{2}-\d{2}:\d{2}:\d{2})")
_IMG_RE = re.compile(r"\[img[^\]]*?\[([^|\]]+)\|[^\]]*?\]\]")
_TOOLTIP_RE = re.compile(r"data-tooltip=['\"]([^'\"]+)['\"]")
_HIT_TOOLTIP_RE = re.compile(
    r"([\d,]+)\s+of\s+([\d,]+)\s+hits\s+-\s+\(([\d.]+)%\)",
    re.IGNORECASE,
)
_PROF_TIDDEL_RE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z ]*?)\s*\}\}")
_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_PIPE_LINK_RE = re.compile(r"\[\[([^\]|]*)\|([^\]]+)\]\]")
_HTML_HREF_RE = re.compile(
    r"<a\b[^>]*\bhref\s*=\s*(['\"])(https?://[^'\"]+)\1",
    re.IGNORECASE,
)
_BARE_HTTP_RE = re.compile(r"https?://[^\s|<>\]\"']+", re.IGNORECASE)
_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?%?$")
_NUMBER_WITH_RATE_RE = re.compile(
    r"^\s*(-?[\d,]+(?:\.\d+)?)\s*(/(?:sec|min))\s*$",
    re.IGNORECASE,
)
# sentinel standing in for the '|' inside [[link|target]] during cell split
_PIPE = "\x1f"

# Combiner stat tables that are leaderboard-shaped (rank/name/prof/value-ish)
# even though they do not carry the "-Leaderboard" title suffix.
_STAT_TABLE_SUFFIXES = [
    "Damage",
    "Heal-Stats",
    "Uptimes",
    "Conditions-In",
    "Conditions-Out",
    "Debuffs-Out",
    "Debuffs-In",
    "Mechanics",
    "Pull-Skills",
    "Attendance",
    "Combat-Resurrect",
    "Support-Summary",
    "Offensive-Summary",
]


_STAT_PROFILES = {
    "Damage": {
        "total_key": "targetdamage", "total_label": "Damage",
        "total_unit": "damage", "rate_key": "targetdamageps",
        "rate_label": "DPS", "rate_unit": "per_second",
    },
    "Heal-Stats": {
        "stat_label": "Healing",
        "total_key": "healing", "total_label": "Healing",
        "total_unit": "healing", "rate_key": "healingps",
        "rate_label": "Healing / sec", "rate_unit": "per_second",
    },
    "Uptimes": {
        "stat_label": "Might Uptime",
        "rate_key": "might", "rate_label": "Might Uptime (%)",
        "rate_unit": "percent",
    },
    "Conditions-Out": {
        "stat_label": "Condition Applications",
        "total_key": "count", "total_label": "Conditions Applied",
        "total_unit": "applications", "rate_label": "Conditions / min",
        "rate_unit": "per_minute", "derive_rate": True,
    },
    "Debuffs-Out": {
        "stat_label": "Debuff Applications",
        "total_key": "count", "total_label": "Debuffs Applied",
        "total_unit": "applications", "rate_label": "Debuffs / min",
        "rate_unit": "per_minute", "derive_rate": True,
    },
    "Mechanics": {
        "stat_label": "Killing Blows",
        "total_key": "kllngblwplayer", "total_label": "Killing Blows",
        "total_unit": "count", "rate_label": "Killing Blows / min",
        "rate_unit": "per_minute", "derive_rate": True,
    },
    "Attendance": {
        "total_key": "numfights", "total_label": "Fights",
        "total_unit": "fights", "show_fights_column": False,
    },
    "Combat-Resurrect": {
        "stat_label": "Combat-Resurrection Healing",
    },
    "Offensive-Summary": {
        "show_as_board": False,
        "total_key": "totaldmg", "total_label": "Damage",
        "total_unit": "damage", "rate_label": "DPS",
        "rate_unit": "per_second", "derive_rate": True,
    },
}


def _plain(cell):
    """Strip a wikitext/HTML cell down to plain text."""
    cell = cell.replace(_PIPE, "|")
    cell = _IMG_RE.sub(r"\1", cell)
    cell = re.sub(r"<[^>]+>", "", cell)
    cell = _LINK_RE.sub(r"\1", cell)
    m = _PROF_TIDDEL_RE.search(cell)
    cell = _PROF_TIDDEL_RE.sub(r"\1", cell)
    cell = re.sub(r"\{\{|\}\}", "", cell)
    cell = re.sub(r"'{2,}", "", cell)
    return cell.strip()


def _prof(cell):
    m = _PROF_TIDDEL_RE.search(cell)
    return m.group(1).strip() if m else None


def _cells(line):
    line = _IMG_RE.sub(r"{\1}", line)
    line = _PIPE_LINK_RE.sub(r"[[\1" + _PIPE + r"\2]]", line)
    return [c.strip() for c in line.split("|")]


def _ends(line):
    line = line.strip()
    for suffix in ("|h", "|k", "|c", "|f"):
        if line.endswith(suffix):
            return suffix
    return ""


def _is_data_row(line):
    line = line.strip()
    return line.startswith("|") and _ends(line) == ""


def _is_header_row(line):
    return _ends(line) == "|h" and "|" in line.strip()[1:-2]


def _num(cell):
    cell = cell.strip()
    if not cell or not _NUM_RE.match(cell):
        return None
    return float(cell.replace(",", "").rstrip("%")) if (
        "." in cell or cell.endswith("%")) else int(
        cell.replace(",", "").rstrip("%"))


def _number_and_unit(cell):
    """Return a source number and its explicit rate unit, if present."""
    value = _num(cell)
    if value is not None:
        return value, "percent" if cell.strip().endswith("%") else None
    match = _NUMBER_WITH_RATE_RE.match(cell)
    if not match:
        return None, None
    value_text, suffix = match.groups()
    value = float(value_text.replace(",", ""))
    return value, "per_second" if suffix.lower() == "/sec" else "per_minute"


def _find_tiddler(tiddlers, suffix):
    return next((t for t in tiddlers
                 if t.get("title", "").endswith(suffix)), None)


def _table_lines(tiddlers, suffix):
    t = _find_tiddler(tiddlers, suffix)
    return t["text"].splitlines() if t and t.get("text") else None


# ---------------------------------------------------------------- meta


def _commander_candidates(tiddlers):
    """Return command-tag rows with their reported fight counts."""
    lines = _table_lines(tiddlers, "-Tag_Stats") or []
    header = next(
        (
            line for line in lines
            if _is_header_row(line) and "fights" in line.casefold()
        ),
        None,
    )
    if not header:
        return []

    headers = [_plain(cell).casefold() for cell in _cells(header)]
    try:
        name_index = headers.index("name")
        fights_index = headers.index("fights")
    except ValueError:
        return []

    candidates = []
    for order, line in enumerate(lines):
        if not _is_data_row(line):
            continue
        account_match = _TOOLTIP_RE.search(line)
        if not account_match or "." not in account_match.group(1):
            continue
        cells = _cells(line)
        if max(name_index, fights_index) >= len(cells):
            continue
        name = _plain(cells[name_index])
        fights = _num(cells[fights_index])
        if not name or not isinstance(fights, (int, float)):
            continue
        candidates.append({
            "name": name,
            "account": account_match.group(1).strip(),
            "fights": int(fights),
            "source_order": order,
        })
    return candidates


def _commander_combat_time(candidate, stat_tables):
    """Find the candidate's longest exported session combat-time value."""
    account = candidate["account"].lstrip(":").casefold()
    name = candidate["name"].casefold()
    combat_times = []
    for table in stat_tables or []:
        for row in table.get("rows") or []:
            row_account = str(row.get("account") or "").lstrip(":").casefold()
            row_name = str(row.get("name") or "").casefold()
            if row_account != account and row_name != name:
                continue
            value = row.get("participation_time")
            if isinstance(value, (int, float)):
                combat_times.append(value)
    return max(combat_times, default=0)


def _select_commander(candidates, stat_tables):
    """Pick most tagged fights, then most combat time, then source order."""
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda candidate: (
            candidate["fights"],
            _commander_combat_time(candidate, stat_tables),
            -candidate["source_order"],
        ),
    )


def _parse_session(tiddlers, stat_tables=None):
    out = {"tag": None, "date": None, "commander": None,
           "commander_account": None, "total_duration": None}

    for title_key in ("-Log-Summary",):
        t = _find_tiddler(tiddlers, title_key)
        if t:
            m = _TAG_RE.search(t["title"])
            if m:
                out["tag"] = m.group(1)
    if not out["tag"]:
        for t in tiddlers:
            m = _TAG_RE.search(t.get("title", ""))
            if m:
                out["tag"] = m.group(1)
                break
    if out["tag"]:
        out["date"] = out["tag"][:10]

    tag = out["tag"]
    ts = _find_tiddler(tiddlers, "-Tag_Stats") if tag else None
    if ts:
        winner = _select_commander(
            _commander_candidates(tiddlers), stat_tables
        )
        if winner:
            out["commander"] = winner["name"]
            out["commander_account"] = winner["account"]
        caption = (ts.get("caption") or "").strip()
        # Preserve old one-name captions only when no command-tag row exists.
        if (
            not winner
            and caption
            and caption.lower() not in (
                "tag summary", "summary by command tag"
            )
        ):
            out["commander"] = caption

    lines = _table_lines(tiddlers, "-Overview")
    if lines:
        for line in lines:
            if line.strip().startswith("|Total Fights"):
                for c in _cells(line):
                    if re.match(r"(?:\d+h )?\d+m \d+s \d+ms$", c):
                        out["total_duration"] = c
                        break
                break
    return out


def _parse_totals(tiddlers):
    lines = _table_lines(tiddlers, "-Tag_Stats")
    if not lines:
        raise ValueError("Tag_Stats tiddler missing")
    header = next((l for l in lines
                   if _ends(l) == "|h" and "KDR" in l), None)
    if not header:
        raise ValueError("Tag_Stats header row missing")
    cols = {}
    for i, c in enumerate(_cells(header)):
        low = c.lower()
        for token, key in (("fights", "fights"),
                           ("downedenemy", "enemy_downs"),
                           ("killed", "enemy_kills"),
                           ("downedally", "ally_downs"),
                           ("deadally", "ally_deaths"),
                           ("kdr", "kdr")):
            if token in low and key not in cols:
                cols[key] = i
    totals_row = next((l for l in lines
                       if l.strip().startswith("|Totals")), None)
    if not totals_row:
        raise ValueError("Tag_Stats Totals row missing")
    cells = _cells(totals_row)
    out = {}
    for key, i in cols.items():
        v = _num(cells[i]) if i < len(cells) else None
        out[key] = v if v is not None else (0.0 if key == "kdr" else 0)
    out["kdr"] = float(out["kdr"])
    return out


# --------------------------------------------------------------- fights

_OVERVIEW_FIELDS = (
    ("report_url", "Fight Link"),
    ("duration", "Duration"),
    ("squad", "Squad"),
    ("allies", "Allies"),
    ("enemy", "Enemy"),
    ("rgb", "R/G/B"),
    ("downs", "DownedEnemy"),
    ("kills", "killed"),
    ("ally_downs", "DownedAlly"),
    ("ally_deaths", "DeadAlly"),
    ("rallies", "rallies"),
    ("damage_out", "{{Damage}}"),
    ("damage_in", "Damage Taken"),
    ("barrier_out", "damageBarrier"),
    ("barrier_out_pct", "damageBarrier"),
    ("shield_out", "damageShield"),
    ("shield_out_pct", "damageShield"),
    ("chart", "Fight Chart"),
)


def _parse_fights(tiddlers):
    lines = _table_lines(tiddlers, "-Overview")
    if not lines:
        raise ValueError("Overview tiddler missing")
    header = next((l for l in lines
                   if _is_header_row(l) and "!#" in l), None)
    if not header:
        raise ValueError("Overview header row missing")
    header_cells = _cells(header)
    cols = {}
    barrier_seen = 0
    shield_seen = 0
    for i, c in enumerate(header_cells):
        for key, token in _OVERVIEW_FIELDS:
            if token not in c:
                continue
            if key in ("barrier_out", "barrier_out_pct") or key in (
                    "shield_out", "shield_out_pct"):
                continue  # handled below, % distinguished by order
            if token in ("damageBarrier", "damageShield"):
                continue
            cols.setdefault(key, i)
        if "damageBarrier" in c:
            barrier_seen += 1
            cols["barrier_out_pct" if "%" in c or barrier_seen == 2
                 else "barrier_out"] = i
        if "damageShield" in c:
            shield_seen += 1
            cols["shield_out_pct" if "%" in c or shield_seen == 2
                 else "shield_out"] = i

    fights = []
    for line in lines:
        if not _is_data_row(line):
            continue
        cells = _cells(line)
        if len(cells) < 3 or _num(cells[1]) is None:
            continue
        fight = {"index": int(_num(cells[1])), "time_label": _plain(cells[2]),
                 "duration": None, "squad": None, "allies": None,
                 "enemy": None, "rgb": None, "downs": None,
                 "kills": None, "ally_downs": None, "ally_deaths": None,
                 "rallies": None, "damage_out": None,
                 "damage_in": None, "barrier_out": None,
                 "barrier_out_pct": None, "shield_out": None,
                 "shield_out_pct": None, "chart": None, "report_url": None}
        for key in ("duration",):
            i = cols.get(key)
            if i is not None and i < len(cells):
                fight[key] = cells[i]
        for key in ("squad", "allies", "enemy", "downs", "kills",
                    "ally_downs", "ally_deaths",
                    "rallies", "damage_out", "damage_in", "barrier_out",
                    "shield_out"):
            i = cols.get(key)
            if i is not None and i < len(cells):
                fight[key] = _num(cells[i])
        for key in ("barrier_out_pct", "shield_out_pct"):
            i = cols.get(key)
            if i is not None and i < len(cells):
                v = _num(cells[i].replace("%", "") + "%")
                fight[key] = float(v) if v is not None else None
        i = cols.get("rgb")
        if i is not None and i < len(cells):
            m = re.match(r"(\d+)/(\d+)/(\d+)$", cells[i])
            if m:
                fight["rgb"] = {"r": int(m.group(1)),
                                "g": int(m.group(2)),
                                "b": int(m.group(3))}
        i = cols.get("chart")
        if i is not None and i < len(cells):
            m = _PIPE_LINK_RE.search(line)
            if m:
                fight["chart"] = m.group(2).strip()
        i = cols.get("report_url")
        if i is not None and i < len(cells):
            link_cell = cells[i].replace(_PIPE, "|")
            href = _HTML_HREF_RE.search(link_cell)
            wiki = _PIPE_LINK_RE.search(link_cell)
            bare = _BARE_HTTP_RE.search(link_cell)
            candidate = (
                href.group(2) if href else
                wiki.group(2).strip() if wiki and wiki.group(2).strip().lower().startswith(("http://", "https://")) else
                bare.group(0) if bare else None
            )
            if candidate:
                fight["report_url"] = candidate
        fights.append(fight)
    if not fights:
        raise ValueError("Overview has no fight rows")
    return fights


# ------------------------------------------------------------ boards


def _metric_key(value):
    return re.sub(r"[^a-z0-9]+", "", _plain(value).lstrip("!").lower())


def _header_keys(line):
    """Make header keys stable without collapsing e.g. value and value %."""
    keys = []
    seen = set()
    for cell in _cells(line):
        plain = _plain(cell).lstrip("!").strip()
        key = _metric_key(cell)
        if key and key in seen:
            key += "pct" if "%" in plain else "2"
        seen.add(key)
        keys.append(key)
    return keys


def _parse_board_rows(line_iter, ranked=False, headers=None):
    rows = []
    for line in line_iter:
        if not _is_data_row(line):
            continue
        raw_cells = _cells(line)
        m = _TOOLTIP_RE.search(line)
        account = m.group(1).strip() if m else None
        profession = _prof(line)
        cells = [_plain(c) for c in raw_cells]
        rank = None
        if ranked and len(cells) > 1:
            rank = _num(cells[1])
        name = None
        name_i = None
        if headers:
            account_i = next(
                (i for i, key in enumerate(headers) if key == "account"),
                None,
            )
            if account_i is not None and account_i < len(cells):
                account = cells[account_i] or account
            name_i = next(
                (
                    i
                    for wanted in ("name", "player")
                    for i, key in enumerate(headers)
                    if key == wanted
                ),
                None,
            )
            profession_i = next(
                (
                    i
                    for i, key in enumerate(headers)
                    if key in ("profession", "prof")
                ),
                None,
            )
            if profession_i is not None and profession_i < len(raw_cells):
                profession = (
                    _prof(raw_cells[profession_i])
                    or cells[profession_i]
                    or profession
                )
            if name_i is not None and name_i < len(raw_cells):
                name = cells[name_i]
                embedded_profession = _prof(raw_cells[name_i])
                if embedded_profession:
                    profession = embedded_profession
                    if name.casefold().startswith(embedded_profession.casefold()):
                        name = name[len(embedded_profession):].strip()
        if not name:
            name_i = None
            for i, c in enumerate(cells):
                if i == 0 and rank is not None:
                    continue
                if c and not _NUM_RE.match(c) and re.search(r"[A-Za-z]", c):
                    name = c
                    name_i = i
                    break
        if name is None:
            continue
        value = None
        nums = []
        metrics = {}
        metric_units = {}
        metric_details = {}
        for i, c in enumerate(cells):
            if i <= (name_i or 0):
                continue
            v, unit = _number_and_unit(c)
            if v is not None:
                nums.append(v)
                if headers and i < len(headers) and headers[i]:
                    metrics[headers[i]] = v
                    if unit:
                        metric_units[headers[i]] = unit
                    hit_detail = _HIT_TOOLTIP_RE.search(raw_cells[i])
                    if hit_detail:
                        metric_details[headers[i]] = {
                            "connected_hits": _num(hit_detail.group(1)),
                            "logged_hit_events": _num(hit_detail.group(2)),
                            "connection_rate": _num(hit_detail.group(3)),
                        }
                if value is None:
                    value = v
        row = {"rank": int(rank) if rank is not None else None,
               "name": name, "account": account,
               "profession": profession, "value": value,
               "cells": nums}
        if headers is not None:
            row["metrics"] = metrics
            row["metric_units"] = metric_units
            row["metric_details"] = metric_details
        rows.append(row)
    return rows


def _display_words(value):
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    return re.sub(r"[_-]+", " ", value).strip().title()


def _rate_label(stat):
    key = _metric_key(stat)
    labels = {
        "damage": "DPS",
        "damagepersecond": "DPS",
        "healing": "Healing / sec",
        "healingpersecond": "Healing / sec",
        "boonstrips": "Boon Strips / sec",
        "stripspersecond": "Boon Strips / sec",
        "cleanses": "Cleanses / sec",
        "cleansespersecond": "Cleanses / sec",
        "crowdcontroloutpersecond": "Crowd Control / sec",
        "downs": "Downs / min",
        "downspersecond": "Downs / sec",
        "kills": "Kills / min",
        "killspersecond": "Kills / sec",
    }
    return labels.get(key, _display_words(stat))


def _sort_metadata(default_key, total_label=None, rate_label=None,
                   participation=False, fight_count=False, raids=False):
    options = []
    if total_label:
        options.append({"key": "total", "label": total_label})
    if rate_label:
        options.append({"key": "rate", "label": rate_label})
    if participation:
        options.append({"key": "participation_time", "label": "Fight Time"})
    if fight_count:
        options.append({"key": "fight_count", "label": "Fights"})
    if raids:
        options.append({"key": "raids", "label": "Raids"})
    return {
        "default_key": default_key,
        "total_key": "total" if total_label else None,
        "rate_key": "rate" if rate_label else None,
        "options": options,
    }


def _parse_explicit_boards(tiddlers):
    """Parse only "{tag}-{stat}-Leaderboard" tiddlers (Glicko boards).

    An empty result is valid: nights built without an accumulated
    Top_Stats.db carry no leaderboard tiddlers.
    """
    boards = []
    for t in tiddlers:
        title = t.get("title", "")
        m = re.match(r"^(.*)-(.+)-Leaderboard$", title)
        if not m or not re.search(r"[A-Za-z]", m.group(2)):
            continue  # menu tiddler "-Leaderboard" carries no stat name
        stat = m.group(2)
        text = t.get("text") or ""
        lines = text.splitlines()
        header_line = next((line for line in lines if _is_header_row(line)), "")
        headers = _header_keys(header_line)
        rows = _parse_board_rows(lines, ranked=True, headers=headers)
        if rows:
            is_rating = "glickorating" in headers
            avg_key = next((key for key in headers if key.startswith("avg")), None)
            value_key = "value" if "value" in headers else avg_key
            rate_label = _rate_label(stat)
            rate_unit = next(
                (
                    row.get("metric_units", {}).get(value_key)
                    for row in rows
                    if row.get("metric_units", {}).get(value_key)
                ),
                None,
            )
            if rate_unit is None and "persecond" in _metric_key(stat):
                rate_unit = "per_second"
            for row in rows:
                metrics = row.get("metrics", {})
                metric_value = metrics.get(value_key) if value_key else None
                row.update({
                    "value": metric_value,
                    "total": None,
                    "rate": metric_value,
                    "participation_time": None,
                    "fight_count": None,
                    "rating": metrics.get("glickorating"),
                    "raids": metrics.get("raids"),
                })
            boards.append({
                "stat": stat,
                "scope": "historical",
                "source_kind": (
                    "historical_rating" if is_rating else "historical_record"
                ),
                "metric": {
                    "label": _display_words(stat),
                    "total_label": None,
                    "total_unit": None,
                    "rate_label": rate_label,
                    "rate_unit": rate_unit,
                },
                "sort": _sort_metadata(
                    "rate", rate_label=rate_label, raids=is_rating
                ),
                "rows": rows,
            })
    return boards


def _session_tag(tiddlers):
    for t in tiddlers:
        m = _TAG_RE.search(t.get("title", ""))
        if m:
            return m.group(1)
    return None


def _named_table(lines, caption):
    """Return one table by caption from a tiddler that contains several."""
    caption_key = caption.casefold()
    caption_index = next(
        (
            index
            for index, line in enumerate(lines)
            if _ends(line) == "|c" and caption_key in _plain(line).casefold()
        ),
        None,
    )
    if caption_index is None:
        return "", []
    header_index = next(
        (
            index
            for index in range(caption_index + 1, len(lines))
            if _is_header_row(lines[index])
        ),
        None,
    )
    if header_index is None:
        return "", []
    rows = []
    for line in lines[header_index + 1:]:
        if _is_data_row(line):
            rows.append(line)
        elif rows:
            break
    return lines[header_index], rows


def _column_label(key):
    labels = {
        "fighttime": "Fight Time",
        "targetdamage": "Damage",
        "targetdamageps": "DPS",
        "targetpower": "Power Damage",
        "targetpowerps": "Power DPS",
        "targetcondition": "Condition Damage",
        "targetconditionps": "Condition DPS",
        "healing": "Healing",
        "healingps": "Healing / sec",
        "boonstrips": "Boon Strips",
        "condicleanse": "Cleanses",
        "appliedcrowdcontrol": "Crowd Control",
        "downcontribution": "Down Contribution",
        "killed": "Kills",
        "downed": "Downs",
        "numfights": "Fights",
        "activetime": "Fight Time",
        "resurrects": "Resurrects",
        "stability": "Stability",
    }
    return labels.get(key, _display_words(key))


def _parse_stability_generation(tiddlers):
    """Parse Stability's dedicated Total Gen and Gen/Sec tables."""
    tiddler = _find_tiddler(tiddlers, "-Boon-Generation-Detailed")
    if not tiddler or not tiddler.get("text"):
        return None
    lines = tiddler["text"].splitlines()
    heading = next(
        (
            index for index, line in enumerate(lines)
            if line.lstrip().startswith("!") and "Stability" in _plain(line)
        ),
        None,
    )
    if heading is None:
        return None

    def table_after(start):
        header_index = next(
            (i for i in range(start, len(lines)) if _is_header_row(lines[i])),
            None,
        )
        if header_index is None:
            return None, [], None
        caption_index = next(
            (
                i for i in range(header_index + 1, len(lines))
                if _ends(lines[i]) == "|c" and "Stability Table" in lines[i]
            ),
            None,
        )
        if caption_index is None:
            return None, [], None
        headers = _header_keys(lines[header_index])
        rows = _parse_board_rows(
            lines[header_index + 1:caption_index], headers=headers
        )
        return headers, rows, caption_index + 1

    headers, total_rows, next_index = table_after(heading + 1)
    if not total_rows or next_index is None:
        return None
    _, rate_rows, _ = table_after(next_index)
    rate_by_account = {
        row["account"].casefold(): row
        for row in rate_rows if row.get("account")
    }
    rate_by_name = {
        row["name"].casefold(): row
        for row in rate_rows if row.get("name")
    }
    rows = []
    for row in total_rows:
        rate_row = None
        if row.get("account"):
            rate_row = rate_by_account.get(row["account"].casefold())
        if rate_row is None:
            rate_row = rate_by_name.get(row["name"].casefold())
        total = row.get("metrics", {}).get("totalgen")
        if total is None:
            continue
        row.update({
            "total": total,
            "rate": (
                rate_row.get("metrics", {}).get("totalgen")
                if rate_row else None
            ),
            "participation_time": row.get("metrics", {}).get("fighttime"),
            "fight_count": None,
            "value": total,
        })
        rows.append(row)
    rows.sort(key=lambda row: row["total"], reverse=True)
    return {
        "stat": "Stability Generation",
        "source_key": "Stability-Generation",
        "source_tiddler": tiddler.get("title"),
        "scope": "session",
        "value_label": "Stability Generation",
        "metric": {
            "label": "Stability Generation",
            "total_label": "Stability Generation",
            "total_unit": "generation",
            "rate_label": "Stability Generation / sec",
            "rate_unit": "per_second",
        },
        "sort": _sort_metadata(
            "total", total_label="Stability Generation",
            rate_label="Stability Generation / sec", participation=True,
            fight_count=True,
        ),
        "columns": [
            {"key": key, "label": _column_label(key)}
            for key in (headers or []) if key
        ],
        "rows": rows,
    }


def _parse_stat_tables(tiddlers, player_skill_evidence=None):
    """Parse the standard per-night combiner stat tables."""
    legacy_value_labels = {
        "Damage": "Damage / sec",
        "Heal-Stats": "Healing / sec",
        "Uptimes": "Might uptime (%)",
        "Conditions-Out": "Conditions applied",
        "Debuffs-Out": "Control effects",
        "Mechanics": "Killing blows",
        "Attendance": "Fights attended",
    }
    tables = []
    cast_rows = (player_skill_evidence or {}).get("players") or []
    casts_by_account = {
        str(item.get("account") or "").strip().lstrip(":").casefold(): item
        for item in cast_rows if item.get("account")
    }
    casts_by_name = {
        str(name).strip().casefold(): item
        for item in cast_rows for name in item.get("names") or []
    }
    tag = _session_tag(tiddlers)
    for suffix in _STAT_TABLE_SUFFIXES:
        exact = f"{tag}-{suffix}" if tag else None
        t = next((x for x in tiddlers if x.get("title", "") == exact),
                 None) if exact else _find_tiddler(tiddlers, "-" + suffix)
        if not t or not t.get("text"):
            continue
        text_lines = t["text"].splitlines()
        if suffix == "Pull-Skills":
            # This tiddler carries two different tables. Only outgoing pulls
            # belong in a performer leaderboard; incoming pulls describe who
            # got pulled and must never be mixed into the same ranking.
            header_line, row_lines = _named_table(text_lines, "Outgoing Pulls")
        else:
            header_line = next(
                (line for line in text_lines if _is_header_row(line)), ""
            )
            row_lines = [
                line for line in text_lines if not _is_header_row(line)
            ]
        headers = _header_keys(header_line)
        rows = _parse_board_rows(
            row_lines,
            headers=headers,
        )
        if rows:
            stat = (
                "Outgoing Pulls"
                if suffix == "Pull-Skills"
                else (
                    _STAT_PROFILES.get(suffix, {}).get("stat_label")
                    or t.get("caption")
                    or suffix.replace("-", " ")
                ).strip()
            )
            profile = _STAT_PROFILES.get(suffix, {})
            total_label = profile.get("total_label")
            rate_label = profile.get("rate_label")
            # ``value``/``value_label`` remain the legacy primary column;
            # Pro consumers use the explicit total/rate/sort fields below.
            value_label = legacy_value_labels.get(
                suffix,
                rate_label if profile.get("rate_key") else total_label,
            )
            for row in rows:
                metrics = row.get("metrics", {})
                participation_time = (
                    metrics.get("fighttime") or metrics.get("activetime")
                )
                total = (
                    metrics.get(profile.get("total_key"))
                    if profile.get("total_key") else None
                )
                rate = (
                    metrics.get(profile.get("rate_key"))
                    if profile.get("rate_key") else None
                )
                if suffix == "Pull-Skills":
                    total_label = "Pull-skill connected hits"
                    value_label = "Pull-skill connected hits"
                    rate_label = "Connected hits / min"
                    total = sum(
                        value for key, value in metrics.items()
                        if key != "fighttime"
                    )
                    pull_details = row.get("metric_details", {})
                    logged_hit_events = sum(
                        detail.get("logged_hit_events") or 0
                        for key, detail in pull_details.items()
                        if key != "fighttime"
                    )
                    evidence = (
                        casts_by_account.get(
                            str(row.get("account") or "").strip()
                            .lstrip(":").casefold()
                        )
                        or casts_by_name.get(
                            str(row.get("name") or "").strip().casefold()
                        )
                    )
                    cast_metrics = {
                        _metric_key(name): count
                        for name, count in (evidence or {}).get(
                            "skill_casts", {}
                        ).items()
                    }
                    pull_keys = [
                        key for key in metrics if key != "fighttime"
                    ]
                    cast_count = sum(
                        cast_metrics.get(key, 0) for key in pull_keys
                    ) if evidence else None
                    row.update({
                        "pull_skill_connected_hits": total or None,
                        "pull_skill_logged_hit_events": logged_hit_events or None,
                        "pull_skill_connection_rate": (
                            total / logged_hit_events * 100
                            if logged_hit_events else None
                        ),
                        "pull_skill_casts": cast_count,
                        "pull_skill_connected_hits_per_cast": (
                            total / cast_count if cast_count else None
                        ),
                    })
                elif suffix == "Combat-Resurrect":
                    total_label = "Combat Resurrect Healing"
                    value_label = "Resurrection output"
                    rate_label = "Combat Resurrect Healing / sec"
                    total = sum(
                        value for key, value in metrics.items()
                        if "resurrect" in key or "naturesrenewal" in key
                    )
                if rate is None and total is not None and participation_time:
                    if profile.get("derive_rate") or suffix == "Pull-Skills":
                        rate = total / participation_time * (
                            60 if (
                                profile.get("rate_unit") == "per_minute"
                                or suffix == "Pull-Skills"
                            ) else 1
                        )
                    elif suffix == "Combat-Resurrect":
                        rate = total / participation_time
                row.update({
                    "total": total,
                    "rate": rate,
                    "participation_time": participation_time,
                    "fight_count": (
                        int(metrics["numfights"])
                        if metrics.get("numfights") is not None else None
                    ),
                    "value": (
                        rate if profile.get("rate_key") else total
                    ),
                })
            if value_label:
                rows = [row for row in rows if row["value"] is not None]
            best_by_player = {}
            for row in rows:
                player_key = (
                    row.get("account")
                    or f"{row.get('name', '').casefold()}|"
                    f"{row.get('profession', '').casefold()}"
                )
                previous = best_by_player.get(player_key)
                row_order = (
                    row.get("value")
                    if row.get("value") is not None
                    else row.get("participation_time") or 0
                )
                previous_order = (
                    previous.get("value")
                    if previous and previous.get("value") is not None
                    else previous.get("participation_time") or 0
                    if previous else -1
                )
                if previous is None or row_order > previous_order:
                    best_by_player[player_key] = row
            rows = list(best_by_player.values())
            if value_label:
                rows.sort(key=lambda row: row["value"], reverse=True)
            tables.append({
                "stat": stat,
                "source_key": suffix,
                "source_tiddler": t.get("title"),
                "scope": "session",
                "show_as_board": profile.get(
                    "show_as_board", bool(value_label)
                ),
                "show_fights_column": profile.get(
                    "show_fights_column", True
                ),
                "value_label": value_label,
                "metric": {
                    "label": total_label or rate_label or stat,
                    "total_label": total_label,
                    "total_unit": profile.get("total_unit"),
                    "rate_label": rate_label,
                    "rate_unit": (
                        profile.get("rate_unit")
                        or ("per_minute" if suffix == "Pull-Skills" else None)
                        or ("per_second" if suffix == "Combat-Resurrect" else None)
                    ),
                },
                "sort": _sort_metadata(
                    "total" if total_label else ("rate" if rate_label else None),
                    total_label=total_label,
                    rate_label=rate_label,
                    participation=True,
                    fight_count=True,
                ),
                "columns": [
                    {"key": key, "label": _column_label(key)}
                    for key in headers if key
                ],
                "rows": rows,
            })

    stability = _parse_stability_generation(tiddlers)
    if stability:
        tables.append(stability)

    attendance = next(
        (table for table in tables if table.get("source_key") == "Attendance"),
        None,
    )
    attendance_rows = attendance.get("rows", []) if attendance else []
    attendance_by_account = {
        row["account"].casefold(): row
        for row in attendance_rows if row.get("account")
    }
    attendance_by_name = {
        row["name"].casefold(): row
        for row in attendance_rows if row.get("name")
    }
    for table in tables:
        for row in table["rows"]:
            attended = None
            if row.get("account"):
                attended = attendance_by_account.get(row["account"].casefold())
            if attended is None and row.get("name"):
                attended = attendance_by_name.get(row["name"].casefold())
            if attended:
                row["fight_count"] = attended.get("fight_count")
                if row.get("participation_time") is None:
                    row["participation_time"] = attended.get(
                        "participation_time"
                    )
    return tables


def _build_night_mvps(stat_tables):
    """Pick evidence-backed session-total winners; never make a composite."""
    tables = {table.get("source_key"): table for table in stat_tables}
    awards = []

    def add_award(category, source_key, metric_key, metric_label,
                  total_unit="count", rate_unit="per_minute",
                  normalized=False):
        table = tables.get(source_key)
        if not table:
            return
        candidates = []
        for row in table.get("rows", []):
            total = row.get("total") if normalized else row.get(
                "metrics", {}
            ).get(metric_key)
            if total is None:
                continue
            candidates.append((total, row))
        if not candidates:
            return
        total, row = max(candidates, key=lambda item: item[0])
        participation = row.get("participation_time")
        rate = row.get("rate") if normalized else None
        if rate is None and participation:
            rate = total / participation * (
                60 if rate_unit == "per_minute" else 1
            )
        awards.append({
            "category": category,
            "metric_label": metric_label,
            "name": row.get("name"),
            "account": row.get("account"),
            "profession": row.get("profession"),
            "total": total,
            "total_unit": total_unit,
            "rate": rate,
            "rate_unit": rate_unit if rate is not None else None,
            "participation_time": participation,
            "fight_count": row.get("fight_count"),
            "source": {
                "scope": "session",
                "table": table.get("stat"),
                "source_key": source_key,
                "tiddler": table.get("source_tiddler"),
            },
            "evidence": {
                "metric_key": metric_key,
                "metrics": copy.deepcopy(row.get("metrics", {})),
                "total": total,
                "rate": rate,
                "participation_time": participation,
            },
        })

    add_award("Damage", "Damage", "targetdamage", "Damage",
              total_unit="damage", rate_unit="per_second", normalized=True)
    add_award("Healing", "Heal-Stats", "healing", "Healing",
              total_unit="healing", rate_unit="per_second", normalized=True)
    add_award("Cleanses", "Support-Summary", "condicleanse", "Cleanses")
    add_award("Boon Strips", "Support-Summary", "boonstrips", "Boon Strips")
    add_award("Resurrection", "Support-Summary", "resurrects", "Resurrects")
    add_award("Crowd Control", "Offensive-Summary", "appliedcrowdcontrol",
              "Crowd Control")
    add_award("Fight Impact", "Offensive-Summary", "downcontribution",
              "Down Contribution", total_unit="damage",
              rate_unit="per_second")
    add_award("Stability", "Stability-Generation", "totalgen",
              "Stability Generation", total_unit="generation",
              rate_unit="per_second", normalized=True)
    return awards


# ------------------------------------------------------- high scores


def _parse_high_scores(tiddlers):
    t = _find_tiddler(tiddlers, "-High-Scores")
    if not t or not t.get("text"):
        raise ValueError("High-Scores tiddler missing")
    text = t["text"]
    blocks = []
    chunks = re.split(r"<div class=.flex-col.>", text)
    for chunk in chunks[1:]:
        caption = None
        m = re.search(r"''([^']+?)''", chunk)
        if m:
            caption = m.group(1).strip()
        rows = []
        for line in chunk.splitlines():
            if not _is_data_row(line):
                continue
            raw_cells = [c for c in _cells(line) if _plain(c)]
            cells = [_plain(c) for c in raw_cells]
            if not cells:
                continue
            score = None
            score_i = None
            for index in range(len(cells) - 1, -1, -1):
                score = _num(cells[index])
                if score is not None:
                    score_i = index
                    break
            identity_raw = raw_cells[0]
            profession = _prof(identity_raw)
            identity = cells[0]
            if profession and identity.casefold().startswith(profession.casefold()):
                identity = identity[len(profession):].strip()
            fight = None
            fight_match = re.match(r"^(.*?)\s*-(\d+)\s*$", identity)
            if fight_match:
                identity = fight_match.group(1).strip()
                fight = int(fight_match.group(2))
            details = cells[1:score_i] if score_i is not None else cells[1:]
            details = [
                re.sub(r"^\{([^{}]+)\}-\1$", r"\1", detail)
                for detail in details
            ]
            display_cells = [identity]
            if profession:
                display_cells.append(profession)
            if fight is not None:
                display_cells.append(f"Fight {fight}")
            display_cells.extend(details)
            rows.append({
                "cells": display_cells,
                "name": identity,
                "account": (
                    account_match.group(1).strip()
                    if (account_match := _TOOLTIP_RE.search(identity_raw))
                    else None
                ),
                "profession": profession,
                "fight": fight,
                "details": details,
                "score": score,
            })
        if rows:
            blocks.append({"caption": caption, "rows": rows})
    if not blocks:
        raise ValueError("High-Scores has no parseable blocks")
    return {"blocks": blocks}


def _parse_player_skill_damage(tiddlers):
    """Parse exported per-player ``Damage-By-Skill`` tables.

    The combiner only emits these tiddlers for qualifying players. Absence is
    therefore "not exported", never zero damage. Repeated display names from
    distinct skill IDs are combined because the source no longer exposes an
    operator-meaningful distinction between them.
    """
    tag = _session_tag(tiddlers)
    prefix = f"{tag}-Damage-By-Skill-" if tag else "-Damage-By-Skill-"
    players = []
    caption_re = re.compile(
        r"\|\{\{\s*([^}]+?)\s*\}\}\s*-\s*(.*?)\s*-\s*([^|]+?)\s*\|c"
    )
    for tiddler in tiddlers:
        title = tiddler.get("title", "")
        if (tag and not title.startswith(prefix)) or (
            not tag and prefix not in title
        ):
            continue
        text = tiddler.get("text") or ""
        identity = caption_re.search(text)
        if not identity:
            continue
        profession, name, account = (
            value.strip() for value in identity.groups()
        )
        lines = text.splitlines()
        header = next(
            (
                line for line in lines
                if _is_header_row(line) and "skill" in line.lower()
            ),
            "",
        )
        headers = _header_keys(header)
        if not headers:
            continue
        positions = {key: index for index, key in enumerate(headers) if key}
        combined = {}
        for line in lines:
            if not _is_data_row(line):
                continue
            cells = [_plain(cell) for cell in _cells(line)]
            skill_index = positions.get("skillname")
            if skill_index is None or skill_index >= len(cells):
                continue
            skill = re.sub(r"^\{[^}]*\}-", "", cells[skill_index]).strip()
            if not skill:
                continue

            def number(key):
                index = positions.get(key)
                return _num(cells[index]) if index is not None and index < len(cells) else None

            row = combined.setdefault(
                skill,
                {
                    "skill": skill,
                    "damage": 0,
                    "down_contribution": 0,
                    "hits": 0,
                    "damage_per_hit": 0,
                    "max_hit": None,
                    "percent_of_total": 0,
                },
            )
            row["damage"] += number("damage") or 0
            row["down_contribution"] += number("downcontrib") or 0
            row["hits"] += number("hits") or 0
            max_hit = number("maxhit")
            if max_hit is not None:
                row["max_hit"] = max(row["max_hit"] or 0, max_hit)
            row["percent_of_total"] += number("oftotal") or 0
        skills = sorted(
            combined.values(), key=lambda row: row["damage"], reverse=True
        )
        for row in skills:
            row["damage_per_hit"] = (
                row["damage"] / row["hits"] if row["hits"] else 0
            )
        if skills:
            players.append(
                {
                    "name": name,
                    "account": account,
                    "profession": profession,
                    "source_tiddler": title,
                    "total_damage": sum(row["damage"] for row in skills),
                    "skills": skills,
                    "coverage_note": (
                        "Per-player skill table exported by the combiner; "
                        "players without a table are not zeroes."
                    ),
                }
            )
    return sorted(players, key=lambda player: player["total_damage"], reverse=True)


# --------------------------------------------------- squad composition


_XTOOLTIP_TEXT_RE = re.compile(
    r'class=.xtooltiptext[^>]*>([^<]+)</span>')


def _parse_squad_composition(tiddlers):
    t = _find_tiddler(tiddlers, "-Squad-Composition")
    if not t or not t.get("text"):
        raise ValueError("Squad-Composition tiddler missing")
    lines = t["text"].splitlines()
    squads = []
    current = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|c"):
            m = re.match(r"\|Fight - (\d+) *\|c$", stripped)
            if m:
                current = {"fight": int(m.group(1)), "players": []}
                squads.append(current)
            continue
        if not _is_data_row(line) or current is None:
            continue
        cells = _cells(line)
        if len(cells) < 3 or not re.match(r"^\d+$", cells[1].strip()):
            continue
        party = int(cells[1].strip())
        slot = 0
        for c in cells[2:]:
            if not c.strip():
                continue
            slot += 1
            prof = _prof(c)
            m = _XTOOLTIP_TEXT_RE.search(c)
            name = m.group(1).strip() if m else _plain(c)
            if name:
                current["players"].append({
                    "profession": prof,
                    "name": name,
                    "party": party,
                    "slot": slot,
                })
    if not squads:
        raise ValueError("Squad-Composition has no squads")
    return {"squads": squads}


# ------------------------------------------------------------------


def build_night_model(
    tiddlers,
    selected_fights=None,
    enemy_role_evidence=None,
    player_skill_evidence=None,
):
    """Parse a combiner tiddler store into one typed night model dict.

    ``tiddlers`` is the JSON list of tiddler dicts (the night summary
    store). Never raises for a bad section -- failed sections come back
    as defaults with an entry in ``warnings``.
    """
    tiddlers = [t for t in tiddlers if isinstance(t, dict)]
    warnings = []
    model = {
        "schema_version": SCHEMA_VERSION,
        "session": None,
        "totals": None,
        "fights": [],
        "leaderboards": [],
        "stat_tables": [],
        "night_mvps": [],
        "high_scores": None,
        "player_skill_damage": [],
        "player_skill_evidence": copy.deepcopy(player_skill_evidence),
        "squad_composition": None,
        "poison": [],
        "pro_navigation": copy.deepcopy(PRO_NAVIGATION),
        "enemy_intel": None,
        "warnings": warnings,
    }

    sections = (
        ("totals", _parse_totals, None),
        ("fights", _parse_fights, []),
        ("leaderboards", _parse_explicit_boards, []),
        (
            "stat_tables",
            lambda source: _parse_stat_tables(source, player_skill_evidence),
            [],
        ),
        (
            "session",
            lambda source: _parse_session(source, model["stat_tables"]),
            None,
        ),
        ("high_scores", _parse_high_scores, None),
        ("player_skill_damage", _parse_player_skill_damage, []),
        ("squad_composition", _parse_squad_composition, None),
    )
    for name, fn, default in sections:
        try:
            model[name] = fn(copy.deepcopy(tiddlers)) or default
        except Exception as exc:  # noqa: no bad sections, just warnings
            warnings.append(f"{name}: {type(exc).__name__}: {exc}")
            model[name] = default

    try:
        model["poison"] = [
            dict(asdict(r), apps_per_min=r.apps_per_min)
            for r in poison_tab.build_model(tiddlers)
        ]
    except Exception as exc:  # noqa
        warnings.append(f"poison: {type(exc).__name__}: {exc}")
        model["poison"] = []

    model["night_mvps"] = _build_night_mvps(model["stat_tables"])

    try:
        reported_fights = (
            model["totals"].get("fights") if model["totals"] else None
        )
        model["enemy_intel"] = build_enemy_intel(
            copy.deepcopy(tiddlers),
            copy.deepcopy(model["fights"]),
            selected_fights=selected_fights,
            reported_fights=reported_fights,
            actor_role_evidence=enemy_role_evidence,
        )
    except Exception as exc:  # noqa
        warnings.append(f"enemy_intel: {type(exc).__name__}: {exc}")
        model["enemy_intel"] = {
            "coverage": {
                "selected_fights": selected_fights,
                "reported_fights": 0,
                "modeled_fights": len(model["fights"]),
                "composition_fights": 0,
                "composition_snapshots": 0,
                "colors": [],
                "sources": {},
            },
            "scopes": [],
            "fights": [],
            "all": {"mode": "comparison_only", "estimated_subgroups": None,
                    "comparisons": []},
            "session_pressure": {},
            "methodology": {},
            "ai_analysis": None,
        }

    return model
