"""Exercise real edit guards, the SDK tool loop and cancellation via the existing API."""
from ast import literal_eval
import os
from pathlib import Path
import shutil
import subprocess
from time import monotonic, sleep

from fastapi.testclient import TestClient
import pytest

from repo_agent import editing, workspace
from repo_agent.persistence import LocalStore
from repo_agent.web import create_app
from tests.repo_agent.test_approval import REVISION, decide, manager, policy, save_plan, send, token
from tests.repo_agent.test_repository import ScriptedModel, answer, request


TARGET = 'code/src/ApiExceptionHandler.java'
NEIGHBOR = 'code/src/Order.java'
OLD = 'return ResponseEntity.status(404).body(Map.of("code", "ORDER_NOT_FOUND"));'
NEW = 'return ResponseEntity.status(404).body(Map.of("code", "ORDER_NOT_FOUND", "message", "订单不存在"));'
DOCKER = pytest.mark.skipif(os.getenv('HARNESS_DOCKER_WORKSPACE_TEST') != '1', reason='Requires pinned image')


def start(client, key, actor='alice', **extra):
    return client.post(f'/approvals/{key}/workspace/edit', headers=token(actor),
        json={**{'plan_revision': REVISION}, **extra})


def ready(client):
    key = send(client).json()['approval_id']
    assert decide(client, key).status_code == 200
    assert start(client, key).status_code == 409
    prepare = client.post(f'/approvals/{key}/workspace', headers=token('alice'), json={'plan_revision': REVISION})
    assert prepare.status_code == 200, prepare.text
    return key


def await_value(probe, timeout=15):
    end = monotonic() + timeout
    while monotonic() < end:
        value = probe()
        if value:
            return value
        sleep(.025)
    raise AssertionError('Expected observation was not reached')


def run_view(client, rid):
    return client.get(f'/runs/{rid}', headers=token('alice')).json()


def terminal(client, rid):
    def observe():
        value = run_view(client, rid)
        return value if value['state'] in ('completed', 'failed', 'cancelled') else None
    return await_value(observe)


def replace(path=TARGET, old=OLD, new=NEW, number=2):
    return request('replace_edit_text', {'path': path, 'old_text': old, 'new_text': new}, number)


@pytest.fixture
def owned_containers(monkeypatch):
    names = set()
    invoke = workspace._docker

    def track(args, timeout, **kwargs):
        if args[0] == 'create':
            names.add(args[args.index('--name') + 1])
        return invoke(args, timeout, **kwargs)

    def remaining():
        running = subprocess.check_output(
            ['docker', 'ps', '-a', '--filter', 'name=harness-workspace-', '--format', '{{.Names}}'],
            text=True, timeout=5).splitlines()
        return names.intersection(running)

    monkeypatch.setattr(workspace, '_docker', track)
    return remaining


@pytest.fixture
def fixture_copy(tmp_path):
    root = tmp_path / 'synthetic'
    shutil.copytree(Path('fixtures/order-service'), root, ignore=shutil.ignore_patterns('*.iml'))
    return root


def test_model_final_text_does_not_create_a_patch(tmp_path, fixture_copy, monkeypatch):
    monkeypatch.setattr(workspace.DockerProbe, 'verify', lambda *_: {'container_removed': True})
    monkeypatch.setattr(editing.model_module, 'create_model', lambda: ScriptedModel([answer('改好了')]))
    folder = tmp_path / 'data'
    with LocalStore(folder) as store:
        save_plan(store, fixture_copy, target=TARGET)
        with TestClient(create_app(manager(store, policy(fixture_copy)), workspace_probe=workspace.DockerProbe(),
                                  enable_edit=True)) as client:
            key = ready(client)
            assert start(client, key, actor='bob').status_code == 403
            assert start(client, key, plan_revision='b' * 64).status_code == 409
            assert start(client, key, target=NEIGHBOR).status_code == 422
            rid = start(client, key).json()['run_id']
            assert terminal(client, rid)['result'] == {'kind': 'unavailable', 'stop_reason': 'no_changes'}
            assert client.get(f'/runs/{rid}/patch', headers=token('alice')).status_code == 404
            assert not list((folder / 'workspaces').glob('.edit-*'))


@DOCKER
def test_edit_success_stale_text_and_denied_neighbor(tmp_path, fixture_copy, monkeypatch, owned_containers):
    fake = ScriptedModel([
        request('read_edit_file', {'path': TARGET}, 1),
        replace(),
        replace(new='MUST NOT WRITE', number=3),
        replace(path=NEIGHBOR, old='public', new='MUST NOT WRITE', number=4),
        request('read_edit_file', {'path': TARGET}, 5),
        answer('受限编辑运行结束'),
    ])
    monkeypatch.setattr(editing.model_module, 'create_model', lambda: fake)
    folder = tmp_path / 'data'
    with LocalStore(folder) as store:
        save_plan(store, fixture_copy, target=TARGET)
        with TestClient(create_app(manager(store, policy(fixture_copy)), workspace_probe=workspace.DockerProbe(),
                                  enable_edit=True)) as client:
            key = ready(client)
            rid = start(client, key).json()['run_id']
            result = terminal(client, rid)['result']
            assert result['kind'] == 'patch_for_review', result
            assert result['changed_paths'] == [TARGET] and result['plan_revision'] == REVISION
            assert '-' in result['patch'] and OLD in result['patch'] and NEW in result['patch']
            assert 'MUST NOT WRITE' not in result['patch'] and NEIGHBOR not in result['patch']
            assert client.get(f'/runs/{rid}/patch', headers=token('alice')).text == result['patch']
            assert client.get(f'/runs/{rid}/patch', headers=token('bob')).status_code == 403
            workspace_dir = folder / 'workspaces' / key
            before_bytes = (fixture_copy / TARGET.removeprefix('code/')).read_bytes()
            assert (workspace_dir / 'base' / TARGET).read_bytes() == (workspace_dir / 'write' / TARGET).read_bytes() == before_bytes
            assert OLD.encode() in before_bytes and NEW.encode() not in before_bytes
            assert (workspace_dir / 'base' / NEIGHBOR).read_bytes() == (fixture_copy / 'src/Order.java').read_bytes()
            assert not owned_containers() and not list(workspace_dir.parent.glob('.edit-*'))
            feedback = [item for item in fake.inputs[-1] if isinstance(item, dict) and item.get('type') == 'function_call_output']
            values = [literal_eval(item['output']) for item in feedback]
            assert [value['status'] for value in values] == ['ok', 'ok', 'old_text_not_found', 'target_denied', 'ok']
            assert 'container_removed' not in values[3]
            assert values[4]['content'] == values[0]['content'].replace(OLD, NEW)
            print('edit observations:', [value['status'] for value in values], 'sources retained; reviewer patch denied')
        with TestClient(create_app(manager(store, policy(fixture_copy)))) as restored:
            assert restored.get(f'/runs/{rid}/patch', headers=token('alice')).text == result['patch']


@DOCKER
def test_cancel_running_container_discards_partial_file(tmp_path, fixture_copy, monkeypatch, owned_containers):
    monkeypatch.setattr(editing.model_module, 'create_model', lambda: ScriptedModel([
        request('read_edit_file', {'path': TARGET}, 1), replace(), answer('本回复不应发布'),
    ]))
    # The fixed editor really writes the file; keep Docker alive so we can cancel mid-operation.
    monkeypatch.setattr(editing, 'REPLACE_PROGRAM', editing.REPLACE_PROGRAM + '\nimport time\ntime.sleep(30)\n')
    folder = tmp_path / 'data'
    with LocalStore(folder) as store:
        save_plan(store, fixture_copy, target=TARGET)
        probe = workspace.DockerProbe(timeout=4)
        with TestClient(create_app(manager(store, policy(fixture_copy)), workspace_probe=probe, enable_edit=True)) as client:
            key = ready(client)
            rid = start(client, key).json()['run_id']
            partial = folder / 'workspaces' / ('.edit-' + rid) / TARGET
            await_value(lambda: partial.is_file() and NEW.encode() in partial.read_bytes(), timeout=10)
            assert owned_containers()
            cancel = client.post(f'/runs/{rid}/cancel', headers=token('alice'))
            assert cancel.status_code == 202 and cancel.json()['state'] == 'cancelling'
            assert terminal(client, rid)['result'] == {'kind': 'unavailable', 'stop_reason': 'cancelled'}
            assert not owned_containers() and not partial.exists()
            assert client.get(f'/runs/{rid}/patch', headers=token('alice')).status_code == 404
            assert NEW.encode() not in (folder / 'workspaces' / key / 'write' / TARGET).read_bytes()
            print('cancel observations: partial bytes observed, cancelling -> cancelled, container absent, patch HTTP 404')
