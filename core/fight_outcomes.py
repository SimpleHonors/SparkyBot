"""Per-fight comparisons from selected Elite Insights JSON documents."""

from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

VERSION = 1

PAST_FIGHTS = {
    "stab_at_worst_hit": {"high": 76, "high_won_pct": 77, "low": 40, "low_won_pct": 23},
    "opening_trade": {"high": 2.6, "high_won_pct": 68, "low": 0.44, "low_won_pct": 22},
    "first_down": {"enemy_won_pct": 54, "us_won_pct": 22},
    "cc_received_per_member_min": {"low": 0.5, "low_won_pct": 80, "high": 2.2, "high_won_pct": 6},
    "stuns_broken_pct": {"high": 61, "high_won_pct": 81, "low": 33, "low_won_pct": 28},
    "downs_to_kills_pct": {"high": 74, "high_won_pct": 87, "low": 32, "low_won_pct": 0},
}

_STAB, _PROT, _AEGIS = 1122, 717, 743
_WHOLE_BOONS = {
    "stability": _STAB, "protection": _PROT, "aegis": _AEGIS,
    "fury": 725, "regeneration": 718, "might": 740,
}
_AVG_KEYS = (
    "stab_at_worst_hit", "opening_trade", "cc_received_per_member_min",
    "stuns_broken_pct", "downs_to_kills_pct", "enemies_per_member",
)


def _num(value, default=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if isinstance(value, float) and not math.isfinite(value):
        return default
    return value


def _dict(value):
    return value if isinstance(value, dict) else {}


def _first(value):
    if isinstance(value, list) and value:
        return value[0]
    return None


def _def0(actor):
    return _dict(_first(_dict(actor).get("defenses")))


def _is_squad(player):
    return (isinstance(player, dict) and not player.get("notInSquad")
            and not player.get("isFake") and not player.get("friendlyNPC"))


def _intervals(actor, key):
    """Return clean [start, end] ms intervals from combatReplayData."""
    raw = _dict(_dict(actor).get("combatReplayData")).get(key)
    out = []
    if isinstance(raw, list):
        for iv in raw:
            if (isinstance(iv, (list, tuple)) and len(iv) >= 2
                    and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in iv[:2])):
                out.append((iv[0], iv[1]))
    return out


def _series(actor, key):
    arr = _first(_dict(actor).get(key))
    if isinstance(arr, list):
        return [_num(x) for x in arr]
    return []


def _cum(arr, t):
    if not arr:
        return 0
    return arr[min(max(int(t), 0), len(arr) - 1)]


def _states(actor, buff_id):
    for entry in _dict(actor).get("buffUptimes") or []:
        if isinstance(entry, dict) and entry.get("id") == buff_id:
            out = []
            for st in entry.get("states") or []:
                if (isinstance(st, (list, tuple)) and len(st) >= 2
                        and isinstance(st[0], (int, float)) and isinstance(st[1], (int, float))):
                    out.append((st[0], st[1]))
            return out
    return []


def _stacks_at(states, t):
    value = 0
    for s, x in states:
        if s > t:
            break
        value = x
    return value


def _in_any(intervals, t):
    return any(a <= t < b for a, b in intervals)


def _boon_share(players, buff_id, start_ms, window_ms=3000, step=250):
    """Mean share (0-100) of alive, not-downed players holding the buff."""
    prepared = []
    for p in players:
        prepared.append((_states(p, buff_id), _intervals(p, "dead"), _intervals(p, "down")))
    shares = []
    t = start_ms
    while t < start_ms + window_ms:
        alive = have = 0
        for states, dead, down in prepared:
            if _in_any(dead, t) or _in_any(down, t):
                continue
            alive += 1
            if _stacks_at(states, t) > 0:
                have += 1
        if alive:
            shares.append(100.0 * have / alive)
        t += step
    return sum(shares) / len(shares) if shares else None


def _first_down_ms(actors):
    times = [a for actor in actors for a, _b in _intervals(actor, "down") if a >= 0]
    return min(times) if times else math.inf


def _ratio(num, den, scale=1.0):
    return num / den * scale if den else None


def _timestamp(value):
    # Accept EI's literal local/offset clock forms, never arbitrary labels or trees.
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,6})?(?: ?(?:Z|[+-][0-9]{2}(?::?[0-9]{2})?))?", value
    ):
        return None
    try:
        datetime.fromisoformat(value.replace(" +", "+").replace(" -", "-").replace(" Z", "Z"))
    except ValueError:
        return None
    return value


def _analyse(data):
    players = [p for p in data.get("players") or [] if _is_squad(p)]
    enemies = [t for t in data.get("targets") or [] if isinstance(t, dict) and t.get("enemyPlayer")]
    n = len(players)
    dur_ms = _num(data.get("durationMS"))
    dur = dur_ms / 1000.0
    fight = {
        "duration_s": dur,
        "squad": n,
        "enemies": len(enemies),
        "enemies_per_member": len(enemies) / n if n else 0.0,
    }
    start = _timestamp(data.get("timeStartStd")) or _timestamp(data.get("timeStart"))
    end = _timestamp(data.get("timeEnd"))
    if start:
        fight["time_start"] = start
    if end:
        fight["time_end"] = end
    our_deaths = sum(int(_num(_def0(p).get("deadCount"))) for p in players)
    enemy_deaths = sum(len(_intervals(e, "dead")) for e in enemies)
    fight["our_deaths"] = our_deaths
    fight["enemy_deaths"] = enemy_deaths
    if enemy_deaths > our_deaths:
        fight["result"] = "win"
    elif enemy_deaths < our_deaths:
        fight["result"] = "loss"
    else:
        fight["result"] = "even" if enemy_deaths > 0 else "none"

    # Per-second squad damage taken and dealt (cumulative arrays).
    seconds = int(dur) + 1
    taken_arrs = [_series(p, "damageTaken1S") for p in players]
    dealt_arrs = [_series(p, "damage1S") for p in players]
    taken = [sum(_cum(a, s) for a in taken_arrs) for s in range(seconds)]
    dealt = [sum(_cum(a, s) for a in dealt_arrs) for s in range(seconds)]

    worst = None
    if n and seconds > 1 and any(taken_arrs):
        best = -1
        for s in range(1, seconds):
            v = taken[s] - taken[s - 1]
            if v > best:
                best, worst = v, s
        if best <= 0:
            worst = None
    fight["worst_hit_s"] = worst
    for key, bid in (("stab_at_worst_hit", _STAB), ("prot_at_worst_hit", _PROT),
                     ("aegis_at_worst_hit", _AEGIS)):
        fight[key] = _boon_share(players, bid, worst * 1000) if worst is not None else None
    parties = defaultdict(list)
    for p in players:
        group = p.get("group")
        if type(group) is int and group > 0:
            parties[group].append(p)
    fight["party_stab_at_worst_hit"] = {}
    if worst is not None:
        for group, members in parties.items():
            val = _boon_share(members, _STAB, worst * 1000)
            if val is not None:
                fight["party_stab_at_worst_hit"][group] = val

    # Contact and opening trade.
    contact = None
    if n:
        for s in range(1, len(taken)):
            if taken[s] - taken[s - 1] >= 200 * n:
                contact = s
                break
    fight["contact_s"] = contact
    fight["opening_trade"] = None
    fight["opening_our_downs"] = 0
    fight["opening_enemy_downs"] = 0
    if contact is not None:
        end = min(contact + 15, len(taken) - 1)
        fight["opening_trade"] = _ratio(dealt[end] - dealt[contact - 1],
                                        taken[end] - taken[contact - 1])
        lo, hi = contact * 1000, end * 1000
        fight["opening_our_downs"] = sum(
            1 for p in players for a, _b in _intervals(p, "down") if lo <= a < hi)
        fight["opening_enemy_downs"] = sum(
            1 for e in enemies for a, _b in _intervals(e, "down") if lo <= a < hi)

    ours, theirs = _first_down_ms(players), _first_down_ms(enemies)
    fight["first_down"] = "enemy" if theirs < ours else "us" if ours < theirs else None

    # CC and stun breaks.
    cc = sum(_num(_def0(p).get("receivedCrowdControl")) for p in players)
    sb = sum(_num(_def0(p).get("stunBreak")) for p in players)
    minutes = dur / 60.0
    fight["cc_received_per_member_min"] = cc / n / minutes if n and minutes > 0 else None
    fight["stuns_broken_pct"] = _ratio(sb, cc, 100.0)

    edowns = sum(len(_intervals(e, "down")) for e in enemies)
    fight["enemy_downs"] = edowns
    fight["downs_to_kills_pct"] = _ratio(enemy_deaths, edowns, 100.0)
    against = sum(_num(_dict(_first(p.get("statsAll"))).get("againstDownedDamage")) for p in players)
    total = sum(_num(_dict(_first(p.get("statsAll"))).get("totalDmg")) for p in players)
    fight["downed_damage_pct"] = _ratio(against, total, 100.0)

    boons = {}
    for name, bid in _WHOLE_BOONS.items():
        vals = []
        for p in players:
            uptime = 0
            for entry in p.get("buffUptimesActive") or []:
                if isinstance(entry, dict) and entry.get("id") == bid:
                    data = _dict(_first(entry.get("buffData")))
                    # Stability stacks in intensity: EI's "uptime" is its average
                    # stack count, and "presence" is the share of time it was up.
                    uptime = _num(data.get("presence" if name == "stability" else "uptime"))
                    break
            vals.append(uptime)
        boons[name] = sum(vals) / n if n else 0.0
    fight["boons"] = boons

    cc_by_group = {g: (sum(_num(_def0(p).get("receivedCrowdControl")) for p in m),
                       sum(_num(_def0(p).get("stunBreak")) for p in m), len(m))
                   for g, m in parties.items()}
    return fight, cc_by_group


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _geomean(values):
    values = [v for v in values if v is not None and v > 0]
    if not values:
        return None
    return math.exp(sum(math.log(v) for v in values) / len(values))


def _group_avg(fights):
    out = {}
    for key in _AVG_KEYS:
        vals = [f.get(key) for f in fights]
        out[key] = _geomean(vals) if key == "opening_trade" else _mean(vals)
    out["enemy_down_first_pct"] = (
        100.0 * sum(1 for f in fights if f.get("first_down") == "enemy") / len(fights)
        if fights else None)
    return out


class _FightOutcomesAccumulator:
    """Existing per-file skip and pooled aggregation, without owning EI trees."""

    def __init__(self):
        self.fights = []
        self.party_acc = defaultdict(lambda: {"fights": 0, "stab": [], "cc": 0.0, "timed_cc": 0.0, "members_min": 0.0, "sb": 0.0})

    def feed(self, data, path):
        try:
            if not isinstance(data, dict) or not isinstance(data.get("players"), list):
                return
            fight, cc_by_group = _analyse(data)
        except Exception:  # one bad file must never break the report
            logger.warning("fight_outcomes: skipped %s", path, exc_info=True)
            return
        if not fight["squad"]:
            return
        self.fights.append(fight)
        minutes = fight["duration_s"] / 60.0
        for group, stab in fight["party_stab_at_worst_hit"].items():
            self.party_acc[group]["stab"].append(stab)
        for group, (cc, sb, count) in cc_by_group.items():
            acc = self.party_acc[group]
            acc["fights"] += 1
            acc["cc"] += cc
            acc["sb"] += sb
            if minutes > 0:
                acc["timed_cc"] += cc
                acc["members_min"] += count * minutes

    def finish(self):
        return _finish_fight_outcomes(self.fights, self.party_acc)


def collect_fight_outcomes(json_paths):
    """Return the per-fight outcome model, or None when no fight is usable."""
    accumulator = _FightOutcomesAccumulator()
    seen = set()
    for path in json_paths or []:
        try:
            resolved = Path(path).resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            data = json.loads(resolved.read_text(encoding="utf-8"))
        except Exception:  # one bad file must never break the report
            logger.warning("fight_outcomes: skipped %s", path, exc_info=True)
            continue
        accumulator.feed(data, path)
    return accumulator.finish()


def _finish_fight_outcomes(fights, party_acc):
    if not fights:
        return None
    fights.sort(key=lambda f: str(f.get("time_start") or ""))
    wins = [f for f in fights if f["result"] == "win"]
    losses = [f for f in fights if f["result"] == "loss"]
    total_downs = sum(f["enemy_downs"] for f in fights)
    total_deaths = sum(f["enemy_deaths"] for f in fights)
    parties = []
    for group in sorted(party_acc):
        acc = party_acc[group]
        parties.append({
            "group": group,
            "fights": acc["fights"],
            "stab_at_worst_hit": _mean(acc["stab"]),
            "cc_received_per_member_min": _ratio(acc["timed_cc"], acc["members_min"]),
            "stuns_broken_pct": _ratio(acc["sb"], acc["cc"], 100.0),
        })
    return {
        "version": VERSION,
        "fights": fights,
        "summary": {
            "fights": len(fights),
            "wins": len(wins),
            "losses": len(losses),
            "wins_avg": _group_avg(wins),
            "losses_avg": _group_avg(losses),
            "downs_to_kills_pct": _ratio(total_deaths, total_downs, 100.0),
        },
        "parties": parties,
        "past_fights": PAST_FIGHTS,
    }
