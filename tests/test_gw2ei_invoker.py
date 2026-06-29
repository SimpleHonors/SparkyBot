"""Concurrency-safety unit test for GW2EIInvoker config isolation.

The manual calibration import runs several Elite Insights parses at once. The
only shared write target across invocations was GW2EI/Settings/wvwupload.conf
(rewritten fresh every call), so a per-job config name was added. This proves
distinct jobs get distinct config files while the default behavior is unchanged.
No EI subprocess is run.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

from core.gw2ei_invoker import GW2EIInvoker


def test_per_job_config_files_are_isolated(tmp_path):
    inv = GW2EIInvoker(config=None)
    inv.home_dir = tmp_path  # keep test artifacts out of the real GW2EI folder

    a = inv._ensure_parse_config("wvwupload_aaa.conf")
    b = inv._ensure_parse_config("wvwupload_bbb.conf")
    default = inv._ensure_parse_config()

    # Distinct concurrent jobs write distinct files -> no shared write target.
    assert a != b
    assert a.exists() and b.exists()
    # Default name unchanged (single-file behavior preserved).
    assert default.name == "wvwupload.conf"
    # Same, non-empty content every time (config is deterministic).
    assert a.read_text() == b.read_text() == default.read_text()
    assert a.read_text().strip() != ""
