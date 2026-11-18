"""FS-L1: real-Qt scope reset while an old discovery is Event-blocked."""
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication
from core.raid_report_tab import RaidReportTab
from core.raid_session import LogInfo, current_session, recent_logs, today_logs


def spin(predicate, seconds=3):
    end = time.monotonic() + seconds
    while not predicate() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(.005)
    QApplication.processEvents()
    if not predicate():
        raise AssertionError('Qt condition timed out')


class FightSummaryScopeQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])
            cls.addClassCleanup(cls.app.shutdown)

    def setUp(self):
        self.tabs = []
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for tab in self.tabs:
            spin(lambda: not tab._discovering)
            tab.hide()
            tab.deleteLater()
        QApplication.processEvents()

    def _root_change_while_old_scan_blocked(self, previous_complete):
        root = ['a']
        old_entered, new_entered = threading.Event(), threading.Event()
        old_gate, new_gate = threading.Event(), threading.Event()
        self.gates.extend((old_gate, new_gate))
        calls = []
        old_rows = [LogInfo(Path('old-' + str(i) + '.zevtc'),
                           datetime(2026, 11, 6, 1), 'filename', i)
                    for i in range(3)]

        def discover(scope):
            calls.append(scope)
            if scope == 'a':
                if not previous_complete or calls.count('a') == 2:
                    old_entered.set()
                    if not old_gate.wait(10):
                        raise AssertionError('old-root gate was not released')
                return list(old_rows)
            new_entered.set()
            if not new_gate.wait(10):
                raise AssertionError('new-root gate was not released')
            return []  # Only this successful current-root result proves empty.

        tab = RaidReportTab(
            discover=discover, discovery_root=lambda: root[0],
            select_session=current_session, select_today=today_logs,
            select_recent=recent_logs,
            runner_factory=lambda: self.fail('listing must not generate'),
            publish=lambda *args: self.fail('listing must not publish'))
        self.tabs.append(tab)
        if previous_complete:
            spin(lambda: not tab._discovering)
            tab._apply_chip('none')
            tab._set_row_checked(0, True)
            tab._update_found()
            self.assertIn('3 fights found', tab._found_label.text())
            self.assertIn('1 selected', tab._found_label.text())
            tab.refresh_btn.click()
        spin(old_entered.is_set)
        root[0] = 'b'
        tab.rescan()

        # Synchronous assertions: no event delivery and neither gate released.
        self.assertFalse(old_gate.is_set())
        self.assertFalse(new_entered.is_set())
        self.assertTrue(tab._discovering)
        self.assertEqual(tab._scope, 'b')
        self.assertIsNone(tab._complete_at)
        self.assertEqual(tab._logs, [])
        self.assertEqual(tab._table.rowCount(), 0)
        self.assertEqual(tab.selected_logs(), [])
        with self.subTest(state='immediate-loading'):
            self.assertEqual(tab._list_state, 'loading')
        with self.subTest(state='no-premature-empty-or-add-files-panel'):
            self.assertTrue(tab.empty_panel.isHidden(),
                            'unscanned root must not show No fights here yet/add-files')
        with self.subTest(state='no-prior-root-counts'):
            self.assertEqual(tab._found_label.text(), 'Loading fights…')

        old_gate.set()
        spin(new_entered.is_set)
        self.assertEqual(tab._logs, [], 'obsolete old-root rows must be rejected')
        self.assertTrue(tab.empty_panel.isHidden())
        self.assertEqual(tab._found_label.text(), 'Loading fights…')
        new_gate.set()
        spin(lambda: not tab._discovering)
        self.assertFalse(tab.empty_panel.isHidden(),
                         'successful complete-empty current root exposes the panel')
        self.assertEqual(tab._found_label.text(), 'No fights found.')
        self.assertEqual(calls, ['a', 'a', 'b'] if previous_complete else ['a', 'b'])

    def test_cold_root_change_while_old_scan_blocked_shows_only_loading(self):
        self._root_change_while_old_scan_blocked(previous_complete=False)

    def test_previous_complete_root_change_while_old_refresh_blocked_clears_counts(self):
        self._root_change_while_old_scan_blocked(previous_complete=True)


if __name__ == '__main__':
    unittest.main()
