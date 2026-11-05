"""A final investigation answer, its observed evidence, and a minimal consumer."""
import json
from dataclasses import replace
from typing import Literal

from agents import AgentOutputSchema
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    statement: str = Field(min_length=1, max_length=1500)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class InvestigationAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    outcome: Literal['answered', 'insufficient_information']
    conclusion: str = Field(min_length=1, max_length=2000)
    facts: list[Finding] = Field(max_length=12)
    conclusion_fact_indices: list[int] = Field(max_length=12)
    gaps: list[str] = Field(max_length=12)
    next_steps: list[str] = Field(max_length=8)


OUTPUT_INSTRUCTIONS = """
本轮最终回答必须符合 InvestigationAnswer；工具观察不是最终结论。
先按问题取得必要观察，观察仍不足且有可用工具时继续查证；无法补齐则结束并交代具体缺口，不为填字段编造。
outcome=answered表示你判断已回答问题，不表示应用已核实；无法回答核心问题用insufficient_information并填写gaps。
facts只放有观察依据的事实；每项source_ids逐字复制工具结果中的observation_id（如obs-1），不编造编号。
只搜索或列出文件不等于读过内容；文件事实必须引用read_file观察，API事实必须引用成功的业务查询。
应用将从原始观察装配文件路径/已读行区间、API数据/来源与时点，你不用重抄这些元数据。
引用确实存在不代表你的解释正确，必须核对事实与原始观察是否相符。失败查询不能支持业务事实。
conclusion_fact_indices用从0开始的facts下标说明结论依据。无法确认时可为空；没有事实时facts为空数组。
next_steps只列解决当前缺口所需的只读查询或人工补充，不扩展成未请求的处置建议，不执行任何写动作。
只围绕当前问题回答；保留资料有效版本、环境、人工审查限制和时间含义，当前状态不能解释过去原因。
"""


def output_contract():
    schema = AgentOutputSchema(InvestigationAnswer)
    return {'name': 'investigation-answer-v1', 'schema': schema.json_schema(),
            'strict': schema.is_strict_json_schema(), 'initial_tool_choice': 'required',
            'reset_tool_choice': True}


def with_observation_ids(tools):
    """Put citation IDs inside tool content, before the SDK serializes it for the model."""
    count = 0

    def observed(invoke):
        async def call(context, arguments):
            nonlocal count
            result = await invoke(context, arguments)
            if not isinstance(result, dict):
                raise TypeError('Observation IDs require dictionary tools')
            count += 1
            return {**result, 'observation_id': f'obs-{count}'}
        return call

    return [replace(tool, on_invoke_tool=observed(tool.on_invoke_tool)) for tool in tools]


def observed_source(message):
    result = json.loads(message['content'])
    if result['status'] != 'ok':
        raise ValueError('Expected a successful observation')
    identity = {'observation_id': result['observation_id'], 'call_id': message['tool_call_id']}
    if message['name'] == 'read_file':
        return {**identity, 'source': result['path'], **{k: result[k] for k in
            ('sha256', 'start_line', 'end_line', 'content', 'content_truncated')}}
    if message['name'] in ('get_order_status', 'get_order_events'):
        return {**identity, 'source': result['source'], 'observed_at': result['observed_at'],
                'as_of': result['data'].get('as_of'), 'data': result['data']}
    raise ValueError('Expected a file read or business query')


def assess_answer(record):
    """Check shape and observed source associations, never free-form claim meaning."""
    report = {'schema': 'not_produced', 'sources': 'not_checked', 'errors': [],
              'evidence': [], 'semantic_review': 'pending'}
    if record.get('answer') is None:
        if record.get('stop_reason') == 'output_invalid':
            report['schema'] = 'failed'
        return report
    try:
        answer = InvestigationAnswer.model_validate(record['answer'])
    except ValidationError:
        report['schema'] = 'failed'
        return report
    report['schema'] = 'passed'
    observations = {json.loads(m['content'])['observation_id']: m
                    for e in record['events'] for m in e['state']['messages'] if m['type'] == 'tool'}
    if (answer.outcome == 'answered' and (not answer.facts or not answer.conclusion_fact_indices)
            or answer.outcome == 'insufficient_information' and not any(g.strip() for g in answer.gaps)
            or any(not 0 <= i < len(answer.facts) for i in answer.conclusion_fact_indices)):
        report['errors'].append('answer_relationships')
    resolved = {}
    for index, fact in enumerate(answer.facts):
        for source_id in fact.source_ids:
            try:
                resolved[source_id] = observed_source(observations[source_id])
            except (KeyError, ValueError, TypeError, IndexError):
                report['errors'].append(f'fact_{index}_source')
    report['evidence'] = list(resolved.values())
    report['errors'] = sorted(set(report['errors']))
    report['sources'] = 'failed' if report['errors'] else 'passed'
    return report


def consume_answer(record):
    """Return a view model for CLI or a future page; never approve business actions."""
    from repo_agent.checks import check_record
    report = check_record(record)
    checks = report.get('answer_checks', {})
    if (not report['ok'] or record['stop_reason'] != 'answer_returned'
            or checks.get('schema') != 'passed' or checks.get('sources') != 'passed'):
        return {'kind': 'unavailable', 'stop_reason': record['stop_reason'],
                'record_errors': report['errors'], 'checks': checks}
    answer = InvestigationAnswer.model_validate(record['answer'])
    return {'kind': 'needs_information' if answer.outcome == 'insufficient_information' else 'answer_for_review',
            **answer.model_dump(), 'evidence': checks['evidence'], 'semantic_review': 'pending'}
