"""Application authorization is independent of the model and HTTP presentation."""
import asyncio
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import httpx
import pytest

from repo_agent.access import AccessDenied, AccessPolicy, Project, load_local_access
from repo_agent.application import RunManager
from repo_agent.conversation import ConversationWorker
from repo_agent.persistence import LocalStore
from repo_agent.web import create_app
from tests.repo_agent.test_repository import ScriptedModel, request
from tests.repo_agent.test_planning import requirement, plan


def policy():
    projects = {
        'alpha': Project({'code': Path('fixtures/order-service')}, 'http://127.0.0.1:8765', 'service-a', frozenset({'o-999'})),
        'beta': Project({'code': Path('fixtures/other-project')}, 'http://127.0.0.1:8765', 'service-b', frozenset({'o-1000'})),
    }
    return AccessPolicy(projects, {'alice': frozenset({'alpha'}), 'bob': frozenset({'alpha', 'beta'})},
        {'alice': 'alice-secret', 'bob': 'bob-secret'})


def worker(store=None, access=None):
    return ConversationWorker({}, business_url='', business_token='', store=store, access=access or policy())


def test_local_provisioning_reuses_credentials_and_config_selects_sources(tmp_path):
    config = Path('fixtures/access/projects.json')
    credentials = tmp_path / 'local-credentials.json'
    access = load_local_access(config, credentials, 'http://127.0.0.1:8765')
    issued = json.loads(credentials.read_text())
    assert credentials.stat().st_mode & 0o777 == 0o600
    restarted = load_local_access(config, credentials, 'http://127.0.0.1:8765')
    assert restarted.authenticate(issued['users']['alice']) == 'alice'
    assert access.authenticate(issued['services']['alpha']) is None
    w = worker(access=access)
    alpha = w.create(principal='alice', project_id='alpha')
    beta = w.create(principal='bob', project_id='beta')
    from repo_agent.tools import load_source_bundle
    def texts(c):
        return load_source_bundle({k: Path(v) for k, v in c.scope['sources'].items()})
    assert 'BETA_PRIVATE_CATALOG' not in json.dumps(texts(alpha))
    assert 'BETA_PRIVATE_CATALOG' in texts(beta)['code/src/ApiExceptionHandler.java']
    assert 'policy/closure-policy.md' not in texts(beta)
    assert alpha.scope['allowed_orders'] == ['o-404', 'o-999']
    assert beta.scope['allowed_orders'] == ['o-1000']


def test_wrong_principal_never_reaches_history_loading(monkeypatch, tmp_path):
    with LocalStore(tmp_path) as store:
        w = worker(store)
        c = w.create(principal='alice', project_id='alpha')
        monkeypatch.setattr(store, 'read_history', lambda *a: pytest.fail('Unauthorized history read'))
        with pytest.raises(AccessDenied):
            asyncio.run(w('reuse', store.records/'never.json', conversation=c, run_id='never',
                emit=lambda *a: None, principal='bob'))
        assert not (store.records/'never.json').exists()


def test_no_claimed_identity_or_project_can_bypass_application(tmp_path):
    access = policy()
    assert access.authenticate('wrong') is None
    assert access.authenticate('alice-secret') == 'alice'
    w = worker(access=access)
    with pytest.raises(AccessDenied):
        w.create(principal='alice', project_id='beta')
    with pytest.raises(AccessDenied):
        w.create()
    c = w.create(principal='alice', project_id='alpha')
    with pytest.raises(AccessDenied):
        w.acquire(c.conversation_id, principal='bob', project_id='alpha')
    manager = RunManager(tmp_path, w)
    with pytest.raises(AccessDenied):
        manager.submit('reuse', conversation_id=c.conversation_id)
    assert not manager.runs and not c.busy


def test_ownership_survives_restart_and_legacy_is_not_claimed(tmp_path):
    data = tmp_path / 'data'
    with LocalStore(data) as store:
        legacy = ConversationWorker({'code': Path('fixtures/order-service')},
            business_url='http://127.0.0.1:8765', business_token='old', store=store).create()
        w = worker(store)
        own = w.create(principal='alice', project_id='alpha')
        store.save_run({'run_id': 'saved', 'question': 'private', 'state': 'completed',
            'conversation_id': own.conversation_id, 'ownership': {'owner_id': 'alice', 'project_id': 'alpha'}}, [])
    with LocalStore(data) as store:
        with pytest.raises(AccessDenied, match='requires an access configuration'):
            ConversationWorker({'code': Path('fixtures/order-service')},
                business_url='http://127.0.0.1:8765', business_token='old', store=store)
        w = worker(store)
        manager = RunManager(store.records, w)
        assert [c.conversation_id for c in w.visible('alice')] == [own.conversation_id]
        assert not w.visible('bob')
        assert manager.get('saved', principal='alice').question == 'private'
        for identity in (legacy.conversation_id, own.conversation_id):
            with pytest.raises(AccessDenied):
                w.acquire(identity, principal='bob', project_id='alpha')
        with pytest.raises(AccessDenied):
            manager.get('saved', principal='bob')
        # A new startup policy removes access even to the user's old records.
        revoked = policy()
        revoked.memberships['alice'] = frozenset()
        w.access = revoked
        assert not w.visible('alice')
        with pytest.raises(AccessDenied):
            manager.get('saved', principal='alice')


def test_web_all_paths_and_restart_preserve_private_plans(monkeypatch, tmp_path):
    models = []
    class PlanningModel(ScriptedModel):
        def __init__(self):
            super().__init__([])
            models.append(self)

        async def get_response(self, system_instructions, input, *args, **kwargs):
            outputs = [ast.literal_eval(i['output']) for i in input if i.get('type') == 'function_call_output']
            item = (request('submit_change_plan', {'plan': plan(source=outputs[-1]['observation_id'], target='code/src/ApiExceptionHandler.java')}, 2)
                if outputs else request('read_file', {'path': 'code/src/ApiExceptionHandler.java'}, 1))
            self.inputs.append(input)
            return await ScriptedModel([item]).get_response(system_instructions, input, *args, **kwargs)

    monkeypatch.setattr('repo_agent.model.create_model', PlanningModel)
    with LocalStore(tmp_path) as store:
        w = worker(store)
        with TestClient(create_app(RunManager(store.records, w))) as client:
            assert client.get('/conversations', headers={'X-User-ID': 'alice'}).status_code == 401
            assert client.post('/session', json={'credential': 'wrong'}).status_code == 401
            response = client.post('/session', json={'credential': 'alice-secret'})
            assert response.status_code == 200 and 'httponly' in response.headers['set-cookie'].lower()
            assert 'samesite=strict' in response.headers['set-cookie'].lower()
            assert client.post('/conversations?project_id=beta').status_code == 403
            assert not models and not w.conversations
            cid = client.post('/conversations?project_id=alpha').json()['conversation_id']
            assert client.post('/runs', json={'question': 'spoof', 'conversation_id': cid,
                'project_id': 'alpha', 'owner_id': 'bob'}).status_code == 422
            body = {'question': requirement()['goal'], 'conversation_id': cid, 'project_id': 'alpha', 'planning_request': requirement()}
            rid = client.post('/runs', json=body).json()['run_id']
            # Cookie-authenticated SSE + Last-Event-ID works without a token in the URL.
            assert 'completed' in client.get(f'/runs/{rid}/events').text
            before = client.get(f'/runs/{rid}').json()
            assert client.get(f'/runs/{rid}').headers['cache-control'] == 'no-store'
            assert before['result']['kind'] == 'plan_for_review', before
            ownership = {'owner_id': 'alice', 'project_id': 'alpha'}
            assert before['ownership'] == before['result']['artifact']['ownership'] == ownership
            record = json.loads((store.records / f'{rid}.json').read_text())
            assert record['ownership'] == ownership and 'BETA_PRIVATE_CATALOG' not in json.dumps(record)
            assert all(secret not in json.dumps(record) for secret in ('alice-secret', 'service-a', 'service-b'))
            assert 'completed' in client.get(f'/runs/{rid}/events', headers={'Last-Event-ID': '1'}).text
            for method, path in [('GET', f'/runs/{rid}'), ('GET', f'/runs/{rid}/events'),
                ('POST', f'/runs/{rid}/cancel'), ('GET', f'/conversations/{cid}/runs')]:
                assert client.request(method, path, headers={'Authorization': 'Bearer bob-secret'}).status_code == 403
            assert client.post('/runs', json=body, headers={'Authorization': 'Bearer bob-secret'}).status_code == 403
            assert client.get('/conversations', headers={'Authorization': 'Bearer bob-secret'}).json() == []
            assert client.post('/conversations?project_id=alpha', headers={'Origin': 'https://evil.test'}).status_code == 403
            assert len(models) == 1 and len(w.conversations[cid].turns) == 1
            client.delete('/session')
            assert client.get(f'/runs/{rid}').status_code == 401
    monkeypatch.setattr('repo_agent.model.create_model', lambda: pytest.fail('Viewing must not call a model'))
    with LocalStore(tmp_path) as store:
        w = worker(store)
        with TestClient(create_app(RunManager(store.records, w))) as client:
            assert client.get(f'/runs/{rid}', headers={'Authorization': 'Bearer alice-secret'}).json() == before
            assert client.get(f'/runs/{rid}', headers={'Authorization': 'Bearer bob-secret'}).status_code == 403


def test_direct_http_and_independent_mcp_client_cannot_expand_service_grant(tmp_path):
    from repo_agent.business import BusinessClient
    from repo_agent.mcp_business import MCPBusinessClient
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    log = tmp_path / 'service.jsonl'
    env = {k: v for k, v in os.environ.items() if not k.startswith('BUSINESS_')}
    env['BUSINESS_GRANTS'] = json.dumps({'service-a': ['o-999'], 'service-b': ['o-1000']})
    child = subprocess.Popen([sys.executable, 'fixtures/business-api/server.py', '--port', '0', '--log', str(log)],
        env=env, stdout=subprocess.PIPE, text=True)
    try:
        url = 'http://127.0.0.1:' + child.stdout.readline().strip().rsplit(':', 1)[1]
        assert httpx.get(url+'/api/v1/orders/o-1000', headers={'Authorization': 'Bearer service-a'}, trust_env=False).status_code == 403
        async def scenario():
            async with MCPBusinessClient(url, 'service-a', allowed_orders=frozenset({'o-999'})) as bridge:
                assert (await bridge.query('status', 'o-1000'))['status'] == 'denied'
                assert (await bridge.query('status', 'o-999'))['status'] == 'ok'
            # Bypass the Agent's bridge; server-side bounds still apply.
            for claimed_scope, expected in [(['o-999'], 'denied'), (['o-999', 'o-1000'], 'forbidden')]:
                params = StdioServerParameters(command=sys.executable, args=['-m', 'repo_agent.mcp_business'],
                    env={'BUSINESS_API_URL': url, 'BUSINESS_API_TOKEN': 'service-a',
                         'BUSINESS_ALLOWED_ORDERS': json.dumps(claimed_scope)})
                async with stdio_client(params) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize()
                        result = await session.call_tool('get_order_status', {'order_id': 'o-1000'})
                        assert json.loads(result.content[0].text)['status'] == expected
            # Even a newly constructed HTTP client with an expanded allowlist cannot add a grant.
            result = await BusinessClient(url, 'service-a').query('status', 'o-1000')
            assert result['status'] == 'forbidden' and result['http_status'] == 403
        asyncio.run(scenario())
    finally:
        child.terminate()
        child.wait(timeout=5)
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(r['status'] == 403 for r in rows) == 3
    assert all('service-a' not in json.dumps(row) for row in rows)
