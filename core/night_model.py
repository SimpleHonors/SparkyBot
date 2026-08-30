"""Night model extractor: combiner tiddler store -> one typed JSON night model.

Parses a GW2-EI log-combiner night store (the JSON list of tiddler dicts
behind the night summary) into a single clean, typed, skin-ready dict.
Never raises for a bad section: every section is guarded and failures are
collected in ``warnings``.

Schema of ``build_night_model(tiddlers) -> dict``::

    {
      "schema_version": 1,
      "session": {                     # session meta
        "tag": str | None,             # e.g. "2026-08-10-20:27:05"
        "date": str | None,            # "YYYY-MM-DD" part of the tag
        "commander": str | None,       # name from the Tag_Stats caption/rows
        "commander_account": str|None, # account from Tag_Stats tooltip
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
         "rows": [{"rank": int|None, "name": str, "account": str|None,
                   "profession": str|None, "value": float|None,
                   "cells": [int|float]}]}
      ],
      "highlights": {                  # headline support boards (operator
                                       # ruling); top-10 per board, sorted
                                       # by value desc, rank 1..N; row
                                       # shape as leaderboards. Keys and
                                       # their source table / metric:
        "cleanses": [rows],            #   Support-Summary  condiCleanse
        "strips": [rows],              #   Support-Summary  boonStrips
        "healing": [rows],             #   Heal-Stats       Healing
        "revives": [rows],             #   Support-Summary  resurrects
        "downs": [rows],               #   Offensive-Summary downed
        "down_contribution": [rows],   #   Offensive-Summary downContribution
        "kills": [rows],               #   Offensive-Summary killed
        "stability_uptime": [rows],    #   Uptimes  Stability (0-100 pct)
      },                               # empty-with-warning only when the
                                       # source table/column is absent
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

SCHEMA_VERSION = 1

_TAG_RE = re.compile(r"(\d{4}-\d{2}-\d{2}-\d{2}:\d{2}:\d{2})")
_IMG_RE = re.compile(r"\[img[^\]]*?\[([^|\]]+)\|[^\]]*?\]\]")
_TOOLTIP_RE = re.compile(r"data-tooltip=['\"]([^'\"]+)['\"]")
_PROF_TIDDEL_RE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z ]*?)\s*\}\}")
_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_PIPE_LINK_RE = re.compile(r"\[\[([^\]|]*)\|([^\]]+)\]\]")
_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?%?$")
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
]

# Headline support boards (operator ruling). Values are top-10 per board,
# sorted by the metric desc; row shape is identical to leaderboards rows.
# Key spellings match the operator ruling verbatim.
HIGHLIGHT_TOP_N = 10
_HIGHLIGHTS_SPEC = (
    ("cleanses",          "-Support-Summary",   "condicleanse"),
    ("strips",            "-Support-Summary",   "boonstrips"),
    ("healing",           "-Heal-Stats",        "healing"),
    ("revives",           "-Support-Summary",   "resurrects"),
    ("downs",             "-Offensive-Summary", "downed"),
    ("down_contribution", "-Offensive-Summary", "downcontribution"),
    ("kills",             "-Offensive-Summary", "killed"),
    ("stability_uptime",  "-Uptimes",           "stability"),
)
_HL_IMG_ALT_RE = re.compile(r"\[img[^\]]*?\[([^|\]]+)\|")


def _norm_header(cell):
    """Normalize a header cell for metric-column matching (img alts
    included): '!{{boonStrips}} %' / '![img ..[Stability|url]..]' ->
    'boonstrips' / 'stability'."""
    m = _HL_IMG_ALT_RE.search(cell)
    name = m.group(1) if m else cell
    return re.sub(r"[^A-Za-z0-9]", "", name).lower()


def _metric_col(header_cells, token):
    for i, c in enumerate(header_cells):
        if _norm_header(c) == token:
            return i
    return None


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


def _find_tiddler(tiddlers, suffix):
    return next((t for t in tiddlers
                 if t.get("title", "").endswith(suffix)), None)


def _table_lines(tiddlers, suffix):
    t = _find_tiddler(tiddlers, suffix)
    return t["text"].splitlines() if t and t.get("text") else None


# ---------------------------------------------------------------- meta


def _parse_session(tiddlers):
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
        caption = (ts.get("caption") or "").strip()
        # Captions like "Tag Summary" carry no name; any richer caption is
        # taken as the commander label. Otherwise fall back to the sole
        # player row of the table (the combined tag is the commander's).
        if caption and caption.lower() not in ("tag summary",
                                               "summary by command tag"):
            out["commander"] = caption
        for line in (ts.get("text") or "").splitlines():
            if not _is_data_row(line):
                continue
            m = _TOOLTIP_RE.search(line)
            if m and "." in m.group(1):
                out["commander_account"] = m.group(1).strip()
                cells = _cells(line)
                names = [_plain(c) for c in cells[1:3] if _plain(c)]
                if names and not out["commander"]:
                    out["commander"] = names[0]
                break

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
    ("duration", "Duration"),
    ("squad", "Squad"),
    ("allies", "Allies"),
    ("enemy", "Enemy"),
    ("rgb", "R/G/B"),
    ("downs", "DownedEnemy"),
    ("kills", "killed"),
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
                 "kills": None, "rallies": None, "damage_out": None,
                 "damage_in": None, "barrier_out": None,
                 "barrier_out_pct": None, "shield_out": None,
                 "shield_out_pct": None, "chart": None}
        for key in ("duration",):
            i = cols.get(key)
            if i is not None and i < len(cells):
                fight[key] = cells[i]
        for key in ("squad", "allies", "enemy", "downs", "kills",
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
        fights.append(fight)
    if not fights:
        raise ValueError("Overview has no fight rows")
    return fights


# ------------------------------------------------------------ boards


def _parse_board_rows(line_iter, ranked=False, value_col=None,
                      require_value=False):
    """Parse wikitext table lines into board rows.

    value_col: take "value" from this split-cell index instead of the
    first numeric cell after the name (used by the highlights boards).
    require_value: drop rows whose value is None.
    """
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
        if ranked and len(raw_cells) > 1:
            rank = _num(raw_cells[1])
        name = None
        name_i = None
        for i, c in enumerate(cells):
            if i == 0 and rank is not None:
                continue
            if c and not _NUM_RE.match(raw_cells[i] if i < len(raw_cells)
                                       else ""):
                if re.search(r"[A-Za-z]", c):
                    name = c
                    name_i = i
                    break
        if name is None:
            continue
        value = None
        nums = []
        if value_col is not None:
            if value_col < len(raw_cells):
                value = _num(raw_cells[value_col])
            if value is not None:
                nums = [value]
        else:
            for i, c in enumerate(raw_cells):
                if i <= (name_i or 0):
                    continue
                v = _num(c)
                if v is not None:
                    nums.append(v)
                    if value is None:
                        value = v
        if require_value and value is None:
            continue
        rows.append({"rank": int(rank) if rank is not None else None,
                     "name": name, "account": account,
                     "profession": profession, "value": value,
                     "cells": nums})
    return rows


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
        rows = _parse_board_rows(text.splitlines(), ranked=True)
        if rows:
            boards.append({"stat": stat, "rows": rows})
    return boards


def _session_tag(tiddlers):
    for t in tiddlers:
        m = _TAG_RE.search(t.get("title", ""))
        if m:
            return m.group(1)
    return None


def _parse_stat_tables(tiddlers):
    """Parse the standard per-night combiner stat tables."""
    tables = []
    tag = _session_tag(tiddlers)
    for suffix in _STAT_TABLE_SUFFIXES:
        exact = f"{tag}-{suffix}" if tag else None
        t = next((x for x in tiddlers if x.get("title", "") == exact),
                 None) if exact else _find_tiddler(tiddlers, "-" + suffix)
        if not t or not t.get("text"):
            continue
        rows = _parse_board_rows(
            l for l in t["text"].splitlines() if not _is_header_row(l))
        if rows:
            stat = (t.get("caption") or suffix.replace("-", " ")).strip()
            tables.append({"stat": stat, "rows": rows})
    return tables


# ------------------------------------------------------------ highlights


def _parse_highlights(tiddlers):
    """Build the eight headline support boards.

    Returns ``(boards, warnings)``. A board is empty WITH a warning only
    when its source table (or metric column) is genuinely absent from the
    store; present-but-blank tables come back empty without a warning.
    """
    boards = {}
    warnings = []
    tag = _session_tag(tiddlers)
    for key, suffix, token in _HIGHLIGHTS_SPEC:
        t = None
        if tag:
            t = next((x for x in tiddlers
                      if x.get("title", "") == tag + suffix), None)
        if not t or not t.get("text"):
            boards[key] = []
            warnings.append(
                f"highlights.{key}: source {suffix.lstrip('-')} tiddler "
                "missing")
            continue
        lines = t["text"].split("\n")
        header = next((l for l in lines if _is_header_row(l)), None)
        col = _metric_col(_cells(header), token) if header else None
        if col is None:
            boards[key] = []
            warnings.append(
                f"highlights.{key}: column '{token}' not found in "
                f"{suffix.lstrip('-')}")
            continue
        rows = _parse_board_rows(lines, value_col=col, require_value=True)
        rows = [r for r in rows
                if not re.search(r"\b(average|totals)\b", r["name"],
                                 re.IGNORECASE)]
        rows.sort(key=lambda r: r["value"], reverse=True)
        top = rows[:HIGHLIGHT_TOP_N]
        for i, r in enumerate(top, 1):
            r["rank"] = i
        boards[key] = top
    return boards, warnings


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
            cells = [_plain(c) for c in _cells(line) if _plain(c)]
            if not cells:
                continue
            score = None
            for c in reversed(cells):
                score = _num(c)
                if score is not None:
                    break
            rows.append({"cells": cells, "score": score})
        if rows:
            blocks.append({"caption": caption, "rows": rows})
    if not blocks:
        raise ValueError("High-Scores has no parseable blocks")
    return {"blocks": blocks}


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
        for c in cells[2:]:
            if not c.strip():
                continue
            prof = _prof(c)
            m = _XTOOLTIP_TEXT_RE.search(c)
            name = m.group(1).strip() if m else _plain(c)
            if name:
                current["players"].append({"profession": prof,
                                            "name": name})
    if not squads:
        raise ValueError("Squad-Composition has no squads")
    return {"squads": squads}


# ------------------------------------------------------------------


def build_night_model(tiddlers):
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
        "highlights": {},
        "high_scores": None,
        "squad_composition": None,
        "poison": [],
        "warnings": warnings,
    }

    sections = (
        ("session", _parse_session, None),
        ("totals", _parse_totals, None),
        ("fights", _parse_fights, []),
        ("leaderboards", _parse_explicit_boards, []),
        ("stat_tables", _parse_stat_tables, []),
        ("high_scores", _parse_high_scores, None),
        ("squad_composition", _parse_squad_composition, None),
    )
    for name, fn, default in sections:
        try:
            model[name] = fn(copy.deepcopy(tiddlers)) or default
        except Exception as exc:  # noqa: no bad sections, just warnings
            warnings.append(f"{name}: {type(exc).__name__}: {exc}")
            model[name] = default

    try:
        boards, hl_warns = _parse_highlights(copy.deepcopy(tiddlers))
        model["highlights"] = boards
        warnings.extend(hl_warns)
    except Exception as exc:  # noqa
        warnings.append(f"highlights: {type(exc).__name__}: {exc}")
        model["highlights"] = {k: [] for k, _, _ in _HIGHLIGHTS_SPEC}

    try:
        model["poison"] = [
            dict(asdict(r), apps_per_min=r.apps_per_min)
            for r in poison_tab.build_model(tiddlers)
        ]
    except Exception as exc:  # noqa
        warnings.append(f"poison: {type(exc).__name__}: {exc}")
        model["poison"] = []

    return model
