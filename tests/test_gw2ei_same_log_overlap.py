import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import core.gw2ei_invoker as invoker_module
from core.gw2ei_invoker import GW2EIInvoker


def test_same_log_parses_return_isolated_json_files(tmp_path, monkeypatch):
    """Live watcher and End Run must not share GW2EI's output path."""
    gw2ei_home = tmp_path / "GW2EI"
    gw2ei_home.mkdir()
    (gw2ei_home / "GuildWars2EliteInsights-CLI.exe").touch()
    monkeypatch.setattr(invoker_module, "gw2ei_dir", lambda: gw2ei_home)

    log_file = tmp_path / "20260831-raid.zevtc"
    log_file.touch()
    generated = tmp_path / "20260831-raid_detailed_wvw_kill.json"

    active = 0
    max_active = 0
    counter_lock = threading.Lock()

    def fake_run(*_args, **_kwargs):
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            # Give the second in-process caller time to enter the same parse.
            time.sleep(0.05)
            generated.write_text('{"fight": "valid"}', encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(invoker_module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        GW2EIInvoker, "_wait_for_json_stable", lambda *_args, **_kwargs: True
    )

    config = SimpleNamespace(gw2ei_exe="GuildWars2EliteInsights-CLI.exe")
    watcher_invoker = GW2EIInvoker(config)
    end_run_invoker = GW2EIInvoker(config)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                watcher_invoker.parse_file,
                log_file,
                config_name="watcher.conf",
            ),
            pool.submit(
                end_run_invoker.parse_file,
                log_file,
                config_name="end-run.conf",
            ),
        ]
        results = [future.result() for future in futures]

    assert max_active == 1
    assert all(path is not None and path.is_file() for path in results)
    assert results[0] != results[1]
    assert [json.loads(path.read_text(encoding="utf-8")) for path in results] == [
        {"fight": "valid"},
        {"fight": "valid"},
    ]
