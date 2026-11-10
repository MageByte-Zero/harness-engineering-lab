"""Single-owner local storage: SDK messages first, application commit publishes a turn."""
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from agents import SQLiteSession


class StoreInUse(RuntimeError):
    pass


class HistoryUnavailable(RuntimeError):
    pass


def message_digest(items):
    return sha256(json.dumps(items, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


class LocalStore:
    """A trusted local directory, held exclusively until close; not a multi-worker backend."""
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / 'application.sqlite3'
        self.messages = self.directory / 'messages.sqlite3'
        self.records = self.directory / 'records'
        self.lock = (self.directory / 'owner.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.lock.close()
            raise StoreInUse('Data directory is already owned by another process') from exc
        try:
            self.db = sqlite3.connect(self.database, check_same_thread=False)
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA journal_mode=WAL')
            if self.db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
                raise ValueError('Unsupported application storage version')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, scope TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS turns (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
                    message_count INTEGER NOT NULL, digest TEXT NOT NULL);
                PRAGMA user_version=1;
            ''')
            self.records.mkdir(exist_ok=True)
            self._recover_interrupted()
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if hasattr(self, 'db'):
            self.db.close()
        self.lock.close()  # Closing also releases flock, including on process death.

    def add_conversation(self, identity, scope):
        with self.db:
            self.db.execute('INSERT INTO conversations VALUES (?, ?)',
                (identity, json.dumps(scope, ensure_ascii=False)))

    def load_conversations(self):
        result = []
        for identity, scope in self.db.execute('SELECT id, scope FROM conversations ORDER BY rowid'):
            turns = [{'run_id': rid, 'message_count': count, 'digest': digest}
                for rid, count, digest in self.db.execute(
                    'SELECT run_id, message_count, digest FROM turns WHERE conversation_id=? ORDER BY seq',
                    (identity,))]
            result.append({'id': identity, 'scope': json.loads(scope), 'turns': turns})
        return result

    def load_runs(self):
        return [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM runs ORDER BY rowid')]

    def save_run(self, view, events, *, turn=None):
        if turn is not None and (view['state'] != 'completed' or not view['conversation_id']):
            raise ValueError('Only a completed conversation run can publish a turn')
        # SDK storage already committed; this transaction makes result and history visible together.
        with self.db:
            self.db.execute('INSERT INTO runs VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                (view['run_id'], json.dumps({'view': view, 'events': events}, ensure_ascii=False)))
            if turn is not None:
                self.db.execute('INSERT INTO turns(conversation_id,run_id,message_count,digest) VALUES (?,?,?,?)',
                    (view['conversation_id'], view['run_id'], turn['message_count'], turn['digest']))

    def _recover_interrupted(self):
        for saved in self.load_runs():
            view, events = saved['view'], saved['events']
            if view['state'] in ('queued', 'running', 'cancelling'):
                view['state'] = 'interrupted'
                view['result'] = {'kind': 'unavailable', 'error_type': 'ProcessInterrupted'}
                events.append({'seq': len(events) + 1, 'run_id': view['run_id'],
                    'at': datetime.now(timezone.utc).isoformat(), 'kind': 'interrupted', 'data': {}})
                self.save_run(view, events)

    async def write_history(self, run_id, items):
        session = SQLiteSession('run:' + run_id, self.messages)
        try:
            if not items or await session.get_items():
                raise ValueError('A durable turn must be nonempty and written only once')
            await session.add_items(items)
            stored = await session.get_items()
            if message_digest(stored) != message_digest(items):
                raise HistoryUnavailable('SDK history did not round trip')
            return {'message_count': len(stored), 'digest': message_digest(stored)}
        finally:
            session.close()

    async def read_history(self, turns):
        result = []
        for turn in turns:
            session = SQLiteSession('run:' + turn['run_id'], self.messages)
            try:
                items = await session.get_items()
            finally:
                session.close()
            if len(items) != turn['message_count'] or message_digest(items) != turn['digest']:
                raise HistoryUnavailable('Committed SDK history is missing or changed')
            result.append({**turn, 'items': items})
        return result
