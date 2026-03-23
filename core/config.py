"""Configuration management for SparkyBot"""

import logging
import os
import configparser
from pathlib import Path
from typing import List, Optional, Union


class Config:
    """Manages application configuration from config.properties"""

    # Default configuration - used both for read_dict and for creating new config files
    _DEFAULTS = {
        'Discord': {
            'discordWebhook': '',
            'discordWebhook2': '',
            'discordWebhook3': '',
            'discordWebhookLabel': 'SparkyBot',
            'activeDiscordWebhook': '1',
            'enableDiscordBot': 'true',
            'guildIcon': 'wvw_icon.png',
            'embedColor': '0x00A86B',
        },
        'Paths': {
            'logFolder': '',
            'gw2eiExe': 'GuildWars2EliteInsights-CLI.exe',
        },
        'Thresholds': {
            'minFightDuration': '10',
            'minFightDowns': '5',
            'minFightTotalDmg': '50000',
            'maxUploadSize': '50',
            'uploadLargeAfterParse': 'false',
        },
        'UI': {
            'showDamage': 'true',
            'showHeals': 'true',
            'showDefense': 'true',
            'showCCs': 'true',
            'showCleanses': 'true',
            'showDownsKills': 'true',
            'showBurstDmg': 'true',
            'showSpikeDmg': 'true',
            'showTopEnemySkills': 'true',
            'showOffensiveBoons': 'true',
            'showDefensiveBoons': 'true',
            'showEnemyBreakdown': 'true',
            'showQuickReport': 'true',
        },
        'Behavior': {
            'closeToTray': 'false',
            'minimizeToTray': 'true',
            'startMinimized': 'false',
            'startWatcherOnStartup': 'false',
            'maxParseMemory': '4096',
        }
    }

    def __init__(self, config_path: Optional[Union[str, Path]] = None):
        self._config = configparser.ConfigParser()
        # Establish all sections and keys as guaranteed fallbacks before reading user file
        self._config.read_dict(self._DEFAULTS)

        # Use app root as home_dir - stable regardless of working directory or config location
        self.home_dir = Path(__file__).parent.parent

        if config_path is None:
            config_path = self.home_dir / "config.properties"
        else:
            config_path = Path(config_path)

        if config_path.exists():
            self._config.read(config_path)
        else:
            self._create_default_config(config_path)

        self._load_values()

    def _create_default_config(self, config_path: Path):
        """Create default configuration file"""
        # All sections/keys already seeded by read_dict(_DEFAULTS) in __init__
        try:
            with open(config_path, 'w') as f:
                self._config.write(f)
        except OSError as e:
            logging.getLogger(__name__).warning(
                f"Could not write default config file {config_path}: {e}. "
                "Using in-memory defaults."
            )

    def _get_int(self, section: str, key: str, default: int) -> int:
        """Read an integer from config, supporting hex (0x...) and decimal."""
        try:
            val = self._config.get(section, key)
            if val.startswith('0x') or val.startswith('0X'):
                return int(val, 16)
            return int(val)
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return default

    def _load_values(self):
        """Load configuration values into object attributes"""
        # All sections and keys are guaranteed to exist via read_dict(_DEFAULTS) in __init__
        # Discord
        self.discord_webhook = self._config.get('Discord', 'discordWebhook')
        self.discord_webhook2 = self._config.get('Discord', 'discordWebhook2')
        self.discord_webhook3 = self._config.get('Discord', 'discordWebhook3')
        self.discord_webhook_label = self._config.get('Discord', 'discordWebhookLabel')
        self.active_discord_webhook = self._config.getint('Discord', 'activeDiscordWebhook')
        self.enable_discord_bot = self._config.getboolean('Discord', 'enableDiscordBot')
        self.guild_icon = self._config.get('Discord', 'guildIcon')
        self.embed_color = self._get_int('Discord', 'embedColor', 0x00A86B)

        # Paths - support both new 'logFolder' and legacy 'defaultLogFolder'
        self.log_folder = self._config.get('Paths', 'logFolder', fallback='')
        self.gw2ei_exe = self._config.get('Paths', 'gw2eiExe')

        # Thresholds
        self.min_fight_duration = self._config.getint('Thresholds', 'minFightDuration')
        self.min_fight_downs = self._config.getint('Thresholds', 'minFightDowns')
        self.min_fight_total_dmg = self._config.getint('Thresholds', 'minFightTotalDmg')
        self.max_upload_size = self._config.getint('Thresholds', 'maxUploadSize')
        self.upload_large_after_parse = self._config.getboolean('Thresholds', 'uploadLargeAfterParse')

        # UI / Display settings
        self.show_damage = self._config.getboolean('UI', 'showDamage')
        self.show_heals = self._config.getboolean('UI', 'showHeals')
        self.show_defense = self._config.getboolean('UI', 'showDefense')
        self.show_ccs = self._config.getboolean('UI', 'showCCs')
        self.show_cleanses = self._config.getboolean('UI', 'showCleanses')
        self.show_downs_kills = self._config.getboolean('UI', 'showDownsKills')
        self.show_burst_dmg = self._config.getboolean('UI', 'showBurstDmg')
        self.show_spike_dmg = self._config.getboolean('UI', 'showSpikeDmg')
        self.show_top_enemy_skills = self._config.getboolean('UI', 'showTopEnemySkills')
        self.show_offensive_boons = self._config.getboolean('UI', 'showOffensiveBoons')
        self.show_defensive_boons = self._config.getboolean('UI', 'showDefensiveBoons')
        self.show_enemy_breakdown = self._config.getboolean('UI', 'showEnemyBreakdown')

        # Behavior settings
        self.close_to_tray = self._config.getboolean('Behavior', 'closeToTray')
        self.minimize_to_tray = self._config.getboolean('Behavior', 'minimizeToTray')
        self.start_minimized = self._config.getboolean('Behavior', 'startMinimized')
        self.show_quick_report = self._config.getboolean('UI', 'showQuickReport')
        self.start_watcher_on_startup = self._config.getboolean('Behavior', 'startWatcherOnStartup')
        self.max_parse_memory = self._config.getint('Behavior', 'maxParseMemory')

    def get_thumbnail_path(self) -> str:
        """Get absolute path to the thumbnail/guild icon file."""
        if not self.guild_icon:
            return ""
        candidate = Path(__file__).parent.parent / self.guild_icon
        return str(candidate) if candidate.exists() else ""

    def get_current_discord_webhook(self) -> str:
        """Get the active Discord webhook URL based on activeDiscordWebhook setting"""
        if self.active_discord_webhook == 1:
            return self.discord_webhook
        elif self.active_discord_webhook == 2:
            return self.discord_webhook2
        elif self.active_discord_webhook == 3:
            return self.discord_webhook3
        else:
            logger = logging.getLogger(__name__)
            logger.warning(
                f"Invalid activeDiscordWebhook value {self.active_discord_webhook!r}; "
                "expected 1, 2, or 3. Using primary webhook."
            )
            return self.discord_webhook

    def get_all_discord_webhooks(self) -> List[str]:
        """Get all configured Discord webhook URLs"""
        webhooks = []
        if self.discord_webhook:
            webhooks.append(self.discord_webhook)
        if self.discord_webhook2:
            webhooks.append(self.discord_webhook2)
        if self.discord_webhook3:
            webhooks.append(self.discord_webhook3)
        return webhooks

    def get_log_folders(self) -> List[Path]:
        """Get all configured log folders"""
        folders = []
        if self.log_folder:
            if os.path.exists(self.log_folder):
                folders.append(Path(self.log_folder))
            else:
                logger = logging.getLogger(__name__)
                logger.warning(
                    f"Configured log folder does not exist: {self.log_folder}. "
                    "Check that the path is correct and any network drives are mounted."
                )
        return folders

    def save(self, config_path: Optional[Union[str, Path]] = None):
        """Save current config values to disk and reload attributes

        Args:
            config_path: Path to write to. Defaults to home_dir / 'config.properties'.
        """
        if config_path is None:
            config_path = self.home_dir / "config.properties"
        else:
            config_path = Path(config_path)

        try:
            with open(config_path, 'w') as f:
                self._config.write(f)
            self._load_values()
        except OSError as e:
            logging.getLogger(__name__).warning(
                f"Could not save config to {config_path}: {e}"
            )
            return False
        return True

    def update(self, section: str, key: str, value: str):
        """Update a single config value in memory."""
        self._config.set(section, key, value)
