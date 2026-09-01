"""Configuration management for SparkyBot"""

import logging
import os
import configparser
import tempfile
from pathlib import Path
from typing import List, Optional, Union

from core.apppaths import app_dir


class Config:
    """Manages application configuration from config.properties"""

    # Config format version stamped into the user's file on every save
    # (Behavior/configVersion). Bump when a one-shot migration is added;
    # migrations gate on the version loaded from the file (config_version).
    # Deliberately NOT in _DEFAULTS: read_dict-seeding would make an old file
    # that lacks the key indistinguishable from one written at the current
    # version, which is exactly the signal migrations need.
    CONFIG_VERSION = 2

    # Default configuration - used both for read_dict and for creating new config files
    _DEFAULTS = {
        'Discord': {
            'discordWebhook': '',
            'discordWebhook2': '',
            'discordWebhook3': '',
            'discordWebhookName1': '',
            'discordWebhookName2': '',
            'discordWebhookName3': '',
            'discordWebhookLabel': 'SparkyBot',
            'activeDiscordWebhook': '1',
            # 0 follows activeDiscordWebhook; 1-3 select a dedicated
            # destination for end-of-run and manual Raid Reports.
            'raidReportDiscordWebhook': '0',
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
            'aiMaxTokens': '450',
            'aiTimeout': '30',
            'aiPromptVersion': '0',
            'aiVocabWeightShock': '33',
            'aiVocabWeightPositive': '33',
            'aiVocabWeightNegative': '33',
            'aiVocabWeightGates': '33',
            'aiDisableThinking': 'false',
            'aiUseV3Pipeline': 'false',
            'aiReasoningStrategy': '',
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
            'ttsLocalUrl': 'http://127.0.0.1:5820',
            'ttsLocalVoice': '',
        },
        'RaidReport': {
            # How raid reports get made (usage-mode preference):
            # 'run-button' = one-button Start Run / End Run flow (default),
            # 'manual' = the user picks fights on the Raid Report page.
            # The behavior lands with the Home/run-session slices; the
            # Settings switch persists the choice now.
            'runMode': 'run-button',
            # Remembered End Run auto-post choice (the End Run confirm
            # dialog's checkbox and Settings > Raid Reports both write it).
            'runAutoPost': 'true',
            'raidreportCacheEnabled': 'true',
            'raidreportCacheDir': '',
            'raidreportCacheRetentionHours': '48',
            'raidreportViewerHtml': '',
            'raidreportOutputDir': '',
            'reportDefaultView': 'sparky',
            'raidreportAlwaysZip': 'false',
            'raidreportPoisonTab': 'true',
            # Wrap-up embed, AI zingers, and voice recap are parked until
            # the recap is respec'd with data worth reporting — the
            # operator judged the current recap output not useful
            # (2026-07-19). Settings toggles still work for opting in.
            'raidreportWrapup': 'false',
            'raidreportWrapupAi': 'false',
            'raidreportWrapupVoice': 'false',
        }
    }

    def __init__(self, config_path: Optional[Union[str, Path]] = None):
        self._config = configparser.ConfigParser(interpolation=None)
        # Establish all sections and keys as guaranteed fallbacks before reading user file
        self._config.read_dict(self._DEFAULTS)

        # Use app root as home_dir - stable regardless of working directory or config location
        self.home_dir = app_dir()

        if config_path is None:
            config_path = self.home_dir / "config.properties"
        else:
            config_path = Path(config_path)
        self.config_path = config_path

        self.is_new_config = not config_path.exists()

        if config_path.exists():
            # utf-8-sig accepts both ordinary UTF-8 and files written with the
            # Windows BOM. The latter must not turn "[Discord]" into an invalid
            # section header.
            self._config.read(config_path, encoding='utf-8-sig')
        else:
            # Do NOT write to disk here. Just use in-memory defaults.
            # The file will only be created when save() is explicitly called.
            pass

        # Format version found in the user's file BEFORE this run stamps it —
        # the gate for one-shot migrations. An existing file without the key
        # is a pre-marker config (1); a brand-new config needs no migration.
        # Absent or malformed values are tolerated, never a load failure.
        if self._config.has_option('Behavior', 'configVersion'):
            self.config_version = self._get_int('Behavior', 'configVersion', 1)
        else:
            self.config_version = self.CONFIG_VERSION if self.is_new_config else 1
        # Stamp the current format version so any save() writes the marker.
        self._config.set('Behavior', 'configVersion', str(self.CONFIG_VERSION))

        self._load_values()

    def _create_default_config(self, config_path: Path):
        """Create default configuration file"""
        # All sections/keys already seeded by read_dict(_DEFAULTS) in __init__
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
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
        self.discord_webhook_name1 = self._config.get(
            'Discord', 'discordWebhookName1', fallback='')
        self.discord_webhook_name2 = self._config.get(
            'Discord', 'discordWebhookName2', fallback='')
        self.discord_webhook_name3 = self._config.get(
            'Discord', 'discordWebhookName3', fallback='')
        self.discord_webhook_label = self._config.get('Discord', 'discordWebhookLabel', fallback='SparkyBot')
        self.active_discord_webhook = self._config.getint('Discord', 'activeDiscordWebhook')
        self.raid_report_discord_webhook = self._get_int(
            'Discord', 'raidReportDiscordWebhook', 0)
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
        self.ai_max_tokens = self._get_int('AI', 'aiMaxTokens', 450)
        self.ai_timeout = self._get_int('AI', 'aiTimeout', 30)
        self.ai_prompt_version = self._get_int('AI', 'aiPromptVersion', 0)
        self.ai_use_v3_pipeline = self._config.getboolean('AI', 'aiUseV3Pipeline', fallback=False)
        self.ai_vocab_weight_shock = self._get_int('AI', 'aiVocabWeightShock', 33) / 100.0
        self.ai_vocab_weight_positive = self._get_int('AI', 'aiVocabWeightPositive', 33) / 100.0
        self.ai_vocab_weight_negative = self._get_int('AI', 'aiVocabWeightNegative', 33) / 100.0
        self.ai_vocab_weight_gates = self._get_int('AI', 'aiVocabWeightGates', 33) / 100.0
        self.ai_disable_thinking = self._config.getboolean('AI', 'aiDisableThinking', fallback=False)
        self.ai_reasoning_strategy = self._config.get('AI', 'aiReasoningStrategy', fallback='')

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
        self.tts_local_url = self._config.get(
            'TTS', 'ttsLocalUrl', fallback='http://127.0.0.1:5820'
        )
        self.tts_local_voice = self._config.get('TTS', 'ttsLocalVoice', fallback='')

        # RaidReport settings
        raw_run_mode = self._config.get('RaidReport', 'runMode', fallback='run-button')
        self.raidreport_run_mode = raw_run_mode if raw_run_mode in ('run-button', 'manual') else 'run-button'
        self.run_auto_post = self._config.getboolean('RaidReport', 'runAutoPost', fallback=True)
        self.raidreport_cache_enabled = self._config.getboolean('RaidReport', 'raidreportCacheEnabled')
        self.raidreport_cache_dir = self._config.get('RaidReport', 'raidreportCacheDir', fallback='')
        self.raidreport_cache_retention_hours = self._config.getint('RaidReport', 'raidreportCacheRetentionHours')
        self.raidreport_viewer_html = self._config.get('RaidReport', 'raidreportViewerHtml', fallback='')
        self.raidreport_output_dir = self._config.get('RaidReport', 'raidreportOutputDir', fallback='')
        raw_default_view = self._config.get(
            'RaidReport', 'reportDefaultView', fallback='sparky'
        ).strip().casefold()
        self.raidreport_default_view = (
            raw_default_view
            if raw_default_view in ('sparky', 'simple', 'classic')
            else 'sparky'
        )
        self.raidreport_always_zip = self._config.getboolean('RaidReport', 'raidreportAlwaysZip')
        self.raidreport_poison_tab = self._config.getboolean('RaidReport', 'raidreportPoisonTab')
        self.raidreport_wrapup = self._config.getboolean('RaidReport', 'raidreportWrapup')
        self.raidreport_wrapup_ai = self._config.getboolean('RaidReport', 'raidreportWrapupAi')
        self.raidreport_wrapup_voice = self._config.getboolean('RaidReport', 'raidreportWrapupVoice')

    def get_raidreport_cache_dir(self) -> Path:
        """Resolve the raid-report cache directory."""
        if self.raidreport_cache_dir:
            return Path(self.raidreport_cache_dir)
        return self.home_dir / "RaidReportCache"

    def get_raidreport_output_dir(self) -> Path:
        """Resolve the raid-report output directory."""
        if self.raidreport_output_dir:
            return Path(self.raidreport_output_dir)
        if self.raidreport_viewer_html:
            viewer = Path(self.raidreport_viewer_html)
            if viewer.parent.exists():
                return viewer.parent
        return self.default_raidreport_output_dir()

    @staticmethod
    def default_raidreport_output_dir() -> Path:
        """Scratch location for baked reports when no folder is configured.

        Lives under the OS temp dir so reports posted to Discord don't
        accumulate on disk forever; prune_raidreport_output() sweeps it
        on startup with the same retention as the parse cache. A user-
        configured output folder bypasses this entirely and is never
        pruned.
        """
        import tempfile
        return Path(tempfile.gettempdir()) / "SparkyBot" / "RaidReports"

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

    def get_raid_report_discord_webhook_index(self) -> int:
        """Return the dedicated Raid Report destination, or the fight route."""
        index = self.raid_report_discord_webhook
        if index in (1, 2, 3):
            return index
        return self.active_discord_webhook

    def get_discord_destination_name(self, index: int) -> str:
        """User-facing name for a webhook slot, with a stable fallback."""
        names = (
            self.discord_webhook_name1,
            self.discord_webhook_name2,
            self.discord_webhook_name3,
        )
        if index not in (1, 2, 3):
            index = 1
        return names[index - 1].strip() or f"Destination {index}"

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
        """Atomically save current config values and reload attributes.

        Args:
            config_path: Optional one-off destination. By default, save back to
                the path this Config instance was loaded from.
        """
        if config_path is None:
            config_path = self.config_path
        else:
            config_path = Path(config_path)

        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                newline='\n',
                prefix=f'.{config_path.name}.',
                suffix='.tmp',
                dir=config_path.parent,
                delete=False,
            ) as f:
                temp_name = f.name
                self._config.write(f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, config_path)
            temp_name = None
            self._load_values()
        except (OSError, UnicodeError) as e:
            logging.getLogger(__name__).warning(
                f"Could not save config to {config_path}: {e}"
            )
            return False
        finally:
            if temp_name is not None:
                try:
                    Path(temp_name).unlink()
                except OSError:
                    pass
        return True

    def update(self, section: str, key: str, value: str):
        """Update a single config value in memory."""
        self._config.set(section, key, value)
