"""Contract tests and independent HTTP evidence; scripted models do not test answer quality."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import httpx
import pytest
from repo_agent.business import BusinessClient, business_tools, BUSINESS_INSTRUCTIONS
from repo_agent.checks import check_record
from repo_agent.execution import run_repository
from tests.repo_agent.test_repository import ScriptedModel, request, answer, invoke

DATA = json.loads(Path('fixtures/business-api/records.json').read_text())


def client_for(handler, **kwargs):
    return BusinessClient('http://127.0.0.1', 'NEVER-RETAIN-THIS', transport=httpx.MockTransport(handler), **kwargs)


@pytest.mark.parametrize('code,status', [(401,'unauthenticated'),(403,'forbidden'),(404,'not_found'),
    (429,'rate_limited'),(500,'service_error'),(302,'service_error')])
def test_http_failures_keep_meaning_and_discard_body(code, status):
    c = client_for(lambda r: httpx.Response(code, text='NEVER-RETAIN-THIS'))
    result = asyncio.run(c.query('status', 'o-999'))
    assert result['status'] == status and result['http_status'] == code
    assert 'NEVER-RETAIN-THIS' not in json.dumps(c.observations)


@pytest.mark.parametrize('payload', [b'not json', b'{}', b' ' * 17000,
    json.dumps({**DATA['o-999']['status'], 'order_id':'o-1000'}).encode(),
    json.dumps({**DATA['o-999']['status'], 'as_of':'yesterday'}).encode()])
def test_invalid_response(payload):
    c = client_for(lambda r: httpx.Response(200, content=payload))
    assert asyncio.run(c.query('status', 'o-999'))['status'] == 'invalid_response'


def test_valid_fields_filter_extra_secret_and_enforce_scope():
    seen = []
    def handler(r):
        seen.append(r)
        return httpx.Response(200, json={**DATA['o-999']['status'], 'internal_secret':'NEVER-RETAIN-THIS'})
    c = client_for(handler)
    for oid in ['../x', 'o-123']:
        assert asyncio.run(c.query('status', oid))['status'] in ('denied','invalid_argument')
    assert not seen
    result = asyncio.run(c.query('status','o-999'))
    assert result['data'] == DATA['o-999']['status']
    assert seen[0].headers['Authorization'] == 'Bearer NEVER-RETAIN-THIS'
    assert 'NEVER-RETAIN-THIS' not in json.dumps(c.observations)
    declaration = str([t.params_json_schema for t in business_tools(c)])
    assert 'base_url' not in declaration and 'token' not in declaration


def test_timeouts_connection_failure_and_history_validation():
    async def slow(r):
        await asyncio.sleep(.1)
        return httpx.Response(200)
    c = client_for(slow, timeout=.01)
    assert asyncio.run(c.query('status','o-999'))['status'] == 'timeout'
    def disconnected(r):
        raise httpx.ConnectError('NEVER-RETAIN-THIS')
    c = client_for(disconnected)
    assert asyncio.run(c.query('status','o-999'))['status'] == 'unavailable'
    c = client_for(lambda r: httpx.Response(200, json=DATA['o-999']['events']))
    assert asyncio.run(c.query('events','o-999'))['data']['complete'] is False
    bad = {**DATA['o-999']['events'], 'events':[{'at':'2026-11-03T00:00:00Z','kind':'created'}]}
    c = client_for(lambda r: httpx.Response(200, json=bad))
    assert asyncio.run(c.query('events','o-999'))['status'] == 'invalid_response'


def test_existing_runner_api_only_and_record_tampering(tmp_path, monkeypatch):
    from repo_agent import model
    monkeypatch.setattr(model, 'create_model', lambda: ScriptedModel([
        request('get_order_status', {'order_id':'o-999'}, 1), answer('UNPAID')]))
    c = client_for(lambda r: httpx.Response(200, json=DATA['o-999']['status']))
    record = run_repository(Path('fixtures/order-policy'), '状态？', tmp_path/'run.json',
        business_client=c, instructions=BUSINESS_INSTRUCTIONS)
    assert record['stop_reason'] == 'answer_returned'
    report = check_record(record)
    assert report['ok'], report
    assert report['successful_reads'] == 0 and report['successful_api_reads'] == 1
    assert 'NEVER-RETAIN-THIS' not in (tmp_path/'run.json').read_text()
    tampered = deepcopy(record)
    observation = next(m for e in tampered['events'] for m in e['state']['messages'] if m['type']=='tool')
    payload = json.loads(observation['content']); payload['data']['state'] = 'PAID'
    observation['content'] = json.dumps(payload)
    assert not check_record(tampered)['ok']
    tampered = deepcopy(record); tampered['model_inputs'][-1]['payload']['tools'].pop()
    assert not check_record(tampered)['ok']


@pytest.mark.parametrize('mode,token,status', [('normal','local-only','ok'),('normal','wrong','unauthenticated'),
    ('forbidden','local-only','forbidden'),('limited','local-only','rate_limited'),
    ('invalid','local-only','invalid_response'),('slow','local-only','timeout')])
def test_independent_real_http_service(tmp_path, mode, token, status):
    process = subprocess.Popen([sys.executable,'fixtures/business-api/server.py','--port','0','--mode',mode,
        '--log',str(tmp_path/'requests.jsonl')], env={**os.environ,'BUSINESS_API_TOKEN':'local-only'},
        stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout is not None
        line = process.stdout.readline()
        assert 'Synthetic service on' in line
        port = int(line.strip().rsplit(':',1)[1])
        c = BusinessClient(f'http://127.0.0.1:{port}', token, timeout=.1 if mode=='slow' else 3)
        result = asyncio.run(c.query('status','o-999'))
        assert result['status'] == status
        log = json.loads((tmp_path/'requests.jsonl').read_text().splitlines()[0])
        assert log['order_id'] == 'o-999' and log['kind'] == 'status'
        if status == 'ok':
            assert result['data'] == DATA['o-999']['status']
    finally:
        process.terminate(); process.wait(timeout=5)


def test_permission_failure_is_not_an_observation(tmp_path, monkeypatch):
    from repo_agent import model
    monkeypatch.setattr(model, 'create_model', lambda: ScriptedModel([
        request('get_order_status', {'order_id':'o-999'}, 1), answer('权限不足，无法确认')]))
    c = client_for(lambda r: httpx.Response(403))
    record = run_repository(Path('fixtures/order-policy'), '状态？', tmp_path/'failure.json',
        business_client=c, instructions=BUSINESS_INSTRUCTIONS)
    assert record['stop_reason'] == 'no_observation'
    assert check_record(record)['ok']
    assert record['tool_issues'][0]['status'] == 'forbidden'


def test_api_requests_share_existing_budget(tmp_path, monkeypatch):
    from repo_agent import model, execution
    monkeypatch.setattr(execution, 'TOOL_LIMIT', 1)
    monkeypatch.setattr(model, 'create_model', lambda: ScriptedModel([
        [request('get_order_status', {'order_id':'o-999'}, 1),
         request('get_order_events', {'order_id':'o-999'}, 2)]]))
    c = client_for(lambda r: httpx.Response(200, json=DATA['o-999']['status']))
    record = run_repository(Path('fixtures/order-policy'), '状态？', tmp_path/'budget.json',
        business_client=c, instructions=BUSINESS_INSTRUCTIONS)
    assert record['stop_reason'] == 'tool_budget_exhausted' and not c.observations
    assert check_record(record)['ok']
