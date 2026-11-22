"""Kill the serving process at two observable boundaries, using real Docker."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
from time import monotonic, sleep

import httpx
import uvicorn

from repo_agent import editing, workspace
from repo_agent.persistence import LocalStore
from repo_agent.recovery import OperationJournal
from repo_agent.verification import OrderVerifier
from repo_agent.web import create_app
from tests.repo_agent.test_approval import REVISION, manager, policy, save_plan, token
from tests.repo_agent.test_editing import DOCKER, TARGET, fixture_copy, ready, replace, start
from tests.repo_agent.test_repository import ScriptedModel, answer, request


CONTRACT = Path('fixtures/planning/add-error-message.json')
MESSAGE = '订单不存在'.encode()


def _serve(directory, root, port, cut):
    # Only this test child injects timing, and never supplies invented Docker output.
    calls = directory / 'calls.jsonl'

    def observe(kind):
        with calls.open('a') as stream:
            stream.write(json.dumps({'child': cut, 'kind': kind}) + '\n')

    create_model = editing.model_module.create_model

    def model():
        observe('model')
        if os.environ.get('HARNESS_RECOVERY_REAL_MODEL') == '1':
            return create_model()
        return ScriptedModel([
            request('read_edit_file', {'path': TARGET}, 1),
            replace(),
            answer('编辑结束'),
        ])

    class ObservedVerifier(OrderVerifier):
        def check(self, *args, **kwargs):
            observe('check')
            result = super().check(*args, **kwargs)
            if cut == 'before-check-receipt':
                os.kill(os.getpid(), signal.SIGSTOP)
            return result

    editing.model_module.create_model = model
    if cut == 'file-written':
        editing.REPLACE_PROGRAM += '\nimport time\ntime.sleep(35)\n'
    elif cut == 'check-receipt':
        save = OperationJournal.ready

        def hold_after_commit(journal, result):
            save(journal, result)
            os.kill(os.getpid(), signal.SIGSTOP)

        OperationJournal.ready = hold_after_commit

    contract = json.loads(CONTRACT.read_text())
    with LocalStore(directory) as store:
        app = create_app(manager(store, policy(root)), workspace_probe=workspace.DockerProbe(timeout=40),
                         enable_edit=True, order_verifier=ObservedVerifier(), order_contract=contract)
        uvicorn.run(app, host='127.0.0.1', port=port, access_log=False, log_level='warning')


@contextmanager
def _server(directory, root, cut):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with (directory / f'{cut}.log').open('a') as log, httpx.Client(
            base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=15) as client:
        child = subprocess.Popen([sys.executable, '-m', 'tests.repo_agent.test_recovery',
                                  str(directory), str(root), str(port), cut], stdout=log, stderr=log)
        try:
            def ready():
                if child.poll() is not None:
                    raise AssertionError(f'Server exited before readiness, code {child.returncode}')
                try:
                    return client.get('/capabilities').status_code == 200
                except httpx.TransportError:
                    return None

            _wait(ready, 12)
            yield client, child
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


def _wait(observe, seconds=30):
    end = monotonic() + seconds
    while monotonic() < end:
        if value := observe():
            return value
        sleep(.08)
    raise AssertionError(f'Timed out waiting for {observe.__name__}')


def _stored(directory, rid):
    with sqlite3.connect(directory / 'application.sqlite3') as db:
        value = db.execute('SELECT payload FROM runs WHERE id=?', (rid,)).fetchone()
    return json.loads(value[0])


def _kill(child):
    child.kill()
    assert child.wait(timeout=6) == -signal.SIGKILL


def _owned(rid):
    return subprocess.check_output(['docker', 'container', 'ls', '-a', '--filter',
                                    f'label={workspace.OPERATION_LABEL}={rid}', '--format', '{{.Names}}'],
                                   text=True, timeout=8).splitlines()


def _recover_twice(client, directory, rid):
    path = f'/runs/{rid}/recovery'
    assert client.post(path, headers=token('bob')).status_code == 403
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: client.post(path, headers=token('alice')), range(2)))
    assert [result.status_code for result in results] == [200, 200], [result.text for result in results]
    first, second = [result.json() for result in results]
    assert first == second and first['run_id'] == rid
    events = _stored(directory, rid)['events']
    assert [event['kind'] for event in events].count('recovery_recorded') == 1
    return first, [result.status_code for result in results]


def _start_check(client, candidate_id, repair=True):
    response = client.post(f'/runs/{candidate_id}/verification', headers=token('alice'), json={'repair': repair})
    assert response.status_code == 202, response.text
    return response.json()['run_id']


@DOCKER
def test_sigkill_recovers_edit_and_verification(tmp_path, fixture_copy):
    directory = tmp_path / 'data'
    ledgers = directory / 'calls.jsonl'
    with LocalStore(directory) as store:
        save_plan(store, fixture_copy, target=TARGET)
        plan = store.load_runs()[0]['view']
        plan['result']['request'] = json.loads(CONTRACT.read_text())
        store.save_run(plan, [])
    source = fixture_copy / 'src/ApiExceptionHandler.java'
    original_digest = sha256(source.read_bytes()).hexdigest()

    with _server(directory, fixture_copy, 'file-written') as (client, child):
        approval = ready(client)
        interrupted_edit = start(client, approval).json()['run_id']
        stage = directory / 'workspaces' / ('.edit-' + interrupted_edit) / TARGET
        def changed_bytes():
            return stage.read_bytes() if stage.is_file() and MESSAGE in stage.read_bytes() else None

        new_bytes = _wait(changed_bytes, 85)
        before_edit = _stored(directory, interrupted_edit)
        assert before_edit['view']['state'] == 'running' and 'receipt' not in before_edit['view']['operation']
        assert len(_owned(interrupted_edit)) == 1
        _kill(child)

    with _server(directory, fixture_copy, 'new-edit') as (client, _):
        assert client.get(f'/runs/{interrupted_edit}', headers=token('alice')).json()['state'] == 'interrupted'
        assert start(client, approval).status_code == 409
        edited, edit_requests = _recover_twice(client, directory, interrupted_edit)
        assert edited['recovery'] == {'status': 'edit_needs_review', 'removed_containers': 1,
                                      'changed_paths': [TARGET], 'unavailable_paths': []}
        assert not _owned(interrupted_edit) and stage.read_bytes() == new_bytes
        assert client.get(f'/runs/{interrupted_edit}/patch', headers=token('alice')).status_code == 404
        assert len(ledgers.read_text().splitlines()) == 1
        candidate = start(client, approval).json()['run_id']
        def patch():
            response = client.get(f'/runs/{candidate}/patch', headers=token('alice'))
            return response.content if response.status_code == 200 else None

        candidate_bytes = _wait(patch, 95)
        assert MESSAGE in candidate_bytes and b'.status(404)' in candidate_bytes

    with _server(directory, fixture_copy, 'check-receipt') as (client, child):
        interrupted_check = _start_check(client, candidate)
        checkpoint = _wait(lambda: _stored(directory, interrupted_check)['view']['operation'].get('receipt'), 65)
        assert _stored(directory, interrupted_check)['view']['state'] == 'running'
        assert checkpoint['attempts'][0]['outcome'] == 'verified'
        _kill(child)

    prior_calls = ledgers.read_text().splitlines()
    with _server(directory, fixture_copy, 'check-recovery') as (client, _):
        assert client.get(f'/runs/{interrupted_check}', headers=token('alice')).json()['state'] == 'interrupted'
        checked, check_requests = _recover_twice(client, directory, interrupted_check)
        assert checked['state'] == 'completed' and checked['recovery']['status'] == 'receipt_restored'
        assert checked['result'] == checkpoint
        assert client.get(f'/runs/{interrupted_check}/patch', headers=token('alice')).content == candidate_bytes
        assert ledgers.read_text().splitlines() == prior_calls
        assert not _owned(interrupted_check)
        assert not (directory / 'workspaces' / ('.verify-' + interrupted_check)).exists()

    with _server(directory, fixture_copy, 'before-check-receipt') as (client, child):
        missing_receipt = _start_check(client, candidate)
        def stopped_before_receipt():
            return subprocess.check_output(['ps', '-o', 'stat=', '-p', str(child.pid)], text=True).strip().startswith('T')

        _wait(stopped_before_receipt, 65)
        assert 'receipt' not in _stored(directory, missing_receipt)['view']['operation']
        _kill(child)

    with _server(directory, fixture_copy, 'retry-check') as (client, _):
        retry, retry_requests = _recover_twice(client, directory, missing_receipt)
        assert retry['recovery']['status'] == 'verification_retry'
        assert retry['recovery']['source_run_id'] == candidate
        assert client.get(f'/runs/{missing_receipt}/patch', headers=token('alice')).status_code == 404
        assert client.post(f'/runs/{candidate}/verification', headers=token('alice'),
                           json={'repair': True}).status_code == 409
        retry_id = _start_check(client, candidate, repair=False)
        def verified():
            view = client.get(f'/runs/{retry_id}', headers=token('alice')).json()
            return view if view['state'] == 'completed' else None

        assert _wait(verified)['result']['kind'] == 'verification_for_review'
    assert sha256(source.read_bytes()).hexdigest() == original_digest
    calls = [json.loads(line) for line in ledgers.read_text().splitlines()]
    assert [event['kind'] for event in calls] == ['model', 'model', 'check', 'check', 'check']
    print(json.dumps({
        'model': 'configured' if os.environ.get('HARNESS_RECOVERY_REAL_MODEL') == '1' else 'scripted',
        'edit': {'run': interrupted_edit, 'record_before_kill': 'running', 'receipt_before_kill': False,
                 'sigkill_returncode': -9, 'recovery': edited['recovery'], 'responses': edit_requests,
                 'base_sha256': original_digest, 'staged_sha256': sha256(new_bytes).hexdigest(),
                 'model_rounds_before_kill': sum(event['kind'] == 'model_started' for event in before_edit['events'])},
        'check': {'run': interrupted_check, 'candidate_run': candidate, 'record_before_kill': 'running',
                  'sigkill_returncode': -9, 'recovery': checked['recovery'], 'responses': check_requests,
                  'receipt': {key: value for key, value in checkpoint['attempts'][0].items() if key != 'patch'}},
        'missing_receipt': {'run': missing_receipt, 'recovery': retry['recovery'], 'responses': retry_requests,
                            'retry_run': retry_id},
        'calls': calls, 'source_unchanged': True,
    }, ensure_ascii=False))


if __name__ == '__main__':
    _, data, source_root, port, cut = sys.argv
    _serve(Path(data), Path(source_root), int(port), cut)
