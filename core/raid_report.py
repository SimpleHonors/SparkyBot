"""Raid Report pipeline orchestrator — glue log discovery → combiner → bake."""

import json
import logging
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from core.raid_session import (
    LogInfo,
    discover_logs,
    current_session,
    today_logs,
    recent_logs,
    plan_report,
    RECENT_WINDOW_HOURS,
)
from core.report_bake import merge_augmented_tiddlers, summarize_tiddlers
from core.report_viewer import convert_report_file
from core.enemy_role_evidence import (
    collect_report_evidence,
)

logger = logging.getLogger(__name__)


@dataclass
class ReportResult:
    name: str
    html_path: Path
    json_path: Path
    fight_count: int
    span: str
    failed_count: int = 0
    failed_names: tuple = ()
    generated_date: date = field(default_factory=date.today)


class RaidReportCancelled(Exception):
    pass


def _sanitize_filename(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9 \-\.\(\)]", "_", name)
    safe = re.sub(r"_+", "_", safe)
    safe = safe.strip("_")
    return safe


class RaidReportRunner:

    def __init__(self, *,
                 log_folder: Path,
                 cache,                       # RaidReportCache
                 parse_log,                   # callable(Path) -> Path | None
                 ei_version: str,
                 settings_fingerprint: str,
                 combiner,                    # CombinerManager-compatible
                 viewer_html: Path,
                 output_dir: Path,
                 guild_name: str = "",
                 guild_id: str = "",
                 api_key: str = "",
                 progress=None,               # callable(stage, done, total, msg)
                 cancelled=None,             # callable() -> bool
                 augment_json=None,         # callable(Path) -> dict | None
                 viewer_factory=None,      # callable() -> Path (lazy resolve)
                 report_default_view="sparky",
                 parse_concurrency=4):
        self.log_folder = Path(log_folder)
        self.cache = cache
        self.parse_log = parse_log
        self.ei_version = ei_version
        self.settings_fingerprint = settings_fingerprint
        self.combiner = combiner
        self.viewer_html = Path(viewer_html)
        self.output_dir = Path(output_dir)
        self.guild_name = guild_name
        self.guild_id = guild_id
        self.api_key = api_key
        self._progress = progress
        self._cancelled = cancelled
        self.augment_json = augment_json
        self._viewer_factory = viewer_factory
        self.report_default_view = report_default_view
        self.parse_concurrency = max(1, min(int(parse_concurrency), 8))

    def _emit(self, stage: str, done: int, total: int, msg: str = ""):
        if self._progress:
            self._progress(stage, done, total, msg)

    def _check_cancelled(self):
        if self._cancelled and self._cancelled():
            raise RaidReportCancelled()

    def _parse_missing_logs(self, logs: list[LogInfo], *,
                            total_selected: int,
                            done_already: int) -> tuple[list[Path], list[str]]:
        """Parse cache misses concurrently and preserve selection order."""
        stored_by_index: dict[int, Path] = {}
        failed_indexes: set[int] = set()

        def parse_one(index_and_log):
            index, log = index_and_log
            result = self.parse_log(log.path)
            if not isinstance(result, Path):
                return index, log, None
            stored = self.cache.store(
                log.path, result, self.ei_version,
                self.settings_fingerprint,
            )
            return index, log, stored

        worker_count = min(self.parse_concurrency, len(logs))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="raid-report-parse",
        ) as executor:
            futures = {
                executor.submit(parse_one, item): item
                for item in enumerate(logs)
            }
            completed = 0
            for future in as_completed(futures):
                self._check_cancelled()
                index, log, stored = future.result()
                completed += 1
                self._emit(
                    "parse", done_already + completed, total_selected,
                    f"Parsing {log.path.stem}",
                )
                if stored is None:
                    logger.warning("Failed to parse log: %s", log.path)
                    failed_indexes.add(index)
                else:
                    stored_by_index[index] = stored

        stored_paths = [
            stored_by_index[index]
            for index in range(len(logs))
            if index in stored_by_index
        ]
        failed_names = [
            logs[index].path.stem
            for index in range(len(logs))
            if index in failed_indexes
        ]
        return stored_paths, failed_names

    def select(self, mode: str = "recent") -> list[LogInfo]:
        """Return logs for the requested selection mode.

        Product modes:
          "recent" — rolling window (last RECENT_WINDOW_HOURS h); default.
          "today"  — local calendar day, 00:00:00–23:59:59.
        Also accepts "session" (gap-based clustering) for API stability.
        """
        logs = discover_logs(self.log_folder)
        if mode == "recent":
            return recent_logs(logs)
        elif mode == "today":
            return today_logs(logs)
        elif mode == "session":
            return current_session(logs)
        else:
            raise ValueError(f"Unknown select mode: {mode!r}")

    def default_name(self, selected: list[LogInfo]) -> str:
        return (
            f"Raid Report {selected[0].timestamp:%Y-%m-%d} "
            f"({len(selected)} fights)"
        )

    def generate(self, selected: list[LogInfo],
                 report_name: str | None = None,
                 work_dir: Path | None = None,
                 progress=None) -> ReportResult:
        if not selected:
            raise ValueError("No logs selected")

        self._check_cancelled()

        orig_progress = self._progress
        if progress is not None:
            self._progress = progress
        try:
            return self._generate_inner(selected, report_name, work_dir)
        finally:
            if progress is not None:
                self._progress = orig_progress

    def _generate_inner(self, selected: list[LogInfo],
                        report_name: str | None = None,
                        work_dir: Path | None = None) -> ReportResult:
        hits, to_parse = plan_report(selected, self.cache,
                                     self.ei_version,
                                     self.settings_fingerprint)
        self._emit("plan", len(hits), len(selected))

        json_paths: list[Path] = [p for _, p in hits]
        total_selected = len(selected)
        failed_names: list[str] = []

        if to_parse:
            done_already = len(hits)
            parsed_paths, parse_failures = self._parse_missing_logs(
                to_parse,
                total_selected=total_selected,
                done_already=done_already,
            )
            json_paths.extend(parsed_paths)
            failed_names.extend(parse_failures)

        if not json_paths:
            raise RuntimeError(
                f"All {total_selected} selected logs failed to parse — "
                f"nothing to combine"
            )

        self._check_cancelled()

        if work_dir is None:
            # mkdtemp needs the parent to already exist; the default
            # output dir lives under %TEMP% and isn't created until a
            # report is written (WinError 3 on fresh session temp).
            self.output_dir.mkdir(parents=True, exist_ok=True)
            input_dir = Path(tempfile.mkdtemp(dir=str(self.output_dir)))
            cleanup_temp = True
        else:
            input_dir = Path(work_dir)
            cleanup_temp = False
        input_dir.mkdir(parents=True, exist_ok=True)

        self._emit("collect", 0, len(json_paths))
        for index, jp in enumerate(json_paths, start=1):
            shutil.copy2(jp, input_dir)
            self._emit("collect", index, len(json_paths))

        self._check_cancelled()

        run_dir = self.output_dir / "combiner_run"
        try:
            self.combiner.ensure_installed()
        except Exception:
            from core.combiner_manager import CombinerNotInstalled
            logger.error("Combiner install failed", exc_info=True)
            raise RuntimeError(
                "SparkyBot needs to download or update its stats builder \u2014 "
                "check your internet connection and try again."
            )
        if self._viewer_factory:
            self._emit("resolve", 0, 1, "Resolving stats viewer...")
            self.viewer_html = self._viewer_factory()
            self._emit("resolve", 1, 1, "Stats viewer ready")
        self.combiner.write_run_config(
            run_dir, input_dir,
            self.guild_name, self.guild_id, self.api_key,
        )
        self._emit("combine", 0, 1)
        dragdrop_json = self.combiner.run(
            input_dir,
            run_dir,
            standalone_html_template=self.viewer_html,
        )
        standalone_html = dragdrop_json.with_suffix(".html")
        self._emit("combine", 1, 1)

        self._check_cancelled()
        if self.augment_json:
            self._emit("augment", 0, 1)
            try:
                original_tiddlers = json.loads(
                    dragdrop_json.read_text(encoding="utf-8")
                )
                self.augment_json(dragdrop_json)
                augmented_tiddlers = json.loads(
                    dragdrop_json.read_text(encoding="utf-8")
                )
                merge_augmented_tiddlers(
                    standalone_html,
                    original_tiddlers,
                    augmented_tiddlers,
                )
            except Exception:
                logger.warning(
                    "Poison augmentation failed — "
                    "report will ship unaugmented",
                    exc_info=True,
                )
            self._emit("augment", 1, 1)

        self._check_cancelled()
        self._emit("view", 0, 1, "Building report views...")
        try:
            enemy_role_evidence, player_skill_evidence = (
                collect_report_evidence(json_paths)
            )
            current_tiddlers = json.loads(
                dragdrop_json.read_text(encoding="utf-8")
            )
            if not isinstance(current_tiddlers, list):
                raise ValueError("combined report data is not a list")
            convert_report_file(
                standalone_html,
                current_tiddlers,
                default_view=self.report_default_view,
                enemy_role_evidence=enemy_role_evidence,
                player_skill_evidence=player_skill_evidence,
            )
            self._emit("view", 1, 1, "Report views ready")
        except Exception as exc:
            logger.error("Report viewer build failed", exc_info=True)
            raise RuntimeError(
                "SparkyBot could not build the Classic, Simple, and Sparky "
                "report views."
            ) from exc

        if report_name is not None and report_name.strip():
            name = report_name.strip()
        else:
            name = self.default_name(selected)

        safe = _sanitize_filename(name)

        self._check_cancelled()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        html_path = self.output_dir / f"{safe}.html"
        shutil.copy2(standalone_html, html_path)
        summary = summarize_tiddlers(dragdrop_json)
        json_path_out = self.output_dir / f"{safe}.json"
        shutil.copy2(dragdrop_json, json_path_out)

        self._emit("bake", 1, 1)

        if cleanup_temp:
            try:
                shutil.rmtree(input_dir, ignore_errors=True)
            except Exception:
                pass

        return ReportResult(
            name=name,
            html_path=html_path,
            json_path=json_path_out,
            fight_count=summary["fight_count"],
            span=summary["span"],
            failed_count=len(failed_names),
            failed_names=tuple(failed_names),
        )


_FIGHT_TIME_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+-\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})"
)
_DURATION_PART_RE = re.compile(
    r"(?:(?P<hours>\d+)h\s*)?(?:(?P<minutes>\d+)m\s*)?"
    r"(?:(?P<seconds>\d+)s)?"
)


def _fight_datetime(label: str) -> datetime | None:
    match = _FIGHT_TIME_RE.search(label or "")
    if not match:
        return None
    try:
        return datetime.strptime(
            f"{match.group('date')} {match.group('time')}",
            "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        return None


def _duration_seconds(value: str | None) -> int:
    match = _DURATION_PART_RE.search(value or "")
    if not match:
        return 0
    return (
        int(match.group("hours") or 0) * 3600
        + int(match.group("minutes") or 0) * 60
        + int(match.group("seconds") or 0)
    )


def _short_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m" + (f" {seconds}s" if minutes < 10 and seconds else "")
    return f"{seconds}s"


def _run_window(fights: list[dict]) -> tuple[str | None, str | None]:
    timed = [
        (stamp, fight)
        for fight in fights
        if (stamp := _fight_datetime(str(fight.get("time_label", ""))))
        is not None
    ]
    if not timed:
        return None, None
    start = timed[0][0]
    last_start, last_fight = timed[-1]
    end = last_start + timedelta(
        seconds=_duration_seconds(str(last_fight.get("duration", "")))
    )
    start_clock = start.strftime("%I:%M %p").lstrip("0")
    end_clock = end.strftime("%I:%M %p").lstrip("0")
    if start.date() == end.date():
        window = f"{start_clock}\u2013{end_clock}"
    else:
        window = (
            f"{start:%b} {start.day}, {start_clock}\u2013"
            f"{end:%b} {end.day}, {end_clock}"
        )
    return window, _short_duration(int((end - start).total_seconds()))


def make_publish_embed(result: ReportResult) -> dict:
    """Build a compact nightly overview; a bad stats file never blocks upload."""
    from core.night_model import build_night_model

    model: dict = {}
    try:
        tiddlers = json.loads(result.json_path.read_text(encoding="utf-8"))
        if isinstance(tiddlers, list):
            model = build_night_model(tiddlers)
    except Exception:
        logger.warning("Nightly Discord summary could not read report data", exc_info=True)

    display_name = re.sub(
        r"\s+\(\d+\s+fights?\)\s*$", "", result.name, flags=re.IGNORECASE
    )
    session = model.get("session") or {}
    totals = model.get("totals") or {}
    fights = model.get("fights") or []
    fight_count = int(totals.get("fights") or result.fight_count)

    run_lines = [f"**{fight_count}** {'fight' if fight_count == 1 else 'fights'}"]
    window, elapsed = _run_window(fights)
    if window:
        run_lines.append(window)
    if elapsed:
        run_lines.append(f"{elapsed} elapsed")
    combat_seconds = _duration_seconds(session.get("total_duration"))
    if combat_seconds:
        run_lines.append(f"{_short_duration(combat_seconds)} in combat")

    fields = [{"name": "Run", "value": "\n".join(run_lines), "inline": True}]
    if totals:
        fields.extend(
            (
                {
                    "name": "Enemies",
                    "value": (
                        f"**{int(totals.get('enemy_kills') or 0):,}** killed\n"
                        f"**{int(totals.get('enemy_downs') or 0):,}** downed\n"
                        f"**{float(totals.get('kdr') or 0):.2f}** K/D"
                    ),
                    "inline": True,
                },
                {
                    "name": "Squad losses",
                    "value": (
                        f"**{int(totals.get('ally_deaths') or 0):,}** killed\n"
                        f"**{int(totals.get('ally_downs') or 0):,}** downed"
                    ),
                    "inline": True,
                },
            )
        )

    commander = session.get("commander")
    description = f"Commanded by **{commander}**" if commander else None
    return {
        "title": f"\U0001f4ca {display_name}",
        "description": description,
        "fields": fields,
        "footer": {"text": "Full Sparky \u2022 Simple \u2022 Classic report follows"},
        "color": 0x5865F2,
    }
