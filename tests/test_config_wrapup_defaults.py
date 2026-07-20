"""The recap extras ship OFF until the recap is respec'd (operator call,
2026-07-19): no Wrap-Up embed, no AI zingers, no voice recap by default.
Explicit config keys still opt back in."""

from core.config import Config


def test_wrapup_extras_default_off(tmp_path):
    p = tmp_path / "settings.ini"
    p.write_text("[RaidReport]\n")
    cfg = Config(str(p))
    assert cfg.raidreport_wrapup is False
    assert cfg.raidreport_wrapup_ai is False
    assert cfg.raidreport_wrapup_voice is False
    assert cfg.raidreport_always_zip is False


def test_wrapup_extras_opt_in_still_works(tmp_path):
    p = tmp_path / "settings.ini"
    p.write_text(
        "[RaidReport]\n"
        "raidreportWrapup = true\n"
        "raidreportWrapupAi = true\n"
        "raidreportWrapupVoice = true\n"
    )
    cfg = Config(str(p))
    assert cfg.raidreport_wrapup is True
    assert cfg.raidreport_wrapup_ai is True
    assert cfg.raidreport_wrapup_voice is True
