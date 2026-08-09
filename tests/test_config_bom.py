import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import Config


def test_windows_utf8_bom_config_loads(tmp_path):
    path = tmp_path / "config.properties"
    path.write_bytes(b"\xef\xbb\xbf[Discord]\nenableDiscordBot = true\n")

    config = Config(path)

    assert config.enable_discord_bot is True
