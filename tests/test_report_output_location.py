"""Reports default to the OS temp dir and get pruned — never a user dir.

Operator call 2026-07-19: baked reports (html/json) were accumulating
forever in the app folder; the local copy is scratch once posted.
"""

import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from core.config import Config
from core.raid_session import prune_raidreport_output


def _cfg(tmp_path, body="[RaidReport]\n"):
    p = tmp_path / "settings.ini"
    p.write_text(body)
    return Config(str(p))


class TestOutputDirDefault:
    def test_default_is_under_temp(self, tmp_path):
        cfg = _cfg(tmp_path)
        out = cfg.get_raidreport_output_dir()
        assert Path(tempfile.gettempdir()) in out.parents
        assert out == Config.default_raidreport_output_dir()

    def test_configured_dir_wins(self, tmp_path):
        cfg = _cfg(
            tmp_path,
            f"[RaidReport]\nraidreportOutputDir = {tmp_path}\n",
        )
        assert cfg.get_raidreport_output_dir() == tmp_path


class TestPruneOutput:
    def test_removes_only_old_files(self, tmp_path):
        root = tmp_path / "RaidReports"
        root.mkdir()
        old = root / "Raid Report old.html"
        new = root / "Raid Report new.html"
        old.write_text("x")
        new.write_text("x")
        stale = time.time() - 72 * 3600
        import os
        os.utime(old, (stale, stale))

        removed = prune_raidreport_output(retention_hours=48.0, root=root)
        assert removed == 1
        assert not old.exists()
        assert new.exists()

    def test_missing_dir_is_noop(self, tmp_path):
        assert prune_raidreport_output(root=tmp_path / "nope") == 0
