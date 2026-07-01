import configparser
from core.config import Config


def test_reasoning_strategy_defaults_empty(tmp_path):
    p = tmp_path / "settings.ini"
    p.write_text("[AI]\naiProvider = custom\n")
    cfg = Config(str(p))
    assert cfg.ai_reasoning_strategy == ""


def test_reasoning_strategy_reads_value(tmp_path):
    p = tmp_path / "settings.ini"
    p.write_text("[AI]\naiProvider = custom\naiReasoningStrategy = think_enable\n")
    cfg = Config(str(p))
    assert cfg.ai_reasoning_strategy == "think_enable"
