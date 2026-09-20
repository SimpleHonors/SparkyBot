"""Wall snapshots and actual imported application callback integration."""
import ast
import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import sparky_wall
from test_sparky_wall import fight


class ApplicationCallbackTests(unittest.TestCase):
    """No AST extraction: real Qt wiring, report, analyst and SQLite recorder.

    Only external boundaries (EI executable, HTTP, watcher OS events, Discord
    and Twitch) are stubbed. State-path redirects keep all writes temporary.
    """

    def setUp(self):
        import main
        from core import calibration
        self.main = main
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        for module in ['core.apppaths', 'core.config', 'core.sparky_wall',
                       'core.vocabulary_config', 'core.vocabulary_tracker',
                       'core.session_history']:
            self.enterContext(patch(module + '.app_dir', return_value=self.home))
        self.enterContext(patch.object(calibration.append_summary, '__defaults__',
                                      (self.home / 'corpus.jsonl',)))
        for name in ['_vocab_config', '_vocab_tracker', '_session_history',
                     '_last_ai_response', '_callout_cooldown']:
            self.enterContext(patch.object(main, name, None))
        self.enterContext(patch.dict('os.environ', {'SPARKY_DEBUG_AI_PROMPT': ''}))
        self.config = main.Config(self.home / 'config.properties')
        for key, value in dict(enable_ai_analysis=True, enable_discord_bot=True,
                               enable_twitch=True, ai_base_url='https://ai.invalid/v1',
                               ai_model='fixture-model', ai_api_key='',
                               twitch_token='fixture', twitch_channel='fixture',
                               min_fight_duration=0, min_fight_downs=0,
                               min_fight_total_dmg=0, tts_enabled=False,
                               tts_discord_attach=False, raidreport_cache_enabled=False).items():
            setattr(self.config, key, value)
        self.data = fight()
        self.path = self.home / 'parsed.json'
        self.raw = self.home / 'fight.zevtc'
        self.invoker = self.enterContext(patch.object(main, 'GW2EIInvoker')).return_value
        def parse(_):
            self.path.write_text(json.dumps(self.data))
            return self.path
        self.invoker.parse_file.side_effect = parse
        self.discord = self.enterContext(patch.object(main, 'DiscordWebhookManager')).return_value
        self.discord.send_to_all.return_value = 2
        self.twitch = self.enterContext(patch('core.twitch_bot.TwitchBot')).return_value
        self.watcher = self.enterContext(patch.object(main, 'FileWatcher'))
        self.watcher.return_value.status_note = ''
        self.http = self.enterContext(patch('core.fight_analyst.requests.post'))
        self.http.return_value.status_code = 200
        self.http.return_value.json.return_value = {
            'choices': [{'message': {'content': '<think>private draft</think>Alice had damage. ' + 'x' * 4200},
                         'finish_reason': 'stop'}]}
        self.worker = main.WatcherWorker(self.config)
        self.events, self.results = [], []
        self.worker.pipeline_event.connect(lambda *args: self.events.append(args))
        self.worker.file_processed.connect(lambda *args: self.results.append(args))
        self.worker.start()
        self.callback = self.watcher.call_args.args[1]
        (self.home / 'run_state.json').write_text(json.dumps({
            'started_at': '2026-09-19T19:00:00', 'ended_at': None}))

    def rows(self):
        # Every call constructs a new store/SQLite connection, never a memory cache.
        return sparky_wall.CommentaryStore().entries([sparky_wall.fight_id(self.data)])

    def test_actual_watcher_callback_records_final_text_before_fanout_and_retry(self):
        observed = []
        def send(**kwargs):
            if not kwargs.get('compact_single_message'):
                observed.append(self.rows())
                self.assertEqual(kwargs['embeds'][0]['description'], 'Alice had damage.')
            return 2
        self.discord.send_to_all.side_effect = send
        def response(*args, **kwargs):
            # Run ends during provider latency; commentary belongs to original run.
            (self.home / 'run_state.json').write_text(json.dumps({
                'started_at': '2026-09-19T19:00:00', 'ended_at': '2026-09-19T21:00:00'}))
            return self.http.return_value
        self.http.side_effect = response
        self.callback(self.raw)
        self.callback(self.raw)
        self.assertEqual([r[1] for r in self.results], ['success', 'success'])
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(self.discord.send_to_all.call_count, 4)
        self.assertEqual(self.twitch.send_message.call_count, 4)
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(len(rows) == 1 for rows in observed))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['text'], 'Alice had damage.')
        self.assertEqual(rows[0]['session_id'], '2026-09-19T19:00:00')
        self.assertEqual([p['name'] for p in rows[0]['players']], ['Alice'])
        self.assertEqual(len([e for e in self.events if e[1] == 'commentary']), 2)
        self.assertIn('Alice had damage.', self.twitch.send_message.call_args.args[0])
        self.assertNotIn('private draft', rows[0]['text'])
        # Independent interpreter proves persistence beyond module/object lifetime.
        import subprocess
        import sys
        script = ('import json,sys; from core.sparky_wall import CommentaryStore; '
                  'print(json.dumps(CommentaryStore(sys.argv[1]).entries([sys.argv[2]])))')
        identity = sparky_wall.fight_id(self.data)
        assert identity is not None
        reloaded = subprocess.run(
            [sys.executable, '-c', script,
             str(self.home / 'sparkybot_commentary.sqlite3'), identity],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(reloaded.stdout), rows)

    def test_actual_callback_transport_failure_keeps_disk_comment_for_retry(self):
        self.discord.send_to_all.side_effect = [0, RuntimeError('transport offline'), 2, 2]
        self.callback(self.raw)
        self.assertEqual(self.results[-1][1], 'error_discord')
        self.assertEqual(len(self.rows()), 1)
        self.callback(self.raw)
        self.assertEqual(self.results[-1][1], 'success')
        self.assertEqual(len(self.rows()), 1)

    def test_actual_callback_ai_disabled_never_requests_or_creates_history(self):
        self.config.enable_ai_analysis = False
        self.callback(self.raw)
        self.assertEqual(self.results[-1][1], 'success')
        self.http.assert_not_called()
        self.assertFalse((self.home / 'sparkybot_commentary.sqlite3').exists())
        self.assertEqual(self.discord.send_to_all.call_count, 1)
        self.assertEqual(self.twitch.send_message.call_count, 1)
        self.assertFalse(any(e[1] == 'commentary' for e in self.events))

    def test_callback_history_reload_only_selected_and_overview_covered_fights(self):
        from core.night_model import build_night_model
        paths, ids = [], []
        for index, stamp in enumerate(['20:00:00', '21:00:00', '22:00:00']):
            self.data = fight('2026-09-19 ' + stamp + ' +00:00')
            self.callback(self.raw)
            self.assertEqual(self.results[-1][1], 'success')
            path = self.home / f'selected-{index}.json'
            path.write_text(json.dumps(self.data))
            paths.append(path)
            ids.append(sparky_wall.fight_id(self.data))
        self.assertEqual(len(sparky_wall.CommentaryStore().entries(ids)), 3)
        snapshot = sparky_wall.report_tiddler(paths[:2], enabled=True)
        self.assertEqual(len(json.loads(snapshot['text'])['entries']), 2)
        overview = {'title': 'fixture-Overview', 'text': '|!#|!Time|h\n|1|2026-09-19 - 20:01:00|'}
        model = build_night_model([overview, snapshot])['sparky_wall']
        self.assertEqual(model['players'][0]['mentions'], 1)
        self.assertEqual(model['players'][0]['comments'][0]['fight_id'], ids[0])
        disabled = sparky_wall.report_tiddler(paths, enabled=False)
        self.assertEqual(build_night_model([overview, disabled])['sparky_wall'],
                         {'enabled': False, 'players': []})


class PipelineTests(unittest.TestCase):
    def test_gui_run_and_headless_runner_construction_pass_master_ai_switch(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'core' / 'raid_report_wiring.py').read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == 'RaidReportRunner']
        self.assertEqual(len(calls), 2)
        for call in calls:
            value = next((kw.value for kw in call.keywords if kw.arg == 'ai_enabled'), None)
            self.assertIsNotNone(value, 'runner omitted AI master switch')
            assert value is not None
            expression = compile(ast.Expression(value), '<ai-switch>', 'eval')
            for enabled in [True, False]:
                self.assertEqual(eval(expression, {'config': SimpleNamespace(enable_ai_analysis=enabled)}), enabled)

    def test_runner_bakes_wall_snapshot_and_persists_report_json(self):
        import inspect
        from datetime import datetime
        from core.raid_report import RaidReportRunner
        from core.raid_session import LogInfo
        from core.report_viewer import unpack_night_model
        self.assertIn('ai_enabled', inspect.signature(RaidReportRunner).parameters)
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            data = fight()
            parsed = home / 'parsed.json'
            parsed.write_text(json.dumps(data))
            overview = [{'title': 'fixture-Overview', 'text': '|!#|!Time|h\n|1|2026-09-19 - 20:01:00|'}]
            combined = home / 'combined.json'
            def combine(*args, **kwargs):
                combined.write_text(json.dumps(overview))
                combined.with_suffix('.html').write_text('<html><head><title>Fixture</title></head><body>Classic fixture</body></html>')
                return combined
            combiner = Mock()
            combiner.run.side_effect = combine
            log = LogInfo(home / 'log.zevtc', datetime(2026, 9, 19, 20), 'filename')
            runner = RaidReportRunner(log_folder=home, cache=Mock(), parse_log=Mock(), ei_version='x',
                                      settings_fingerprint='x', combiner=combiner, viewer_html=home / 'viewer.html',
                                      output_dir=home / 'out', ai_enabled=True)
            with patch('core.sparky_wall.app_dir', return_value=home), \
                 patch('core.raid_report.plan_report', return_value=([(log, parsed)], [])):
                sparky_wall.CommentaryStore().record(data, 'Alice had damage.')
                with self.assertLogs('core.report_bake', level='WARNING'):
                    result = runner.generate([log])
                payload = unpack_night_model(result.html_path.read_text())['sparky_wall']
                self.assertTrue(payload['enabled'])
                self.assertEqual(payload['players'][0]['mentions'], 1)
                self.assertIn(sparky_wall.WALL_TIDDLER, [t['title'] for t in json.loads(result.json_path.read_text())])

    def test_final_truncated_text_recorded_once_before_transport_fanout(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'main.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'process_log_file')
        source = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
        summary = {'outliers': {'healing': {'name': 'Bob'}}}
        report = Mock(duration_ms=60000, total_downs=5, total_damage=100, EMBED_COLOR=1, AUTHOR_ICON_URL='')
        report.get_ai_summary.return_value = summary
        scope = {'logging': logging, 'json': json, 'FightReport': Mock(return_value=report),
                 '_callout_cooldown': None, '_get_ai_components': lambda: (None, None, None),
                 '_last_ai_response': None, '_cache_or_delete_json': Mock(),
                 'ProcessResult': SimpleNamespace(SUCCESS='success', ERROR_OTHER='error', ERROR_DISCORD='discord')}
        exec(compile(ast.fix_missing_locations(source), str(root / 'main.py'), 'exec'), scope)
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            data = fight()
            path = home / 'fight.json'
            path.write_text(json.dumps(data))
            (home / 'run_state.json').write_text(json.dumps({'started_at': '2026-09-19T19:00:00', 'ended_at': None}))
            config = Mock(enable_ai_analysis=True, enable_discord_bot=True, enable_twitch=True,
                          min_fight_duration=0, min_fight_downs=0, min_fight_total_dmg=0,
                          tts_enabled=False, tts_discord_attach=False, home_dir=home)
            final = 'Alice had damage. ' + 'x' * 4200
            with patch('core.ai_analyst.FightAnalyst') as analyst, \
                 patch('core.twitch_bot.TwitchBot') as twitch, \
                 patch('core.dpsreport.links_active', return_value=False), \
                 patch('core.calibration.append_summary'), \
                 patch('core.sparky_wall.app_dir', return_value=home):
                def analyze(*args, **kwargs):
                    # A run can end while the slow provider call is in flight.
                    (home / 'run_state.json').write_text(json.dumps({'started_at': '2026-09-19T19:00:00', 'ended_at': '2026-09-19T21:00:00'}))
                    return final
                analyst.return_value.analyze.side_effect = analyze
                discord = Mock()
                discord.send_to_all.return_value = 2
                for _ in range(2):
                    self.assertEqual(scope['process_log_file'](home / 'fight.zevtc', config, Mock(parse_file=Mock(return_value=path)), discord), 'success')
                self.assertEqual(discord.send_to_all.call_count, 4)
                self.assertEqual(twitch.return_value.send_message.call_count, 4)
                rows = sparky_wall.CommentaryStore().entries([sparky_wall.fight_id(data)])
                self.assertEqual(len(rows), 1, 'actual final commentary not recorded')
                self.assertEqual(rows[0]['text'], 'Alice had damage.')
                self.assertEqual(rows[0]['session_id'], '2026-09-19T19:00:00')
                self.assertEqual([p['name'] for p in rows[0]['players']], ['Alice'])