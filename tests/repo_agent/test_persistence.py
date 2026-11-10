"""Real SQLite/SDK storage; substitute only remote model and business HTTP."""
import asyncio
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from repo_agent.application import ActiveRun, RunManager
from repo_agent.conversation import ConversationWorker
from repo_agent.web import create_app
from tests.repo_agent.test_conversation import setup, ConversationModel


def persistent(monkeypatch, directory, *, sources=None, url='http://127.0.0.1'):
    from repo_agent.persistence import LocalStore
    setup(monkeypatch, directory)
    store = LocalStore(directory)
    worker = ConversationWorker(sources or {'policy': Path('fixtures/order-policy')},
        business_url=url, business_token='not-stored', store=store)
    return store, worker, RunManager(store.records, worker)


def test_restart_restores_results_and_complete_history_with_new_evidence(monkeypatch, tmp_path):
    async def scenario():
        store, worker, manager = persistent(monkeypatch, tmp_path)
        a, b = worker.create(), worker.create()
        first = manager.submit('o-999状态', conversation_id=a.conversation_id)
        await first.task
        assert first.state == 'completed'
        await manager.close()
        store.close()
        store, worker, manager = persistent(monkeypatch, tmp_path)
        try:
            assert manager.runs[first.run_id].result == first.result
            assert len(worker.conversations[a.conversation_id].turns) == 1
            assert worker.conversations[b.conversation_id].turns == []
            second = manager.submit('它现在呢', conversation_id=a.conversation_id)
            await second.task
            assert second.state == 'completed'
            record = json.loads((store.records / f'{second.run_id}.json').read_text())
            assert record['conversation']['selected_run_ids'] == [first.run_id]
            assert second.result['evidence'][0]['observation_id'] == second.run_id + '/obs-1'
            assert len(worker.conversations[a.conversation_id].turns) == 2
            await manager.close()
        finally:
            store.close()
        # Restarting repeatedly must not append the same round again.
        store, worker, manager = persistent(monkeypatch, tmp_path)
        assert len(worker.conversations[a.conversation_id].turns) == 2
        assert 'not-stored' not in store.database.read_bytes().decode('utf8', errors='ignore')
        store.close()
    asyncio.run(scenario())


def test_incomplete_message_write_does_not_publish_a_turn(monkeypatch, tmp_path):
    async def scenario():
        store, worker, manager = persistent(monkeypatch, tmp_path)
        a = worker.create()
        run = ActiveRun('orphan-run', 'q', conversation_id=a.conversation_id)
        run.emit('queued')
        store.save_run(run.view(), run.events)
        # A process dies after SDK messages commit but before the application transaction.
        turn = await store.write_history(run.run_id, [{'role': 'user', 'content': 'uncommitted'}])
        assert turn['message_count'] == 1
        store.close()
        store, worker, manager = persistent(monkeypatch, tmp_path)
        assert worker.conversations[a.conversation_id].turns == []
        restored = manager.runs[run.run_id]
        assert restored.state == 'interrupted'
        assert restored.result['error_type'] == 'ProcessInterrupted'
        assert restored.events[-1]['kind'] == 'interrupted'
        store.close()
    asyncio.run(scenario())


def test_atomic_run_and_turn_commit_rolls_back_together(monkeypatch, tmp_path):
    async def scenario():
        store, worker, manager = persistent(monkeypatch, tmp_path)
        a = worker.create()
        run = ActiveRun('transaction-run', 'q', conversation_id=a.conversation_id)
        run.emit('queued')
        store.save_run(run.view(), run.events)
        turn = await store.write_history(run.run_id, [{'role': 'user', 'content': 'q'}])
        # Fail the real SQLite turn insert after updating the Run in that transaction.
        with sqlite3.connect(store.database) as db:
            db.execute("CREATE TRIGGER reject_turn BEFORE INSERT ON turns BEGIN SELECT RAISE(ABORT, 'fault'); END")
        view = run.view() | {'state': 'completed', 'result': {'kind': 'answer_for_review'}}
        with pytest.raises(sqlite3.IntegrityError):
            store.save_run(view, run.events, turn=turn)
        stored = store.load_runs()[0]['view']
        assert stored['state'] == 'queued'
        assert store.load_conversations()[0]['turns'] == []
        store.close()
    asyncio.run(scenario())


def test_failed_and_cancelled_attempts_never_enter_restored_history(monkeypatch, tmp_path):
    async def scenario():
        store, worker, manager = persistent(monkeypatch, tmp_path)
        a = worker.create()
        first = manager.submit('o-999状态', conversation_id=a.conversation_id)
        await first.task
        monkeypatch.setattr('repo_agent.model.create_model', lambda: ConversationModel(old_source=True))
        bad = manager.submit('继续', conversation_id=a.conversation_id)
        await bad.task
        assert bad.state == 'failed'
        stopped = manager.submit('取消这轮', conversation_id=a.conversation_id)
        manager.cancel(stopped)
        await stopped.task
        assert stopped.state == 'cancelled'
        await manager.close()
        store.close()
        store, worker, manager = persistent(monkeypatch, tmp_path)
        assert [t['run_id'] for t in worker.conversations[a.conversation_id].turns] == [first.run_id]
        assert manager.runs[bad.run_id].state == 'failed'
        assert manager.runs[stopped.run_id].state == 'cancelled'
        store.close()
    asyncio.run(scenario())


def test_changed_binding_blocks_old_conversation_but_allows_new_one(monkeypatch, tmp_path):
    from repo_agent.conversation import ConversationScopeChanged
    store, worker, manager = persistent(monkeypatch, tmp_path)
    old = worker.create()
    store.close()
    store, worker, manager = persistent(monkeypatch, tmp_path, url='http://127.0.0.1:9999')
    try:
        with pytest.raises(ConversationScopeChanged):
            worker.acquire(old.conversation_id)
        assert worker.acquire(worker.create().conversation_id)
    finally:
        store.close()


def test_data_directory_has_one_owner_and_releases_lock(tmp_path):
    from repo_agent.persistence import LocalStore, StoreInUse
    with LocalStore(tmp_path):
        with pytest.raises(StoreInUse):
            LocalStore(tmp_path)
    with LocalStore(tmp_path):
        pass


def test_page_api_can_find_old_conversation_run_and_terminal_events(monkeypatch, tmp_path):
    store, worker, manager = persistent(monkeypatch, tmp_path)
    identity = worker.create().conversation_id
    with TestClient(create_app(manager)) as client:
        rid = client.post('/runs', json={'question': '状态', 'conversation_id': identity}).json()['run_id']
        assert 'completed' in client.get(f'/runs/{rid}/events').text
    store.close()
    store, worker, manager = persistent(monkeypatch, tmp_path)
    with TestClient(create_app(manager)) as client:
        assert client.get('/capabilities').json()['persistence']
        assert client.get('/conversations').json()[0]['conversation_id'] == identity
        assert client.get(f'/conversations/{identity}/runs').json()[0]['run_id'] == rid
        assert client.get(f'/runs/{rid}').json()['state'] == 'completed'
        assert client.get(f'/conversations/{identity}/runs').json()[0]['result'] is not None
        assert client.get('/conversations/unknown/runs').status_code == 404
        assert client.get('/runs/unknown').status_code == 404
    store.close()


def test_missing_committed_messages_fail_explicitly_without_silent_history_loss(monkeypatch, tmp_path):
    async def scenario():
        from agents import SQLiteSession
        store, worker, manager = persistent(monkeypatch, tmp_path)
        identity = worker.create().conversation_id
        first = manager.submit('状态', conversation_id=identity)
        await first.task
        await manager.close()
        session = SQLiteSession('run:' + first.run_id, store.messages)
        await session.clear_session()
        session.close()
        store.close()
        store, worker, manager = persistent(monkeypatch, tmp_path)
        second = manager.submit('继续', conversation_id=identity)
        await second.task
        assert second.state == 'failed'
        assert second.result['error_type'] == 'HistoryUnavailable'
        assert len(worker.conversations[identity].turns) == 1
        store.close()
    asyncio.run(scenario())


def test_changed_source_snapshot_blocks_old_conversation(monkeypatch, tmp_path):
    from repo_agent.conversation import ConversationScopeChanged
    sources = tmp_path / 'sources'
    sources.mkdir()
    source = sources / 'rules.md'
    source.write_text('first')
    directory = tmp_path / 'data'
    store, worker, manager = persistent(monkeypatch, directory, sources={'policy': sources})
    identity = worker.create().conversation_id
    store.close()
    source.write_text('changed')
    store, worker, manager = persistent(monkeypatch, directory, sources={'policy': sources})
    with pytest.raises(ConversationScopeChanged):
        worker.acquire(identity)
    assert worker.create().scope == worker.scope
    store.close()


def test_final_index_failure_does_not_publish_memory_history_and_stops_admission(monkeypatch, tmp_path):
    async def scenario():
        store, worker, manager = persistent(monkeypatch, tmp_path)
        identity = worker.create().conversation_id
        original = store.save_run
        def fail_final(view, events, *, turn=None):
            if turn is not None:
                raise sqlite3.OperationalError('disk fault')
            original(view, events, turn=turn)
        monkeypatch.setattr(store, 'save_run', fail_final)
        run = manager.submit('状态', conversation_id=identity)
        await run.task
        assert run.state == 'failed' and run.result['error_type'] == 'PersistenceError'
        assert worker.conversations[identity].turns == []
        assert manager.closed
        store.close()
        store, worker, manager = persistent(monkeypatch, tmp_path)
        assert manager.runs[run.run_id].state == 'interrupted'
        assert worker.conversations[identity].turns == []
        store.close()
    asyncio.run(scenario())


def test_launcher_rejects_resume_without_a_persistent_question():
    import subprocess
    import sys
    launcher = 'examples/repo_reading/12_app.py'
    invalid = [(['--data-dir', 'runs/no-create'], '--data-dir requires --conversation'),
               (['--conversation', '--conversation-id', 'missing'], '--conversation-id requires --data-dir and --question')]
    for options, expected in invalid:
        result = subprocess.run([sys.executable, launcher, *options], capture_output=True, text=True)
        assert result.returncode == 2
        assert expected in result.stderr


@pytest.mark.parametrize('cut, expected_turns, expected_state', [
    ('before_messages', 0, 'interrupted'),
    ('after_messages', 0, 'interrupted'),
    ('after_index', 1, 'completed'),
])
def test_process_exit_at_commit_boundaries_never_exposes_half_turn(tmp_path, cut, expected_turns, expected_state):
    import subprocess
    import sys
    # Abrupt exit skips all Python cleanup and closes the OS lock/SQLite handles.
    program = '''
import asyncio, os, sys
from pathlib import Path
from repo_agent.persistence import LocalStore
from repo_agent.application import ActiveRun
store = LocalStore(Path(sys.argv[1]))
store.add_conversation('conversation', {'source_hash': 'fixture'})
run = ActiveRun('run', 'question', conversation_id='conversation')
run.state = 'running'
run.emit('running')
store.save_run(run.view(), run.events)
if sys.argv[2] == 'before_messages': os._exit(23)
turn = asyncio.run(store.write_history('run', [{'role':'user','content':'complete message'}]))
if sys.argv[2] == 'after_messages': os._exit(23)
run.state = 'completed'
run.result = {'kind':'answer_for_review'}
run.emit('completed')
store.save_run(run.view(), run.events, turn=turn)
os._exit(23)
'''
    result = subprocess.run([sys.executable, '-c', program, str(tmp_path), cut], capture_output=True, text=True)
    assert result.returncode == 23, result.stderr
    from repo_agent.persistence import LocalStore
    with LocalStore(tmp_path) as store:
        turns = store.load_conversations()[0]['turns']
        assert len(turns) == expected_turns
        assert store.load_runs()[0]['view']['state'] == expected_state
        if turns:
            assert asyncio.run(store.read_history(turns))[0]['items'] == [{'role':'user','content':'complete message'}]


def test_cancel_accepted_after_message_write_prevents_turn_publication(monkeypatch, tmp_path):
    async def scenario():
        store, worker, manager = persistent(monkeypatch, tmp_path)
        identity = worker.create().conversation_id
        original = store.write_history
        async def cancel_before_publish(run_id, items):
            turn = await original(run_id, items)
            asyncio.get_running_loop().call_soon(manager.cancel, manager.runs[run_id])
            return turn
        monkeypatch.setattr(store, 'write_history', cancel_before_publish)
        run = manager.submit('状态', conversation_id=identity)
        await run.task
        assert run.cancel_requested
        assert run.state == 'cancelled'
        assert worker.conversations[identity].turns == []
        assert store.load_conversations()[0]['turns'] == []
        assert store.load_runs()[0]['view']['state'] == 'cancelled'
        store.close()
    asyncio.run(scenario())


def test_sigterm_launcher_releases_data_directory_and_synthetic_service(tmp_path):
    import os
    import signal
    import socket
    import subprocess
    import sys
    import time
    import httpx
    def free_port():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            return sock.getsockname()[1]
    port, business_port = free_port(), free_port()
    env = {k:v for k,v in os.environ.items() if not k.startswith(('MODEL_', 'OPENAI_'))}
    env.update(MODEL_API_KEY='test-only', MODEL_BASE_URL='http://127.0.0.1:1/v1', MODEL_NAME='test')
    for attempt in range(2):
        with (tmp_path / f'launcher-{attempt}.txt').open('w') as log:
            process = subprocess.Popen([sys.executable, 'examples/repo_reading/12_app.py', '--conversation',
                '--data-dir', str(tmp_path / 'data'), '--port', str(port), '--business-port', str(business_port)],
                env=env, stdout=log, stderr=log, start_new_session=True)
            try:
                with httpx.Client(trust_env=False, timeout=.2) as client:
                    for _ in range(150):
                        assert process.poll() is None
                        try:
                            if client.get(f'http://127.0.0.1:{port}/capabilities').status_code == 200:
                                break
                        except httpx.TransportError:
                            time.sleep(.05)
                    else:
                        pytest.fail('launcher did not become ready')
                process.send_signal(signal.SIGTERM)
                process.wait(timeout=15)
                # A dead launcher must not leave its synthetic API occupying the restart port.
                with socket.socket() as probe:
                    assert probe.connect_ex(('127.0.0.1', business_port)) != 0
                from repo_agent.persistence import LocalStore
                with LocalStore(tmp_path / 'data'):
                    pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
