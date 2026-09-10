"""Regression coverage for missing/GUI-only parser installs after an app update."""
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.ei_updater import EIUpdater

CLI = 'GuildWars2EliteInsights-CLI'
FILES = [CLI + suffix for suffix in ('.exe', '.dll', '.deps.json', '.runtimeconfig.json')]


def archive(names):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        for name in names:
            z.writestr(name, b'fixture payload')
    return stream.getvalue()


def download(monkeypatch, names):
    payload = archive(names)
    response = Mock(status_code=200, headers={'content-length': str(len(payload))})
    response.iter_content.return_value = [payload]
    monkeypatch.setattr('core.ei_updater.requests.get', Mock(return_value=response))


def installed(folder):
    folder.mkdir(parents=True)
    for name in FILES:
        (folder / name).write_bytes(b'existing parser')
    (folder / '.ei_version').write_text('3.27.0')


def test_missing_install_resolves_cli_even_when_gui_is_first(monkeypatch, tmp_path):
    redirect = Mock(status_code=200, headers={})
    release = Mock(status_code=200)
    release.json.return_value = {'tag_name': 'v3.28.0.1', 'assets': [
        {'name': 'GW2EI.zip', 'browser_download_url': 'https://example.test/GUI.zip'},
        {'name': 'GW2EICLI.zip', 'browser_download_url': 'https://example.test/CLI.zip'},
    ]}
    monkeypatch.setattr('core.ei_updater.requests.get', Mock(side_effect=[redirect, release]))
    available, version, url = EIUpdater(tmp_path / 'GW2EI').check_for_update()
    assert available and version == '3.28.0.1'
    assert url == 'https://example.test/CLI.zip'


def test_new_install_creates_destination(monkeypatch, tmp_path):
    download(monkeypatch, FILES)
    target = tmp_path / 'SparkyBot' / 'GW2EI'
    updater = EIUpdater(target)
    ok, message = updater.download_and_update('https://example.test/CLI.zip', '3.28.0.1')
    assert ok, message
    assert all((target / name).is_file() for name in FILES)
    assert updater.get_current_version() == '3.28.0.1'


@pytest.mark.parametrize('names', [
    ['GuildWars2EliteInsights.exe'], [CLI + '.exe'],
    ['wrapper/other.exe'], ['../escape.exe', *FILES],
])
def test_invalid_archive_preserves_existing_parser(monkeypatch, tmp_path, names):
    target = tmp_path / 'GW2EI'
    installed(target)
    download(monkeypatch, names)
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    ok, _ = EIUpdater(target).download_and_update('https://example.test/bad.zip', '3.28.0.1')
    assert not ok
    assert {p.name: p.read_bytes() for p in target.iterdir()} == before


def test_gui_only_install_is_replaced_and_settings_preserved(monkeypatch, tmp_path):
    target = tmp_path / 'GW2EI'
    (target / 'Settings').mkdir(parents=True)
    (target / 'Settings' / 'custom.conf').write_text('keep me')
    (target / 'GuildWars2EliteInsights.exe').write_bytes(b'wrong package')
    download(monkeypatch, ['release/' + name for name in FILES])
    ok, message = EIUpdater(target).download_and_update('https://example.test/CLI.zip', '3.28.0.1')
    assert ok, message
    assert (target / 'Settings' / 'custom.conf').read_text() == 'keep me'
    assert (target / (CLI + '.exe')).is_file()


def test_frozen_install_destination_does_not_depend_on_folder_existence(monkeypatch, tmp_path):
    from core import apppaths
    monkeypatch.setattr(apppaths, 'app_dir', lambda: tmp_path / 'application')
    monkeypatch.setattr(apppaths, 'bundle_dir', lambda: tmp_path / '_internal')
    monkeypatch.setattr(apppaths, 'is_frozen', lambda: True)
    assert apppaths.gw2ei_dir() == tmp_path / 'application' / 'GW2EI'


def test_concurrent_repairs_download_once(monkeypatch, tmp_path):
    target = tmp_path / 'GW2EI'
    def install(self, *args, **kwargs):
        installed(target)
        return True, 'ready'
    mock = Mock(side_effect=lambda *a, **k: install(None))
    monkeypatch.setattr(EIUpdater, 'download_and_update', mock)
    monkeypatch.setattr(EIUpdater, 'latest_release', lambda self: ('3.28.0.1', 'https://example.test/CLI.zip'))
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: EIUpdater(target).ensure_installed(), range(8)))
    assert all(ok for ok, _ in results)
    assert mock.call_count == 1


def test_failed_repair_does_not_download_for_every_log(monkeypatch, tmp_path):
    target = tmp_path / 'GW2EI'
    mock = Mock(return_value=(False, 'offline'))
    monkeypatch.setattr(EIUpdater, 'download_and_update', mock)
    monkeypatch.setattr(EIUpdater, 'latest_release', lambda self: ('3.28.0.1', 'https://example.test/CLI.zip'))
    for _ in range(30):
        assert not EIUpdater(target).ensure_installed()[0]
    assert mock.call_count == 1


def test_invoker_ignores_mistaken_external_path(monkeypatch, tmp_path):
    from core import gw2ei_invoker
    target = tmp_path / 'GW2EI'
    wrong = tmp_path / 'selected-directory'
    wrong.mkdir()
    monkeypatch.setattr(gw2ei_invoker, 'gw2ei_dir', lambda: target)
    invoker = gw2ei_invoker.GW2EIInvoker(SimpleNamespace(gw2ei_exe=str(wrong)))
    assert invoker.get_gw2ei_path() is None
    installed(target)
    assert invoker.get_gw2ei_path() == target / (CLI + '.exe')


@pytest.fixture(scope='module')
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_startup_repairs_with_optional_update_checks_disabled(monkeypatch, tmp_path, app):
    from core.update_flow import UpdateFlow
    target = tmp_path / 'GW2EI'
    monkeypatch.setattr('core.apppaths.gw2ei_dir', lambda: target)
    monkeypatch.setattr(EIUpdater, 'latest_release', lambda self: ('3.28.0.1', 'https://example.test/CLI.zip'))
    download(monkeypatch, FILES)
    flow = UpdateFlow(SimpleNamespace(check_updates_on_launch=False), app_root=tmp_path)
    outcomes = []
    flow.sig_parser_ready.connect(lambda ok, msg: outcomes.append((ok, msg)))
    flow._repair_parser_sync()
    assert outcomes[-1][0], outcomes
    assert EIUpdater(target).is_installed()


def test_wizard_uses_cli_and_checks_files_before_continuing(monkeypatch, tmp_path, app):
    from core.setup_wizard import GW2EIPage
    from PySide6.QtWidgets import QLineEdit
    target = tmp_path / 'GW2EI'
    monkeypatch.setattr('core.apppaths.gw2ei_dir', lambda: target)
    monkeypatch.setattr('core.gw2ei_invoker.gw2ei_dir', lambda: target)
    monkeypatch.setattr(EIUpdater, 'latest_release', lambda self: ('3.28.0.1', 'https://example.test/CLI.zip'))
    download(monkeypatch, FILES)
    page = GW2EIPage(SimpleNamespace())
    assert not page.findChildren(QLineEdit)
    assert not page.validatePage()
    page._download_worker()
    assert page.validatePage()
    (target / (CLI + '.exe')).unlink()
    assert not page.validatePage()  # cached success must not bypass validation
    page.deleteLater()


def test_install_failure_rolls_back(monkeypatch, tmp_path):
    from pathlib import Path
    target = tmp_path / 'GW2EI'
    installed(target)
    download(monkeypatch, FILES)
    original_rename = Path.rename
    def fail_activation(path, dest):
        if path.name == 'extracted':
            raise PermissionError('activation denied')
        return original_rename(path, dest)
    monkeypatch.setattr(Path, 'rename', fail_activation)
    ok, _ = EIUpdater(target).download_and_update('https://example.test/CLI.zip', '3.28.0.1')
    assert not ok
    assert (target / (CLI + '.exe')).read_bytes() == b'existing parser'
    assert (target / '.ei_version').read_text() == '3.27.0'
