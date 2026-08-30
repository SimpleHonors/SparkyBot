"""Raid Report pipeline orchestrator — glue log discovery → combiner → bake."""

import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date
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
from core.report_bake import (
    apply_sticky_table_headers,
    merge_augmented_tiddlers,
    summarize_tiddlers,
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
                 viewer_factory=None):     # callable() -> Path (lazy resolve)
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

    def _emit(self, stage: str, done: int, total: int, msg: str = ""):
        if self._progress:
            self._progress(stage, done, total, msg)

    def _check_cancelled(self):
        if self._cancelled and self._cancelled():
            raise RaidReportCancelled()

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
            for i, log in enumerate(to_parse, start=1):
                self._check_cancelled()
                # Count against ALL selected fights, with previously-read
                # ones already complete — "4 of 4" when 9 are selected
                # reads like fights went missing.
                self._emit("parse", done_already + i, total_selected,
                           f"Parsing {log.path.stem}")
                result = self.parse_log(log.path)
                if isinstance(result, Path):
                    stored = self.cache.store(
                        log.path, result, self.ei_version,
                        self.settings_fingerprint)
                    json_paths.append(stored)
                else:
                    logger.warning("Failed to parse log: %s", log.path)
                    failed_names.append(log.path.stem)

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

        for jp in json_paths:
            shutil.copy2(jp, input_dir)

        self._emit("collect", 1, 1)

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
            self._emit("resolve", 1, 1, "Resolving stats viewer...")
            self.viewer_html = self._viewer_factory()
        self.combiner.write_run_config(
            run_dir, input_dir,
            self.guild_name, self.guild_id, self.api_key,
        )
        dragdrop_json = self.combiner.run(
            input_dir,
            run_dir,
            standalone_html_template=self.viewer_html,
        )
        standalone_html = dragdrop_json.with_suffix(".html")
        self._emit("combine", 1, 1)

        self._check_cancelled()
        if self.augment_json:
            self._emit("augment", 1, 1)
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

        # Same augmentation path: pin report table headers so scrolling a
        # long fight keeps the column labels. Never fails the report.
        apply_sticky_table_headers(standalone_html)

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


def make_publish_caption(result: ReportResult) -> str:
    # Default report filenames already include "(N fights)". Discord uses a
    # cleaner caption shape and adds the authoritative count exactly once.
    display_name = re.sub(
        r"\s+\(\d+\s+fights?\)\s*$", "", result.name, flags=re.IGNORECASE)
    return (
        f"{display_name} \u2014 {result.fight_count} "
        f"{'fight' if result.fight_count == 1 else 'fights'} \u00b7 "
        f"{result.generated_date:%m/%d/%Y}"
    )
