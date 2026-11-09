"""Exercise ownership and cancellation through the real SDK core, not a duplicate loop."""
import asyncio
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import httpx
import pytest

from repo_agent.application import CapacityExceeded, RunManager
from repo_agent.checks import check_record
from repo_agent.execution import run_repository_async
from repo_agent.web import create_app
from tests.repo_agent.test_business import client_for, DATA
from tests.repo_agent.test_output import payload
from tests.repo_agent.test_repository import ScriptedModel, answer, request


def worker():
    async def run(question, output, **kwargs):
        client = client_for(lambda r: httpx.Response(200, json=DATA['o-999']['status']))
        return await run_repository_async(Path('fixtures/order-policy'), question, output,
            business_client=client, structured_output=True, **kwargs)
    return run


def script():
    return [request('get_order_status', {'order_id': 'o-999'}, 1), answer(json.dumps(payload()))]


def test_concurrent_runs_cancel_model_wait_without_stopping_peer(monkeypatch, tmp_path):
    async def scenario():
        gate = asyncio.Event()
        started, cancelled = [], []
        class WaitingModel(ScriptedModel):
            async def get_response(self, *args, **kwargs):
                started.append(self)
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    cancelled.append(self)
                    raise
                return await super().get_response(*args, **kwargs)
        monkeypatch.setattr('repo_agent.model.create_model', lambda: WaitingModel(script()))
        manager = RunManager(tmp_path, worker())
        a, b = manager.submit('one'), manager.submit('two')
        while len(started) < 2:
            await asyncio.sleep(0)
        with pytest.raises(CapacityExceeded):
            manager.submit('third')
        manager.cancel(a)
        manager.cancel(a)  # Repeated cancellation must not interrupt cleanup.
        assert a.task is not None
        await a.task
        assert len(cancelled) == 1 and a.state == 'cancelled' and b.state == 'running'
        record = json.loads((tmp_path / f'{a.run_id}.json').read_text())
        assert record['run_id'] == a.run_id and record['stop_reason'] == 'cancelled'
        assert check_record(record)['ok']
        gate.set()
        assert b.task is not None
        await b.task
        assert b.result is not None
        assert b.state == 'completed' and b.result['kind'] == 'answer_for_review'
        assert b.result['evidence'][0]['data']['state'] == 'UNPAID'
        before = b.view()
        manager.cancel(b)
        assert b.view() == before
        assert all(e['run_id'] == a.run_id for e in a.events)
        assert all(e['run_id'] == b.run_id for e in b.events)
        await manager.close()
    asyncio.run(scenario())


def test_cancel_waiting_business_http_preserves_pending_tool(monkeypatch, tmp_path):
    async def scenario():
        entered, released = asyncio.Event(), asyncio.Event()
        async def handler(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()
        monkeypatch.setattr('repo_agent.model.create_model', lambda: ScriptedModel(script()))
        client = client_for(handler)
        async def run_worker(question, output, **kwargs):
            return await run_repository_async(Path('fixtures/order-policy'), question, output,
                business_client=client, structured_output=True, **kwargs)
        manager = RunManager(tmp_path, run_worker)
        run = manager.submit('status')
        await entered.wait()
        manager.cancel(run)
        assert run.task is not None
        await run.task
        assert released.is_set() and run.state == 'cancelled'
        record = json.loads((tmp_path / f'{run.run_id}.json').read_text())
        report = check_record(record)
        assert report['ok'] and report['pending_tool_ids'] == ['call-1']
        assert run.result is not None
        assert not client.observations and run.result['kind'] == 'unavailable'
        assert [e['kind'] for e in run.events][-2:] == ['cancel_requested', 'cancelled']
        await manager.close()
    asyncio.run(scenario())


def test_cancel_search_kills_and_reaps_actual_subprocess(monkeypatch):
    async def scenario():
        from repo_agent.tools import search_text
        original = asyncio.create_subprocess_exec
        spawned, ready = [], asyncio.Event()
        async def spawn(*args, **kwargs):
            process = await original(sys.executable, '-c', 'import time; time.sleep(60)', **kwargs)
            spawned.append(process)
            ready.set()
            return process
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
        task = asyncio.create_task(search_text('needle', 'content'))
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert spawned[0].returncode is not None
    asyncio.run(scenario())


def test_cancel_before_start_and_retained_capacity(tmp_path):
    async def scenario():
        async def must_not_run(*args, **kwargs):
            raise AssertionError('Cancelled before execution')
        manager = RunManager(tmp_path, must_not_run, retained_limit=1)
        run = manager.submit('one')
        manager.cancel(run)
        assert run.task is not None
        await run.task
        assert run.state == 'cancelled' and run.execution is None
        with pytest.raises(CapacityExceeded):
            manager.submit('two')
        await manager.close()
    asyncio.run(scenario())


def test_shutdown_waits_for_active_execution_cleanup(tmp_path):
    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def waiting(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        manager = RunManager(tmp_path, waiting)
        run = manager.submit('one')
        await entered.wait()
        await manager.close()
        assert run.task is not None
        assert cleaned.is_set() and run.state == 'cancelled' and run.task.done()
        with pytest.raises(CapacityExceeded):
            manager.submit('two')
    asyncio.run(scenario())


def test_api_validation_sse_replay_and_checked_result(monkeypatch, tmp_path):
    monkeypatch.setattr('repo_agent.model.create_model', lambda: ScriptedModel(script()))
    manager = RunManager(tmp_path, worker())
    with TestClient(create_app(manager)) as client:
        assert client.get('/').status_code == 200
        for value in ({'question':' '}, {'question':'q', 'business_url':'http://evil'}, {'question':'x'*2001}):
            assert client.post('/runs', json=value).status_code == 422
        assert client.post('/runs', json={'question':'q'}, headers={'Origin':'http://evil'}).status_code == 403
        assert client.get('/', headers={'Host':'evil.invalid'}).status_code == 400
        assert client.get('/runs/unknown').status_code == 404
        response = client.post('/runs', json={'question':'当前状态？'})
        assert response.status_code == 202
        run_id = response.json()['run_id']
        stream = client.get(f'/runs/{run_id}/events')
        events = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith('data: ')]
        assert [e['seq'] for e in events] == list(range(1, len(events)+1))
        assert events[-1]['kind'] == 'completed'
        assert all(e['run_id'] == run_id for e in events)
        assert 'tool_started' in [e['kind'] for e in events]
        after = client.get(f'/runs/{run_id}/events', headers={'Last-Event-ID':'2'})
        assert json.loads(next(l[6:] for l in after.text.splitlines() if l.startswith('data: ')))['seq'] == 3
        assert client.get(f'/runs/{run_id}/events?after=999').status_code == 422
        view = client.get(f'/runs/{run_id}').json()
        assert view['result']['kind'] == 'answer_for_review'
        assert view['result']['semantic_review'] == 'pending'
        assert client.post(f'/runs/{run_id}/cancel').json()['state'] == 'completed'
        assert 'MODEL_API_KEY' not in stream.text


def test_bad_source_stays_unavailable_in_web_result(monkeypatch, tmp_path):
    value = payload(); value['facts'][0]['source_ids'] = ['invented']
    monkeypatch.setattr('repo_agent.model.create_model', lambda: ScriptedModel([
        request('get_order_status', {'order_id':'o-999'}, 1), answer(json.dumps(value))]))
    with TestClient(create_app(RunManager(tmp_path, worker()))) as client:
        run_id = client.post('/runs', json={'question':'状态？'}).json()['run_id']
        assert 'failed' in client.get(f'/runs/{run_id}/events').text
        run = client.get(f'/runs/{run_id}').json()
        assert run['state'] == 'failed' and run['result']['kind'] == 'unavailable'
        assert run['result']['checks']['sources'] == 'failed'


def test_exception_body_is_not_exposed(tmp_path):
    async def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE_TOKEN')
    with TestClient(create_app(RunManager(tmp_path, fail))) as client:
        run_id = client.post('/runs', json={'question':'q'}).json()['run_id']
        events = client.get(f'/runs/{run_id}/events').text
        result = client.get(f'/runs/{run_id}').text
        assert 'PRIVATE_TOKEN' not in events + result
        assert 'RuntimeError' in result
