"""Plan checks certify associations and coverage, never the meaning of a proposal."""
from copy import deepcopy
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from repo_agent.application import RunManager
from repo_agent.checks import check_record
from repo_agent.conversation import ConversationWorker
from repo_agent.output import consume_answer
from repo_agent.persistence import LocalStore
from repo_agent.planning import PlanningRequest
from repo_agent.web import create_app
from tests.repo_agent.test_repository import ScriptedModel, request


def requirement():
    return {'goal': '为订单不存在响应增加 message',
        'constraints': [{'id': 'c1', 'text': '保留 HTTP 404 和 ORDER_NOT_FOUND'}],
        'acceptance': [{'id': 'a1', 'text': '订单不存在响应含面向用户的 message'}],
        'open_questions': []}


def plan(*, source='obs-1', target='src/ApiExceptionHandler.java'):
    return {'outcome': 'ready_for_review', 'summary': '在异常映射中补充 message，保留原状态码和 code',
        'facts': [{'statement': '异常处理器返回 404 与 ORDER_NOT_FOUND', 'source_ids': [source]}],
        'steps': [{'title': '补充错误消息', 'target': target, 'change': '在响应体增加固定 message',
            'fact_indices': [0], 'constraint_ids': ['c1'], 'acceptance_ids': ['a1']}],
        'checks': [{'acceptance_id': 'a1', 'method': '请求不存在订单并核对响应字段',
            'expected': 'HTTP 404，code 保留，message 为约定文案'}],
        'tradeoffs': ['放在异常处理器，避免在 Controller 复制异常映射'], 'questions': []}


def run_plan(monkeypatch, tmp_path, value=None, req=None, *, question=None):
    from repo_agent.execution import run_repository
    req = PlanningRequest.model_validate(req or requirement())
    value = plan() if value is None else value
    monkeypatch.setattr('repo_agent.model.create_model', lambda: ScriptedModel([
        request('read_file', {'path': 'src/ApiExceptionHandler.java'}, 1),
        request('submit_change_plan', {'plan': value}, 2)]))
    return run_repository(Path('fixtures/order-service'), question or req.goal, tmp_path/'run.json',
        structured_output=True, planning_request=req)


def test_plan_uses_same_sdk_and_retains_bound_artifact(monkeypatch, tmp_path):
    record = run_plan(monkeypatch, tmp_path)
    report = check_record(record)
    assert report['ok'], report
    view = consume_answer(record)
    assert view['kind'] == 'plan_for_review'
    assert view['semantic_review'] == 'pending' and view['approval'] == 'not_requested'
    assert view['artifact']['run_id'] == record['run_id']
    assert len(view['artifact']['revision']) == 64
    assert view['request'] == requirement()
    assert view['evidence'][0]['source'] == 'src/ApiExceptionHandler.java'
    assert record['model_inputs'][0]['payload']['output_schema']['name'] == 'change-plan-v1'
    assert record['model_inputs'][0]['payload']['output_schema']['delivery'] == 'submit_change_plan'
    assert len(record['model_inputs']) == 2  # SDK stops after submission; no extra answer-generation call.
    assert 'ORDER_NOT_FOUND' in json.dumps(record['model_inputs'][0]['payload'], ensure_ascii=False)
    assert {t['name'] for t in record['model_inputs'][0]['payload']['tools']} == {
        'search_code', 'read_file', 'list_source_files', 'submit_change_plan'}


@pytest.mark.parametrize('change,error', [
    ('source', 'fact_0_source'), ('target', 'step_0_target'),
    ('unread', 'step_0_evidence'), ('fact', 'step_0_fact_indices'),
    ('constraint', 'constraint_coverage'), ('acceptance', 'acceptance_coverage'),
    ('unknown_constraint', 'step_0_constraint_ids'), ('missing_check', 'check_coverage')])
def test_invalid_plan_is_retained_and_blocked(monkeypatch, tmp_path, change, error):
    value = plan()
    if change == 'source': value['facts'][0]['source_ids'] = ['invented']
    elif change == 'target': value['steps'][0]['target'] = '../outside.java'
    elif change == 'unread': value['steps'][0]['target'] = 'src/OrderController.java'
    elif change == 'fact': value['steps'][0]['fact_indices'] = [10]
    elif change == 'constraint': value['steps'][0]['constraint_ids'] = []
    elif change == 'acceptance': value['steps'][0]['acceptance_ids'] = []
    elif change == 'unknown_constraint': value['steps'][0]['constraint_ids'] = ['invented']
    else: value['checks'] = []
    record = run_plan(monkeypatch, tmp_path, value)
    assert record['answer'] == value and record['stop_reason'] == 'answer_returned'
    assert check_record(record)['ok']
    view = consume_answer(record)
    assert view['kind'] == 'unavailable'
    assert error in view['checks']['errors']


def test_open_request_cannot_become_ready(monkeypatch, tmp_path):
    req = requirement(); req['open_questions'] = ['客户端文案是否固定？']
    record = run_plan(monkeypatch, tmp_path, req=req)
    assert 'unresolved_request' in consume_answer(record)['checks']['errors']


def test_missing_information_is_a_saved_result_not_a_fake_plan(monkeypatch, tmp_path):
    req = requirement(); req['open_questions'] = ['客户端文案是否固定？']
    value = plan(); value.update(outcome='needs_information', steps=[], checks=[],
        questions=['请确认错误文案要求'])
    record = run_plan(monkeypatch, tmp_path, value, req)
    view = consume_answer(record)
    assert view['kind'] == 'plan_needs_information' and view['steps'] == []
    assert view['request']['open_questions'] == req['open_questions']


def test_real_reference_cannot_prove_a_plan_is_correct(monkeypatch, tmp_path):
    value = plan(); value['steps'][0]['change'] = '把 404 改成 200'
    view = consume_answer(run_plan(monkeypatch, tmp_path, value))
    assert view['kind'] == 'plan_for_review' and view['semantic_review'] == 'pending'
    # Coverage IDs alone cannot certify that the prose obeys their meaning.


@pytest.mark.parametrize('field', ['request', 'assessment', 'answer'])
def test_plan_binding_and_assessment_tampering_is_detected(monkeypatch, tmp_path, field):
    record = deepcopy(run_plan(monkeypatch, tmp_path))
    if field == 'request': record['planning']['request']['goal'] = '悄悄换需求'
    elif field == 'assessment': record['answer_checks']['sources'] = 'failed'
    else: record['answer']['summary'] = '改写最终输出'
    assert not check_record(record)['ok']


def test_request_ids_are_unique_and_text_not_blank():
    value = requirement(); value['constraints'].append(value['constraints'][0])
    with pytest.raises(ValueError): PlanningRequest.model_validate(value)
    value = requirement(); value['goal'] = ' '
    with pytest.raises(ValueError): PlanningRequest.model_validate(value)


def test_unread_parallel_results_cannot_be_used_as_evidence(monkeypatch, tmp_path):
    from repo_agent.execution import run_repository
    monkeypatch.setattr('repo_agent.model.create_model', lambda: ScriptedModel([[
        request('read_file', {'path': 'src/ApiExceptionHandler.java'}, 1),
        request('submit_change_plan', {'plan': plan()}, 2)]]))
    record = run_repository(Path('fixtures/order-service'), '计划', tmp_path/'parallel.json',
        structured_output=True, planning_request=PlanningRequest.model_validate(requirement()))
    assert check_record(record)['ok']
    assert consume_answer(record)['kind'] == 'unavailable'
    assert 'fact_0_source' in record['answer_checks']['errors']


def test_two_submissions_cannot_silently_choose_one(monkeypatch, tmp_path):
    from repo_agent.execution import run_repository
    monkeypatch.setattr('repo_agent.model.create_model', lambda: ScriptedModel([[
        request('submit_change_plan', {'plan': plan()}, 1),
        request('submit_change_plan', {'plan': plan()}, 2)]]))
    record = run_repository(Path('fixtures/order-service'), '计划', tmp_path/'duplicate.json',
        structured_output=True, planning_request=PlanningRequest.model_validate(requirement()))
    assert record['stop_reason'] == 'model_error' and record['answer'] is None
    assert check_record(record)['ok'] and consume_answer(record)['kind'] == 'unavailable'


def test_input_gap_can_be_returned_without_inventing_code_facts(monkeypatch, tmp_path):
    from repo_agent.execution import run_repository
    req = requirement(); req['open_questions'] = ['请接口使用方确认文案契约']
    value = plan(); value.update(outcome='needs_information', facts=[], steps=[], checks=[],
        questions=['请接口使用方确认 message 是否要求固定文案'])
    monkeypatch.setattr('repo_agent.model.create_model', lambda: ScriptedModel([
        request('submit_change_plan', {'plan': value}, 1)]))
    record = run_repository(Path('fixtures/order-service'), req['goal'], tmp_path/'gap.json',
        structured_output=True, planning_request=PlanningRequest.model_validate(req))
    assert check_record(record)['ok']
    assert consume_answer(record)['kind'] == 'plan_needs_information'


def test_request_change_produces_another_bound_revision(monkeypatch, tmp_path):
    first = consume_answer(run_plan(monkeypatch, tmp_path/'first'))
    req = requirement(); req['constraints'][0]['text'] += '，由接口使用方核对'
    second = consume_answer(run_plan(monkeypatch, tmp_path/'second', req=req))
    assert first['artifact']['revision'] != second['artifact']['revision']
    assert first['artifact']['id'] != second['artifact']['id']


def test_supplemental_question_is_also_bound_to_revision(monkeypatch, tmp_path):
    first = consume_answer(run_plan(monkeypatch, tmp_path/'first', question='只看现有异常映射'))
    second = consume_answer(run_plan(monkeypatch, tmp_path/'second', question='补充核对 Controller'))
    assert first['artifact']['revision'] != second['artifact']['revision']


def test_invalid_tool_payload_is_retained_without_text_repair(monkeypatch, tmp_path):
    from repo_agent.execution import run_repository
    monkeypatch.setattr('repo_agent.model.create_model', lambda: ScriptedModel([
        request('submit_change_plan', {'plan': {'summary': '少了必需字段'}}, 1)]))
    record = run_repository(Path('fixtures/order-service'), '计划', tmp_path/'invalid.json',
        structured_output=True, planning_request=PlanningRequest.model_validate(requirement()))
    assert record['answer'] is None and record['stop_reason'] == 'model_error'
    assert check_record(record)['ok'] and consume_answer(record)['kind'] == 'unavailable'


def test_web_plan_survives_restart_without_calling_model_again(monkeypatch, tmp_path):
    class PlanningModel(ScriptedModel):
        def __init__(self): super().__init__([])
        async def get_response(self, system_instructions, input, *args, **kwargs):
            import ast
            outputs = [ast.literal_eval(i['output']) for i in input if i.get('type') == 'function_call_output']
            item = (request('submit_change_plan', {'plan': plan(source=outputs[-1]['observation_id'], target='code/src/ApiExceptionHandler.java')}, 2)
                if outputs else request('read_file', {'path': 'code/src/ApiExceptionHandler.java'}, 1))
            return await ScriptedModel([item]).get_response(system_instructions, input, *args, **kwargs)
    monkeypatch.setattr('repo_agent.model.create_model', PlanningModel)
    sources = {'code': Path('fixtures/order-service')}
    with LocalStore(tmp_path) as store:
        worker = ConversationWorker(sources, business_url='http://127.0.0.1:1', business_token='test', store=store)
        with TestClient(create_app(RunManager(store.records, worker))) as client:
            cid = client.post('/conversations').json()['conversation_id']
            response = client.post('/runs', json={'question': requirement()['goal'], 'conversation_id': cid,
                'planning_request': requirement()})
            assert response.status_code == 202, response.text
            rid = response.json()['run_id']
            assert 'completed' in client.get(f'/runs/{rid}/events').text
            before = client.get(f'/runs/{rid}').json()
            assert before['result']['kind'] == 'plan_for_review', before
            assert len(worker.conversations[cid].turns) == 1
    monkeypatch.setattr('repo_agent.model.create_model', lambda: pytest.fail('Viewing must not invoke a model'))
    with LocalStore(tmp_path) as store:
        worker = ConversationWorker(sources, business_url='http://127.0.0.1:1', business_token='test', store=store)
        with TestClient(create_app(RunManager(store.records, worker))) as client:
            assert client.get(f'/runs/{rid}').json() == before
            assert client.get(f'/conversations/{cid}/runs').json()[0]['result'] == before['result']
