"""A read-only change proposal, bound to its request and observed source snapshot."""
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from repo_agent.output import Finding, observed_source
from repo_agent.tools import snapshot_hash


class PlanModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)


class RequirementItem(PlanModel):
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,31}$')
    text: str = Field(min_length=1, max_length=500)


class PlanningRequest(PlanModel):
    goal: str = Field(min_length=1, max_length=2000)
    constraints: list[RequirementItem] = Field(max_length=8)
    acceptance: list[RequirementItem] = Field(max_length=8)
    open_questions: list[str] = Field(max_length=8)

    @model_validator(mode='after')
    def unique_ids(self):
        for items in (self.constraints, self.acceptance):
            if len({i.id for i in items}) != len(items):
                raise ValueError('Requirement IDs must be unique within each collection')
        if any(not q.strip() or len(q) > 500 for q in self.open_questions):
            raise ValueError('Open questions must contain 1–500 characters')
        return self


class PlanStep(PlanModel):
    title: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=300)
    change: str = Field(min_length=1, max_length=1000)
    fact_indices: list[int] = Field(max_length=12)
    constraint_ids: list[str] = Field(max_length=8)
    acceptance_ids: list[str] = Field(max_length=8)


class AcceptanceCheck(PlanModel):
    acceptance_id: str = Field(min_length=1, max_length=32)
    method: str = Field(min_length=1, max_length=700)
    expected: str = Field(min_length=1, max_length=700)


class ChangePlan(PlanModel):
    outcome: Literal['ready_for_review', 'needs_information']
    summary: str = Field(min_length=1, max_length=1500)
    facts: list[Finding] = Field(max_length=12)
    steps: list[PlanStep] = Field(max_length=8)
    checks: list[AcceptanceCheck] = Field(max_length=8)
    tradeoffs: list[str] = Field(max_length=4)
    questions: list[str] = Field(max_length=8)


PLAN_INSTRUCTIONS = """
本轮是修改计划任务，完成取材后调用submit_change_plan提交ChangePlan，不用普通文本或最终JSON消息代替提交。
用户输入含 planning_request：goal是目标，constraints是必须遵守的约束，acceptance是用户给定的独立接受条件，open_questions是尚未确认的阻塞问题。
只读取与修改相关的代码与资料，目录可见不等于都要读取。需要任务方法时加载plan-change Skill；Skill不是事实依据。
输入已有明确open_questions时，可以直接提交needs_information并逐条写清待谁确认什么；不必先读取与这个问题无关的全部源码或规程。
facts只记录当前观察支持的现状，source_ids逐字复制本轮工具观察编号；不把用户需求或未来改法写成已发生事实。
steps描述尚未执行的改法，target使用实际读取过的本次快照相对路径，fact_indices引用facts的从0开始下标。
每一步用constraint_ids和acceptance_ids关联相关输入条目，覆盖全部约束和接受条件；不能自创ID或改写输入要求。
checks逐项使用输入acceptance_id，分别写核对方法和预期结果；这些是后续独立检查的计划，不是已经执行的测试。
tradeoffs只写与当前改法有关的取舍；尚未知的客户端契约等不能假设成事实。
有open_questions，或缺少约束/接受条件，或依据不足时用needs_information，questions说明需要谁补什么；不要装成可审计划。
ready_for_review须有事实、具体修改步骤、逐项检查，且无阻塞问题；仅表示提交审查，不表示通过审批或方案正确。
当前只为快照内已有文件规划小改动。所有工具仍只读，不写文件、不执行代码、不申请批准，不声称测试或变更已经发生。
submit_change_plan只提交待检查的建议；提交后SDK结束本轮，不会再向模型询问一次。先取得所引用的工具结果，再在后续一轮提交；不要在同一批并行请求里一边读文件一边引用尚未收到的结果。
"""


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


def planning_input(question, request):
    return question + '\nplanning_request=' + json.dumps(request, ensure_ascii=False, sort_keys=True)


def submission_tool():
    from agents import function_tool

    @function_tool(failure_error_function=None)
    async def submit_change_plan(plan: ChangePlan) -> dict:
        """提交已取材的修改计划或待补充问题。只返回待检查产物，不批准、不修改文件、不执行测试。"""
        return {'status': 'submitted', 'scope': 'plan_proposal', 'plan': plan.model_dump(mode='json')}

    return submit_change_plan


def assess_plan(record):
    """Check provenance and explicit associations. Text meaning stays pending review."""
    report = {'schema': 'not_produced', 'sources': 'not_checked', 'errors': [],
              'evidence': [], 'semantic_review': 'pending'}
    if record.get('answer') is None:
        if record.get('stop_reason') == 'output_invalid':
            report['schema'] = 'failed'
        return report
    from pydantic import ValidationError
    try:
        plan = ChangePlan.model_validate(record['answer'])
    except ValidationError:
        report['schema'] = 'failed'
        return report
    report['schema'] = 'passed'
    req = PlanningRequest.model_validate(record['planning']['request'])
    observations = {json.loads(m['content'])['observation_id']: m
        for e in record['events'] for m in e['state']['messages'] if m['type'] == 'tool'}
    # A read in the same parallel batch has not yet reached the model that proposed the plan.
    visible_calls = {i['call_id'] for i in record['model_inputs'][-1]['payload']['input']
                     if i.get('type') == 'function_call_output'}
    resolved = {}
    for index, fact in enumerate(plan.facts):
        for source_id in fact.source_ids:
            try:
                observation = observations[source_id]
                if observation['tool_call_id'] not in visible_calls:
                    raise ValueError('Evidence was not visible when the plan was submitted')
                resolved[source_id] = observed_source(observation)
            except (KeyError, ValueError, TypeError, IndexError):
                report['errors'].append(f'fact_{index}_source')
    constraints, acceptance = {i.id for i in req.constraints}, {i.id for i in req.acceptance}
    seen_constraints, seen_acceptance = set(), set()
    for index, step in enumerate(plan.steps):
        if step.target not in record['sources']:
            report['errors'].append(f'step_{index}_target')
        if not step.fact_indices or any(not 0 <= f < len(plan.facts) for f in step.fact_indices):
            report['errors'].append(f'step_{index}_fact_indices')
        sources = [resolved[s] for f in step.fact_indices if 0 <= f < len(plan.facts)
            for s in plan.facts[f].source_ids if s in resolved]
        if not any(s['source'] == step.target and 'sha256' in s for s in sources):
            report['errors'].append(f'step_{index}_evidence')
        if not set(step.constraint_ids) <= constraints:
            report['errors'].append(f'step_{index}_constraint_ids')
        if not set(step.acceptance_ids) <= acceptance:
            report['errors'].append(f'step_{index}_acceptance_ids')
        seen_constraints.update(step.constraint_ids)
        seen_acceptance.update(step.acceptance_ids)
    check_ids = [c.acceptance_id for c in plan.checks]
    if not set(check_ids) <= acceptance or len(check_ids) != len(set(check_ids)):
        report['errors'].append('check_ids')
    if any(not q.strip() or len(q) > 700 for q in [*plan.questions, *plan.tradeoffs]):
        report['errors'].append('blank_or_oversized_text')
    if plan.outcome == 'ready_for_review':
        if req.open_questions or plan.questions or not constraints or not acceptance:
            report['errors'].append('unresolved_request')
        if not plan.steps or not plan.facts:
            report['errors'].append('empty_plan')
        if seen_constraints != constraints:
            report['errors'].append('constraint_coverage')
        if seen_acceptance != acceptance:
            report['errors'].append('acceptance_coverage')
        if set(check_ids) != acceptance:
            report['errors'].append('check_coverage')
    elif not plan.questions:
        report['errors'].append('missing_questions')
    report['evidence'] = list(resolved.values())
    report['errors'] = sorted(set(report['errors']))
    report['sources'] = 'failed' if report['errors'] else 'passed'
    return report


def consume_plan(record):
    from repo_agent.checks import check_record
    report = check_record(record)
    checks = report.get('answer_checks', {})
    if (not report['ok'] or record['stop_reason'] != 'answer_returned'
            or checks.get('schema') != 'passed' or checks.get('sources') != 'passed'):
        return {'kind': 'unavailable', 'stop_reason': record['stop_reason'],
                'record_errors': report['errors'], 'checks': checks}
    request = record['planning']['request']
    snapshot = snapshot_hash({p: s['content'] for p, s in record['sources'].items()})
    binding = {'question': record['question'], 'request': request, 'snapshot_id': snapshot, 'plan': record['answer'],
               'evidence': checks['evidence']}
    return {'kind': 'plan_for_review' if record['answer']['outcome'] == 'ready_for_review'
            else 'plan_needs_information', **record['answer'], 'request': request,
        'artifact': {'id': record['run_id'] + '/plan', 'run_id': record['run_id'],
            'revision': digest(binding), 'request_sha256': digest(request), 'snapshot_id': snapshot},
        'evidence': checks['evidence'], 'semantic_review': 'pending', 'approval': 'not_requested'}
