"""Tests for core/raid_report.py — Raid Report pipeline orchestrator."""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.raid_report import (
    RaidReportRunner,
    RaidReportCancelled,
    ReportResult,
    make_publish_caption,
    RECENT_WINDOW_HOURS,
)
from core.raid_session import LogInfo


class FakeRaidReportCache:
    def __init__(self):
        self._store = {}
        self.store_calls = []

    def lookup(self, log_path, ei_version, fingerprint):
        return self._store.get(log_path)

    def store(self, log_path, json_path, ei_version, fingerprint):
        self.store_calls.append((log_path, json_path, ei_version, fingerprint))
        self._store[log_path] = json_path
        return json_path

    def preload(self, log_path, json_path):
        self._store[log_path] = json_path


class FakeCombiner:
    def __init__(self, dragdrop_json=None):
        self._output = dragdrop_json
        self.ensure_calls = 0
        self.write_config_calls = []
        self.run_calls = []
        self.last_input_files = []

    def ensure_installed(self, progress_callback=None):
        self.ensure_calls += 1

    def write_run_config(self, run_dir, input_dir,
                         guild_name="", guild_id="", api_key=""):
        self.write_config_calls.append(
            (run_dir, input_dir, guild_name, guild_id, api_key)
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir / "top_stats_config.ini"

    def run(self, input_dir, run_dir, timeout=900, progress_callback=None):
        self.run_calls.append((input_dir, run_dir))
        self.last_input_files = sorted(
            p.name for p in input_dir.iterdir() if p.is_file()
        )
        return self._output


def _make_viewer(tmp_path):
    p = tmp_path / "viewer.html"
    p.write_text(
        '<html><head><title>Viewer</title></head><body>\n'
        '<script class="tiddlywiki-tiddler-store" type="application/json">'
        '[{"title":"$:/boot","text":"hi"}]'
        '</script>\n'
        '</body></html>',
        encoding="utf-8",
    )
    return p


def _make_dragdrop(tmp_path, tiddlers=None):
    if tiddlers is None:
        tiddlers = [
            {"title": "Fight_01", "tags": "2026-07-08-21:00:06"},
            {"title": "Extra stuff"},
        ]
    p = tmp_path / "Drag_and_Drop_Log_Summary_test.json"
    p.write_text(json.dumps(tiddlers, ensure_ascii=False), encoding="utf-8")
    return p


def _dummy_log(tmp_path, stem, year=2026, month=7, day=18, hour=21, minute=0):
    path = tmp_path / f"{stem}.zevtc"
    path.write_text("")
    ts = datetime(year, month, day, hour, minute)
    return LogInfo(path=path, timestamp=ts, source="filename")


def _runner(tmp_path, **overrides):
    kwargs = dict(
        log_folder=tmp_path / "logs",
        cache=FakeRaidReportCache(),
        parse_log=lambda p: None,
        ei_version="v1.2.3",
        settings_fingerprint="abc123def456",
        combiner=None,
        viewer_html=_make_viewer(tmp_path),
        output_dir=tmp_path / "out",
        progress=None,
        cancelled=None,
    )
    kwargs.update(overrides)
    return RaidReportRunner(**kwargs)


class TestSelect:
    def test_select_default_is_recent(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        runner = _runner(tmp_path, log_folder=log_dir)
        selected = runner.select()
        assert isinstance(selected, list)

    def test_select_recent_returns_recent_logs(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        runner = _runner(tmp_path, log_folder=log_dir)
        selected = runner.select("recent")
        from core.raid_session import recent_logs
        assert isinstance(selected, list)

    def test_select_session_still_accepted(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        # Timestamps RELATIVE to now — an hourly cluster ending 2h ago sits
        # inside current_session's 24h lookback at any time of day. (A
        # fixed date here rotted the day after it was written.)
        now = datetime.now()
        for hours_ago in (5, 4, 3, 2):
            ts = now - timedelta(hours=hours_ago)
            (log_dir / f"{ts:%Y%m%d-%H%M%S}.zevtc").write_text("")

        runner = _runner(tmp_path, log_folder=log_dir)
        selected = runner.select("session")
        assert len(selected) >= 1

    def test_select_today_returns_todays_logs(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        today = datetime.now()
        ts = today.strftime("%Y%m%d")
        path = log_dir / f"{ts}-120000.zevtc"
        path.write_text("")

        runner = _runner(tmp_path, log_folder=log_dir)

        selected = runner.select("today")
        assert len(selected) >= 1

    def test_select_bad_mode_raises(self, tmp_path):
        runner = _runner(tmp_path)

        with pytest.raises(ValueError, match="Unknown select mode"):
            runner.select("nonsense")

    def test_today_calendar_day_boundary(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        late = _dummy_log(
            tmp_path, "20260718-235900", year=2026, month=7, day=18,
            hour=23, minute=59
        )
        early = _dummy_log(
            tmp_path, "20260719-000100", year=2026, month=7, day=19,
            hour=0, minute=1
        )
        (log_dir / late.path.name).write_text("")
        (log_dir / early.path.name).write_text("")

        runner = _runner(tmp_path, log_folder=log_dir)

        selected = runner.select("today")
        timestamps = [l.timestamp for l in selected]
        for ts in timestamps:
            assert ts.date() == datetime.now().date()
        assert late.timestamp.date() != early.timestamp.date()


class TestDefaultName:
    def test_format(self):
        logs = [
            _dummy_log(Path("/tmp"), "fight1", hour=20),
            _dummy_log(Path("/tmp"), "fight2", hour=21),
            _dummy_log(Path("/tmp"), "fight3", hour=22),
        ]
        runner = _runner(Path("/tmp"))
        name = runner.default_name(logs)
        assert name == "Raid Report 2026-07-18 (3 fights)"


class TestGenerateHappyPath:
    def test_full_pipeline(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        log_a = log_dir / "20260718-200001.zevtc"
        log_b = log_dir / "20260718-210001.zevtc"
        log_c = log_dir / "20260718-220001.zevtc"
        log_a.write_text("")
        log_b.write_text("")
        log_c.write_text("")

        info_a = LogInfo(path=log_a, timestamp=datetime(2026, 7, 18, 20, 0, 1),
                         source="filename")
        info_b = LogInfo(path=log_b, timestamp=datetime(2026, 7, 18, 21, 0, 1),
                         source="filename")
        info_c = LogInfo(path=log_c, timestamp=datetime(2026, 7, 18, 22, 0, 1),
                         source="filename")

        cached_json_a = tmp_path / "cached_a.json"
        cached_json_b = tmp_path / "cached_b.json"
        cached_json_a.write_text('{"title":"A"}')
        cached_json_b.write_text('{"title":"B"}')

        cache = FakeRaidReportCache()
        cache.preload(log_a, cached_json_a)
        cache.preload(log_b, cached_json_b)

        parsed_c = tmp_path / "parsed_c.json"
        parsed_c.write_text('{"title":"C"}')
        parse_calls = []

        def fake_parse(log_path):
            parse_calls.append(log_path)
            return parsed_c

        dragdrop = _make_dragdrop(tmp_path)
        combiner = FakeCombiner(dragdrop)

        runner = _runner(
            tmp_path,
            log_folder=log_dir,
            cache=cache,
            parse_log=fake_parse,
            combiner=combiner,
            progress=lambda stage, done, total, msg: None,
            output_dir=out_dir,
        )

        result = runner.generate([info_a, info_b, info_c])

        assert len(parse_calls) == 1
        assert parse_calls[0] == log_c
        assert len(cache.store_calls) == 1
        assert cache.store_calls[0][0] == log_c

        assert len(combiner.run_calls) == 1
        assert len(combiner.last_input_files) == 3

        assert result.html_path == out_dir / "Raid Report 2026-07-18 (3 fights).html"
        assert result.json_path == out_dir / "Raid Report 2026-07-18 (3 fights).json"
        assert result.json_path.exists()
        assert result.fight_count == 1
        assert result.span == "21:00\u201321:00"

    def test_output_dir_created_when_missing(self, tmp_path):
        # The default output dir under %TEMP% doesn't exist on a fresh
        # Windows session; generation must create it rather than let
        # mkdtemp fail with WinError 3 (broke the live Raid Report
        # button all day 2026-07-19).
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        out_dir = tmp_path / "temp" / "SparkyBot" / "RaidReports"

        log_a = log_dir / "20260718-200001.zevtc"
        log_a.write_text("")
        info_a = LogInfo(path=log_a, timestamp=datetime(2026, 7, 18, 20, 0, 1),
                         source="filename")

        cached_json_a = tmp_path / "cached_a.json"
        cached_json_a.write_text('{"title":"A"}')
        cache = FakeRaidReportCache()
        cache.preload(log_a, cached_json_a)

        dragdrop = _make_dragdrop(tmp_path)
        combiner = FakeCombiner(dragdrop)

        runner = _runner(
            tmp_path,
            log_folder=log_dir,
            cache=cache,
            parse_log=lambda p: None,
            combiner=combiner,
            progress=lambda stage, done, total, msg: None,
            output_dir=out_dir,
        )

        result = runner.generate([info_a])

        assert out_dir.is_dir()
        assert result.html_path.parent == out_dir
        assert result.json_path.exists()

    def test_custom_report_name(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        log_a = (tmp_path / "logs").mkdir(exist_ok=True) or tmp_path / "logs" / "a.zevtc"
        log_dir = tmp_path / "logs"
        log_dir.mkdir(exist_ok=True)
        (log_dir / "20260718-200001.zevtc").write_text("")

        info = LogInfo(path=log_dir / "20260718-200001.zevtc",
                       timestamp=datetime(2026, 7, 18, 20, 0, 1),
                       source="filename")

        cached = tmp_path / "cached.json"
        cached.write_text('{"title":"X"}')

        cache = FakeRaidReportCache()
        cache.preload(info.path, cached)

        dragdrop = _make_dragdrop(tmp_path)
        combiner = FakeCombiner(dragdrop)

        runner = _runner(
            tmp_path,
            log_folder=log_dir,
            cache=cache,
            combiner=combiner,
            output_dir=out_dir,
        )

        result = runner.generate([info], report_name="My Special Report")

        assert result.name == "My Special Report"
        assert result.html_path.name == "My Special Report.html"


class TestParseFailure:
    def test_single_failure_still_succeeds(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        log_ok = log_dir / "20260718-200001.zevtc"
        log_bad = log_dir / "20260718-210001.zevtc"
        log_ok.write_text("")
        log_bad.write_text("")

        info_ok = LogInfo(path=log_ok, timestamp=datetime(2026, 7, 18, 20, 0, 1),
                          source="filename")
        info_bad = LogInfo(path=log_bad, timestamp=datetime(2026, 7, 18, 21, 0, 1),
                           source="filename")

        cached = tmp_path / "cached.json"
        cached.write_text('{"title":"K"}')

        cache = FakeRaidReportCache()
        cache.preload(log_ok, cached)

        def fake_parse(log_path):
            if log_path == log_bad:
                return None
            return cached

        dragdrop = _make_dragdrop(tmp_path)
        combiner = FakeCombiner(dragdrop)

        runner = _runner(tmp_path, log_folder=log_dir, cache=cache,
                         parse_log=fake_parse, combiner=combiner,
                         output_dir=out_dir)

        result = runner.generate([info_ok, info_bad])
        assert result.html_path.exists()
        assert len(combiner.run_calls) == 1

    def test_all_fail_raises(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        log_a = log_dir / "20260718-200001.zevtc"
        log_a.write_text("")

        info = LogInfo(path=log_a, timestamp=datetime(2026, 7, 18, 20, 0, 1),
                       source="filename")

        cache = FakeRaidReportCache()

        def fake_parse(log_path):
            return None

        dragdrop = _make_dragdrop(tmp_path)
        combiner = FakeCombiner(dragdrop)

        runner = _runner(tmp_path, log_folder=log_dir, cache=cache,
                         parse_log=fake_parse, combiner=combiner,
                         output_dir=out_dir)

        with pytest.raises(RuntimeError, match="All .* failed"):
            runner.generate([info])


class TestCancellation:
    def test_cancelled_during_parse_raises(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        log_a = log_dir / "20260718-200001.zevtc"
        log_a.write_text("")

        info = LogInfo(path=log_a, timestamp=datetime(2026, 7, 18, 20, 0, 1),
                       source="filename")

        cache = FakeRaidReportCache()
        parse_done = False

        def fake_parse(log_path):
            nonlocal parse_done
            parse_done = True
            return tmp_path / "fake.json"

        dragdrop = _make_dragdrop(tmp_path)
        combiner = FakeCombiner(dragdrop)

        def cancelled():
            return parse_done

        runner = _runner(tmp_path, log_folder=log_dir, cache=cache,
                         parse_log=fake_parse, combiner=combiner,
                         cancelled=cancelled, output_dir=out_dir)

        with pytest.raises(RaidReportCancelled):
            runner.generate([info])

        assert len(combiner.run_calls) == 0

    def test_cancelled_before_combine_raises(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        log_a = log_dir / "20260718-200001.zevtc"
        log_a.write_text("")

        info = LogInfo(path=log_a, timestamp=datetime(2026, 7, 18, 20, 0, 1),
                       source="filename")

        cached = tmp_path / "cached.json"
        cached.write_text('{"title":"X"}')

        cache = FakeRaidReportCache()
        cache.preload(log_a, cached)

        dragdrop = _make_dragdrop(tmp_path)
        combiner = FakeCombiner(dragdrop)

        def cancelled():
            return True

        runner = _runner(tmp_path, log_folder=log_dir, cache=cache,
                         combiner=combiner, cancelled=cancelled,
                         output_dir=out_dir)

        with pytest.raises(RaidReportCancelled):
            runner.generate([info])


class TestSanitization:
    def test_path_separators_removed(self, tmp_path):
        safe = __import__("core.raid_report", fromlist=["_sanitize_filename"]
                          )._sanitize_filename
        result = safe("We/the\\blob: really?")
        assert "/" not in result
        assert "\\" not in result
        assert ":" not in result
        assert "?" not in result


class TestProgress:
    def test_progress_sequence(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        log_a = log_dir / "20260718-200001.zevtc"
        log_b = log_dir / "20260718-210001.zevtc"
        log_a.write_text("")
        log_b.write_text("")

        info_a = LogInfo(path=log_a, timestamp=datetime(2026, 7, 18, 20, 0, 1),
                         source="filename")
        info_b = LogInfo(path=log_b, timestamp=datetime(2026, 7, 18, 21, 0, 1),
                         source="filename")

        cache = FakeRaidReportCache()

        def fake_parse(log_path):
            p = tmp_path / f"parsed_{log_path.stem}.json"
            p.write_text("{}")
            return p

        dragdrop = _make_dragdrop(tmp_path)
        combiner = FakeCombiner(dragdrop)

        progress_calls = []

        def progress(stage, done, total, msg):
            progress_calls.append((stage, done, total, msg))

        runner = _runner(tmp_path, log_folder=log_dir, cache=cache,
                         parse_log=fake_parse, combiner=combiner,
                         progress=progress, output_dir=out_dir)

        runner.generate([info_a, info_b])

        stages = [c[0] for c in progress_calls]
        assert stages == ["plan", "parse", "parse", "collect", "combine", "bake"]


class TestJsonCopied:
    def test_json_copied_next_to_html(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        log_a = log_dir / "20260718-200001.zevtc"
        log_a.write_text("")

        info_a = LogInfo(path=log_a, timestamp=datetime(2026, 7, 18, 20, 0, 1),
                         source="filename")

        cached = tmp_path / "cached.json"
        cached.write_text('{"title":"X"}')

        cache = FakeRaidReportCache()
        cache.preload(log_a, cached)

        dragdrop = _make_dragdrop(tmp_path)
        combiner = FakeCombiner(dragdrop)

        runner = _runner(tmp_path, log_folder=log_dir, cache=cache,
                         combiner=combiner, output_dir=out_dir)

        result = runner.generate([info_a])

        assert result.json_path.exists()
        assert result.json_path.suffix == ".json"
        assert result.json_path.stem == result.html_path.stem


class TestMakePublishCaption:
    def test_uses_local_generation_date_not_fight_span(self):
        result = ReportResult(
            name="Fellas",
            html_path=Path("/tmp/x.html"),
            json_path=Path("/tmp/x.json"),
            fight_count=20,
            span="19:28\u201321:00",
            generated_date=date(2026, 7, 20),
        )
        caption = make_publish_caption(result)
        assert caption == "Fellas — 20 fights · 07/20/2026"
        assert "19:28" not in caption

    def test_omits_duplicate_count_from_default_report_name(self):
        result = ReportResult(
            name="Raid Report 2026-07-18 (5 fights)",
            html_path=Path("/tmp/x.html"),
            json_path=Path("/tmp/x.json"),
            fight_count=5,
            span="",
            generated_date=date(2026, 7, 20),
        )
        caption = make_publish_caption(result)
        assert caption == "Raid Report 2026-07-18 — 5 fights · 07/20/2026"

    def test_singular_fight(self):
        result = ReportResult(
            name="Solo",
            html_path=Path("/tmp/x.html"),
            json_path=Path("/tmp/x.json"),
            fight_count=1,
            span="",
            generated_date=date(2026, 7, 20),
        )
        assert make_publish_caption(result) == (
            "Solo — 1 fight · 07/20/2026")
