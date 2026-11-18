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


class ApprovalConflict(RuntimeError):
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
            if self.db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1, 2):
                raise ValueError('Unsupported application storage version')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, scope TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS turns (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
                    message_count INTEGER NOT NULL, digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
                    binding TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending'
                        CHECK(state IN ('pending', 'approved', 'rejected', 'withdrawn')),
                    decided_by TEXT, decided_at TEXT);
                CREATE TABLE IF NOT EXISTS handoffs (
                    id TEXT PRIMARY KEY, approval_id TEXT NOT NULL UNIQUE REFERENCES approvals(id));
                PRAGMA user_version=2;
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

    def add_approval(self, approval_id, run_id, binding):
        with self.db:
            result = self.db.execute(
                'INSERT INTO approvals(id,run_id,binding) VALUES (?,?,?) ON CONFLICT(run_id) DO NOTHING',
                (approval_id, run_id, json.dumps(binding, ensure_ascii=False)))
            if result.rowcount != 1:
                raise ApprovalConflict('This plan has already been submitted for review')

    def approvals(self, approval_id=None):
        query = ('SELECT a.id,a.binding,a.state,a.decided_by,a.decided_at,h.id '
                 'FROM approvals a LEFT JOIN handoffs h ON a.id=h.approval_id')
        args = ()
        if approval_id is not None:
            query += ' WHERE a.id=?'
            args = (approval_id,)
        return [{'approval_id': identity, **json.loads(binding), 'state': state,
                 'decided_by': actor, 'decided_at': at, 'handoff_id': handoff_id}
                for identity, binding, state, actor, at, handoff_id in self.db.execute(query, args)]

    def decide_approval(self, approval_id, state, principal, at):
        if state not in ('approved', 'rejected', 'withdrawn'):
            raise ValueError('Invalid decision')
        # A failed handoff insert rolls back the decision as part of the same transaction.
        with self.db:
            updated = self.db.execute(
                "UPDATE approvals SET state=?,decided_by=?,decided_at=? WHERE id=? AND state='pending'",
                (state, principal, at, approval_id))
            if updated.rowcount != 1:
                raise ApprovalConflict('This approval is no longer pending')
            if state == 'approved':
                self.db.execute('INSERT INTO handoffs(id,approval_id) VALUES (?,?)',
                    (approval_id + '/handoff', approval_id))

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
