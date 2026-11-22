"""Trace a real edit, then join the last observed call to its post-SIGKILL recovery."""
from hashlib import sha256
import json
import os
from uuid import uuid4

from repo_agent.observability import RunTelemetry
from repo_agent.persistence import LocalStore
from tests.repo_agent.test_approval import save_plan, token
from tests.repo_agent.test_editing import DOCKER, TARGET, fixture_copy, ready, start
from tests.repo_agent.test_recovery import CONTRACT, MESSAGE, _kill, _owned, _recover_twice, _server, _start_check, _wait


def _trace(client, run_id):
    response = client.get(f'/runs/{run_id}/trace', headers=token('alice'))
    assert response.status_code == 200
    return response.json()


def _result(client, run_id):
    response = client.get(f'/runs/{run_id}', headers=token('alice'))
    assert response.status_code == 200
    return response.json()


def _missing_finish(view):
    running = [event['data']['call_id'] for event in view['timeline']
               if event['kind'] == 'tool_started' and event['data'].get('tool') == 'replace_edit_text']
    assert len(running) == 1
    call = running[0]
    assert any(event['kind'] == 'tool_requested' and event['data'].get('call_id') == call
               for event in view['timeline'])
    assert not any(event['kind'] == 'tool_finished' and event['data'].get('call_id') == call
                   for event in view['timeline'])
    assert not any(span['name'] == 'agent.tool' and span['attributes']['harness.tool.call_id'] == call
                   for span in view['spans'])
    return call


def _requested(spans, call):
    return next(span for span in spans if span['name'] == 'agent.model' and any(
        item['attributes'].get('harness.tool.call_id') == call for item in span['events']))


def _assert_metadata(view):
    serialized = json.dumps(view, ensure_ascii=False)
    for content in ('订单不存在', 'ORDER_NOT_FOUND', 'alice-token', 'old_text', 'new_text', 'ApiExceptionHandler.java'):
        assert content not in serialized
    assert all(span['end_ns'] >= span['start_ns'] for span in view['spans'])


def test_trace_export_reads_around_a_torn_line(tmp_path):
    telemetry = RunTelemetry(tmp_path)
    key = str(uuid4())
    first = telemetry.start(key, 'edit')
    first.end()
    with telemetry.exporter.path(key).open('ab') as stream:
        stream.write(b'{"unfinished":')
    assert len(telemetry.exporter.read(key)) == 1
    second = telemetry.start(key, 'edit')
    second.end()
    assert len(telemetry.exporter.read(key)) == 2


@DOCKER
def test_trace_links_interrupted_edit_and_recovery(tmp_path, fixture_copy):
    directory = tmp_path / 'data'
    with LocalStore(directory) as store:
        save_plan(store, fixture_copy, target=TARGET)
        plan = store.load_runs()[0]['view']
        plan['result']['request'] = json.loads(CONTRACT.read_text())
        store.save_run(plan, [])
    source = fixture_copy / TARGET.removeprefix('code/')
    original_digest = sha256(source.read_bytes()).hexdigest()

    with _server(directory, fixture_copy, 'trace-complete') as (client, _):
        approval = ready(client)
        candidate = start(client, approval).json()['run_id']

        def patch():
            result = client.get(f'/runs/{candidate}/patch', headers=token('alice'))
            return result.content if result.status_code == 200 else None

        assert MESSAGE in _wait(patch, 100)
        completed = _trace(client, candidate)
        assert completed['business']['result_kind'] == 'patch_for_review'
        completed_root = next(span for span in completed['spans'] if span['name'] == 'agent.run')
        assert completed_root['trace_id'] == completed['original_trace']['trace_id']
        assert completed_root['attributes']['harness.run_state'] == 'completed'
        replacement = next(span for span in completed['spans'] if span['name'] == 'agent.tool'
                           and span['attributes']['harness.tool.name'] == 'replace_edit_text')
        assert replacement['attributes']['harness.tool.result'] == 'ok'
        request = _requested(completed['spans'], replacement['attributes']['harness.tool.call_id'])
        assert request['trace_id'] == replacement['trace_id'] == completed_root['trace_id']
        assert request['parent_span_id'] == replacement['parent_span_id'] == completed_root['span_id']
        check_id = _start_check(client, candidate, repair=False)

        def checked():
            result = _result(client, check_id)
            return result if result['state'] == 'completed' else None

        assert _wait(checked, 45)['result']['kind'] == 'verification_for_review'
        checked_trace = _trace(client, check_id)
        check = next(span for span in checked_trace['spans'] if span['name'] == 'agent.verification')
        assert check['attributes']['harness.source_run_id'] == candidate
        assert check['attributes']['harness.check.count'] == 4
        assert check['attributes']['harness.check.failed_count'] == 0

    with _server(directory, fixture_copy, 'file-written') as (client, child):
        interrupted = start(client, approval).json()['run_id']
        staging = directory / 'workspaces' / ('.edit-' + interrupted) / TARGET

        def wrote_into_copy():
            return staging.read_bytes() if staging.is_file() and MESSAGE in staging.read_bytes() else None

        staged_bytes = _wait(wrote_into_copy, 90)
        assert len(_owned(interrupted)) == 1
        _kill(child)

    with _server(directory, fixture_copy, 'trace-recovery') as (client, _):
        before = _trace(client, interrupted)
        assert before['business']['state'] == 'interrupted'
        assert before['business']['receipt_saved'] is False
        assert not any(span['name'] == 'agent.run' for span in before['spans'])
        call = _missing_finish(before)
        model = _requested(before['spans'], call)
        assert model['trace_id'] == before['original_trace']['trace_id']
        assert client.get(f'/runs/{interrupted}/trace', headers=token('bob')).status_code == 403
        recovered, codes = _recover_twice(client, directory, interrupted)
        assert recovered['recovery']['status'] == 'edit_needs_review'
        after = _trace(client, interrupted)
        assert after['original_trace'] == before['original_trace']
        assert after['business']['changed_file_count'] == 1 and after['business']['removed_containers'] == 1
        assert _missing_finish(after) == call
        recoveries = [span for span in after['spans'] if span['name'] == 'agent.recovery']
        assert len(recoveries) == len({span['trace_id'] for span in recoveries}) == 2
        assert all(span['trace_id'] != after['original_trace']['trace_id']
                   and span['links'] == [after['original_trace']] for span in recoveries)
        assert {span['attributes']['harness.recovery.reused'] for span in recoveries} == {True, False}
        assert sum(event['kind'] == 'recovery_recorded' for event in after['timeline']) == 1
        assert not _owned(interrupted)

    assert sha256(source.read_bytes()).hexdigest() == original_digest
    assert staging.read_bytes() == staged_bytes
    calls = [json.loads(line) for line in (directory/'calls.jsonl').read_text().splitlines()]
    assert [call['kind'] for call in calls] == ['model', 'check', 'model']
    for view in (completed, checked_trace, before, after):
        _assert_metadata(view)
    print(json.dumps({
        'model': 'configured' if os.getenv('HARNESS_RECOVERY_REAL_MODEL') == '1' else 'scripted',
        'completed_edit': completed, 'verification': checked_trace,
        'interrupted_run': after, 'unclosed_tool_call': call,
        'interrupted_before_recovery': before['business'],
        'sigkill_returncode': -9, 'recovery_http_statuses': codes, 'other_user_trace_http': 403,
        'model_and_check_invocations': calls,
        'staged_changed': sha256(staged_bytes).hexdigest() != original_digest,
        'source_unchanged': True,
    }, ensure_ascii=False))
