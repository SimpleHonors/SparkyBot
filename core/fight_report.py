"""Fight Report - Parses GW2EI JSON into formatted Discord embeds

Inspired by MzFightReporter's FightReport.java
Field names verified against MzApp-Latest.jar bytecode disassembly.
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from dataclasses import dataclass

from version import VERSION

logger = logging.getLogger(__name__)


@dataclass
class PlayerStats:
    name: str
    profession: str
    account: str
    group: str
    damage: int
    dps: int
    downs: int
    kills: int
    cleanse: int = 0
    healing: int = 0
    downed_healing: int = 0
    barrier: int = 0
    dead: int = 0
    down_time: int = 0
    down_count: int = 0
    outgoing_cc: int = 0
    soft_cc: int = 0
    immob_cc: int = 0
    interrupts: int = 0
    evaded: int = 0
    blocked: int = 0
    invulned: int = 0
    boon_strips: int = 0
    downed_damage: int = 0  # downContribution from statsAll[0]
    stab_uptime: float = 0.0
    aegis_uptime: float = 0.0


@dataclass
class EnemyStats:
    name: str
    profession: str = ""
    team: str = ""
    damage: int = 0
    dps: int = 0
    health: int = 0
    dead: int = 0
    down_count: int = 0
    killed_by: int = 0


@dataclass
class BurstWindow:
    """A single 4-second burst damage window for a player."""
    player_name: str
    profession: str
    time_s: int      # start time in seconds from fight start
    dmg_4s: int      # damage in the 4-second window
    dmg_2s: int      # best 2-second window within the 4-second window


class FightReport:
    """Parses GW2EI JSON and generates formatted strings for Discord"""

    LF = "\n"
    NAME_WIDTH = 20
    TABLE_WIDTH = 52
    EMBED_COLOR = 0x00A86B  # Jade Green default
    AUTHOR_ICON_URL = "https://i.imgur.com/f7t0fAe.png"

    MAP_ICONS = {
        "Eternal": "https://i.imgur.com/eFMK8D4.png",
        "Green": "https://i.imgur.com/vyO4yKd.png",
        "Blue": "https://i.imgur.com/xlg6JZp.png",
        "Red": "https://i.imgur.com/hIq5RuB.png",
        "Edge": "https://i.imgur.com/MFjFSZW.png",
    }
    DEFAULT_ICON = "https://i.imgur.com/B0iKe5d.png"

    PROFESSION_NAMES = {
        # Core professions
        "GUAR": "Guardian", "WARR": "Warrior", "ENGI": "Engineer",
        "RANG": "Ranger", "THIE": "Thief", "ELEM": "Elementalist",
        "MESM": "Mesmer", "NECR": "Necromancer", "REVE": "Revenant",
        # HoT elite specs
        "DRAG": "Dragonhunter", "BERS": "Berserker", "SCRA": "Scrapper",
        "DRUI": "Druid", "DARE": "Daredevil", "TEMP": "Tempest",
        "CHRO": "Chronomancer", "REAP": "Reaper", "HERA": "Herald",
        # PoF elite specs
        "FIRE": "Firebrand", "SPEL": "Spellbreaker", "HOLO": "Holosmith",
        "SOUL": "Soulbeast", "DEAD": "Deadeye", "WEAV": "Weaver",
        "MIRA": "Mirage", "SCOU": "Scourge", "RENE": "Renegade",
        # EoD elite specs
        "WILL": "Willbender", "BLAD": "Bladesworn", "MECH": "Mechanist",
        "UNTA": "Untamed", "SPEC": "Specter", "CATA": "Catalyst",
        "VIRT": "Virtuoso", "HARB": "Harbinger", "VIND": "Vindicator",
        # JW elite specs
        "AMAL": "Amalgam", "COND": "Conduit", "PARA": "Paragon",
        "EVOK": "Evoker", "LUMI": "Luminary", "TROU": "Troubadour",
        "RITU": "Ritualist", "GALE": "Galeshot", "ANTI": "Antiquary",
    }

    def _fmt_table(self, lines: List[str]) -> str:
        """Enforce uniform table width with a ruler at start and end.

        Discord uses the longest line as the code block width, but trailing
        spaces are stripped. A ruler line at top AND bottom forces the
        minimum width regardless of content or trailing-space stripping.
        """
        ruler = "-" * self.TABLE_WIDTH
        return self.LF.join([ruler] + lines + [ruler])

    def _get_full_profession_name(self, abbrev: str) -> str:
        """Convert 4-letter profession abbreviation to full name."""
        return self.PROFESSION_NAMES.get(abbrev, abbrev)

    def __init__(self, json_data: Dict[str, Any]):
        self.data = json_data

        self.zone = self._get_fight_name()
        self.duration = self.data.get('duration', '0s')
        self.duration_ms = self.data.get('durationMS', 0)
        self.total_seconds = self.duration_ms // 1000 if self.duration_ms else 0
        self._allies: List[PlayerStats] = []

        self.players: List[PlayerStats] = self._parse_players()
        self.enemies: List[EnemyStats] = self._parse_enemies()

        self.total_damage = sum(p.damage for p in self.players)
        self.total_downs = sum(p.downs for p in self.players)
        self.total_kills = sum(p.kills for p in self.players)

        self.commander = self._get_commander()
        # JAR uses 'recordedAccountBy' not 'recordedBy'
        self.recorded_by = self.data.get('recordedAccountBy',
                           self.data.get('recordedBy', 'Unknown'))
        self.arc_version = self.data.get('arcVersion', 'Unknown')
        self.ei_version = self.data.get('eliteInsightsVersion', 'Unknown')
        self.end_time = self.data.get('timeEnd', '')
        self.url = ""

    def _get_fight_name(self) -> str:
        name = self.data.get('fightName', 'Unknown Fight')
        if name.startswith('Detailed WvW'):
            return name.replace('Detailed WvW - ', '')
        return name

    def _get_commander(self) -> Optional[str]:
        for p in self.data.get('players', []):
            if p.get('hasCommanderTag', False):
                return p.get('name', None)
        return None

    def _parse_players(self) -> List[PlayerStats]:
        """Parse player stats from JSON.

        Field sources verified against MzApp JAR:
        - damage/dps: statsAll[0].totalDmg / calc (JAR uses targetDamage1S sum,
          but statsAll[0].totalDmg should be equivalent)
        - downContribution: statsAll[0].downContribution
        - interrupts/appliedCrowdControl: statsAll[0]
        - condiCleanse/boonStrips: support[0] (NOT statsAll, NOT defenses)
        - healing: extHealingStats.outgoingHealingAllies[][0].healing (summed)
        - barrier: extBarrierStats.outgoingBarrierAllies[][0].barrier (summed)
        - evadedCount/blockedCount/invulnedCount/downCount/deadCount: defenses[0]
        - soft_cc/immob_cc: extracted from target buffs (buffs[] on enemy targets)
        """
        # Soft CC and immobilize come from target buffs, not statsAll
        IMMOB_ID = 727
        SOFT_CC_IDS = {722, 721, 720, 833, 742, 26766}  # chill, cripple, blind, daze, weakness, slow

        soft_cc_counts: Dict[str, int] = {}
        immob_counts: Dict[str, int] = {}

        for target in self.data.get('targets', []):
            for buff in target.get('buffs', []):
                buff_id = buff.get('id', 0)
                if buff_id != IMMOB_ID and buff_id not in SOFT_CC_IDS:
                    continue
                buff_data = buff.get('buffData', [])
                if not buff_data or not isinstance(buff_data[0], dict):
                    continue
                generated = buff_data[0].get('generated', {})
                if not isinstance(generated, dict):
                    continue
                for player_name, value in generated.items():
                    try:
                        val = int(value) if value else 1
                    except (ValueError, TypeError):
                        val = 1
                    if buff_id == IMMOB_ID:
                        immob_counts[player_name] = immob_counts.get(player_name, 0) + val
                    elif buff_id in SOFT_CC_IDS:
                        soft_cc_counts[player_name] = soft_cc_counts.get(player_name, 0) + val

        players = []
        for p in self.data.get('players', []):
            is_ally = p.get('notInSquad', False)

            stats_all = p.get('statsAll', [{}])
            if not stats_all:
                continue
            stats = stats_all[0] if isinstance(stats_all[0], dict) else {}
            logger.debug(f"statsAll[0] keys for {p.get('name')}: {list(stats.keys())}")

            defenses = p.get('defenses', [{}])
            def_data = defenses[0] if defenses else {}
            if not isinstance(def_data, dict):
                def_data = {}

            # Cleanses and strips come from support[0], not statsAll
            support = p.get('support', [{}])
            sup_data = support[0] if support else {}
            if not isinstance(sup_data, dict):
                sup_data = {}

            cleanse = (sup_data.get('condiCleanse', 0) +
                       sup_data.get('condiCleanseSelf', 0))
            boon_strips = sup_data.get('boonStrips', 0)

            # Heals from extHealingStats.outgoingHealingAllies
            healing = 0
            downed_healing = 0
            ext_heal = p.get('extHealingStats')
            if ext_heal and isinstance(ext_heal, dict):
                heal_allies = ext_heal.get('outgoingHealingAllies', [])
                for entry in heal_allies:
                    if entry and isinstance(entry, list) and len(entry) > 0:
                        h = entry[0]
                        if isinstance(h, dict):
                            healing += h.get('healing', 0)
                            downed_healing += h.get('downedHealing', 0)

            # Barrier from extBarrierStats.outgoingBarrierAllies
            barrier = 0
            ext_barrier = p.get('extBarrierStats')
            if ext_barrier and isinstance(ext_barrier, dict):
                barrier_allies = ext_barrier.get('outgoingBarrierAllies', [])
                for entry in barrier_allies:
                    if entry and isinstance(entry, list) and len(entry) > 0:
                        b = entry[0]
                        if isinstance(b, dict):
                            barrier += b.get('barrier', 0)

            stab_uptime = 0.0
            aegis_uptime = 0.0
            for buff in p.get('buffUptimes', []):
                if not isinstance(buff, dict):
                    continue
                bid = buff.get('id', 0)
                if bid in (1122, 743):
                    data = buff.get('buffData', [{}])
                    if data and isinstance(data[0], dict):
                        if bid == 1122:
                            stab_uptime = data[0].get('uptime', 0.0)
                        elif bid == 743:
                            aegis_uptime = data[0].get('uptime', 0.0)

            damage = stats.get('totalDmg', 0)
            dps = self._calc_dps(damage)

            # CC fields — hard_cc from statsAll, soft_cc/immob_cc from target buffs
            hard_cc = stats.get('appliedCrowdControl', 0)
            player_name = p.get('name', '')
            soft_cc = soft_cc_counts.get(player_name, 0)
            immob_cc = immob_counts.get(player_name, 0)

            stats_obj = PlayerStats(
                name=p.get('name', 'Unknown'),
                profession=p.get('profession', 'Unknown'),
                account=p.get('account', ''),
                group=str(p.get('group', 1)),
                damage=damage,
                dps=dps,
                downs=stats.get('downed', 0),
                kills=stats.get('killed', 0),
                cleanse=cleanse,
                healing=healing,
                downed_healing=downed_healing,
                barrier=barrier,
                dead=def_data.get('deadCount', 0),
                down_time=def_data.get('downDuration', 0),
                down_count=def_data.get('downCount', 0),
                outgoing_cc=hard_cc,
                soft_cc=soft_cc,
                immob_cc=immob_cc,
                interrupts=stats.get('interrupts', 0),
                evaded=def_data.get('evadedCount', 0),
                blocked=def_data.get('blockedCount', 0),
                invulned=def_data.get('invulnedCount', 0),
                boon_strips=boon_strips,
                downed_damage=stats.get('downContribution', 0),
                stab_uptime=stab_uptime,
                aegis_uptime=aegis_uptime,
            )

            if is_ally:
                self._allies.append(stats_obj)
            else:
                players.append(stats_obj)
        return players

    def _parse_enemies(self) -> List[EnemyStats]:
        enemies = []
        for t in self.data.get('targets', []):
            if t.get('name', '').startswith('Dummy'):
                continue
            logger.debug(
                f"Target teamID={t.get('teamID')} name={t.get('name', '')[:20]} "
                f"profession={repr(t.get('profession'))}"
            )
            # Read from dpsAll[0] like the JAR does for targets
            dps_all = t.get('dpsAll', [{}])
            dps_data = dps_all[0] if dps_all else {}
            if not isinstance(dps_data, dict):
                dps_data = {}

            defenses = t.get('defenses', [{}])
            def_data = defenses[0] if defenses else {}
            if not isinstance(def_data, dict):
                def_data = {}

            # Determine team from teamID
            team_id = t.get('teamID')
            team = self._map_team_id(team_id) if team_id is not None else "Enemy"

            # Profession from name if notInSquad target has None/empty profession
            raw_name = t.get('name', '')
            prof_from_name = raw_name.split(' ')[0] if ' ' in raw_name else raw_name
            profession = t.get('profession') or prof_from_name

            enemy = EnemyStats(
                name=raw_name or 'Unknown',
                profession=profession,
                team=team,
                damage=dps_data.get('damage', 0),
                dps=dps_data.get('dps', 0),
                health=t.get('health', 0) or 0,
                dead=def_data.get('deadCount', 0),
                down_count=def_data.get('downCount', 0),
            )

            stats_all = t.get('statsAll', [{}])
            if stats_all:
                s = stats_all[0]
                if isinstance(s, dict) and s.get('killed', False):
                    enemy.killed_by = 1

            enemies.append(enemy)
        return enemies

    def _map_team_id(self, team_id: int) -> str:
        """Map GW2 WvW team IDs to color names.

        Values sourced from MzFightReporter's ParseBot.mapTeamID().
        WvW and Guild Hall IDs both included.
        """
        RED = {697, 705, 706, 707, 882, 885, 2520}
        GREEN = {39, 2739, 2741, 2752, 2763, 2767}
        BLUE = {432, 433, 1277, 1989}

        if team_id in RED:
            return "Red"
        if team_id in GREEN:
            return "Green"
        if team_id in BLUE:
            return "Blue"

        logger.warning(f"Unmapped teamID {team_id} — update _map_team_id")
        return "Enemy"

    def _calc_dps(self, damage: int) -> int:
        if self.total_seconds > 0:
            return damage // self.total_seconds
        return 0

    def _format_duration(self) -> str:
        """Format duration with milliseconds to match old bot format."""
        ms = self.duration_ms
        total_sec = ms // 1000
        rem_ms = ms % 1000
        if ms >= 3600000:
            hours = total_sec // 3600
            mins = (total_sec % 3600) // 60
            secs = total_sec % 60
            return f"{hours:02d}h {mins:02d}m {secs:02d}s {rem_ms:03d}ms"
        elif ms >= 60000:
            mins = total_sec // 60
            secs = total_sec % 60
            return f"{mins:02d}m {secs:02d}s {rem_ms:03d}ms"
        else:
            return f"{total_sec:02d}s {rem_ms:03d}ms"

    def _fmt_num(self, n: int) -> str:
        """Format number with k/m suffix matching old bot's withSuffix logic.

        Old bot thresholds (from DPSer.toString bytecode):
        - >= 10,000,000: show as Xm with 1 decimal  (e.g. 10.5m)
        - >= 1,000,000:  show as X.XXm               (e.g. 1.22m)
        - >= 10,000:     show as XXXk no decimal      (e.g. 103k)
        - >= 1,000:      show as X.Xk                 (e.g. 7.6k)
        - < 1,000:       plain integer
        """
        n = max(0, n)
        if n >= 10_000_000:
            return f"{n/1_000_000:.1f}m"
        if n >= 1_000_000:
            return f"{n/1_000_000:.2f}m"
        if n >= 10_000:
            return f"{n/1_000:.0f}k"
        if n >= 1_000:
            return f"{n/1_000:.1f}k"
        return str(n)

    # === Squad / Enemy Summaries ===

    def _get_squad_team_from_zone(self) -> str:
        zone = self.zone.lower()
        if 'red' in zone:
            return "Red"
        if 'blue' in zone:
            return "Blue"
        if 'green' in zone or 'alpine' in zone:
            return "Green"
        if 'eternal' in zone or 'edge' in zone:
            return "Green"
        return "Green"

    def get_squad_summary(self) -> str:
        """Squad overview matching old bot's Players/Dmg/DPS/Downs/Deaths table."""
        total_dps = self._calc_dps(self.total_damage)
        total_dead = sum(p.dead for p in self.players)
        squad_size = len(self.players)
        ally_count = len(self._allies)
        squad_team = self._get_squad_team_from_zone()

        lines = [
            f"{'Players':<12} {'Dmg':>6} {'DPS':>6} {'Downs':>6} {'Deaths':>6}",
            f"{(str(squad_size) + ' ' + squad_team):<12} "
            f"{self._fmt_num(self.total_damage):>6} "
            f"{self._fmt_num(total_dps):>6} "
            f"{self.total_downs:>6} "
            f"{total_dead:>6}",
        ]

        if ally_count > 0:
            ally_team = squad_team
            lines.append(f"{ally_count} {ally_team} Allies")

        return self._fmt_table(lines)

    def get_enemy_summary(self) -> str:
        """Enemy summary grouped by team matching old bot format."""
        teams: Dict[str, List[EnemyStats]] = {}
        for e in self.enemies:
            team = e.team or "Enemy"
            teams.setdefault(team, []).append(e)

        lines = [
            f"{'Players':<12} {'Dmg':>6} {'DPS':>6} {'Downs':>6} {'Deaths':>6}",
        ]

        for team_name, team_enemies in teams.items():
            count = len(team_enemies)
            total_dmg = sum(e.damage for e in team_enemies)
            total_dps = total_dmg // self.total_seconds if self.total_seconds else 0
            total_downs = sum(e.down_count for e in team_enemies)
            total_dead = sum(e.dead for e in team_enemies)
            lines.append(
                f"{(str(count) + ' ' + team_name):<12} "
                f"{self._fmt_num(total_dmg):>6} "
                f"{self._fmt_num(total_dps):>6} "
                f"{total_downs:>6} "
                f"{total_dead:>6}"
            )

        return self._fmt_table(lines) if len(lines) > 1 else "No enemy data"

    def get_damage(self) -> str:
        """Top 10 damage dealers with profession, DPS and downed contribution."""
        if not self.players:
            return ""

        sorted_players = sorted(self.players, key=lambda p: p.damage, reverse=True)[:10]
        lines = [
            f" #  {'Player':<{self.NAME_WIDTH}} {'Prof':<4} {'Dmg':>6} {'DPS':>5} {'DownC':>5}",
        ]
        for i, p in enumerate(sorted_players, 1):
            if p.damage > 0:
                prof = p.profession[:4].upper()
                lines.append(
                    f"{i:>2}  {p.name[:self.NAME_WIDTH]:<{self.NAME_WIDTH}} {prof:<4} "
                    f"{self._fmt_num(p.damage):>6} {self._fmt_num(p.dps):>5} "
                    f"{self._fmt_num(p.downed_damage):>5}"
                )
        return self._fmt_table(lines)

    def _parse_burst_windows(self) -> List[BurstWindow]:
        """Parse burst windows from targetDamage1S.

        targetDamage1S is a per-player array of per-target cumulative damage arrays.
        The old bot sums across all targets to get a single cumulative list, then
        computes sliding 4-second windows: dmg_4s = cumulative[i] - cumulative[i-4].
        The 2s window is the best consecutive 2s within each 4s window.
        Windows within 3 seconds of a higher-scoring window are zeroed out.
        """
        windows = []
        for p in self.data.get('players', []):
            if p.get('notInSquad', False):
                continue
            name = p.get('name', 'Unknown')
            profession = p.get('profession', '')

            target_dmg_1s = p.get('targetDamage1S', [])
            if not target_dmg_1s:
                continue

            # Sum across all targets to get combined cumulative list
            combined: List[int] = []
            for target_arr in target_dmg_1s:
                if not target_arr or not isinstance(target_arr, list):
                    continue
                for phase_arr in target_arr:
                    if not phase_arr or not isinstance(phase_arr, list):
                        continue
                    if not combined:
                        combined = list(phase_arr)
                    else:
                        for idx in range(min(len(combined), len(phase_arr))):
                            combined[idx] += phase_arr[idx]

            if not combined or len(combined) < 5:
                continue

            # Compute 4s windows starting at index 4
            player_windows: List[BurstWindow] = []
            window_map: Dict[int, BurstWindow] = {}

            for i in range(4, len(combined)):
                dmg_4s = combined[i] - combined[i - 4]
                if dmg_4s <= 0:
                    continue

                # Best 2s window within this 4s range
                dmg_2s = 0
                for j in range(i - 4, i - 1):
                    if j + 2 < len(combined):
                        candidate = combined[j + 2] - combined[j]
                        if candidate > dmg_2s:
                            dmg_2s = candidate

                start_time = i - 4
                bw = BurstWindow(
                    player_name=name,
                    profession=profession,
                    time_s=start_time,
                    dmg_4s=dmg_4s,
                    dmg_2s=dmg_2s,
                )
                player_windows.append(bw)
                window_map[start_time] = bw

            # Zero out overlapping windows (within 3 seconds of a higher-burst window)
            player_windows.sort(key=lambda w: w.dmg_4s, reverse=True)
            for bw in player_windows:
                if bw.dmg_4s > 0:
                    for offset in (1, 2, 3):
                        neighbour = window_map.get(bw.time_s + offset)
                        if neighbour:
                            neighbour.dmg_4s = 0

            windows.extend(player_windows)

        return windows

    def get_bursters(self) -> str:
        """Top burst windows across all players, sorted by 4s damage."""
        all_windows = self._parse_burst_windows()
        top = sorted(
            [w for w in all_windows if w.dmg_4s > 0],
            key=lambda w: w.dmg_4s, reverse=True
        )[:10]

        if not top:
            return ""

        lines = [
            f" #  {'Player':<{self.NAME_WIDTH}} {'Prof':<4} {'4sec':>5} {'2sec':>5} {'Time':>5}",
        ]
        for i, w in enumerate(top, 1):
            mins = w.time_s // 60
            secs = w.time_s % 60
            time_str = f"{mins}:{secs:02d}"
            prof = w.profession[:4].upper()
            lines.append(
                f"{i:>2}  {w.player_name[:self.NAME_WIDTH]:<{self.NAME_WIDTH}} {prof:<4} "
                f"{self._fmt_num(w.dmg_4s):>5} {self._fmt_num(w.dmg_2s):>5} {time_str:>5}"
            )
        return self._fmt_table(lines)

    def get_strips(self) -> str:
        """Top boon strippers. Source: support[0].boonStrips"""
        strippers = [p for p in self.players if p.boon_strips > 0]
        if not strippers:
            return ""

        sorted_s = sorted(strippers, key=lambda p: p.boon_strips, reverse=True)[:10]

        lines = [
            f" #  {'Player':<{self.NAME_WIDTH}} {'Prof':<4} {'Total':>5}  {'SPS':>4}",
        ]
        for i, p in enumerate(sorted_s, 1):
            sps = p.boon_strips / self.total_seconds if self.total_seconds else 0
            prof = p.profession[:4].upper()
            lines.append(
                f"{i:>2}  {p.name[:self.NAME_WIDTH]:<{self.NAME_WIDTH}} {prof:<4} "
                f"{self._fmt_num(p.boon_strips):>5}  {sps:>4.2f}"
            )
        return self._fmt_table(lines)

    def get_cleanses(self) -> str:
        """Top condition cleansers. Source: support[0].condiCleanse + condiCleanseSelf"""
        cleansers = [p for p in self.players if p.cleanse > 0]
        if not cleansers:
            return ""

        sorted_c = sorted(cleansers, key=lambda p: p.cleanse, reverse=True)[:10]

        lines = [
            f" #  {'Player':<{self.NAME_WIDTH}} {'Prof':<4} {'Total':>5}  {'CPS':>4}",
        ]
        for i, p in enumerate(sorted_c, 1):
            cps = p.cleanse / self.total_seconds if self.total_seconds else 0
            prof = p.profession[:4].upper()
            lines.append(
                f"{i:>2}  {p.name[:self.NAME_WIDTH]:<{self.NAME_WIDTH}} {prof:<4} "
                f"{self._fmt_num(p.cleanse):>5}  {cps:>4.2f}"
            )
        return self._fmt_table(lines)

    def get_healers(self) -> str:
        sections = []

        # Table 1: Heals
        healers = sorted([p for p in self.players if p.healing > 0],
                         key=lambda p: p.healing, reverse=True)[:5]
        if healers:
            lines = [
                f" #  {'Player':<{self.NAME_WIDTH}} {'Prof':<4} {'Heals':>6}  {'HPS':>5}",
            ]
            for i, p in enumerate(healers, 1):
                hps = p.healing / self.total_seconds if self.total_seconds else 0
                lines.append(
                    f"{i:>2}  {p.name[:self.NAME_WIDTH]:<{self.NAME_WIDTH}} {p.profession[:4].upper():<4} "
                    f"{self._fmt_num(p.healing):>6}  {self._fmt_num(int(hps)):>5}"
                )
            sections.append(self._fmt_table(lines))

        # Table 2: Barrier
        barriers = sorted([p for p in self.players if p.barrier > 0],
                         key=lambda p: p.barrier, reverse=True)[:5]
        if barriers:
            lines = [
                f" #  {'Player':<{self.NAME_WIDTH}} {'Prof':<4} {'Barrier':>7}  {'BPS':>5}",
            ]
            for i, p in enumerate(barriers, 1):
                bps = p.barrier / self.total_seconds if self.total_seconds else 0
                lines.append(
                    f"{i:>2}  {p.name[:self.NAME_WIDTH]:<{self.NAME_WIDTH}} {p.profession[:4].upper():<4} "
                    f"{self._fmt_num(p.barrier):>7}  {self._fmt_num(int(bps)):>5}"
                )
            sections.append(self._fmt_table(lines))

        # Table 3: Downed Heals
        downed = sorted([p for p in self.players if p.downed_healing > 0],
                        key=lambda p: p.downed_healing, reverse=True)[:5]
        if downed:
            lines = [
                f" #  {'Player':<{self.NAME_WIDTH}} {'Prof':<4} {'D-Heals':>7}  {'HPS':>5}",
            ]
            for i, p in enumerate(downed, 1):
                hps = p.downed_healing / self.total_seconds if self.total_seconds else 0
                lines.append(
                    f"{i:>2}  {p.name[:self.NAME_WIDTH]:<{self.NAME_WIDTH}} {p.profession[:4].upper():<4} "
                    f"{self._fmt_num(p.downed_healing):>7}  {self._fmt_num(int(hps)):>5}"
                )
            sections.append(self._fmt_table(lines))

        return (self.LF + self.LF).join(sections)

    def get_defense(self) -> str:
        """Defense stats - deaths, downs, evades, blocks, invulns."""
        if not self.players:
            return ""

        sorted_p = sorted(
            self.players, key=lambda p: p.dead + p.down_count, reverse=True
        )[:10]

        lines = [
            f" #  {'Player':<{self.NAME_WIDTH}} {'Prof':<4} {'Invuln':>6} {'Evade':>5} {'Block':>5}",
        ]
        for i, p in enumerate(sorted_p, 1):
            prof = p.profession[:4].upper()
            lines.append(
                f"{i:>2}  {p.name[:self.NAME_WIDTH]:<{self.NAME_WIDTH}} {prof:<4} "
                f"{p.invulned:>6} {p.evaded:>5} {p.blocked:>5}"
            )
        return self._fmt_table(lines)

    # Boon IDs and short names
    DEFENSIVE_BOONS = [
        (1122, "Stab"), (743, "Aegi"), (717, "Prot"),
        (26980, "Resi"), (873, "Reso"), (30328, "Alac"), (1187, "Quik"),
    ]
    OFFENSIVE_BOONS = [
        (740, "Might"), (725, "Fury"), (726, "Vigor"),
        (719, "Swift"), (30328, "Alac"), (1187, "Quick"),
    ]

    def _get_boon_uptime_by_party(self, boon_list: list) -> str:
        groups: Dict[str, Dict[int, float]] = {}
        group_counts: Dict[str, int] = {}
        for p in self.data.get('players', []):
            if p.get('notInSquad', False):
                continue
            group = str(p.get('group', 1))
            groups.setdefault(group, {bid: 0.0 for bid, _ in boon_list})
            group_counts.setdefault(group, 0)
            group_counts[group] += 1
            for boon_entry in p.get('buffUptimes', []):
                if not isinstance(boon_entry, dict):
                    continue
                bid = boon_entry.get('id', 0)
                data = boon_entry.get('buffData', [{}])
                if data and isinstance(data[0], dict) and bid in groups[group]:
                    groups[group][bid] += data[0].get('uptime', 0)
        if not groups:
            return ""
        short_names = [s for _, s in boon_list]
        lines = [
            f" # {' '.join(f'{s:>5}' for s in short_names)}",
        ]
        for group_id in sorted(groups.keys()):
            count = group_counts.get(group_id, 0)
            if count == 0:
                continue
            row = f"{group_id:>2} " + " ".join(
                f"{int(groups[group_id][bid] / count):>5}" for bid, _ in boon_list
            )
            lines.append(row)
        return self._fmt_table(lines)

    def get_defensive_boons(self) -> str:
        return self._get_boon_uptime_by_party(self.DEFENSIVE_BOONS)

    def get_offensive_boons(self) -> str:
        return self._get_boon_uptime_by_party(self.OFFENSIVE_BOONS)

    def get_ccs(self) -> str:
        """Outgoing CC and interrupts."""
        ccs = [p for p in self.players if p.outgoing_cc > 0 or p.interrupts > 0]
        if not ccs:
            return ""

        sorted_c = sorted(ccs, key=lambda p: p.outgoing_cc + p.interrupts, reverse=True)[:10]

        lines = [
            f" #  {'Player':<{self.NAME_WIDTH}} {'Prof':<4} {'Hard':>4} {'Soft':>4} {'Immob':>5} {'Int':>3}",
        ]
        for i, p in enumerate(sorted_c, 1):
            prof = p.profession[:4].upper()
            lines.append(
                f"{i:>2}  {p.name[:self.NAME_WIDTH]:<{self.NAME_WIDTH}} {prof:<4} "
                f"{p.outgoing_cc:>4} {p.soft_cc:>4} {p.immob_cc:>5} {p.interrupts:>3}"
            )
        return self._fmt_table(lines)

    def get_downs_kills(self) -> str:
        """Outgoing downs and kills."""
        if not self.players:
            return ""

        sorted_p = sorted(self.players, key=lambda p: p.downs + p.kills, reverse=True)[:10]

        lines = [
            f" #  {'Player':<{self.NAME_WIDTH}} {'Prof':<4} {'Downs':>5} {'Kills':>5}",
        ]
        for i, p in enumerate(sorted_p, 1):
            prof = p.profession[:4].upper()
            lines.append(
                f"{i:>2}  {p.name[:self.NAME_WIDTH]:<{self.NAME_WIDTH}} {prof:<4} "
                f"{p.downs:>5} {p.kills:>5}"
            )
        return self._fmt_table(lines)

    def get_enemy_top_skills(self) -> str:
        skill_map = self.data.get('skillMap', {})
        skill_totals: Dict[int, int] = {}

        for t in self.data.get('targets', []):
            dist = t.get('totalDamageDist', [])
            if not dist:
                continue
            phase_dist = dist[0] if dist else []
            if not isinstance(phase_dist, list):
                continue
            if skill_totals == {} and phase_dist:
                logger.debug(f"totalDamageDist entry keys: {list(phase_dist[0].keys()) if phase_dist else []}")
            for entry in phase_dist:
                if not isinstance(entry, dict):
                    continue
                skill_id = entry.get('id', 0)
                dmg = entry.get('totalDamage', 0)
                skill_totals[skill_id] = skill_totals.get(skill_id, 0) + dmg

        if not skill_totals:
            return ""

        top = sorted(skill_totals.items(), key=lambda x: x[1], reverse=True)[:10]

        lines = [
            f" #  {'Skill':<21} {'Dmg':>6}",
        ]
        for i, (skill_id, dmg) in enumerate(top, 1):
            skill_key = f"s{skill_id}"
            skill_info = skill_map.get(skill_key, {})
            skill_name = (skill_info.get('name', f'Skill {skill_id}')
                          if isinstance(skill_info, dict) else f'Skill {skill_id}')
            lines.append(f"{i:>2}  {skill_name[:21]:<21} {self._fmt_num(dmg):>6}")

        return self._fmt_table(lines)

    def get_enemy_breakdown(self) -> str:
        teams: Dict[str, Dict[str, Dict]] = {}

        for t in self.data.get('targets', []):
            if t.get('name', '').startswith('Dummy'):
                continue
            team_id = t.get('teamID')
            team = self._map_team_id(team_id) if team_id is not None else "Enemy"
            raw_name = t.get('name', '')
            prof_from_name = raw_name.split(' ')[0] if ' ' in raw_name else raw_name
            prof_full = t.get('profession') or prof_from_name
            prof = prof_full[:4].upper() if prof_full else 'UNKN'

            dps_all = t.get('dpsAll', [{}])
            dps_data = dps_all[0] if dps_all and isinstance(dps_all[0], dict) else {}
            dmg = dps_data.get('damage', 0)

            teams.setdefault(team, {})
            if prof not in teams[team]:
                teams[team][prof] = {'count': 0, 'dmg': 0}
            teams[team][prof]['count'] += 1
            teams[team][prof]['dmg'] += dmg

        if not teams:
            return ""

        lines = [
            f" #  {'Profession':<13}  {'Dmg':<6}   #  {'Profession':<13}  {'Dmg':<6}",
        ]

        for team_name, profs in teams.items():
            total_count = sum(v['count'] for v in profs.values())
            lines.append(f">>> {team_name}: {total_count}")
            sorted_profs = sorted(profs.items(), key=lambda x: x[1]['count'], reverse=True)
            mid = (len(sorted_profs) + 1) // 2
            left = sorted_profs[:mid]
            right = sorted_profs[mid:]
            for j in range(len(left)):
                lp, ld = left[j]
                lc = ld['count']
                ldmg = self._fmt_num(ld['dmg'])
                lp_name = self._get_full_profession_name(lp)
                if j < len(right):
                    rp, rd = right[j]
                    rc = rd['count']
                    rdmg = self._fmt_num(rd['dmg'])
                    rp_name = self._get_full_profession_name(rp)
                    lines.append(
                        f"{lc:>2}  {lp_name:<13}  {ldmg:<6}   {rc:>2}  {rp_name:<13}  {rdmg:<6}"
                    )
                else:
                    lines.append(f"{lc:>2}  {lp_name:<13}  {ldmg:<6}")

        return self._fmt_table(lines)

    def get_overview(self) -> str:
        """Quick overview placed at top of report for at-a-glance summary."""
        squad_dead = sum(p.dead for p in self.players)
        ally_count = len(self._allies)
        allies_str = f" (+{ally_count} allies)" if ally_count > 0 else ""

        enemy_count = len(self.enemies)
        enemy_dead = sum(e.dead for e in self.enemies)
        squad_downed_by_enemy = sum(p.down_count for p in self.players)

        # KDR: squad kills / squad deaths (avoid div by zero)
        squad_kdr = self.total_kills / squad_dead if squad_dead > 0 else float(self.total_kills)

        lines = [
            f"KDR: {squad_kdr:.2f}   Duration: {self._format_duration()}",
            f"Squad: {len(self.players)} players{allies_str}",
            f"  Downs: {self.total_downs:>4}   Kills: {self.total_kills:>4}   Deaths: {squad_dead:>4}",
            f"Enemy: {enemy_count} players",
            f"  Downs: {squad_downed_by_enemy:>4}   Kills: {squad_dead:>4}   Deaths: {enemy_dead:>4}",
        ]
        return self._fmt_table(lines)

    def get_twitch_summary(self) -> str:
        """Return a plain-text one-liner suitable for Twitch chat (no code blocks, no formatting)."""
        squad_dead = sum(p.dead for p in self.players)
        enemy_dead = sum(e.dead for e in self.enemies)
        ally_count = len(self._allies)

        squad_dmg = self._fmt_num(self.total_damage)
        enemy_dmg = self._fmt_num(sum(e.damage for e in self.enemies))

        summary = (
            f"[Report] Squad: {len(self.players)}"
            f" (Dmg: {squad_dmg}, Down/Dead: {self.total_downs}/{squad_dead})"
        )
        if ally_count > 0:
            summary += f" +{ally_count} Allies"
        summary += (
            f" | Enemy: {len(self.enemies)}"
            f" (Dmg: {enemy_dmg}, Down/Dead: {sum(e.down_count for e in self.enemies)}/{enemy_dead})"
        )
        return summary

    def get_map_icon(self) -> str:
        for map_key, icon_url in self.MAP_ICONS.items():
            if self.zone.startswith(map_key):
                return icon_url
        return self.DEFAULT_ICON

    def set_embed_color(self, color_int: int):
        """Override the embed accent color (Discord embed sidebar color)."""
        self.EMBED_COLOR = color_int

    def get_discord_embeds(self, display_config, icon_filename: str = None) -> List[Dict]:
        """Generate Discord embeds matching MzFightReporter format."""
        embeds = []

        # Author icon (upper-left circle) — always the hosted SparkyBot icon
        author_icon_url = self.AUTHOR_ICON_URL

        # Thumbnail (upper-right) — user-selected guild icon from local file
        if icon_filename:
            thumbnail_url = f"attachment://{Path(icon_filename).name}"
        else:
            thumbnail_url = self.get_map_icon()  # fallback to map icon

        # Intro embed — metadata + quick report as a field
        intro_lines = []
        if self.commander:
            intro_lines.append(f"**Commander**: {self.commander}")
        intro_lines.append(f"**Time**: {self.end_time}")
        intro_lines.append(f"**Recorded By**: {self.recorded_by}")
        intro_lines.append(f"**ArcDps version**: {self.arc_version}")
        intro_lines.append(f"**Elite Insights version**: {self.ei_version}")

        intro = {
            "color": self.EMBED_COLOR,
            "title": "Full Report",
            "author": {
                "name": self.zone,
                "icon_url": author_icon_url,
            },
            "thumbnail": {"url": thumbnail_url},
            "description": "\n".join(intro_lines),
            "footer": {"text": f"SparkyBot v{VERSION}"}
        }
        embeds.append(intro)

        # Build field list
        fields = []

        if display_config.get('showQuickReport', True):
            content = self.get_overview()
            if content:
                fields.append({"name": "Quick Report", "value": f"```\n{content}\n```", "inline": False})

        section_configs = [
            ('showSquadSummary', 'Squad Summary', self.get_squad_summary),
            ('showEnemySummary', 'Enemy Summary', self.get_enemy_summary),
            ('showDamage', 'Damage & Down Contribution', self.get_damage),
            ('showBurstDmg', 'Burst Damage', self.get_bursters),
            ('showStrips', 'Strips', self.get_strips),
            ('showCleanses', 'Cleanses', self.get_cleanses),
            ('showHeals', 'Heals & Barrier (heal addon required)', self.get_healers),
            ('showDefense', 'Defense', self.get_defense),
            ('showCCs', 'Outgoing CCs & Interrupts', self.get_ccs),
            ('showDownsKills', 'Outgoing Downs & Kills', self.get_downs_kills),
            ('showDefensiveBoons', 'Defensive Boon Uptime by Party', self.get_defensive_boons),
            ('showOffensiveBoons', 'Offensive Boon Uptime by Party', self.get_offensive_boons),
            ('showTopEnemySkills', 'Enemy Top Damage By Skill', self.get_enemy_top_skills),
            ('showEnemyBreakdown', 'Enemy Breakdown', self.get_enemy_breakdown),
        ]

        for config_key, title, getter in section_configs:
            if display_config.get(config_key, True):
                content = getter()
                if content:
                    # Discord field value max is 1024 chars
                    value = f"```\n{content}\n```"
                    if len(value) > 1024:
                        value = f"```\n{content[:1016]}\n```"
                    fields.append({"name": title, "value": value, "inline": False})

        # Split fields into chunks of 4 to stay under Discord's 6000 char embed limit
        FIELDS_PER_EMBED = 4
        for i in range(0, len(fields), FIELDS_PER_EMBED):
            chunk = fields[i:i + FIELDS_PER_EMBED]
            embeds.append({
                "color": self.EMBED_COLOR,
                "fields": chunk,
            })

        return embeds

    def get_ai_summary(self) -> Dict[str, Any]:
        """Export fight data as a structured dict for AI analysis."""
        squad_dead = sum(p.dead for p in self.players)
        ally_dead = sum(a.dead for a in self._allies)
        ally_damage = sum(a.damage for a in self._allies)
        squad_kdr = self.total_kills / squad_dead if squad_dead > 0 else float(self.total_kills)
        enemy_dead = sum(e.dead for e in self.enemies)

        # Determine outcome explicitly
        if squad_kdr >= 2.0 and squad_dead < enemy_dead:
            outcome = "Decisive Win"
        elif self.total_kills > squad_dead:
            outcome = "Win"
        elif self.total_kills == squad_dead:
            outcome = "Draw"
        elif squad_kdr >= 0.5:
            outcome = "Loss"
        else:
            outcome = "Decisive Loss"

        top_damage = sorted(self.players, key=lambda p: p.damage, reverse=True)[:5]
        top_cleanses = sorted([p for p in self.players if p.cleanse > 0], key=lambda p: p.cleanse, reverse=True)[:3]
        top_strips = sorted([p for p in self.players if p.boon_strips > 0], key=lambda p: p.boon_strips, reverse=True)[:3]
        top_healers = sorted([p for p in self.players if p.healing > 0], key=lambda p: p.healing, reverse=True)[:3]

        # Aggregate squad stats for WvW-specific metrics
        total_strips = sum(p.boon_strips for p in self.players)
        total_cleanses = sum(p.cleanse for p in self.players)
        total_healing = sum(p.healing for p in self.players)
        total_barrier = sum(p.barrier for p in self.players)

        enemy_profs = {}
        for e in self.enemies:
            abbrev = (e.profession or "Unknown")[:4].upper()
            full_name = self._get_full_profession_name(abbrev)
            if full_name not in enemy_profs:
                enemy_profs[full_name] = {"count": 0, "damage": 0}
            enemy_profs[full_name]["count"] += 1
            enemy_profs[full_name]["damage"] += e.damage

        top_enemy_profs = dict(sorted(enemy_profs.items(), key=lambda item: item[1]["damage"], reverse=True)[:5])

        enemy_total_damage = sum(e.damage for e in self.enemies)

        friendly_count = len(self.players) + len(self._allies)
        is_outnumbered = friendly_count < len(self.enemies)
        squad_outdamaged_enemy = self.total_damage > enemy_total_damage

        enemy_teams = {}
        for e in self.enemies:
            team = e.team or "Unknown"
            enemy_teams[team] = enemy_teams.get(team, 0) + 1

        # Parse Burst Windows
        all_bursts = self._parse_burst_windows()
        top_bursts = sorted([w for w in all_bursts if w.dmg_4s > 0], key=lambda w: w.dmg_4s, reverse=True)[:3]

        # Parse CC and Interrupts
        top_cc = sorted([p for p in self.players if (p.outgoing_cc + p.interrupts) > 0], key=lambda p: (p.outgoing_cc + p.interrupts), reverse=True)[:3]

        # Parse Enemy Top Skills
        skill_map = self.data.get('skillMap', {})
        skill_totals: Dict[int, int] = {}
        for t in self.data.get('targets', []):
            dist = t.get('totalDamageDist', [])
            if not dist:
                continue
            phase_dist = dist[0] if dist else []
            if not isinstance(phase_dist, list):
                continue
            for entry in phase_dist:
                if not isinstance(entry, dict):
                    continue
                skill_id = entry.get('id', 0)
                dmg = entry.get('totalDamage', 0)
                skill_totals[skill_id] = skill_totals.get(skill_id, 0) + dmg

        top_enemy_skills = []
        for skill_id, dmg in sorted(skill_totals.items(), key=lambda x: x[1], reverse=True)[:10]:
            if len(top_enemy_skills) >= 5:
                break
            skill_key = f"s{skill_id}"
            skill_info = skill_map.get(skill_key, {})
            skill_name = skill_info.get('name', f'Skill {skill_id}') if isinstance(skill_info, dict) else f'Skill {skill_id}'

            # Skip unmapped ArcDPS skills
            if skill_name.startswith("Skill "):
                continue

            top_enemy_skills.append({"name": skill_name, "damage": dmg})

        # Calculate outliers — players who beat second place by at least 1.5x
        def get_outlier(items, key_func, min_val):
            valid = [x for x in items if key_func(x) >= min_val]
            if not valid:
                return None
            sorted_items = sorted(valid, key=key_func, reverse=True)
            name_attr = getattr(sorted_items[0], 'name', getattr(sorted_items[0], 'player_name', 'Unknown'))
            if len(sorted_items) == 1:
                return {"name": name_attr, "value": key_func(sorted_items[0])}
            if key_func(sorted_items[0]) >= key_func(sorted_items[1]) * 1.5:
                return {"name": name_attr, "value": key_func(sorted_items[0])}
            return None

        outliers = {}
        if val := get_outlier(self.players, lambda p: p.downed_damage, 50000): outliers["down_contribution"] = {**val, "unit": "down damage"}
        if val := get_outlier(self.players, lambda p: p.boon_strips, 20): outliers["boon_strips"] = {**val, "action": "boons stripped"}
        if val := get_outlier(self.players, lambda p: p.downs, 5): outliers["outgoing_downs"] = {**val, "unit": "downs"}
        if val := get_outlier(self.players, lambda p: p.kills, 5): outliers["outgoing_kills"] = {**val, "unit": "kills"}
        if val := get_outlier(self.players, lambda p: p.cleanse, 50): outliers["cleanses"] = {**val, "unit": "cleanses"}
        if val := get_outlier(self.players, lambda p: p.healing, 100000): outliers["healing"] = {**val, "unit": "healing"}
        if val := get_outlier(self.players, lambda p: p.stab_uptime, 20.0): outliers["stability_uptime"] = {**val, "unit": "stability uptime"}
        if val := get_outlier(self.players, lambda p: p.aegis_uptime, 5.0): outliers["aegis_uptime"] = {**val, "unit": "aegis uptime"}

        all_bursts = self._parse_burst_windows()
        if val := get_outlier(all_bursts, lambda w: w.dmg_4s, 20000): outliers["burst_damage_4s"] = {**val, "unit": "burst damage (4s)"}

        return {
            "outliers": outliers,
            "outcome": outcome,
            "zone": self.zone,
            "commander": self.commander,
            "duration": self._format_duration(),
            "duration_seconds": self.total_seconds,
            "kdr": round(squad_kdr, 2),
            "squad_count": len(self.players),
            "ally_count": len(self._allies),
            "friendly_count": friendly_count,
            "is_outnumbered": is_outnumbered,
            "squad_outdamaged_enemy": squad_outdamaged_enemy,
            "squad_damage": self.total_damage,
            "squad_dps": self._calc_dps(self.total_damage),
            "squad_downs": self.total_downs,
            "squad_kills": self.total_kills,
            "squad_deaths": squad_dead,
            "squad_strips": total_strips,
            "squad_cleanses": total_cleanses,
            "squad_healing": total_healing,
            "squad_barrier": total_barrier,
            "enemy_count": len(self.enemies),
            "enemy_deaths": enemy_dead,
            "enemy_total_damage": enemy_total_damage,
            "top_damage": [
                {"name": p.name, "profession": self._get_full_profession_name(p.profession[:4].upper()),
                 "damage": p.damage, "downs": p.downs, "kills": p.kills}
                for p in top_damage
            ],
            "top_cleanses": [
                {"name": p.name, "profession": self._get_full_profession_name(p.profession[:4].upper()),
                 "cleanses": p.cleanse}
                for p in top_cleanses
            ],
            "top_strips": [
                {"name": p.name, "profession": self._get_full_profession_name(p.profession[:4].upper()),
                 "strips": p.boon_strips}
                for p in top_strips
            ],
            "top_healers": [
                {"name": p.name, "profession": self._get_full_profession_name(p.profession[:4].upper()),
                 "healing": p.healing}
                for p in top_healers
            ],
            "top_bursts": [
                {"name": w.player_name, "profession": self._get_full_profession_name(w.profession[:4].upper()),
                 "dmg_4s": w.dmg_4s, "time_s": w.time_s}
                for w in top_bursts
            ],
            "top_cc": [
                {"name": p.name, "profession": self._get_full_profession_name(p.profession[:4].upper()),
                 "hard_cc": p.outgoing_cc, "interrupts": p.interrupts}
                for p in top_cc
            ],
            "top_enemy_skills": top_enemy_skills,
            "enemy_breakdown": {
                prof: {
                    "count": data["count"],
                    "damage": data["damage"],
                    "damage_per_player": data["damage"] // data["count"] if data["count"] > 0 else 0,
                }
                for prof, data in top_enemy_profs.items()
            },
            "enemy_teams": enemy_teams,
            "outliers": outliers,
        }
