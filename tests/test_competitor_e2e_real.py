"""Real end-to-end regression: plant an actual filesystem layout and run the
SHIPPED detection -> merge -> apply chain with NO mocks. Every other
competitor test mocks discovery; this one proves the real code path writes a
correct config from real files on disk (the bug class mocks cannot catch)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.arcdps_config import discover_arcdps_setups, discover_gw2_installations
from core.competitor_import import (
    apply_competitor_import,
    build_import_plan,
    discover_competitor_configs,
    merge_competitor_findings,
)
from core.config import Config

H1 = "https://discord.com/api/webhooks/111111111111111111/logspamtoken"
H2 = "https://discord.com/api/webhooks/222222222222222222/nightlytoken"
H3 = "https://discord.com/api/webhooks/333333333333333333/plenbottoken"


def _plant(base: Path):
    gw2 = base / "Games" / "Guild Wars 2"
    (gw2 / "addons" / "arcdps").mkdir(parents=True)
    (gw2 / "Gw2-64.exe").write_bytes(b"MZ")
    custom_logs = base / "D_drive" / "arcdps_logs"
    (custom_logs / "arcdps.cbtlogs" / "1").mkdir(parents=True)
    (gw2 / "addons" / "arcdps" / "arcdps.ini").write_text(
        f"[session]\nboss_encounter_path={custom_logs}\n", encoding="utf-8"
    )
    tools = base / "Tools"
    mz = tools / "MzFightReporter"
    mz.mkdir(parents=True)
    (mz / "config.properties").write_text(
        f"discordWebhook={H1}\ndiscordWebhookName1=Logspam\n"
        f"discordWebhook2={H2}\ndiscordWebhookName2=NightlyLogs\n"
        "activeDiscordWebhook=1\nraidReportDiscordWebhook=2\n"
        f"customLogFolder={custom_logs / 'arcdps.cbtlogs'}\n"
        "minFightDuration=7\nembedColor=0x00A86B\n",
        encoding="utf-8",
    )
    plen = tools / "PlenBotLogUploader"
    plen.mkdir(parents=True)
    (plen / "app_settings.json").write_text(
        json.dumps({"logsLocation": str(custom_logs / "arcdps.cbtlogs")}),
        encoding="utf-8",
    )
    (plen / "discord_webhooks.json").write_text(
        json.dumps([{"isActive": True, "name": "PlenChannel", "url": H3}]),
        encoding="utf-8",
    )
    return gw2, tools


def test_real_detection_merge_apply_writes_correct_config(tmp_path):
    gw2, tools = _plant(tmp_path)

    # Real GW2 detection at a nonstandard location.
    installs = discover_gw2_installations(extra_candidates=(gw2,), include_system=False)
    assert any(Path(i.directory) == gw2 for i in installs)

    # Real ArcDPS custom boss_encounter_path detection.
    setups = discover_arcdps_setups(
        tmp_path / "Documents", gw2_installations=installs, include_system=False
    )
    assert any("arcdps_logs" in str(s.log_directory) for s in setups)

    # Real competitor discovery — both tools, from real config files.
    findings = discover_competitor_configs(
        gw2_dirs=[i.directory for i in installs], portable_roots=[tools]
    )
    apps = sorted({f.app for f in findings})
    assert apps == ["MzFightReporter", "PlenBot Log Uploader"], apps

    # Real merge (MzFightReporter primary) + real apply to a fresh config.
    primary = next(i for i, f in enumerate(findings) if "Mz" in f.app)
    merged = merge_competitor_findings(findings, primary_index=primary)
    plan = build_import_plan(merged)
    cfg = Config(tmp_path / "config.properties")
    apply_competitor_import(cfg, plan, persist=True, include_discord=True)

    written = (tmp_path / "config.properties").read_text(encoding="utf-8")
    g = cfg._config.get
    assert g("Discord", "discordWebhook") == H1          # Logspam -> slot 1
    assert H2 in written                                  # Nightly present
    assert H3 in written                                  # PlenBot unioned in
    assert g("Discord", "enableDiscordBot") == "true"
    assert g("Thresholds", "minFightDuration") == "7"     # Mz parity setting
    assert g("Paths", "logFolder")                        # a real log folder
    assert g("AI", "enableAiAnalysis") == "false"         # optional off
    assert g("Twitch", "enableTwitchBot") == "false"


def test_sparkybot_own_config_is_not_self_detected(tmp_path):
    # SparkyBot's own config.properties (logFolder, no customLogFolder) must
    # NOT be picked up as MzFightReporter — the guard that made the first
    # real-E2E run correctly reject an unrealistic fixture.
    own = tmp_path / "SparkyBot"
    own.mkdir()
    Config(own / "config.properties").save()
    findings = discover_competitor_configs(portable_roots=[tmp_path])
    assert all("MzFightReporter" != f.app for f in findings)
