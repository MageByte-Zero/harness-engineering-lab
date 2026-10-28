"""Retrieval boundaries; scripted model outputs are not answer-quality evidence."""

import asyncio
import copy
from pathlib import Path

import pytest

from tests.repo_agent.test_repository import ScriptedModel, answer, request


def test_bundle_namespaces_sources_and_keeps_previous_input_unchanged():
    from repo_agent.tools import load_source_bundle, load_sources

    old = load_sources(Path('fixtures/order-service'))
    combined = load_source_bundle({'code': Path('fixtures/order-service'),
                                   'policy': Path('fixtures/order-policy')})
    assert combined['code/src/ApiExceptionHandler.java'] == old['src/ApiExceptionHandler.java']
    assert 'policy/closure-policy.md' in combined
    assert load_sources(Path('fixtures/order-service')) == old
    with pytest.raises(ValueError, match='namespace'):
        load_source_bundle({'../escape': Path('fixtures/order-service')})


def test_bundle_enforces_combined_limit(tmp_path):
    from repo_agent.tools import load_source_bundle

    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    for root in (first, second):
        for i in range(51):
            (root / f'{i}.md').write_text('safe')
    with pytest.raises(ValueError, match='size limit'):
        load_source_bundle({'first': first, 'second': second})


def test_fixed_retrieval_reads_context_with_provenance_and_stops_at_budget():
    from repo_agent.retrieval import collect_fixed

    observations = []
    sources = {'policy.md': 'before\nneedle\ncondition\n' + '\n'.join(['needle'] * 30)}
    limited = asyncio.run(collect_fixed(sources, ('needle',), limit=2,
                                        emit=observations.append))
    assert limited is True
    assert [o['name'] for o in observations] == ['search_code', 'read_file']
    assert observations[0]['result']['truncated'] is True
    read = observations[1]['result']
    assert read['path'] == 'policy.md' and read['start_line'] == 1
    assert '3: condition' in read['content'] and read['scope'] == 'snapshot_only'


@pytest.mark.parametrize('queries', [(), ('',), ('a\nb',), tuple('abcde'), 'abc'])
def test_bad_fixed_queries_rejected_before_reserving_output(monkeypatch, tmp_path, queries):
    from repo_agent.execution import run_repository
    from repo_agent import model as model_module

    def unexpected_model():
        raise AssertionError('Invalid queries must not initialize a model')

    monkeypatch.setattr(model_module, 'create_model', unexpected_model)

    output = tmp_path / 'unused' / 'run.json'
    with pytest.raises(ValueError, match='queries'):
        run_repository(Path('fixtures/order-service'), 'check', output, retrieval_queries=queries)
    assert not output.parent.exists()


def test_fixed_run_supplies_real_material_without_model_tools_and_checks_tampering(monkeypatch, tmp_path):
    from repo_agent import model as model_module
    from repo_agent.execution import run_repository
    from repo_agent.checks import check_record

    model = ScriptedModel([answer('故意错误的答案：MySQL；机械核对不能判语义')])
    monkeypatch.setattr(model_module, 'create_model', lambda: model)
    record = run_repository({'code': Path('fixtures/order-service')}, '查异常映射',
                            tmp_path / 'fixed.json', retrieval_queries=('OrderNotFound',))
    assert record['stop_reason'] == 'answer_returned'
    assert len(model.inputs) == 1 and model.tools == [[]]
    assert model.inputs[0][0] == {'role': 'user', 'content': '查异常映射'}
    assert 'ORDER_NOT_FOUND' in model.inputs[0][1]['content']
    assert record['retrieval']['complete'] is True
    report = check_record(record)
    assert report['ok'] and report['prefetched_tools'] > 1
    assert report['requested_tools'] == 0 and report['semantic_review'] == 'pending'

    damaged = copy.deepcopy(record)
    damaged['model_inputs'][0]['payload']['input'][1]['content'] += 'invented material'
    assert 'context_input' in check_record(damaged)['errors']
    damaged = copy.deepcopy(record)
    observation = next(m for e in damaged['events'] for m in e['state']['messages']
                       if m['type'] == 'prefetch' and m['name'] == 'read_file')
    import json
    result = json.loads(observation['content'])
    result['content'] = 'invented source'
    observation['content'] = json.dumps(result)
    assert 'read_observation' in check_record(damaged)['errors']


def test_fixed_no_match_does_not_count_as_read_or_complete_task(monkeypatch, tmp_path):
    from repo_agent import model as model_module
    from repo_agent.execution import run_repository
    from repo_agent.checks import check_record

    monkeypatch.setattr(model_module, 'create_model', lambda: ScriptedModel([answer('没有依据')]))
    record = run_repository(Path('fixtures/order-service'), 'check', tmp_path / 'empty.json',
                            retrieval_queries=('never-present-marker',))
    assert record['stop_reason'] == 'no_source_read'
    assert check_record(record)['ok'] and check_record(record)['successful_reads'] == 0


def test_prefetched_material_is_included_in_context_gate(monkeypatch, tmp_path):
    from repo_agent import model as model_module
    from repo_agent.execution import run_repository
    from repo_agent.checks import check_record

    model = ScriptedModel([])
    monkeypatch.setattr(model_module, 'create_model', lambda: model)
    record = run_repository(Path('fixtures/order-service'), 'check', tmp_path / 'blocked.json',
                            retrieval_queries=('OrderNotFound',), context_char_limit=10)
    assert record['stop_reason'] == 'context_budget_exhausted' and not model.inputs
    assert check_record(record)['ok']


def test_agent_can_read_namespaced_bundle_and_old_checker_still_works(monkeypatch, tmp_path):
    from repo_agent import model as model_module
    from repo_agent.execution import run_repository
    from repo_agent.checks import check_record

    model = ScriptedModel([request('read_file', {'path': 'code/src/Order.java'}, 1), answer('替身回答')])
    monkeypatch.setattr(model_module, 'create_model', lambda: model)
    record = run_repository({'code': Path('fixtures/order-service')}, 'check', tmp_path / 'agent.json')
    assert record['stop_reason'] == 'answer_returned'
    assert check_record(record)['ok'] and check_record(record)['prefetched_tools'] == 0


def test_fixed_search_truncation_is_not_reported_as_complete_coverage():
    from repo_agent.retrieval import collect_fixed

    observations = []
    limited = asyncio.run(collect_fixed({'many.md': '\n'.join(['needle'] * 30)},
        ('needle',), limit=16, emit=observations.append))
    assert limited is True


def test_fixed_search_failure_is_retained_and_does_not_call_model(monkeypatch, tmp_path):
    import subprocess
    from repo_agent import model as model_module
    from repo_agent.execution import run_repository
    from repo_agent.checks import check_record

    model = ScriptedModel([])
    monkeypatch.setattr(model_module, 'create_model', lambda: model)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 2, '', 'SECRET'))
    record = run_repository(Path('fixtures/order-service'), 'check', tmp_path / 'failed.json',
                            retrieval_queries=('OrderNotFound',))
    assert record['stop_reason'] == 'tool_error' and not model.inputs
    assert record['retrieval']['complete'] is False
    assert check_record(record)['ok']
    assert 'SECRET' not in (tmp_path / 'failed.json').read_text()
