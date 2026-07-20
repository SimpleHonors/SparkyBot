"""ProcessFilesWidget method API (slice 2 seam).

The app controller must drive the Process Files tab exclusively through
set_processing / show_progress / mark_file_result / finish_processing —
never by poking child widgets — and row outcomes must live in an item data
role, never be parsed back out of the visible text.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

pytest.importorskip("PySide6", reason="PySide6 not installed in this environment")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _widget(qapp):
    from core.gui_settings import ProcessFilesWidget
    return ProcessFilesWidget(SimpleNamespace(get_log_folders=lambda: []))


def test_widget_is_a_real_module_level_class():
    # The seam: importable/testable directly, not a closure inside the window
    from core.gui_settings import ProcessFilesWidget  # noqa: F401


def test_app_controller_uses_api_not_child_widgets():
    # Regression guard for the audit finding: main.py must never reach into
    # the tab's child widgets again.
    src = (_ROOT / "main.py").read_text(encoding="utf-8")
    for needle in (
        "process_files_widget.process_btn",
        "process_files_widget.status_label",
        "process_files_widget.file_list",
    ):
        assert needle not in src, f"main.py pokes tab internals: {needle}"


def test_add_file_dedupes_and_enables_process(qapp, tmp_path):
    w = _widget(qapp)
    assert not w.process_btn.isEnabled()
    w._add_file(str(tmp_path / "a.zevtc"))
    w._add_file(str(tmp_path / "a.zevtc"))  # duplicate ignored
    w._add_file(str(tmp_path / "b.evtc"))
    assert w.file_list.count() == 2
    assert w.process_btn.isEnabled()


def test_set_processing_locks_and_restores_button(qapp, tmp_path):
    w = _widget(qapp)
    w._add_file(str(tmp_path / "a.zevtc"))
    w.set_processing(True)
    assert not w.process_btn.isEnabled()
    w.set_processing(False)
    assert w.process_btn.isEnabled()
    # With an empty queue the button stays disabled after a run
    w.file_list.clear()
    w.set_processing(False)
    assert not w.process_btn.isEnabled()


def test_show_progress_updates_status(qapp, tmp_path):
    w = _widget(qapp)
    w.show_progress(2, 5, "fight.zevtc")
    assert w.status_label.text() == "Processing 2 of 5: fight.zevtc"


def test_mark_file_result_records_outcome_in_data_role(qapp, tmp_path):
    w = _widget(qapp)
    ok = tmp_path / "ok.zevtc"
    bad = tmp_path / "bad.zevtc"
    w._add_file(str(ok))
    w._add_file(str(bad))

    # Path objects must match the stored strings (resolve() normalization)
    w.mark_file_result(ok, True)
    w.mark_file_result(bad, False)

    assert w.file_list.item(0).data(w.RESULT_ROLE) is True
    assert w.file_list.item(1).data(w.RESULT_ROLE) is False
    # Path role survives untouched so a re-run still finds the file
    assert w.file_list.item(0).data(w.PATH_ROLE) == str(ok)
    # Outcome is carried by data + tooltip, never a decorative glyph prefix.
    assert w.file_list.item(0).text() == "ok.zevtc"
    assert w.file_list.item(1).text() == "bad.zevtc"
    assert "processed" in w.file_list.item(0).toolTip()
    assert "failed" in w.file_list.item(1).toolTip()


def test_finish_processing_prunes_successes_keeps_failures(qapp, tmp_path):
    w = _widget(qapp)
    ok = tmp_path / "ok.zevtc"
    bad = tmp_path / "bad.zevtc"
    w._add_file(str(ok))
    w._add_file(str(bad))
    w.set_processing(True)
    w.mark_file_result(ok, True)
    w.mark_file_result(bad, False)

    w.finish_processing(2)

    assert w.status_label.text() == "Done — processed 2 file(s)"
    assert w.file_list.count() == 1
    assert w.file_list.item(0).data(w.PATH_ROLE) == str(bad)
    # A failure left in the queue keeps the button usable for a retry
    assert w.process_btn.isEnabled()

    # Retry succeeds -> queue empties and the button locks
    w.set_processing(True)
    w.mark_file_result(bad, True)
    w.finish_processing(1)
    assert w.file_list.count() == 0
    assert not w.process_btn.isEnabled()


def test_pending_rows_survive_finish(qapp, tmp_path):
    # Rows never marked (e.g. run aborted early) must not be pruned
    w = _widget(qapp)
    w._add_file(str(tmp_path / "never-ran.zevtc"))
    w.finish_processing(0)
    assert w.file_list.count() == 1
    assert w.file_list.item(0).data(w.RESULT_ROLE) is None
