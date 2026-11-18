"""Synthetic IO regressions; neither external parser is executed."""
import ctypes
import os
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from core import combiner_manager as combiner
from core import gw2ei_invoker as ei


@pytest.mark.parametrize(('name', 'drive_type', 'remote', 'probe'), [
    (r'\\server\sample\demo.evtc', None, True, None),
    (r'M:\demo.evtc', 4, True, 'M:\\'),
    (r'C:\demo.evtc', 3, False, 'C:\\'),
    (r'\\?\C:\demo.evtc', 3, False, '\\\\?\\C:\\'),
    ('demo.evtc', None, False, None),
])
def test_share_detection(name, drive_type, remote, probe, monkeypatch):
    calls = []

    def get_drive_type(root):
        calls.append(root)
        return drive_type

    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(
        kernel32=SimpleNamespace(GetDriveTypeW=get_drive_type)), raising=False)
    assert ei._is_network_share(PureWindowsPath(name)) is remote
    assert calls == ([] if probe is None else [probe])


@pytest.mark.parametrize('remote', [True, False])
def test_parse_and_claim_from_working_directory(remote, tmp_path, monkeypatch):
    source = tmp_path / 'input' / 'sample.zevtc'
    source.parent.mkdir()
    source.write_bytes(b'synthetic raw')
    seen = []
    invoker = ei.GW2EIInvoker(None)
    monkeypatch.setattr(invoker, 'get_gw2ei_path', lambda: tmp_path / 'dummy')
    monkeypatch.setattr(ei, 'gw2ei_dir', lambda: tmp_path / 'tool')
    monkeypatch.setattr(ei.tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setattr(ei, '_is_network_share', lambda path: remote, raising=False)

    # Model a write in the same coarse clock tick as launch. Do not retime
    # the generated file; only its observed filesystem precision is coarser.
    bucket = int(source.stat().st_mtime)
    monkeypatch.setattr(ei, 'time', SimpleNamespace(time=lambda: bucket + 0.5,
                                                   sleep=ei.time.sleep,
                                                   monotonic=ei.time.monotonic))
    stat = Path.stat

    def coarse_stat(path, *args, **kwargs):
        result = stat(path, *args, **kwargs)
        if path.name == 'sample_detailed_wvw_kill.json':
            fields = list(result)
            fields[8] = bucket  # st_mtime
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(Path, 'stat', coarse_stat)

    def parse(cmd, **kwargs):
        working = Path(cmd[-1])
        seen.append(working)
        assert working.read_bytes() == b'synthetic raw'
        assert 'SaveAtOut=False' in Path(cmd[2]).read_text().splitlines()
        assert (working == source) is (not remote)
        output = working.parent / (working.stem + '_detailed_wvw_kill.json')
        output.write_text('{"ok":1}')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(ei.subprocess, 'run', parse)
    claimed = invoker.parse_file(source, config_name='sample.conf')
    assert claimed is not None and claimed.read_text() == '{"ok":1}'
    assert claimed.parent != source.parent and seen
    assert list(source.parent.iterdir()) == [source] and source.read_bytes() == b'synthetic raw'
    assert not remote or not seen[0].exists()
    assert not (tmp_path / 'tool/Settings/sample.conf').exists()


def test_overlapping_combiner_runs_keep_their_config(tmp_path, monkeypatch):
    data, run_dir = tmp_path / 'data', tmp_path / 'run'
    managers = [combiner.CombinerManager(data) for _ in range(2)]
    inputs = [tmp_path / 'one', tmp_path / 'two']
    for directory in inputs:
        directory.mkdir()
    configs = []
    entry = str(tmp_path / 'synthetic-cli')
    monkeypatch.setattr(combiner.CombinerManager, '_find_entry', staticmethod(lambda _: entry))

    def fake_run(cmd, **kwargs):
        i = len(configs)
        config = Path(cmd[cmd.index('-c') + 1])
        configs.append(config)
        assert config.parent == run_dir and kwargs['cwd'] == str(run_dir)
        original = config.read_bytes()
        if i == 0:
            managers[1].run(inputs[1], run_dir)
        assert config.read_bytes() == original
        assert 'input_directory = ' + str(inputs[i]) in original.decode('utf-8')
        assert 'db_path = ' + str(managers[i].db_dir) in original.decode('utf-8')
        (inputs[i] / 'Drag_and_Drop_Log_Summary_demo.json').write_text('[]')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(combiner.subprocess, 'run', fake_run)
    managers[0].run(inputs[0], run_dir)
    assert len(set(configs)) == 2


def test_combiner_reports_process_result_before_failure(tmp_path, monkeypatch, caplog):
    manager = combiner.CombinerManager(tmp_path / 'data')
    monkeypatch.setattr(manager, '_find_entry', lambda _: 'synthetic-cli')
    monkeypatch.setattr(combiner.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=7, stdout='abc', stderr='oops'))
    with caplog.at_level('INFO'), pytest.raises(combiner.CombinerRunError, match='oops'):
        manager.run(tmp_path, tmp_path / 'run')
    assert 'Combiner exited with code 7 (stdout 3 chars, stderr 4 chars)' in caplog.messages


def test_failed_staging_cleans_partial_copy_and_unlocks(tmp_path, monkeypatch):
    source = tmp_path / 'demo.zevtc'
    source.write_bytes(b'synthetic source')
    invoker = ei.GW2EIInvoker(None)
    monkeypatch.setattr(invoker, 'get_gw2ei_path', lambda: tmp_path / 'dummy')
    monkeypatch.setattr(ei, '_is_network_share', lambda _: True, raising=False)
    monkeypatch.setattr(ei.tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setattr(ei, 'gw2ei_dir', lambda: tmp_path / 'tool')
    copied = []

    def interrupted_copy(src, dest):
        dest.write_bytes(b'partial')
        copied.append(dest)
        raise OSError('synthetic interrupted copy')

    monkeypatch.setattr(ei.shutil, 'copy2', interrupted_copy)
    monkeypatch.setattr(ei.subprocess, 'run', lambda *a, **kw: pytest.fail('unexpected parse'))
    lock = ei._same_log_lock(source)
    assert invoker.parse_file(source) is None
    assert len(copied) == 1 and not copied[0].parent.exists()
    assert source.read_bytes() == b'synthetic source'
    assert lock.acquire(blocking=False)
    lock.release()
