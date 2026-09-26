"""Hosted report metadata, separate from immutable raw/EI logs and the Wall.

SHA-256 of actual bytes binds upload results to raw logs and parsed EI files.
No clock, basename or input-order joins; ambiguous EI bindings stay missing.
"""
import hashlib
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from core.apppaths import app_dir

logger = logging.getLogger(__name__)


def safe_report_url(value):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in '\\<>"\'|' for c in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            return None
        # The combiner serializes this value inside a wiki-link/table cell.
        # Require delimiters to be percent-encoded, never normalize the URL.
        if any(c in '|[]' for c in url.path + url.query + url.fragment):
            return None
        url.port  # reject malformed ports
    except ValueError:
        return None
    return value


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _path():
    return app_dir() / 'sparkybot_fight_links.sqlite3'


def remember(raw_hash, ei_hash, url):
    """Best-effort durable metadata; never break a successful upload/post."""
    if not safe_report_url(url):
        return
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=10)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS links (raw_hash TEXT PRIMARY KEY, url TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS bindings (ei_hash TEXT, raw_hash TEXT, PRIMARY KEY(ei_hash, raw_hash))')
            db.execute('INSERT OR REPLACE INTO links VALUES (?, ?)', (raw_hash, url))
            if ei_hash:
                db.execute('INSERT OR IGNORE INTO bindings VALUES (?, ?)', (ei_hash, raw_hash))
    except (OSError, sqlite3.Error):
        logger.warning('Could not save hosted fight link metadata', exc_info=True)


def _lookup(column, value):
    path = _path()
    if not path.is_file():
        return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
            if column == 'raw_hash':
                rows = db.execute('SELECT url FROM links WHERE raw_hash=?', (value,)).fetchall()
            else:
                rows = db.execute('SELECT links.url FROM bindings JOIN links USING(raw_hash) WHERE ei_hash=?', (value,)).fetchall()
        return safe_report_url(rows[0][0]) if len(rows) == 1 else None
    except (OSError, sqlite3.Error):
        logger.warning('Could not read hosted fight link metadata', exc_info=True)
        return None


def report_url(ei_json):
    try:
        return _lookup('ei_hash', digest(ei_json))
    except OSError:
        return None


def bind_parsed(raw_hash, ei_json):
    """Bind a fresh parse of an unchanged raw log, not an old cache guess."""
    url = _lookup('raw_hash', raw_hash)
    if url:
        try:
            remember(raw_hash, digest(ei_json), url)
        except OSError:
            logger.warning('Could not bind parsed fight link metadata', exc_info=True)
