"""Actual final fight commentary, not planned callouts or inferred quotes.

SQLite transactions provide restart-safe, cross-thread/process deduplication.
Only commentary and minimal fight/player metadata enter this apppaths store.
No prompts, provider configuration, raw logs, or credentials are persisted.
"""
import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path

from core.apppaths import app_dir

WALL_TIDDLER = '$:/sparkybot/commentary'
logger = logging.getLogger(__name__)


def fight_id(data):
    stamp = data.get('timeStartStd') or data.get('timeStart')
    if not stamp or not data.get('durationMS'):
        return None
    identity = [stamp, data['durationMS'], data.get('triggerID'), data.get('mapID')]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


_CATEGORIES = {
    'damage': r'\b(?:damage|dps|burst)\b',
    'healing': r'\b(?:heals?|healing|hps)\b',
    'cleanses': r'\bcleans(?:e[sd]?|ing)\b',
    'boon_strips': r'\b(?:boon strips?|stripp?ing|strips?|corrupts?|corruptions?)\b',
    'stability': r'\b(?:stability|stab)\b',
    'resurrection': r'\b(?:resurrect\w*|reviv\w*|rezz?\w*)\b',
    'crowd_control': r'\b(?:crowd control|cc|pulls?|stuns?)\b',
    'deaths': r'\b(?:deaths?|died|dead)\b',
    'downs': r'\b(?:downs?|downed)\b',
}


def _mentions(data, text):
    aliases = {}
    commanders = set()
    for player in data.get('players', []):
        name = str(player.get('name') or '').strip()
        account = str(player.get('account') or '').lstrip(':').casefold()
        if name:
            aliases.setdefault(name.casefold(), {})[account] = name
            if player.get('hasCommanderTag') is True:
                commanders.add(account)
    if not aliases:
        return []
    pattern = re.compile(r'(?<!\w)(?:' + '|'.join(
        re.escape(name) for name in sorted(aliases, key=len, reverse=True)
    ) + r')(?!\w)', re.I)
    result = {}
    for sentence in re.split(r'(?<=[.!?;])\s+|\n+', text):
        matches = list(pattern.finditer(sentence))
        names = {match.group().casefold() for match in matches}
        # A sentence mentioning several people is not evidence that each
        # performed every category in it. Keep the quote, abstain on category.
        categories = sorted(key for key, regex in _CATEGORIES.items()
                            if len(names) == 1 and re.search(regex, sentence, re.I))
        for name in names:
            identities = aliases[name]
            if len(identities) != 1 or not next(iter(identities)):
                continue
            account, display = next(iter(identities.items()))
            if account in commanders:
                # A tag announcement is not a callout. Require an explicit
                # contribution/event in this commander's own name segment;
                # never borrow the next named player's performance words.
                segments = [sentence[match.end():matches[i + 1].start()
                                     if i + 1 < len(matches) else len(sentence)]
                            for i, match in enumerate(matches)
                            if match.group().casefold() == name]
                if len(names) == 1:
                    # With no competing named player, prefix phrasing such as
                    # "Excellent healing from Alice" is equally explicit.
                    segments.append(sentence[:matches[0].start()])
                evidence = '|'.join(_CATEGORIES.values()) + (
                    r'|\b(?:carried|carr(?:y|ies|ying)|saved|clutched|fed|feeding|'
                    r'threw|choked|whiffed|inting|MVP|brilliant|excellent|'
                    r'terrible|useless|hero|clown)\b')
                if not any(re.search(evidence, segment, re.I) for segment in segments):
                    continue
            player = result.setdefault(account, {'id': account, 'name': display, 'categories': set()})
            player['categories'].update(categories or ['unknown'])
    return [dict(player, categories=sorted(player['categories'])) for player in result.values()]


class CommentaryStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else app_dir() / 'sparkybot_commentary.sqlite3'

    def record(self, data, text, *, session_id=None, timestamp=None):
        key = fight_id(data)
        if not key or not text or not text.strip():
            return
        entry = {'fight_id': key, 'fight_timestamp': data.get('timeStartStd') or data.get('timeStart'),
                 'text': text, 'timestamp': timestamp or datetime.now(timezone.utc).isoformat(),
                 'session_id': session_id, 'players': _mentions(data, text)}
        digest = hashlib.sha256(text.encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            connection.execute('CREATE TABLE IF NOT EXISTS comments (fight_id TEXT, digest TEXT, entry TEXT NOT NULL, PRIMARY KEY (fight_id, digest))')
            connection.execute('INSERT OR IGNORE INTO comments VALUES (?, ?, ?)',
                               (key, digest, json.dumps(entry, ensure_ascii=False)))

    def entries(self, fight_ids):
        wanted = set(fight_ids)
        if not wanted or not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as connection:
            rows = connection.execute('SELECT fight_id, entry FROM comments ORDER BY rowid')
            return [json.loads(entry) for key, entry in rows if key in wanted]


def _time_key(value):
    match = re.search(r'(\d{4}-\d{2}-\d{2})[ T]+(?:-\s*)?(\d{2}:\d{2}:\d{2})', str(value))
    return ' '.join(match.groups()) if match else None


def capture_session_id(config):
    """Bind the run before slow generation, never infer it at completion."""
    try:
        from core.run_session import RunSession
        home = getattr(config, 'home_dir', None)
        session = RunSession(home) if home else None
        started_at = session.started_at if session else None
        return started_at.isoformat() if started_at else None
    except (OSError, ValueError, TypeError):
        return None


def record_final_comment(data, text, config, *, session_id=None):
    """Best-effort recording at the final-text boundary, before transport fanout."""
    if not getattr(config, 'enable_ai_analysis', False):
        return
    try:
        CommentaryStore().record(data, text, session_id=session_id)
    except (OSError, ValueError, TypeError, sqlite3.Error):
        logger.warning('Final commentary could not be saved', exc_info=True)


def report_tiddler(json_paths, *, enabled=False, store=None):
    """Freeze selected, successfully parsed inputs; never query by broad date range."""
    payload = {'enabled': bool(enabled), 'coverage_basis': 'ei.timeEnd', 'fights': [], 'entries': []}
    if enabled:
        try:
            for path in json_paths:
                data = json.loads(Path(path).read_text(encoding='utf-8'))
                key = fight_id(data)
                if key:
                    # Overview uses the literal date/clock in EI timeEnd, not
                    # timeEndStd and not the start clock used by fight_id.
                    payload['fights'].append({'id': key, 'time': _time_key(data.get('timeEnd')),
                                              'end_timestamp': data.get('timeEnd')})
            payload['entries'] = (store or CommentaryStore()).entries(
                row['id'] for row in payload['fights'])
        except (OSError, ValueError, TypeError, sqlite3.Error):
            logger.warning('Commentary history unavailable for report', exc_info=True)
    return {'title': WALL_TIDDLER, 'text': json.dumps(payload, ensure_ascii=False)}


def build_wall(tiddlers, covered_fights):
    """Model only exact, unambiguous matches to the combiner Overview coverage."""
    empty = {'enabled': False, 'players': []}
    source = next((t for t in tiddlers if t.get('title') == WALL_TIDDLER), None)
    if source is None:
        return empty
    payload = json.loads(source['text'])
    if payload.get('enabled') is not True:
        return empty
    if payload.get('coverage_basis') != 'ei.timeEnd':
        # Earlier local snapshots stored start clocks, which cannot safely be
        # joined to Overview end clocks. Regenerate from selected EI inputs.
        return {'enabled': True, 'players': []}
    candidates = {}
    for fight in payload.get('fights', []):
        if fight.get('time'):
            candidates.setdefault(fight['time'], set()).add(fight['id'])
    covered = {}
    for fight in covered_fights:
        stamp = _time_key(fight.get('time_label'))
        covered.setdefault(stamp, []).append(fight)
    allowed = {next(iter(ids)) for stamp, ids in candidates.items()
               if len(ids) == 1 and len(covered.get(stamp, [])) == 1}
    end_timestamps = {fight['id']: fight.get('end_timestamp') for fight in payload.get('fights', [])}
    players = {}
    seen = set()
    for entry in payload.get('entries', []):
        key = (entry['fight_id'], entry['text'])
        if entry['fight_id'] not in allowed or key in seen:
            continue
        seen.add(key)
        for mention in entry['players']:
            player = players.setdefault(mention['id'], {
                'id': mention['id'], 'name': mention['name'], 'mentions': 0,
                'categories': {}, 'comments': []})
            player['mentions'] += 1
            for category in mention['categories']:
                player['categories'][category] = player['categories'].get(category, 0) + 1
            player['comments'].append({
                'text': entry['text'], 'fight_id': entry['fight_id'],
                'timestamp': entry['timestamp'], 'categories': mention['categories'],
                'session_id': entry.get('session_id'),
                'fight_timestamp': entry.get('fight_timestamp'),
                'fight_end_timestamp': end_timestamps.get(entry['fight_id']),
            })
    return {'enabled': True, 'players': sorted(players.values(), key=lambda p: (-p['mentions'], p['id']))}