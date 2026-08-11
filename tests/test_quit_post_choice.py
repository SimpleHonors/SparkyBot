from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

import core.main_window as main_window
from core.main_window import MainWindow


class _QuitWindow:
    def __init__(self, choice):
        self.choice = choice
        self.hidden = False
        self.ended = []
        self.quit_now = False

    def _run_confirm_needed(self):
        return True

    def _confirm_quit_with_run(self):
        return self.choice

    def hide(self):
        self.hidden = True

    def _end_run_and_quit(self, auto_post):
        self.ended.append(auto_post)

    def _quit_app_now(self):
        self.quit_now = True


@pytest.mark.parametrize(
    ("choice", "expected_hidden", "expected_posts"),
    [
        ("tray", True, []),
        ("end_no_post", False, [False]),
        ("end_post", False, [True]),
        ("cancel", False, []),
    ],
)
def test_quit_with_open_run_honors_explicit_post_choice(
        choice, expected_hidden, expected_posts):
    window = _QuitWindow(choice)

    MainWindow._quit_app(window)

    assert window.hidden is expected_hidden
    assert window.ended == expected_posts
    assert window.quit_now is False


class _RunSession:
    recorded_logs = []

    def end(self):
        stamp = datetime(2026, 8, 10, 19, 0)
        return stamp, stamp


class _EndRunWindow:
    def __init__(self):
        self._run_session = _RunSession()
        self.config = SimpleNamespace(run_auto_post=True)
        self.calls = []

    def _discover_run_logs(self):
        return []

    def _start_run_report(self, selected, name, auto_post, quit_after=False):
        self.calls.append((auto_post, quit_after))


@pytest.mark.parametrize("explicit_choice", [False, True])
def test_end_run_uses_explicit_choice_not_remembered_setting(
        monkeypatch, explicit_choice):
    window = _EndRunWindow()
    selected = [SimpleNamespace(timestamp=datetime(2026, 8, 10, 19, 7))]
    monkeypatch.setattr(main_window, "collect_run_logs", lambda *args: selected)

    MainWindow._end_run_and_quit(window, auto_post=explicit_choice)

    assert window.calls == [(explicit_choice, True)]
