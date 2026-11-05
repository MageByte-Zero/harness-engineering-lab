"""The final-answer contract belongs to the app; semantic quality is reviewed separately."""
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from repo_agent.business import BUSINESS_INSTRUCTIONS
from repo_agent.checks import check_record
from repo_agent.execution import run_repository
from tests.repo_agent.test_business import DATA, client_for
from tests.repo_agent.test_repository import ScriptedModel, answer, request


def payload(*, outcome='answered', statement='数据时点状态为未支付'):
    return {'outcome': outcome, 'conclusion': '查询到未支付状态',
            'facts': [{'statement': statement, 'source_ids': ['obs-1']}],
            'conclusion_fact_indices': [0], 'gaps': [], 'next_steps': []}


def run_case(tmp_path, monkeypatch, value=None, *, code=200, responses=None, **kwargs):
    from repo_agent import model
    value = payload() if value is None else value
    monkeypatch.setattr(model, 'create_model', lambda: ScriptedModel(responses or [
        request('get_order_status', {'order_id': 'o-999'}, 1), answer(json.dumps(value))]))
    client = client_for(lambda r: httpx.Response(code, json=DATA['o-999'][
        'events' if r.url.path.endswith('/events') else 'status']))
    return run_repository(Path('fixtures/order-policy'), '当前状态？', tmp_path/'run.json',
        instructions=BUSINESS_INSTRUCTIONS, business_client=client, structured_output=True, **kwargs)


def test_typed_answer_round_trip_and_consumer(tmp_path, monkeypatch):
    record = run_case(tmp_path, monkeypatch)
    assert record['stop_reason'] == 'answer_returned'
    assert record['answer']['facts'][0]['statement'] == '数据时点状态为未支付'
    assert json.loads((tmp_path/'run.json').read_text())['answer'] == record['answer']
    report = check_record(record)
    assert report['ok'], report
    assert report['answer_checks']['schema'] == 'passed'
    assert report['answer_checks']['sources'] == 'passed'
    from repo_agent.output import consume_answer
    view = consume_answer(record)
    assert view['kind'] == 'answer_for_review'
    assert view['evidence'][0]['source'] == '/api/v1/orders/o-999'
    assert view['evidence'][0]['as_of'] == '2026-11-04T01:00:00Z'
    assert view['evidence'][0]['data']['state'] == 'UNPAID'
    assert view['semantic_review'] == 'pending'


def test_tools_then_more_tools_then_insufficient_information(tmp_path, monkeypatch):
    value = payload(outcome='insufficient_information')
    value.update(conclusion='无法确认昨天原因', gaps=['缺少昨天的完整事件'], next_steps=['申请补充历史记录'])
    record = run_case(tmp_path, monkeypatch, value, responses=[
        request('get_order_status', {'order_id': 'o-999'}, 1),
        request('get_order_events', {'order_id': 'o-999'}, 2), answer(json.dumps(value))])
    assert record['stop_reason'] == 'answer_returned'
    assert len(record['model_inputs']) == 3
    from repo_agent.output import consume_answer
    assert consume_answer(record)['kind'] == 'needs_information'
    assert check_record(record)['ok']


@pytest.mark.parametrize('value', ['not-json', '{}', '{"outcome":"answered"}'])
def test_invalid_output_is_retained_not_repaired(tmp_path, monkeypatch, value):
    record = run_case(tmp_path, monkeypatch, responses=[answer(value)])
    assert record['stop_reason'] == 'output_invalid' and record['answer'] is None
    assert len(record['model_inputs']) == 1
    assert check_record(record)['ok']
    assert record['answer_checks']['schema'] == 'failed'
    from repo_agent.output import consume_answer
    assert consume_answer(record)['kind'] == 'unavailable'


def test_forbidden_is_not_success_despite_valid_output(tmp_path, monkeypatch):
    value = {'outcome':'insufficient_information', 'conclusion':'权限不足，无法确认',
             'facts':[], 'conclusion_fact_indices':[], 'gaps':['接口返回403'],
             'next_steps':['请有权人员检查服务授权']}
    record = run_case(tmp_path, monkeypatch, value, code=403)
    assert record['answer']['outcome'] == 'insufficient_information'
    assert record['stop_reason'] == 'no_observation' and check_record(record)['ok']
    from repo_agent.output import consume_answer
    assert consume_answer(record)['kind'] == 'unavailable'


def test_failed_observation_cannot_support_a_fact(tmp_path, monkeypatch):
    record = run_case(tmp_path, monkeypatch, code=403)
    assert record['answer_checks']['sources'] == 'failed'


def test_invented_observation_is_blocked(tmp_path, monkeypatch):
    value = payload(); value['facts'][0]['source_ids'] = ['invented']
    record = run_case(tmp_path, monkeypatch, value)
    assert check_record(record)['ok']
    assert record['answer_checks']['sources'] == 'failed'
    from repo_agent.output import consume_answer
    assert consume_answer(record)['kind'] == 'unavailable'


def test_real_source_does_not_certify_claim_meaning(tmp_path, monkeypatch):
    record = run_case(tmp_path, monkeypatch, payload(statement='订单已经支付'))
    report = check_record(record)
    assert report['answer_checks']['sources'] == 'passed'
    assert report['semantic_review'] == 'pending'
    assert report['answer_checks']['evidence'][0]['data']['state'] == 'UNPAID'
    assert record['answer']['facts'][0]['statement'] == '订单已经支付'


@pytest.mark.parametrize('change', ['schema', 'answer', 'assessment', 'observation'])
def test_record_tampering_is_detected(tmp_path, monkeypatch, change):
    record = run_case(tmp_path, monkeypatch); changed = deepcopy(record)
    if change == 'schema':
        changed['model_inputs'][0]['payload']['output_schema']['schema']['properties'].pop('gaps')
    elif change == 'answer':
        changed['answer']['conclusion'] = '改写模型结果'
    elif change == 'assessment':
        changed['answer_checks']['sources'] = 'failed'
    else:
        tool = next(m for e in changed['events'] for m in e['state']['messages'] if m['type']=='tool')
        content = json.loads(tool['content']); content['observation_id'] = 'changed'
        tool['content'] = json.dumps(content)
    assert not check_record(changed)['ok']


def test_output_schema_counts_toward_context_limit(tmp_path, monkeypatch):
    record = run_case(tmp_path, monkeypatch, context_char_limit=1)
    assert record['stop_reason'] == 'context_budget_exhausted'
    assert record['model_inputs'][0]['payload']['output_schema']['strict'] is True
    assert record['answer_checks']['schema'] == 'not_produced'
    assert check_record(record)['ok']


@pytest.mark.parametrize('tool,args,want', [
    ('read_file', {'path':'rule.md','start_line':1,'end_line':1}, 'passed'),
    ('list_source_files', {}, 'failed'), ('search_code', {'keyword':'规则'}, 'failed')])
def test_file_evidence_is_built_from_actual_read_not_file_presence(tmp_path, monkeypatch, tool, args, want):
    from repo_agent import model
    source = tmp_path/'input'; source.mkdir()
    (source/'rule.md').write_text('规则第一行\n第二行\n')
    monkeypatch.setattr(model, 'create_model', lambda: ScriptedModel([
        request(tool, args, 1), answer(json.dumps(payload()))]))
    record = run_repository(source, '解释规则', tmp_path/'file.json', structured_output=True)
    assert check_record(record)['ok']
    assert record['answer_checks']['sources'] == want
    if want == 'passed':
        item = record['answer_checks']['evidence'][0]
        assert item['source'] == 'rule.md' and item['start_line'] == item['end_line'] == 1
        assert item['content'] == '1: 规则第一行'


@pytest.mark.parametrize('change', ['missing_gap', 'bad_index', 'no_facts'])
def test_answer_relationships_are_checked(tmp_path, monkeypatch, change):
    value = payload()
    if change == 'missing_gap': value['outcome'] = 'insufficient_information'
    elif change == 'bad_index': value['conclusion_fact_indices'] = [20]
    else: value.update(facts=[], conclusion_fact_indices=[])
    record = run_case(tmp_path, monkeypatch, value)
    assert record['answer_checks']['sources'] == 'failed'


def test_unsupported_provider_is_not_silently_downgraded(tmp_path, monkeypatch):
    from repo_agent import model
    from openai import BadRequestError
    import httpx2
    class RejectSchema(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            raise BadRequestError('unsupported', response=httpx2.Response(400,
                request=httpx2.Request('POST','https://example.invalid')), body=None)
    monkeypatch.setattr(model, 'create_model', lambda: RejectSchema([]))
    record = run_repository(Path('fixtures/order-policy'), '问题', tmp_path/'rejected.json', structured_output=True)
    assert record['stop_reason'] == 'model_error'
    assert record['error'] == {'type':'BadRequestError','status_code':400}
    assert record['answer'] is None and len(record['model_inputs']) == 1
    assert check_record(record)['ok']


def test_first_tool_required_then_sdk_releases_choice(tmp_path, monkeypatch):
    from repo_agent import model
    choices = []
    class CaptureSettings(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            choices.append(kwargs['model_settings'].tool_choice)
            return await super().get_response(*args, **kwargs)
    monkeypatch.setattr(model, 'create_model', lambda: CaptureSettings([
        request('get_order_status',{'order_id':'o-999'},1),answer(json.dumps(payload()))]))
    client = client_for(lambda r:httpx.Response(200,json=DATA['o-999']['status']))
    record = run_repository(Path('fixtures/order-policy'),'状态？',tmp_path/'choice.json',
        business_client=client,structured_output=True)
    assert choices == ['required', None]
    assert 'obs-1' in str(record['model_inputs'][1]['payload']['input'])
    assert record['answer_checks']['sources'] == 'passed'
    assert check_record(record)['ok']
