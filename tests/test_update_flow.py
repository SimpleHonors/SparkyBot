"""UpdateFlow — the window-free update engine (slice 2 seam).

The whole point of the extraction: launch checks, manual checks, download,
and staging into .update_pending/ must all work when no window was ever
constructed (the start-minimized silent-failure bug). These tests therefore
never build a widget; the *_sync internals are driven directly with mocked
network I/O and signals captured through plain connections.
"""

import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))

pytest.importorskip("PySide6", reason="PySide6 not installed in this environment")

from core.update_flow import UpdateFlow, parse_version
from core.version import VERSION


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _Resp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code=200, headers=None, json_data=None, content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data
        self._content = content

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=8192):
        yield self._content


def _flow(tmp_path, check_on_launch=True):
    cfg = SimpleNamespace(check_updates_on_launch=check_on_launch)
    return UpdateFlow(cfg, app_root=tmp_path)


def _capture(signal):
    got = []
    signal.connect(lambda *args: got.append(args))
    return got


def _block_threads(monkeypatch):
    """Fail the test if a check/download verb actually spawns a thread."""
    import core.update_flow as uf
    started = []

    def _thread(**kwargs):
        started.append(kwargs)
        return SimpleNamespace(start=lambda: None)

    monkeypatch.setattr(uf, "threading", SimpleNamespace(Thread=_thread))
    return started


def _release_zip():
    """A release zip shaped like GitHub's: one top-level dir wrapping the tree."""
    buf = io.BytesIO()
    top = "SimpleHonors-SparkyBot-abc123/"
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(top, "")
        zf.writestr(top + "main.py", "print('new main')\n")
        zf.writestr(top + "core/config.py", "# new config module\n")
        zf.writestr(top + "config.properties", "[Discord]\n")      # protected
        zf.writestr(top + "GW2EI/EI.dll", "binary")                # protected
        zf.writestr(top + "LICENSE", "MIT")                        # repo-only
        zf.writestr(top + ".github/workflows/ci.yml", "on: push")  # repo-only
    return buf.getvalue()


# ---------------------------------------------------------------------------
# zero window dependency
# ---------------------------------------------------------------------------

def test_module_has_no_widget_dependency():
    # The seam contract: the flow must never grow a window dependency again.
    import core.update_flow as uf
    src = Path(uf.__file__).read_text(encoding="utf-8")
    assert "QtWidgets" not in src
    assert "gui_settings" not in src


# ---------------------------------------------------------------------------
# resolve_download_url
# ---------------------------------------------------------------------------

def test_resolve_url_prefers_zip_asset():
    data = {
        "assets": [
            {"name": "notes.txt", "browser_download_url": "http://x/notes.txt"},
            {"name": "SparkyBot-9.zip", "browser_download_url": "http://x/sb.zip"},
        ],
        "zipball_url": "http://x/zipball",
    }
    assert UpdateFlow.resolve_download_url(data) == "http://x/sb.zip"


def test_resolve_url_falls_back_to_zipball():
    assert UpdateFlow.resolve_download_url({"zipball_url": "http://x/zb"}) == "http://x/zb"
    # A .zip asset with a missing URL must still fall through to the zipball
    data = {"assets": [{"name": "a.zip"}], "zipball_url": "http://x/zb"}
    assert UpdateFlow.resolve_download_url(data) == "http://x/zb"


def test_resolve_url_none_when_nothing_usable():
    assert UpdateFlow.resolve_download_url({}) is None
    assert UpdateFlow.resolve_download_url({"zipball_url": "None"}) is None
    assert UpdateFlow.resolve_download_url(None) is None


# ---------------------------------------------------------------------------
# launch check gating
# ---------------------------------------------------------------------------

def test_check_on_launch_skips_when_disabled(tmp_path, monkeypatch):
    flow = _flow(tmp_path, check_on_launch=False)
    started = _block_threads(monkeypatch)
    flow.check_on_launch()
    assert not started


def test_check_on_launch_skips_when_update_pending(tmp_path, monkeypatch):
    # Anti-loop guard: a staged-but-unapplied update suppresses re-checking
    flow = _flow(tmp_path)
    flow.pending_dir().mkdir()
    started = _block_threads(monkeypatch)
    flow.check_on_launch()
    assert not started


def test_check_on_launch_spawns_worker_when_clear(tmp_path, monkeypatch):
    flow = _flow(tmp_path)
    started = _block_threads(monkeypatch)
    flow.check_on_launch()
    assert len(started) == 1 and started[0]["daemon"] is True


# ---------------------------------------------------------------------------
# launch check body
# ---------------------------------------------------------------------------

def test_launch_check_emits_available_from_redirect(tmp_path, monkeypatch):
    """Redirect resolves a newer version; throttled API (403) forces the
    synthesized release data with the conventional download URL."""
    flow = _flow(tmp_path)

    def fake_get(url, **kwargs):
        if url == flow.RELEASES_LATEST_URL:
            return _Resp(302, headers={
                "Location": "https://github.com/SimpleHonors/SparkyBot/releases/tag/v999.0.0"
            })
        return _Resp(403)

    monkeypatch.setattr("requests.get", fake_get)
    got = _capture(flow.sig_launch_available)
    ei_got = _capture(flow.sig_ei_launch_available)

    flow._check_on_launch_sync()

    assert len(got) == 1
    version, release_data = got[0]
    assert version == "999.0.0"
    url = UpdateFlow.resolve_download_url(release_data)
    assert url.endswith("v999.0.0/SparkyBot-999.0.0.zip")
    # SparkyBot needs the update, so the EI check must NOT run
    assert not ei_got


def test_launch_check_up_to_date_falls_through_to_ei(tmp_path, monkeypatch):
    flow = _flow(tmp_path)

    def fake_get(url, **kwargs):
        return _Resp(302, headers={
            "Location": f"https://github.com/SimpleHonors/SparkyBot/releases/tag/v{VERSION}"
        })

    class _FakeInvoker:
        def __init__(self, config):
            pass

        def get_gw2ei_folder(self):
            return tmp_path

    class _FakeEI:
        def __init__(self, folder):
            pass

        def check_for_update(self):
            return True, "3.5.0", "http://x/ei.zip"

        def get_current_version(self):
            return "3.4.0"

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr("core.gw2ei_invoker.GW2EIInvoker", _FakeInvoker)
    monkeypatch.setattr("core.ei_updater.EIUpdater", _FakeEI)

    got = _capture(flow.sig_launch_available)
    ei_got = _capture(flow.sig_ei_launch_available)

    flow._check_on_launch_sync()

    assert not got
    assert ei_got == [("3.4.0", "3.5.0", "http://x/ei.zip")]


# ---------------------------------------------------------------------------
# manual check body
# ---------------------------------------------------------------------------

def test_check_now_emits_available(tmp_path, monkeypatch):
    flow = _flow(tmp_path)
    data = {
        "tag_name": "v999.1.0",
        "assets": [{"name": "SparkyBot-999.1.0.zip",
                    "browser_download_url": "http://x/sb.zip"}],
    }
    monkeypatch.setattr("requests.get", lambda url, **kw: _Resp(200, json_data=data))

    progress = _capture(flow.sig_progress)
    got = _capture(flow.sig_available)

    flow._check_now_sync()

    assert ("Checking GitHub for updates...",) in progress
    assert got == [("999.1.0", data)]


def test_check_now_up_to_date(tmp_path, monkeypatch):
    flow = _flow(tmp_path)
    data = {"tag_name": f"v{VERSION}", "assets": []}
    monkeypatch.setattr("requests.get", lambda url, **kw: _Resp(200, json_data=data))

    not_avail = _capture(flow.sig_not_available)
    got = _capture(flow.sig_available)

    flow._check_now_sync()

    assert not got
    assert len(not_avail) == 1 and "latest SparkyBot" in not_avail[0][0]


def test_check_now_api_failure_emits_error(tmp_path, monkeypatch):
    flow = _flow(tmp_path)
    monkeypatch.setattr("requests.get", lambda url, **kw: _Resp(500))

    errors = _capture(flow.sig_error)
    flow._check_now_sync()
    assert errors == [("GitHub API returned 500",)]


# ---------------------------------------------------------------------------
# download + staging
# ---------------------------------------------------------------------------

def test_download_and_stage_writes_pending_tree(tmp_path, monkeypatch):
    flow = _flow(tmp_path)
    monkeypatch.setattr(
        "requests.get", lambda url, **kw: _Resp(200, content=_release_zip())
    )

    staged = _capture(flow.sig_staged)
    errors = _capture(flow.sig_error)

    flow._download_and_stage_sync("http://x/sb.zip", "9.9.9")

    assert not errors
    assert staged == [("9.9.9",)]
    pending = flow.pending_dir()
    assert flow.has_pending_update()
    # Top-level zip dir stripped; live-tree files staged
    assert (pending / "main.py").read_text() == "print('new main')\n"
    assert (pending / "core" / "config.py").exists()
    # User data is never staged; repo-only files are skipped
    assert not (pending / "config.properties").exists()
    assert not (pending / "GW2EI").exists()
    assert not (pending / "LICENSE").exists()
    assert not (pending / ".github").exists()


def test_download_failure_emits_error(tmp_path, monkeypatch):
    flow = _flow(tmp_path)

    def boom(url, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr("requests.get", boom)
    errors = _capture(flow.sig_error)
    staged = _capture(flow.sig_staged)

    flow._download_and_stage_sync("http://x/sb.zip", "9.9.9")

    assert not staged
    assert len(errors) == 1 and errors[0][0].startswith("Update failed:")
    assert not flow.has_pending_update()


def test_start_update_without_url_errors_and_never_downloads(tmp_path, monkeypatch):
    flow = _flow(tmp_path)
    downloads = []
    monkeypatch.setattr(flow, "download_and_stage", lambda *a: downloads.append(a))
    errors = _capture(flow.sig_error)

    flow.start_update({"assets": []}, "9.9.9")

    assert not downloads
    assert len(errors) == 1


def test_start_update_resolves_and_delegates(tmp_path, monkeypatch):
    flow = _flow(tmp_path)
    downloads = []
    monkeypatch.setattr(flow, "download_and_stage", lambda *a: downloads.append(a))

    flow.start_update(
        {"assets": [{"name": "a.zip", "browser_download_url": "http://x/a.zip"}]},
        "9.9.9",
    )
    assert downloads == [("http://x/a.zip", "9.9.9")]


# ---------------------------------------------------------------------------
# version parsing
# ---------------------------------------------------------------------------

def test_parse_version_tolerates_prefixes_and_junk():
    assert parse_version("v1.5") == (1, 5)
    assert parse_version("1.12.3") == (1, 12, 3)
    assert parse_version("1.x.3") == (1, 0, 3)
    assert parse_version("") == (0,)
    assert parse_version(None) == (0,)
    assert parse_version("2.0") > parse_version("1.9.9")
