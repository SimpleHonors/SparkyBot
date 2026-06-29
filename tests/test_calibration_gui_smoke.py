"""Import smoke test for the GUI Calibration wiring.

The PyQt panel is intentionally NOT unit-tested beyond this — it's thin glue
over the pure calibration/performance_buckets logic, which is covered in
test_calibration.py and test_performance_buckets_loader.py.

This test is skipped automatically where PyQt6 is unavailable (e.g. headless
CI), so it never produces a false failure.
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

pytest.importorskip("PyQt6", reason="PyQt6 not installed in this environment")


def test_settings_window_exposes_calibration_methods():
    from core.gui_settings import SettingsWindow
    # The Calibration tab's handlers must exist on the window class so the
    # _setup_ui / _connect_thread_signals wiring resolves at construction time.
    for name in (
        "_create_calibration_tab",
        "_calib_import_logs",
        "_calib_import_worker",
        "_calib_recalibrate",
        "_show_recalibration_preview",
        "_calib_reset_defaults",
        "_refresh_calib_count",
        "_on_calib_progress",
        "_on_calib_import_done",
    ):
        assert hasattr(SettingsWindow, name), f"SettingsWindow missing {name}"
