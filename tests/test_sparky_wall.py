import importlib
import importlib.util
import json
import tempfile
from datetime import datetime, timedelta
import unittest
from pathlib import Path


def fight(stamp='2026-09-19 20:00:00 +00:00'):
    start = datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S %z')
    end = start + timedelta(minutes=1)
    return {'timeStartStd': stamp, 'timeStart': stamp,
            'timeEnd': end.strftime('%Y-%m-%d %H:%M:%S') + ' +00:00',
            'timeEndStd': end.strftime('%Y-%m-%d %H:%M:%S') + ' +00:00',
            'durationMS': 60000, 'triggerID': 1,
            'players': [{'name': 'Alice', 'account': ':Alice.1234'},
                        {'name': 'Bob', 'account': 'Bob.5678'}]}


class WallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'wall.sqlite3'

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('core.sparky_wall'),
                             'durable commentary module missing')
        return importlib.import_module('core.sparky_wall')

    def test_final_comments_survive_restart_and_retries_are_deduplicated(self):
        wall = self.module()
        store = wall.CommentaryStore(self.path)
        data = fight()
        store.record(data, 'Alice delivered damage.', session_id='run-start', timestamp='now')
        store.record(data, 'Alice delivered damage.', session_id='run-start', timestamp='later')
        rows = wall.CommentaryStore(self.path).entries([wall.fight_id(data)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['text'], 'Alice delivered damage.')
        self.assertEqual(rows[0]['session_id'], 'run-start')
        self.assertEqual(rows[0]['timestamp'], 'now')
        self.assertEqual(rows[0]['players'], [
            {'id': 'alice.1234', 'name': 'Alice', 'categories': ['damage']}])
        self.assertEqual(store.entries(['not-selected']), [])

    def test_parallel_retries_are_transactionally_deduplicated(self):
        from concurrent.futures import ThreadPoolExecutor
        wall = self.module()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: wall.CommentaryStore(self.path).record(
                fight(), 'Alice had damage.'), range(12)))
        self.assertEqual(len(wall.CommentaryStore(self.path).entries([wall.fight_id(fight())])), 1)

    def test_disabled_missing_history_and_unscoped_fights_create_no_store(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        wall = self.module()
        with patch('core.sparky_wall.app_dir', return_value=Path(self.tmp.name)):
            wall.record_final_comment(fight(), 'Alice had damage.', SimpleNamespace(enable_ai_analysis=False))
            wall.CommentaryStore().record({}, 'Alice had damage.')
            self.assertEqual(wall.CommentaryStore().entries(['missing']), [])
            self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_names_are_not_substrings_and_multi_player_categories_abstain(self):
        wall = self.module()
        data = fight()
        data['players'].append({'name': 'Alice Smith', 'account': 'Smith.1234'})
        store = wall.CommentaryStore(self.path)
        store.record(data, 'Alice Smith and Bob had healing and damage.')
        players = store.entries([wall.fight_id(data)])[0]['players']
        self.assertEqual({p['id'] for p in players}, {'smith.1234', 'bob.5678'})
        self.assertTrue(all(p['categories'] == ['unknown'] for p in players))

    def test_each_category_has_text_evidence(self):
        wall = self.module()
        store = wall.CommentaryStore(self.path)
        store.record(fight(), 'Alice had stability, healing, cleanses, boon strips, resurrection, crowd control, damage, deaths and downs.')
        categories = store.entries([wall.fight_id(fight())])[0]['players'][0]['categories']
        self.assertEqual(set(categories), set(wall._CATEGORIES))

    def test_attribution_uses_unambiguous_final_text_not_planned_outliers(self):
        wall = self.module()
        data = fight()
        data['players'] += [{'name': 'Alice', 'account': 'Other.9999'},
                            {'name': 'Bo', 'account': 'Bo.1234'}]
        data['outliers'] = {'healing': {'name': 'Bo'}}
        store = wall.CommentaryStore(self.path)
        store.record(data, 'Alice had damage. Bob was here.')
        self.assertEqual(store.entries([wall.fight_id(data)])[0]['players'],
                         [{'id': 'bob.5678', 'name': 'Bob', 'categories': ['unknown']}])

    def test_commander_metadata_alone_is_not_a_callout(self):
        wall = self.module()
        data = fight()
        data['players'][0]['hasCommanderTag'] = True
        store = wall.CommentaryStore(self.path)
        for text in ['Commander: Alice.', 'Tonight Alice is on tag.', 'Alice led the raid.']:
            store.record(data, text)
        self.assertTrue(all(row['players'] == [] for row in store.entries([wall.fight_id(data)])))

    def test_commander_real_performance_and_roast_are_counted(self):
        wall = self.module()
        data = fight()
        data['players'][0]['hasCommanderTag'] = True
        store = wall.CommentaryStore(self.path)
        for text in ['Commander Alice had healing and boon strips.', 'Alice fed again.']:
            store.record(data, text)
        rows = store.entries([wall.fight_id(data)])
        self.assertEqual(rows[0]['players'][0]['categories'], ['boon_strips', 'healing'])
        self.assertEqual(rows[1]['players'][0]['categories'], ['unknown'])

    def test_commander_intro_does_not_borrow_other_player_performance(self):
        wall = self.module()
        data = fight()
        data['players'][0]['hasCommanderTag'] = True
        text = 'Commander Alice, Bob delivered healing and damage.'
        store = wall.CommentaryStore(self.path)
        store.record(data, text)
        row = store.entries([wall.fight_id(data)])[0]
        self.assertEqual(row['text'], text)
        self.assertEqual([p['name'] for p in row['players']], ['Bob'])

    def test_commander_prefix_performance_counts_without_borrowing_previous_player(self):
        wall = self.module()
        data = fight()
        data['players'][0]['hasCommanderTag'] = True
        store = wall.CommentaryStore(self.path)
        for text in ['Excellent healing from Alice.', 'MVP: Alice.',
                     'Bob delivered healing, commander Alice on tag.']:
            store.record(data, text)
        rows = store.entries([wall.fight_id(data)])
        self.assertEqual(rows[0]['players'], [
            {'id': 'alice.1234', 'name': 'Alice', 'categories': ['healing']}])
        self.assertEqual([p['name'] for p in rows[1]['players']], ['Alice'])
        self.assertEqual([p['name'] for p in rows[2]['players']], ['Bob'])


    def test_categories_are_local_to_named_sentence_and_account_aliases_merge(self):
        wall = self.module()
        store = wall.CommentaryStore(self.path)
        data = fight()
        store.record(data, 'Alice had healing and cleanses. Bob had damage and boon strips.')
        players = store.entries([wall.fight_id(data)])[0]['players']
        self.assertEqual(players[0]['categories'], ['cleanses', 'healing'])
        self.assertEqual(players[1]['categories'], ['boon_strips', 'damage'])

    def test_report_model_only_includes_selected_and_combiner_covered_fights(self):
        wall = self.module()
        self.assertTrue(hasattr(wall, 'report_tiddler'), 'report snapshot missing')
        store = wall.CommentaryStore(self.path)
        selected = fight()
        # Combiner deliberately uses timeEnd, not the preferred Std identity clock.
        selected['timeStart'] = '2026-09-19 22:00:00 +02:00'
        selected['timeEnd'] = '2026-09-19 22:01:00 +02:00'
        skipped = fight('2026-09-19 21:00:00 +00:00')
        outside = fight('2026-09-18 21:00:00 +00:00')
        paths = []
        for i, data in enumerate([selected, skipped, outside]):
            store.record(data, 'Alice had damage.', timestamp=str(i))
            path = Path(self.tmp.name) / f'{i}.json'
            path.write_text(json.dumps(data))
            paths.append(path)
        snapshot = wall.report_tiddler(paths[:2], enabled=True, store=store)
        frozen = json.loads(snapshot['text'])
        self.assertEqual(frozen['fights'][0]['time'], '2026-09-19 22:01:00')
        fights = [{'index': 1, 'time_label': '2026-09-19 - 22:01:00 - WvW'}]
        payload = wall.build_wall([snapshot], fights)
        self.assertTrue(payload['enabled'])
        self.assertEqual(payload['players'][0]['mentions'], 1)
        self.assertEqual(payload['players'][0]['categories'], {'damage': 1})
        self.assertEqual(payload['players'][0]['comments'][0]['fight_id'], wall.fight_id(selected))
        comment = payload['players'][0]['comments'][0]
        self.assertEqual(comment['fight_timestamp'], selected['timeStartStd'])
        self.assertEqual(comment['fight_end_timestamp'], selected['timeEnd'])
        self.assertEqual(comment['timestamp'], '0')
        self.assertEqual(wall.build_wall([], fights), {'enabled': False, 'players': []})
        disabled = wall.report_tiddler(paths, enabled=False, store=store)
        self.assertEqual(wall.build_wall([disabled], fights), {'enabled': False, 'players': []})
        from core.night_model import build_night_model
        from unittest.mock import patch
        with patch('core.night_model._parse_fights', return_value=fights):
            self.assertEqual(build_night_model([snapshot])['sparky_wall'], payload)

    def test_legacy_start_clock_snapshot_fails_closed_even_on_accidental_match(self):
        wall = self.module()
        store = wall.CommentaryStore(self.path)
        data = fight()
        store.record(data, 'Alice had damage.')
        legacy = {'enabled': True,
                  'fights': [{'id': wall.fight_id(data), 'time': '2026-09-19 20:00:00'}],
                  'entries': store.entries([wall.fight_id(data)])}
        snapshot = {'title': wall.WALL_TIDDLER, 'text': json.dumps(legacy)}
        self.assertEqual(wall.build_wall([snapshot], [
            {'time_label': '2026-09-19 - 20:00:00'}]), {'enabled': True, 'players': []})

    def test_end_clock_collisions_and_missing_end_fail_closed(self):
        wall = self.module()
        store = wall.CommentaryStore(self.path)
        first = fight()
        collision = fight('2026-09-19 19:59:00 +00:00')
        collision['timeEnd'] = first['timeEnd']
        missing = fight('2026-09-19 21:00:00 +00:00')
        del missing['timeEnd']  # timeEndStd must not silently become coverage.
        paths = []
        for i, data in enumerate([first, collision, missing]):
            store.record(data, 'Alice had damage.')
            path = Path(self.tmp.name) / f'collision-{i}.json'
            path.write_text(json.dumps(data))
            paths.append(path)
        overview = [{'time_label': '2026-09-19 - 20:01:00'}]
        for selected, covered in [(paths[:2], overview),
                                  (paths[:1], overview * 2),
                                  (paths[2:], [{'time_label': '2026-09-19 - 21:01:00'}])]:
            snapshot = wall.report_tiddler(selected, enabled=True, store=store)
            self.assertEqual(wall.build_wall([snapshot], covered)['players'], [])
        snapshot = wall.report_tiddler(paths[:1] * 2, enabled=True, store=store)
        self.assertEqual(wall.build_wall([snapshot], overview)['players'][0]['mentions'], 1)
