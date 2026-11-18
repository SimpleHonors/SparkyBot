"""Synthetic EI process boundary: the child never runs."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from core import fight_links, gw2ei_invoker as ei
from core.raid_report import RaidReportRunner
from core.raid_session import LogInfo, RaidReportCache


@pytest.fixture
def parser(tmp_path, monkeypatch):
    invoker = ei.GW2EIInvoker(None)
    monkeypatch.setattr(invoker, 'get_gw2ei_path', lambda: tmp_path / 'dummy')
    monkeypatch.setattr(ei, 'gw2ei_dir', lambda: tmp_path / 'tool')
    monkeypatch.setattr(ei.tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setattr(fight_links, 'app_dir', lambda: tmp_path)
    # Freshness must not confer ownership even when every file looks fresh.
    monkeypatch.setattr(ei, 'time', SimpleNamespace(time=lambda: 0, sleep=lambda _: None,
                                                   monotonic=ei.time.monotonic))
    return invoker


def complete(code=0):
    return SimpleNamespace(returncode=code, stdout='', stderr='')


def emit(raw, suffix='_detailed_wvw_kill.json'):
    output = raw.with_name(raw.stem + suffix)
    output.write_text(json.dumps({'marker': raw.read_text()}))
    return output


@pytest.mark.parametrize('remote', [True, False])
def test_two_outputs_are_not_one_owned_result(remote, tmp_path, monkeypatch, parser):
    raw = tmp_path / 'test.evtc'
    raw.write_text('test')
    monkeypatch.setattr(ei, '_is_network_share', lambda _: remote)

    def child(cmd, **kwargs):
        working = Path(cmd[-1])
        emit(working)
        emit(working, '_detailed_gh_kill.json')
        return complete()

    monkeypatch.setattr(ei.subprocess, 'run', child)
    assert parser.parse_file(raw) is None
    assert len(list(tmp_path.glob('*.json'))) == (0 if remote else 2)
    assert not list(tmp_path.rglob('GW2EIClaims/*.json'))


def test_shared_directory_does_not_claim_a_neighbours_result(tmp_path, monkeypatch, parser):
    raw = tmp_path / 'demo.evtc'
    neighbour = tmp_path / 'demo_other.evtc'
    raw.write_text('selected')
    neighbour.write_text('neighbour')

    def child(cmd, **kwargs):
        emit(neighbour)
        return complete()

    monkeypatch.setattr(ei.subprocess, 'run', child)
    assert parser.parse_file(raw) is None
    assert json.loads(neighbour.with_name('demo_other_detailed_wvw_kill.json').read_text()) == {'marker': 'neighbour'}


def test_preexisting_local_output_is_preserved(tmp_path, monkeypatch, parser):
    raw = tmp_path / 'demo.evtc'
    raw.write_text('selected')
    stale = emit(raw)
    stale.write_text('{"old":true}')
    monkeypatch.setattr(ei.subprocess, 'run', lambda *a, **kw: complete())
    assert parser.parse_file(raw) is None
    assert stale.read_text() == '{"old":true}'


@pytest.mark.parametrize('remote', [False, True])
def test_concurrent_claims_match_working_bytes(remote, tmp_path, monkeypatch, parser):
    if remote:
        inputs = [tmp_path / p / 'demo.evtc' for p in ('a', 'b')]
    else:
        inputs = [tmp_path / p for p in ('demo.evtc', 'demo.zevtc')]
    for i, raw in enumerate(inputs):
        raw.parent.mkdir(exist_ok=True)
        raw.write_text(str(i))
    monkeypatch.setattr(ei, '_is_network_share', lambda _: remote)
    both_remote = Barrier(2)
    first_output, second_output = Event(), Event()
    working_paths = []

    def child(cmd, **kwargs):
        working = Path(cmd[-1])
        working_paths.append(working)
        emit(working)
        if remote:
            both_remote.wait(timeout=3)
        elif working == inputs[0]:
            first_output.set()
            second_output.wait(timeout=0.3)
        else:
            second_output.set()
        return complete()

    monkeypatch.setattr(ei.subprocess, 'run', child)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(parser.parse_file, inputs[0], config_name='a.conf')
        if not remote:
            assert first_output.wait(timeout=3)
        second = pool.submit(parser.parse_file, inputs[1], config_name='b.conf')
        claims = [first.result(), second.result()]
    assert all(claims) and len(set(claims)) == 2
    assert [json.loads(p.read_text())['marker'] for p in claims] == ['0', '1']
    assert [p.read_text() for p in inputs] == ['0', '1']
    if remote:
        assert len({p.parent for p in working_paths}) == 2
        assert not any(p.parent.exists() for p in working_paths)
    else:
        assert set(working_paths) == set(inputs)


@pytest.mark.parametrize('fault', ['short', 'same-size', 'source-changed', 'changed-after-parse'])
def test_bad_input_cannot_enter_cache_or_link_registry(fault, tmp_path, monkeypatch, parser):
    bad, good = [tmp_path / (name + '.zevtc') for name in ('bad', 'good')]
    bad.write_text('bad!')
    good.write_text('good')
    original_copy = ei.shutil.copy2
    parsed = []
    bound = []

    def copy(src, dest):
        original_copy(src, dest)
        if src == bad:
            if fault == 'short':
                dest.write_text('b')
            elif fault == 'same-size':
                dest.write_text('oops')
            elif fault == 'source-changed':
                src.write_text('next')
        return dest

    def child(cmd, **kwargs):
        working = Path(cmd[-1])
        parsed.append(working.name)
        emit(working)
        return complete()

    def parse(raw):
        result = parser.parse_file(raw, config_name=raw.stem + '.conf')
        if raw == bad and fault == 'changed-after-parse':
            raw.write_text('next')
        return result

    monkeypatch.setattr(ei, '_is_network_share', lambda _: True)
    monkeypatch.setattr(ei.shutil, 'copy2', copy)
    monkeypatch.setattr(ei.subprocess, 'run', child)
    monkeypatch.setattr(fight_links, 'bind_parsed', lambda digest, _: bound.append(digest))
    cache = RaidReportCache(tmp_path / 'cache')
    runner = RaidReportRunner(log_folder=tmp_path, cache=cache, parse_log=parse,
                              ei_version='v', settings_fingerprint='f', combiner=None,
                              viewer_html=tmp_path/'unused', output_dir=tmp_path/'out')
    paths, failures = runner._parse_missing_logs(
        [LogInfo(p, datetime.now(), 'mtime') for p in (bad, good)], total_selected=2, done_already=0)
    assert failures == ['bad']
    assert paths == [cache.lookup(good, 'v', 'f')]
    assert json.loads(paths[0].read_text()) == {'marker': 'good'}
    assert cache.lookup(bad, 'v', 'f') is None
    assert bound == [fight_links.digest(good)]
    assert (bad.name in parsed) == (fault == 'changed-after-parse')
    assert not list(tmp_path.rglob('GW2EIClaims/*.json'))
    assert good.read_text() == 'good'
    assert bad.read_text() == ('next' if fault in ('source-changed', 'changed-after-parse') else 'bad!')


@pytest.mark.parametrize('child_exit', [0, 7])
def test_staging_cleanup_error_preserves_result_and_unlocks(child_exit, tmp_path, monkeypatch, parser, caplog):
    raw = tmp_path / 'demo.evtc'
    raw.write_text('selected')
    monkeypatch.setattr(ei, '_is_network_share', lambda _: True)
    cleanup = ei.tempfile.TemporaryDirectory.cleanup
    denied = []

    def failed_cleanup(directory):
        denied.append(directory)  # Keep the failed directory observable.
        raise OSError('private-detail' * 100)

    def child(cmd, **kwargs):
        if child_exit == 0:
            emit(Path(cmd[-1]))
        return complete(child_exit)

    monkeypatch.setattr(ei.tempfile.TemporaryDirectory, 'cleanup', failed_cleanup)
    monkeypatch.setattr(ei.subprocess, 'run', child)
    lock = ei._same_log_lock(raw)
    result = parser.parse_file(raw, config_name='job.conf')
    if child_exit == 0:
        assert json.loads(result.read_text()) == {'marker': 'selected'}
    else:
        assert result is None
    assert set(tmp_path.rglob('GW2EIClaims/*.json')) == ({result} if result else set())
    assert lock.acquire(blocking=False)
    lock.release()
    assert raw.read_text() == 'selected' and not (tmp_path/'tool/Settings/job.conf').exists()
    assert any('clean' in m.lower() and len(m) < 160 for m in caplog.messages)
    assert 'private-detail' not in caplog.text
    assert len(denied) == 1 and Path(denied[0].name).is_dir()
    cleanup(denied[0])


@pytest.mark.parametrize('changed', ['working', 'source'])
def test_changed_bytes_during_remote_parse_are_never_claimed(changed, tmp_path, monkeypatch, parser):
    raw = tmp_path / 'demo.evtc'
    raw.write_text('first')
    monkeypatch.setattr(ei, '_is_network_share', lambda _: True)

    def child(cmd, **kwargs):
        working = Path(cmd[-1])
        (working if changed == 'working' else raw).write_text('other')
        emit(working)
        return complete()

    monkeypatch.setattr(ei.subprocess, 'run', child)
    assert parser.parse_file(raw) is None
    assert not list(tmp_path.rglob('GW2EIClaims/*.json'))
    assert raw.read_text() == ('other' if changed == 'source' else 'first')
