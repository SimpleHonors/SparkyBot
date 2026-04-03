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
            'guildIcon': 'assets/wvw_icon.png',
            'embedColor': '0x00A86B',
        },
        'Paths': {
            'logFolder': '',
            'gw2eiExe': 'GuildWars2EliteInsights-CLI.exe',
            'pollInterval': '5',
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
            'showStrips': 'true',
            'showCleanses': 'true',
            'showDownsKills': 'true',
            'showBurstDmg': 'true',
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
            'hideConsole': 'false',
            'maxParseMemory': '4096',
            'checkUpdatesOnLaunch': 'true',
        },
        'AI': {
            'enableAiAnalysis': 'false',
            'aiProvider': 'Custom',
            'aiBaseUrl': '',
            'aiApiKey': '',
            'aiModel': '',
            'aiSystemPrompt': '',
            'aiMaxTokens': '350',
            'aiPromptVersion': '0',
            'aiVocabWeightShock': '33',
            'aiVocabWeightPositive': '33',
            'aiVocabWeightNegative': '33',
            'aiVocabWeightGates': '33',
        },
        'Twitch': {
            'enableTwitchBot': 'false',
            'twitchChannelName': '',
            'twitchBotToken': '',
            'twitchUseTLS': 'true',
        },
        'TTS': {
            'enableTts': 'false',
            'ttsProvider': 'edge',
            'ttsEdgeVoice': 'en-GB-RyanNeural',
            'ttsVolume': '80',
            'ttsDiscordAttach': 'false',
            'ttsElevenLabsApiKey': '',
            'ttsElevenLabsVoiceId': 'JBFqnCBsd6RMkjVDRZzb',
            'ttsElevenLabsModel': 'eleven_multilingual_v2',
            'ttsElevenLabsStability': '0.35',
            'ttsElevenLabsSimilarityBoost': '0.75',
            'ttsElevenLabsStyle': '0.15',
            'ttsElevenLabsSpeakerBoost': 'true',
            'ttsElevenLabsSpeed': '1.0',
        }
    }

    def __init__(self, config_path: Optional[Union[str, Path]] = None):
        self._config = configparser.ConfigParser(interpolation=None)
        # Establish all sections and keys as guaranteed fallbacks before reading user file
        self._config.read_dict(self._DEFAULTS)

        # Use app root as home_dir - stable regardless of working directory or config location
        self.home_dir = Path(__file__).parent.parent

        if config_path is None:
            config_path = self.home_dir / "config.properties"
        else:
            config_path = Path(config_path)

        self.is_new_config = not config_path.exists()

        if config_path.exists():
            self._config.read(config_path)
        else:
            # Do NOT write to disk here. Just use in-memory defaults.
            # The file will only be created when save() is explicitly called.
            pass

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
        self.discord_webhook_label = self._config.get('Discord', 'discordWebhookLabel', fallback='SparkyBot')
        self.active_discord_webhook = self._config.getint('Discord', 'activeDiscordWebhook')
        self.enable_discord_bot = self._config.getboolean('Discord', 'enableDiscordBot')
        self.guild_icon = self._config.get('Discord', 'guildIcon')
        self.embed_color = self._get_int('Discord', 'embedColor', 0x00A86B)

        # Paths - support both new 'logFolder' and legacy 'defaultLogFolder'
        self.log_folder = self._config.get('Paths', 'logFolder', fallback='')
        self.gw2ei_exe = self._config.get('Paths', 'gw2eiExe')
        self.poll_interval = self._get_int('Paths', 'pollInterval', 5)

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
        self.show_strips = self._config.getboolean('UI', 'showStrips')
        self.show_cleanses = self._config.getboolean('UI', 'showCleanses')
        self.show_downs_kills = self._config.getboolean('UI', 'showDownsKills')
        self.show_burst_dmg = self._config.getboolean('UI', 'showBurstDmg')
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
        self.hide_console = self._config.getboolean('Behavior', 'hideConsole')
        self.max_parse_memory = self._config.getint('Behavior', 'maxParseMemory')
        self.check_updates_on_launch = self._config.getboolean('Behavior', 'checkUpdatesOnLaunch')

        # AI Analysis settings
        self.enable_ai_analysis = self._config.getboolean('AI', 'enableAiAnalysis')
        self.ai_provider = self._config.get('AI', 'aiProvider')
        self.ai_base_url = self._config.get('AI', 'aiBaseUrl')
        self.ai_api_key = self._config.get('AI', 'aiApiKey')
        self.ai_model = self._config.get('AI', 'aiModel')
        self.ai_system_prompt = self._config.get('AI', 'aiSystemPrompt')
        self.ai_max_tokens = self._get_int('AI', 'aiMaxTokens', 350)
        self.ai_prompt_version = self._get_int('AI', 'aiPromptVersion', 0)
        self.ai_vocab_weight_shock = self._get_int('AI', 'aiVocabWeightShock', 33) / 100.0
        self.ai_vocab_weight_positive = self._get_int('AI', 'aiVocabWeightPositive', 33) / 100.0
        self.ai_vocab_weight_negative = self._get_int('AI', 'aiVocabWeightNegative', 33) / 100.0
        self.ai_vocab_weight_gates = self._get_int('AI', 'aiVocabWeightGates', 33) / 100.0

        # Twitch settings
        self.enable_twitch = self._config.getboolean('Twitch', 'enableTwitchBot')
        self.twitch_channel = self._config.get('Twitch', 'twitchChannelName')
        self.twitch_token = self._config.get('Twitch', 'twitchBotToken')
        self.twitch_use_tls = self._config.getboolean('Twitch', 'twitchUseTLS', fallback=True)

        # TTS settings
        self.tts_enabled = self._config.getboolean('TTS', 'enableTts')
        self.tts_provider = self._config.get('TTS', 'ttsProvider')
        self.tts_edge_voice = self._config.get('TTS', 'ttsEdgeVoice')
        self.tts_volume = self._get_int('TTS', 'ttsVolume', 80)
        self.tts_discord_attach = self._config.getboolean('TTS', 'ttsDiscordAttach')
        self.tts_elevenlabs_api_key = self._config.get('TTS', 'ttsElevenLabsApiKey')
        self.tts_elevenlabs_voice_id = self._config.get('TTS', 'ttsElevenLabsVoiceId')
        self.tts_elevenlabs_model = self._config.get('TTS', 'ttsElevenLabsModel')
        self.tts_elevenlabs_stability = float(
            self._config.get('TTS', 'ttsElevenLabsStability', fallback='0.35')
        )
        self.tts_elevenlabs_similarity_boost = float(
            self._config.get('TTS', 'ttsElevenLabsSimilarityBoost', fallback='0.75')
        )
        self.tts_elevenlabs_style = float(
            self._config.get('TTS', 'ttsElevenLabsStyle', fallback='0.15')
        )
        self.tts_elevenlabs_speaker_boost = self._config.getboolean(
            'TTS', 'ttsElevenLabsSpeakerBoost', fallback=True
        )
        self.tts_elevenlabs_speed = float(
            self._config.get('TTS', 'ttsElevenLabsSpeed', fallback='1.0')
        )

    def get_thumbnail_path(self):
        if not self.guild_icon:
            return None

        home_dir = Path(__file__).parent.parent
        icon_path = Path(self.guild_icon)

        if not icon_path.is_absolute():
            icon_path = home_dir / icon_path

        if icon_path.exists():
            return str(icon_path)

        # Migration fallback: check assets/ for files referenced without the prefix
        if not self.guild_icon.startswith('assets'):
            migrated_path = home_dir / "assets" / self.guild_icon
            if migrated_path.exists():
                return str(migrated_path)

        return None

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
