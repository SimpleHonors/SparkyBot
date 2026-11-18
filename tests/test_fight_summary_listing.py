"""Portable regressions: real discovery + actual tab worker (no Qt import)."""
import ast
import logging
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from datetime import datetime
from core.raid_session import LogInfo, discover_logs


def worker_harness(discover):
    source = Path(__file__).parents[1] / 'core' / 'raid_report_tab.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'RaidReportTab')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_start_discover')
    method.decorator_list = []
    env = {'threading': threading, 'logger': logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), env)
    done = threading.Event()
    payloads = []
    def emit(*args):
        payloads.append(args)
        done.set()
    obj = types.SimpleNamespace(_discover=discover, _discovering=False,
        sig_logs_discovered=types.SimpleNamespace(emit=emit), _scope=None, _epoch=0,
        _discovery_root=None, _request_id=0)
    obj._start_discover = types.MethodType(env['_start_discover'], obj)
    return obj, done, payloads


class MetadataWorkerTests(unittest.TestCase):
    def test_carried_sizes_including_zero_do_not_stat_again(self):
        logs = [LogInfo(Path('20261106-010000.zevtc'), datetime(2026,11,6), 'filename', 0),
                LogInfo(Path('20261106-020000.evtc'), datetime(2026,11,6,2), 'filename', 42)]
        obj, done, payloads = worker_harness(lambda: logs)
        with patch.object(Path, 'stat', side_effect=OSError('unexpected redundant read')) as stat:
            obj._start_discover(preserve=None, chip='recent')
            self.assertTrue(done.wait(2))
            self.assertEqual(stat.call_count, 0, 'carried metadata must reach the actual GUI worker')
        payload = payloads[0]
        if len(payload) == 4:  # accepted R2 signal contract
            sizes = payload[1]
        else:
            sizes = dict(payload[0].sizes)
        self.assertEqual(sizes, {logs[0].path: 0, logs[1].path: 42})


    def test_missing_size_failure_is_incomplete_even_without_exception_message(self):
        row = LogInfo(Path('20261106-010000.zevtc'), datetime(2026,11,6), 'filename')
        obj, done, payloads = worker_harness(lambda: [row])
        with patch.object(Path, 'stat', side_effect=OSError()):
            obj._start_discover(None, None)
            self.assertTrue(done.wait(2))
        self.assertTrue(payloads[0][0]['error'])
        self.assertTrue(payloads[0][0]['partial'])

    def test_legacy_missing_size_reads_once_off_calling_thread(self):
        row = LogInfo(Path('20261106-010000.zevtc'), datetime(2026,11,6), 'filename')
        obj, done, payloads = worker_harness(lambda: [row])
        reads = []
        owner = threading.get_ident()
        def stat(*args, **kwargs):
            reads.append(threading.get_ident())
            return types.SimpleNamespace(st_size=9)
        with patch.object(Path, 'stat', stat):
            obj._start_discover(None, None)
            self.assertTrue(done.wait(2))
        self.assertEqual(len(reads), 1)
        self.assertNotEqual(reads[0], owner)
        self.assertEqual(payloads[0][1][row.path], 9)


class CompleteScanTests(unittest.TestCase):
    def test_legacy_missing_root_is_still_best_effort_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(discover_logs(Path(tmp)/'absent'), [])

    def test_strict_iterator_failure_preserves_partial_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'20261106-010000.evtc').write_bytes(b'abc')
            import os
            with os.scandir(root) as entries:
                entry = next(entries)
            class BrokenIterator:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def __iter__(self):
                    yield entry
                    raise OSError('synthetic iterator disconnect')
            with patch('core.raid_session.os.scandir', return_value=BrokenIterator()):
                with self.assertRaises(OSError) as caught:
                    discover_logs(root, include_size=True, strict=True)
            self.assertEqual(len(caught.exception.logs), 1)
            self.assertEqual(caught.exception.logs[0].size_bytes, 3)

    def test_legacy_iterator_failure_still_raises(self):
        class BrokenIterator:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __iter__(self):
                raise OSError('synthetic iterator disconnect')
        with patch('core.raid_session.os.scandir', return_value=BrokenIterator()):
            with self.assertRaises(OSError):
                discover_logs(Path('synthetic'))

    def test_missing_root_is_failure_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OSError):
                discover_logs(Path(tmp) / 'absent', include_size=True, strict=True)

    def test_empty_root_is_complete_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(discover_logs(Path(tmp), include_size=True, strict=True), [])

    def test_partial_scan_retains_rows_in_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'20261106-010000.zevtc').write_bytes(b'')
            (root/'blocked').mkdir()
            import os
            real = os.scandir
            def scan(p):
                if Path(p).name == 'blocked':
                    raise PermissionError('synthetic inaccessible subtree')
                return real(p)
            with patch('core.raid_session.os.scandir', side_effect=scan):
                with self.assertRaises(OSError) as caught:
                    discover_logs(root, include_size=True, strict=True)
            self.assertEqual(len(caught.exception.logs), 1)
            self.assertEqual(caught.exception.logs[0].size_bytes, 0)


if __name__ == '__main__':
    unittest.main()
