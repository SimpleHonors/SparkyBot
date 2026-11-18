"""Real Qt filename-list producer/consumer tests; require PySide6, never skip."""
import threading
import time
import tempfile
from datetime import datetime
from pathlib import Path
import unittest
from unittest.mock import patch
from PySide6.QtCore import QTimer, QThread, Qt, Slot
from PySide6.QtWidgets import QApplication
from core.raid_session import LogInfo, recent_logs, today_logs, current_session
from core.raid_report_tab import RaidReportTab, _ROLE_SORT, _COL_SIZE, _COL_FIGHT


def spin(predicate, seconds=3):
    end = time.monotonic() + seconds
    while not predicate() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(.005)
    QApplication.processEvents()
    if not predicate():
        raise AssertionError('Qt condition timed out')


def log(name='20261106-010000.zevtc', size=0):
    return LogInfo(Path(name), datetime(2026,11,6,1), 'filename', size)


class ObservedTab(RaidReportTab):
    @Slot(object, object, object, object)
    def _on_logs_discovered(self, *args):
        self.receiver_threads.append(QThread.currentThread())
        super()._on_logs_discovered(*args)


class FightSummaryQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])
            cls.addClassCleanup(cls.app.shutdown)

    def setUp(self):
        self.tabs = []
        self.gates = []
        self.worker_threads = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for tab in self.tabs:
            spin(lambda: not tab._discovering)
            tab.hide()
            tab.deleteLater()
        QApplication.processEvents()

    def tab(self, discover, **kwargs):
        def guarded(*args):
            self.worker_threads.append(QThread.currentThread())
            return discover(*args)
        # Set before __init__ because the synthetic producer can finish fast.
        tab = ObservedTab.__new__(ObservedTab)
        tab.receiver_threads = []
        tab.__init__(discover=guarded, select_session=current_session,
            select_today=today_logs, select_recent=recent_logs,
            runner_factory=lambda: self.fail('refresh must not generate'),
            publish=lambda *a: self.fail('refresh must not publish'), **kwargs)
        self.tabs.append(tab)
        return tab

    def test_refresh_uses_async_path_retains_selection_sizes_sort_and_no_generation(self):
        rows = [log(size=0), log('20261106-020000.evtc', 42)]
        calls = []
        def discover():
            calls.append(1)
            return list(rows)
        tab = self.tab(discover)
        spin(lambda: not tab._discovering)
        tab._apply_chip('none')
        tab._set_row_checked(0, True)
        selected = {x.path for x in tab.selected_logs()}
        tab._table.sortItems(_COL_SIZE, Qt.SortOrder.AscendingOrder)
        rows.append(log('20261106-030000.zevtc', 100))
        self.assertTrue(hasattr(tab, 'refresh_btn'), 'Fight Summary needs manual Refresh')
        tab.refresh_btn.click()
        spin(lambda: not tab._discovering and len(tab._logs) == 3)
        self.assertEqual({x.path for x in tab.selected_logs()}, selected)
        self.assertEqual(tab._table.horizontalHeader().sortIndicatorSection(), _COL_SIZE)
        self.assertEqual([tab._table.item(i, _COL_SIZE).data(_ROLE_SORT) for i in range(3)], [0,42,100])
        self.assertEqual(len(calls), 2)
        self.assertFalse(tab._running)

    def test_warm_reentry_does_not_walk(self):
        calls = []
        tab = self.tab(lambda: calls.append(1) or [log()])
        spin(lambda: not tab._discovering)
        tab.rescan(); tab.rescan()
        QApplication.processEvents()
        time.sleep(.03)
        QApplication.processEvents()
        self.assertEqual(len(calls), 1, 'warm entries must not repeat the full walk')

    def test_first_failure_constructor_entry_overlap_retries_and_gui_heartbeat(self):
        gate = threading.Event(); self.gates.append(gate)
        entered = threading.Event()
        calls = []
        def discover():
            calls.append(1)
            if len(calls) == 1:
                entered.set(); gate.wait(3)
                raise OSError('synthetic disconnected share')
            return [log()]
        tab = self.tab(discover)
        spin(entered.is_set)
        beats = []
        timer = QTimer(); timer.setInterval(5); timer.timeout.connect(lambda: beats.append(QThread.currentThread())); timer.start()
        tab.rescan()
        spin(lambda: len(beats) >= 3)
        self.assertIn('Loading', tab._found_label.text())
        self.assertNotIn('No fights found', tab._found_label.text())
        gate.set()
        spin(lambda: not tab._discovering and len(tab._logs) == 1)
        timer.stop()
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(x == self.app.thread() for x in beats + tab.receiver_threads))
        self.assertTrue(all(x != self.app.thread() for x in self.worker_threads))

    def test_failed_refresh_retains_last_complete_rows_and_checked_paths(self):
        calls = []
        def discover():
            calls.append(1)
            if len(calls) > 1:
                raise OSError('synthetic offline')
            return [log()]
        tab = self.tab(discover)
        spin(lambda: not tab._discovering)
        tab._apply_chip('all')
        tab.refresh_btn.click()
        spin(lambda: not tab._discovering)
        self.assertEqual(tab._table.rowCount(), 1)
        self.assertEqual(len(tab.selected_logs()), 1)
        self.assertIn('failed', tab._found_label.text().lower())
        self.assertIn('last complete', tab._found_label.text().lower())
        self.assertTrue(tab.empty_panel.isHidden())

    def test_manual_refresh_coalesces_to_one_trailing_scan(self):
        gate = threading.Event(); self.gates.append(gate)
        calls = []
        def discover():
            calls.append(1)
            if len(calls) == 1:
                gate.wait(3)
            return [log()]
        tab = self.tab(discover)
        spin(lambda: len(calls) == 1)
        for _ in range(12):
            tab.refresh_btn.click()
        gate.set()
        spin(lambda: not tab._discovering and len(calls) >= 2)
        self.assertEqual(len(calls), 2)

    def test_real_wiring_bypasses_negative_cache_and_reconnects_with_no_gui_io(self):
        from core.raid_report_wiring import build_raid_report_tab
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'reconnect'
            class Config:
                log_folder = str(root)
                def get_log_folders(self):
                    raise AssertionError('negative existence cache must not gate filename discovery')
            tab = build_raid_report_tab(Config())
            self.tabs.append(tab)
            spin(lambda: not tab._discovering)
            self.assertNotEqual(tab._found_label.text(), 'No fights found.')
            root.mkdir(); (root/'20261106-010000.zevtc').write_bytes(b'')
            original_stat = Path.stat
            def stat(p, *args, **kwargs):
                self.assertNotEqual(QThread.currentThread(), self.app.thread(), 'source stat on GUI thread')
                return original_stat(p,*args,**kwargs)
            with patch.object(Path, 'stat', stat):
                tab.refresh_btn.click()
                spin(lambda: not tab._discovering and len(tab._logs) == 1)
            self.assertEqual(tab._log_sizes[tab._logs[0].path], 0)
            # Explicit refresh observes additions, deletions and renames.
            tab._apply_chip('all')
            (root/'20261106-010000.zevtc').rename(root/'20261106-020000.evtc')
            tab.refresh_btn.click()
            spin(lambda: not tab._discovering)
            self.assertEqual(tab._logs[0].path.name, '20261106-020000.evtc')
            self.assertEqual(tab.selected_logs(), [], 'renamed files are new paths, not old selections')
            (root/'20261106-020000.evtc').unlink()
            tab.refresh_btn.click(); spin(lambda: not tab._discovering)
            self.assertEqual(tab._logs, [])
            self.assertEqual(tab._found_label.text(), 'No fights found.')

    def test_root_change_rejects_late_results_and_generation_defers(self):
        root = ['a']; gate = threading.Event(); self.gates.append(gate)
        calls = []
        def discover(scope):
            calls.append(scope)
            if scope == 'a':
                gate.wait(3)
            return [log(scope+'.zevtc')]
        tab = self.tab(discover, discovery_root=lambda: root[0])
        spin(lambda: calls == ['a'])
        root[0] = 'b'; tab.rescan()
        tab._set_running(True)
        gate.set()
        spin(lambda: not tab._discovering)
        self.assertEqual(tab._table.rowCount(), 0)
        tab._set_running(False)
        spin(lambda: not tab._discovering and len(tab._logs) == 1)
        self.assertEqual(tab._logs[0].path, Path('b.zevtc'))
        self.assertEqual(calls, ['a','b'])

    def test_root_changed_during_generation_never_exposes_old_root_after_release(self):
        root = ['a']; gate = threading.Event(); self.gates.append(gate)
        entered = threading.Event()
        def discover(scope):
            if scope == 'b':
                entered.set(); gate.wait(3)
            return [log(scope+'.zevtc')]
        tab = self.tab(discover, discovery_root=lambda: root[0])
        spin(lambda: not tab._discovering)
        tab._apply_chip('all')
        tab._set_running(True)
        root[0] = 'b'; tab.rescan()
        self.assertEqual(tab._table.rowCount(), 1, 'generation owns its frozen view')
        tab._set_running(False)
        spin(entered.is_set)
        self.assertEqual(tab._table.rowCount(), 0, 'old-root rows must not appear as new-root stale data')
        self.assertEqual(tab.selected_logs(), [])
        gate.set(); spin(lambda: not tab._discovering)
        self.assertEqual(tab._logs[0].path, Path('b.zevtc'))

    def test_refresh_burst_failure_has_only_one_trailing_retry(self):
        gate = threading.Event(); self.gates.append(gate)
        calls = []
        def discover():
            calls.append(1)
            if len(calls) == 1:
                gate.wait(3)
            raise OSError('persistent synthetic failure')
        tab = self.tab(discover)
        spin(lambda: bool(calls))
        for _ in range(10):
            tab.refresh_btn.click()
        gate.set(); spin(lambda: len(calls) >= 2 and not tab._discovering)
        self.assertFalse(tab._retry_timer.isActive(), 'coalesced retry must consume the automatic retry budget')
        self.assertEqual(len(calls), 2)

    def test_automatic_retry_without_reentry_recovers(self):
        calls = []
        def discover():
            calls.append(1)
            if len(calls) == 1:
                raise OSError('temporary synthetic outage')
            return [log()]
        tab = self.tab(discover)
        spin(lambda: not tab._discovering)
        self.assertIn('failed', tab._found_label.text().lower())
        spin(lambda: len(calls) == 2 and not tab._discovering)
        self.assertEqual(tab._table.rowCount(), 1)

    def test_show_hide_warm_and_expired_entry(self):
        calls = []
        tab = self.tab(lambda: calls.append(1) or [log()])
        tab.show(); spin(lambda: not tab._discovering)
        tab.hide(); tab.show(); QApplication.processEvents()
        self.assertEqual(len(calls), 1)
        tab._complete_at -= 61
        tab.hide(); tab.show(); spin(lambda: len(calls) == 2 and not tab._discovering)
        self.assertEqual(tab._table.rowCount(), 1)

    def test_compact_page_keeps_refresh_and_quick_picks_inside_420px(self):
        tab = self.tab(lambda: [log()])
        spin(lambda: not tab._discovering)
        # Apply production sizing only to the widget whose geometry is tested.
        from PySide6.QtGui import QFont
        from core import theme
        tab.setFont(QFont("Segoe UI", 9))
        tab.setStyleSheet(theme.load_stylesheet())
        tab.resize(420, 700); tab.show(); QApplication.processEvents()
        self.assertLessEqual(tab.minimumSizeHint().width(), 420)
        self.assertLessEqual(tab.refresh_btn.geometry().right(), tab.width()-12)

    def test_same_root_result_defers_without_extra_walk_and_keeps_live_check_edits(self):
        gate = threading.Event(); self.gates.append(gate)
        calls = []
        def discover():
            calls.append(1)
            if len(calls) == 2:
                gate.wait(3)
            return [log(), log('new.evtc', 99)]
        tab = self.tab(discover); spin(lambda: not tab._discovering)
        tab._apply_chip('none')
        tab.refresh_btn.click()
        spin(lambda: len(calls) == 2)
        tab._set_row_checked(0, True)
        chosen = {x.path for x in tab.selected_logs()}
        tab._set_running(True); gate.set(); spin(lambda: not tab._discovering)
        self.assertIsNotNone(tab._deferred_discovery)
        self.assertEqual({x.path for x in tab.selected_logs()}, chosen)
        tab._set_running(False); QApplication.processEvents()
        self.assertEqual({x.path for x in tab.selected_logs()}, chosen)
        self.assertIsNone(tab._deferred_discovery)
        self.assertEqual(len(calls), 2)

    def test_root_change_at_generation_completion_starts_new_root(self):
        root = ['a']; calls = []
        tab = self.tab(lambda scope: calls.append(scope) or [log(scope+'.evtc')],
                       discovery_root=lambda: root[0])
        spin(lambda: not tab._discovering)
        tab._set_running(True); root[0] = 'b'; tab._set_running(False)
        spin(lambda: len(calls) == 2 and not tab._discovering)
        self.assertEqual(tab._logs[0].path, Path('b.evtc'))

    def test_destroy_during_worker_does_not_touch_deleted_widgets(self):
        import shiboken6
        gate = threading.Event(); self.gates.append(gate)
        entered = threading.Event(); threads = []
        def discover():
            threads.append(threading.current_thread())
            entered.set(); gate.wait(3)
            return [log()]
        tab = self.tab(discover); spin(entered.is_set)
        self.tabs.remove(tab)
        errors = []
        old_hook = threading.excepthook
        threading.excepthook = lambda args: errors.append(args.exc_value)
        try:
            shiboken6.delete(tab)
            gate.set(); threads[0].join(2)
            self.assertFalse(threads[0].is_alive())
            QApplication.processEvents()
            self.assertEqual(errors, [])
        finally:
            threading.excepthook = old_hook

    def test_partial_refresh_does_not_replace_complete_rows(self):
        from core.raid_session import LogDiscoveryError
        calls = []
        def discover():
            calls.append(1)
            if len(calls) > 1:
                raise LogDiscoveryError([log('partial.zevtc')], ['inaccessible subtree'])
            return [log('complete.zevtc')]
        tab = self.tab(discover); spin(lambda: not tab._discovering)
        tab.refresh_btn.click(); spin(lambda: not tab._discovering)
        self.assertEqual(tab._logs[0].path, Path('complete.zevtc'))
        self.assertIn('incomplete', tab._found_label.text().lower())


if __name__ == '__main__':
    unittest.main()
