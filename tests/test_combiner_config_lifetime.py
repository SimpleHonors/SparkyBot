"""Combiner config ownership, exercised without launching external tools."""
import configparser
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import combiner_manager as cm
from core import fight_links, raid_report


def test_overlapping_report_runners_pass_only_their_own_config(tmp_path, monkeypatch):
    template = tmp_path / 'viewer.html'
    template.touch()
    output = tmp_path / 'out'
    run_dir = output / 'combiner_run'
    monkeypatch.setattr(cm.CombinerManager, 'ensure_installed', lambda self: None)
    monkeypatch.setattr(cm.CombinerManager, '_find_entry', staticmethod(lambda _: 'synthetic-cli'))
    monkeypatch.setattr(fight_links, 'app_dir', lambda: tmp_path)
    paths = []

    class CombineFinished(Exception):
        """Stop at the successful combine boundary, before rendering a report."""

    def stop_after_combine(stage, done, total, message):
        if stage == 'combine' and done == total:
            raise CombineFinished

    def generate(tag):
        source = tmp_path / f'{tag}.json'
        source.write_text('{}')
        runner = raid_report.RaidReportRunner(
            log_folder=tmp_path, cache=SimpleNamespace(lookup=lambda *args: source),
            parse_log=lambda _: pytest.fail('unexpected parse'), ei_version='demo',
            settings_fingerprint='demo', combiner=cm.CombinerManager(tmp_path),
            viewer_html=template, output_dir=output, guild_name=f'guild-{tag}',
            guild_id=f'id-{tag}', api_key=f'sentinel-{tag}', progress=stop_after_combine)
        log = raid_report.LogInfo(source.with_suffix('.zevtc'), datetime(2026, 1, 1), 'fixture')
        with pytest.raises(CombineFinished):
            runner.generate([log], work_dir=tmp_path / tag)

    def process(cmd, **kwargs):
        tag = ('one', 'two')[len(paths)]
        config = Path(cmd[cmd.index('-c') + 1])
        paths.append(config)
        assert kwargs['cwd'] == str(run_dir) and config.parent == run_dir
        assert cmd[cmd.index('-s') + 1] == str(template)
        original = config.read_bytes()
        parsed = configparser.ConfigParser()
        parsed.read_string(original.decode('utf-8'))
        settings = parsed['TopStatsCfg']
        assert settings['guild_name'] == f'guild-{tag}'
        assert settings['guild_id'] == f'id-{tag}'
        assert settings['api_key'] == f'sentinel-{tag}'
        inputs = Path(settings['input_directory'])
        assert inputs.parent == tmp_path / tag
        assert [path.name for path in inputs.glob('*.json')] == [f'{tag}.json']
        assert settings['db_path'] == str(tmp_path / 'topstats_db')
        assert set(run_dir.iterdir()) == set(paths)
        if tag == 'one':
            generate('two')  # Run two finishes while run one's process still needs its config.
            assert config.read_bytes() == original
            assert set(run_dir.iterdir()) == {config}
        summary = inputs / 'Drag_and_Drop_Log_Summary_demo.json'
        summary.write_text('[]')
        summary.with_suffix('.html').touch()
        return subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr(cm.subprocess, 'run', process)
    generate('one')
    assert len(set(paths)) == 2
    assert list(run_dir.iterdir()) == []


@pytest.mark.parametrize('failure', ['invalid-template', 'timeout', 'nonzero', 'spawn', 'write'])
def test_failure_does_not_leave_config_or_remove_neighbour(tmp_path, monkeypatch, failure):
    manager = cm.CombinerManager(tmp_path / 'data')
    monkeypatch.setattr(manager, '_find_entry', lambda _: 'synthetic-cli')
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    neighbour = run_dir / 'top_stats_config_other.ini'
    neighbour.write_bytes(b'other run')

    def process(cmd, **kwargs):
        assert Path(cmd[cmd.index('-c') + 1]).is_file()
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(cmd, kwargs['timeout'])
        if failure == 'spawn':
            raise OSError('synthetic spawn failure')
        assert failure == 'nonzero', 'child must not start without a valid config and template'
        return subprocess.CompletedProcess(cmd, 7, '', 'synthetic failure')

    if failure == 'write':
        allocate = cm.tempfile.NamedTemporaryFile

        def partially_writable(*args, **kwargs):
            handle = allocate(*args, **kwargs)
            write = handle.write

            def partial_write(text):
                write(text[:12])
                handle.flush()
                raise OSError('synthetic write failure')

            handle.write = partial_write
            return handle

        monkeypatch.setattr(cm.tempfile, 'NamedTemporaryFile', partially_writable)

    monkeypatch.setattr(cm.subprocess, 'run', process)
    template = tmp_path / 'absent.html' if failure == 'invalid-template' else None
    message = {'invalid-template': 'template is not a file', 'timeout': 'timed out',
               'nonzero': 'exited with code 7', 'spawn': 'spawn failure', 'write': 'write failure'}[failure]
    with pytest.raises(OSError if failure in ('spawn', 'write') else cm.CombinerRunError, match=message):
        manager.run(tmp_path, run_dir, standalone_html_template=template)
    assert list(run_dir.iterdir()) == [neighbour]
    assert neighbour.read_bytes() == b'other run'
