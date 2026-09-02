import threading
import time
from datetime import datetime
from pathlib import Path

from core.raid_report import RaidReportRunner
from core.raid_session import LogInfo


class _Cache:
    def __init__(self, root):
        self.root = root

    def store(self, log_path, json_path, ei_version, fingerprint):
        destination = self.root / f"{Path(log_path).stem}.json"
        destination.write_text(Path(json_path).read_text(encoding="utf-8"), encoding="utf-8")
        return destination


def _runner(tmp_path, parse_log, progress=None, workers=4):
    return RaidReportRunner(
        log_folder=tmp_path,
        cache=_Cache(tmp_path / "cache"),
        parse_log=parse_log,
        ei_version="3.28.0.1",
        settings_fingerprint="test",
        combiner=object(),
        viewer_html=tmp_path / "viewer.html",
        output_dir=tmp_path / "out",
        progress=progress,
        parse_concurrency=workers,
    )


def test_missing_fights_parse_concurrently_but_return_in_selected_order(tmp_path):
    (tmp_path / "cache").mkdir()
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def parse_log(path):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.04)
        result = tmp_path / f"parsed-{path.stem}.json"
        result.write_text("{}", encoding="utf-8")
        with lock:
            active -= 1
        return result

    logs = [
        LogInfo(tmp_path / f"20260901-20{i:04}.zevtc", datetime.now(), "filename")
        for i in range(4)
    ]
    runner = _runner(tmp_path, parse_log)

    stored, failed = runner._parse_missing_logs(logs, total_selected=4, done_already=0)

    assert maximum_active > 1
    assert [path.stem for path in stored] == [log.path.stem for log in logs]
    assert failed == []


def test_failed_fight_names_remain_in_selected_order(tmp_path):
    (tmp_path / "cache").mkdir()

    def parse_log(path):
        if path.stem.endswith(("1", "3")):
            return None
        result = tmp_path / f"parsed-{path.stem}.json"
        result.write_text("{}", encoding="utf-8")
        return result

    logs = [
        LogInfo(tmp_path / f"fight-{i}.zevtc", datetime.now(), "mtime")
        for i in range(4)
    ]
    runner = _runner(tmp_path, parse_log, workers=2)

    _, failed = runner._parse_missing_logs(logs, total_selected=4, done_already=0)

    assert failed == ["fight-1", "fight-3"]
