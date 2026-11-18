"""Verify persisted approvals through the same authenticated HTTP endpoints."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from repo_agent.access import AccessPolicy, Project
from repo_agent.application import RunManager
from repo_agent.approval import PlanApprovals
from repo_agent.conversation import ConversationWorker
from repo_agent.persistence import LocalStore
from repo_agent.tools import load_source_bundle, snapshot_hash
from repo_agent.web import create_app
from tests.repo_agent.test_planning import plan, requirement


REVISION = 'a' * 64


def policy(root, reviewers=('bob',)):
    people = ('alice', 'bob', 'carol', 'dave')
    return AccessPolicy({'alpha': Project({'code': root}, 'http://127.0.0.1:8765', 'service')},
        {name: frozenset({'alpha'}) for name in people}, {name: name + '-token' for name in people},
        {'alpha': frozenset(reviewers)})


def manager(store, access):
    worker = ConversationWorker({}, business_url='', business_token='', store=store, access=access)
    return RunManager(store.records, worker)


def save_plan(store, root, run_id='plan-run', *, kind='plan_for_review'):
    ownership = {'owner_id': 'alice', 'project_id': 'alpha'}
    result = {**plan(), 'kind': kind, 'request': requirement(),
              'artifact': {'id': run_id + '/plan', 'revision': REVISION,
                           'snapshot_id': snapshot_hash(load_source_bundle({'code': root}))}}
    store.save_run({'run_id': run_id, 'question': 'Alice private question', 'result': result,
                    'state': 'completed', 'ownership': ownership}, [])


def token(actor):
    return {'Authorization': f'Bearer {actor}-token'}


def send(client, reviewer='bob', *, run_id='plan-run', actor='alice'):
    return client.post(f'/runs/{run_id}/approval', headers=token(actor),
                       json={'reviewer_id': reviewer, 'plan_revision': REVISION})


def decide(client, key, actor='bob', decision='approve', revision=REVISION, **extra):
    return client.post(f'/approvals/{key}/decision', headers=token(actor),
                       json={'plan_revision': revision, 'decision': decision, **extra})


def test_restart_preserves_wait_and_concurrent_approval_creates_one_handoff(tmp_path):
    db_dir = tmp_path / 'store'
    root = Path('fixtures/order-service')
    with LocalStore(db_dir) as store:
        save_plan(store, root)
        with TestClient(create_app(manager(store, policy(root, reviewers=())))) as client:
            assert send(client).status_code == 403  # Bob is a project member without the action grant.
            assert client.get('/approvals', headers=token('bob')).json() == []
        with TestClient(create_app(manager(store, policy(root, reviewers=('bob', 'carol'))))) as client:
            assert send(client, actor='bob').status_code == 403
            pending = send(client)
            assert pending.status_code == 201
            record = pending.json()
            key = record['approval_id']
            assert record['state'] == 'pending' and record['handoff_id'] is None
            assert send(client).status_code == 409
            assert client.get('/runs/plan-run', headers=token('bob')).status_code == 403
            assert client.get('/runs/plan-run/events', headers=token('bob')).status_code == 403
            assert client.get(f'/approvals/{key}', headers=token('carol')).status_code == 403
            assert decide(client, key, actor='alice').status_code == 403
            assert decide(client, key, actor='carol').status_code == 403
            assert decide(client, key, actor='dave').status_code == 403
            assert decide(client, key, reviewer_id='bob').status_code == 422
            assert decide(client, key, action='change_source').status_code == 422
    with LocalStore(db_dir) as store:
        with TestClient(create_app(manager(store, policy(root, reviewers=())))) as client:
            assert client.get(f'/approvals/{key}', headers=token('bob')).status_code == 403
            assert decide(client, key).status_code == 403
        with TestClient(create_app(manager(store, policy(root)))) as client:
            restored = client.get(f'/approvals/{key}', headers=token('bob')).json()
            assert restored['plan_revision'] == record['plan_revision'] and restored['state'] == 'pending'
            assert 'Alice private question' not in str(restored)
            assert decide(client, key, revision='b' * 64).status_code == 409
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: decide(client, key).status_code, range(2)))
            assert sorted(outcomes) == [200, 409]
            assert decide(client, key, decision='reject').status_code == 409
            assert store.db.execute('SELECT count(*) FROM handoffs').fetchone() == (1,)
    with LocalStore(db_dir) as store:
        with TestClient(create_app(manager(store, policy(root)))) as client:
            restored = client.get(f'/approvals/{key}', headers=token('alice')).json()
            assert restored['state'] == 'approved' and restored['decided_by'] == 'bob'
            assert restored['handoff_id'] == key + '/handoff'
            assert decide(client, key).status_code == 409


def test_reject_withdraw_stale_snapshot_and_expiry_never_publish_handoff(tmp_path):
    root = tmp_path / 'src'
    root.mkdir()
    source = root / 'input.py'
    source.write_text('A = 1\n')
    moment = datetime(2026, 11, 18, tzinfo=timezone.utc)
    with LocalStore(tmp_path / 'store') as store:
        for run_id in ('reject', 'withdraw', 'stale', 'expires'):
            save_plan(store, root, run_id)
        save_plan(store, root, 'incomplete', kind='plan_needs_information')
        with TestClient(create_app(manager(store, policy(root)))) as client:
            assert send(client, run_id='incomplete').status_code == 409
        access = policy(root)
        service = PlanApprovals(manager(store, access), store, access, clock=lambda: moment)
        ids = {name: service.submit('alice', name, REVISION, 'bob')['approval_id']
               for name in ('reject', 'withdraw', 'stale', 'expires')}
        assert service.decide('bob', ids['reject'], REVISION, 'reject')['state'] == 'rejected'
        service.withdraw('alice', ids['withdraw'], REVISION)
        source.write_text('A = 2\n')
        # Keep the expiry test independent of the machine clock.
        service.clock = lambda: moment + timedelta(hours=1)
        from repo_agent.persistence import ApprovalConflict
        with pytest.raises(ApprovalConflict, match='snapshot changed'):
            service.decide('bob', ids['stale'], REVISION, 'approve')
        with pytest.raises(ApprovalConflict, match='no longer pending'):
            service.decide('bob', ids['withdraw'], REVISION, 'approve')
        service.clock = lambda: moment + timedelta(hours=24)
        with pytest.raises(ApprovalConflict, match='expired'):
            service.decide('bob', ids['expires'], REVISION, 'approve')
        assert not service.get('bob', ids['expires'])['can_decide']
        assert store.db.execute('SELECT count(*) FROM handoffs').fetchone() == (0,)


def test_storage_rolls_back_decision_when_handoff_cannot_be_saved(tmp_path):
    root = Path('fixtures/order-service')
    with LocalStore(tmp_path) as store:
        save_plan(store, root)
        access = policy(root)
        service = PlanApprovals(manager(store, access), store, access)
        key = service.submit('alice', 'plan-run', REVISION, 'bob')['approval_id']
        store.db.executescript("""
            CREATE TRIGGER fail_handoff BEFORE INSERT ON handoffs
            BEGIN SELECT RAISE(ABORT, 'test-only storage failure'); END;
        """)
        with pytest.raises(sqlite3.IntegrityError):
            service.decide('bob', key, REVISION, 'approve')
        record = service.get('alice', key)
        assert record['state'] == 'pending' and record['decided_by'] is None and record['handoff_id'] is None
