"""Run the fixed order checker on a stored patch and exercise the one-repair boundary."""
from difflib import unified_diff
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from repo_agent import editing, workspace
from repo_agent.persistence import LocalStore
from repo_agent.verification import OrderVerifier
from repo_agent.web import create_app
from tests.repo_agent.test_approval import REVISION, decide, manager, policy, save_plan, send, token
from tests.repo_agent.test_editing import TARGET, OLD, NEW, terminal
from tests.repo_agent.test_repository import ScriptedModel, answer, request


REQUIREMENT = Path('fixtures/planning/add-error-message.json')


@pytest.mark.skipif(os.getenv('HARNESS_DOCKER_WORKSPACE_TEST') != '1', reason='Requires both local images')
def test_independent_verification_and_one_repair(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    shutil.copytree('fixtures/order-service', root, ignore=shutil.ignore_patterns('*.iml'))
    data = tmp_path / 'data'
    contract = json.loads(REQUIREMENT.read_text())
    original = (root / 'src/ApiExceptionHandler.java').read_bytes()
    base = original.decode()
    repaired = base.replace(OLD, NEW)
    broken = repaired.replace('.status(404)', '.status(200)')
    fake = ScriptedModel([
        request('read_edit_file', {'path': TARGET}, 1),
        request('replace_edit_text', {'path': TARGET, 'old_text': '.status(200)', 'new_text': '.status(404)'}, 2),
        answer('已修正状态码'),
    ])
    monkeypatch.setattr(editing.model_module, 'create_model', lambda: fake)

    def post(client, candidate, repair=False, actor='alice'):
        return client.post(f'/runs/{candidate}/verification', headers=token(actor), json={'repair': repair})

    def checked(client, candidate, repair=False):
        response = post(client, candidate, repair)
        assert response.status_code == 202, response.text
        rid = response.json()['run_id']
        return rid, terminal(client, rid)['result']

    with LocalStore(data) as store:
        save_plan(store, root, target=TARGET)
        plan = store.load_runs()[0]['view']
        plan['result']['request'] = contract
        store.save_run(plan, [])
        probe = workspace.DockerProbe()

        def app(verifier=None, required=contract):
            return TestClient(create_app(manager(store, policy(root)), workspace_probe=probe, enable_edit=True,
                              order_verifier=verifier, order_contract=required))

        with app() as client:
            approval = send(client).json()['approval_id']
            assert decide(client, approval).status_code == 200
            ready = client.post(f'/approvals/{approval}/workspace', headers=token('alice'),
                                json={'plan_revision': REVISION}).json()
            binding = {key: ready[key] for key in ('approval_id', 'handoff_id', 'project_id',
                       'owner_id', 'plan_revision', 'snapshot_id', 'targets', 'workspace_id')}

        def store_candidate(text):
            rid = str(uuid4())
            patch = ''.join(unified_diff(base.splitlines(keepends=True), text.splitlines(keepends=True),
                                        'a/' + TARGET, 'b/' + TARGET))
            store.save_run({'run_id': rid, 'question': '受控候选', 'state': 'completed',
                            'ownership': {'owner_id': 'alice', 'project_id': 'alpha'},
                            'result': {'kind': 'patch_for_review', 'run_id': rid, **binding,
                                       'changed_paths': [TARGET], 'patch': patch}}, [])
            return rid

        valid = store_candidate(repaired)
        invalid = store_candidate(broken)
        with app(OrderVerifier()) as client:
            assert post(client, valid, actor='bob').status_code == 403
            _, positive = checked(client, valid, repair=True)
            assert [a['outcome'] for a in positive['attempts']] == ['verified']
            rid, rejected = checked(client, invalid)
            assert rejected['stop_reason'] == 'requirements_failed'
            assert rejected['attempts'][0]['checks'][0] == {
                'id': 'missing_status', 'expected': 404, 'actual': 200, 'passed': False}
            assert client.get(f'/runs/{rid}/patch', headers=token('alice')).status_code == 404
            assert fake.inputs == []
            rid, fixed = checked(client, invalid, repair=True)
            assert fixed['kind'] == 'verification_for_review', fixed
            assert [a['outcome'] for a in fixed['attempts']] == ['requirements_failed', 'verified']
            assert fixed['patch'] == positive['patch']
            assert 'verification_failures' in str(fake.inputs[0]) and '200' in str(fake.inputs[0])
            assert client.get(f'/runs/{rid}/patch', headers=token('alice')).text == positive['patch']
            assert len(fake.inputs) == 3

            wrong_fix = ScriptedModel([
                request('read_edit_file', {'path': TARGET}, 4),
                request('replace_edit_text', {'path': TARGET, 'old_text': '.status(200)',
                                              'new_text': '.status(201)'}, 5),
                answer('再试一个状态码'),
            ])
            monkeypatch.setattr(editing.model_module, 'create_model', lambda: wrong_fix)
            _, exhausted = checked(client, invalid, repair=True)
            assert exhausted['stop_reason'] == 'requirements_failed'
            assert [a['outcome'] for a in exhausted['attempts']] == ['requirements_failed', 'requirements_failed']
            assert exhausted['attempts'][1]['checks'][0]['actual'] == 201
            assert len(wrong_fix.inputs) == 3

        with app(OrderVerifier('harness-order-check:lesson22-deliberately-absent')) as client:
            rid, blocked = checked(client, valid, repair=True)
            assert blocked['stop_reason'] == 'environment_failure'
            assert blocked['attempts'][0]['reason'] == 'verification_image_unavailable'
            assert not any('checks' in attempt for attempt in blocked['attempts'])
            assert len(wrong_fix.inputs) == 3
            assert client.get(f'/runs/{rid}/patch', headers=token('alice')).status_code == 404
        changed_contract = {**contract, 'acceptance': [{'id': 'a_new', 'text': '返回另一种业务响应'}]}
        with app(OrderVerifier(), changed_contract) as client:
            assert post(client, valid, repair=True).status_code == 409
            assert len(wrong_fix.inputs) == 3
        assert (root / 'src/ApiExceptionHandler.java').read_bytes() == original
        assert not list((data / 'workspaces').glob('.verify-*'))
