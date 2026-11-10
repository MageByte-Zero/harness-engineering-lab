import ast
import asyncio
from copy import deepcopy
import json
from pathlib import Path

from agents import ModelResponse, Usage
from fastapi.testclient import TestClient
import httpx
import pytest

from repo_agent.application import RunManager
from repo_agent.checks import check_record
from repo_agent.conversation import ConversationWorker, Conversation
from repo_agent.web import create_app
from tests.repo_agent.test_business import DATA
from tests.repo_agent.test_output import payload
from tests.repo_agent.test_repository import ScriptedModel, answer, request


class ConversationModel(ScriptedModel):
    def __init__(self, *, old_source=False):
        super().__init__([])
        self.old_source = old_source

    async def get_response(self, system_instructions, input, *args, **kwargs):
        self.inputs.append(deepcopy(input))
        if len(self.inputs) == 1:
            item = request('get_order_status', {'order_id':'o-999'}, 1)
        else:
            outputs = [ast.literal_eval(i['output']) for i in input if i.get('type')=='function_call_output']
            value = payload()
            value['facts'][0]['source_ids'] = [outputs[0 if self.old_source else -1]['observation_id']]
            item = answer(json.dumps(value))
        return ModelResponse(output=[item], usage=Usage(requests=1), response_id=None)


def setup(monkeypatch, tmp_path):
    from repo_agent.business import BusinessClient
    original = BusinessClient
    monkeypatch.setattr('repo_agent.application.BusinessClient', lambda url,token: original(url,token,
        transport=httpx.MockTransport(lambda r: httpx.Response(200,json=DATA['o-999']['status']))))
    models=[]
    def factory():
        model=ConversationModel();models.append(model);return model
    monkeypatch.setattr('repo_agent.model.create_model',factory)
    worker=ConversationWorker({'policy':Path('fixtures/order-policy')},business_url='http://127.0.0.1',business_token='test')
    return worker, RunManager(tmp_path,worker), models


def test_follow_up_history_current_evidence_and_session_isolation(monkeypatch,tmp_path):
    async def scenario():
        worker,manager,models=setup(monkeypatch,tmp_path)
        a,b=worker.create(),worker.create()
        first=manager.submit('o-999状态',conversation_id=a.conversation_id)
        assert first.task is not None
        await first.task
        assert first.state=='completed'
        second=manager.submit('它现在呢',conversation_id=a.conversation_id)
        assert second.task is not None
        await second.task
        assert second.state=='completed' and len(a.turns)==2
        record=json.loads((tmp_path/f'{second.run_id}.json').read_text())
        assert check_record(record)['ok']
        assert record['conversation']['selected_run_ids']==[first.run_id]
        assert first.run_id+'/obs-1' in json.dumps(models[1].inputs[0])
        assert second.result is not None
        assert second.result['evidence'][0]['observation_id']==second.run_id+'/obs-1'
        other=manager.submit('独立问题',conversation_id=b.conversation_id)
        assert other.task is not None
        await other.task
        assert other.state=='completed'
        assert '它现在呢' not in json.dumps(models[-1].inputs[0],ensure_ascii=False)
        await manager.close()
    asyncio.run(scenario())


def test_old_reference_is_rejected_and_failed_turn_not_committed(monkeypatch,tmp_path):
    async def scenario():
        worker,manager,_=setup(monkeypatch,tmp_path);c=worker.create()
        first=manager.submit('first',conversation_id=c.conversation_id)
        assert first.task is not None
        await first.task
        monkeypatch.setattr('repo_agent.model.create_model',lambda:ConversationModel(old_source=True))
        second=manager.submit('second',conversation_id=c.conversation_id)
        assert second.task is not None
        await second.task
        assert second.state=='failed' and len(c.turns)==1 and not c.busy
        assert second.result is not None
        assert second.result['checks']['sources']=='failed'
        await manager.close()
    asyncio.run(scenario())


def test_selects_whole_turns_and_drops_oversized_latest_turn():
    c=Conversation()
    for i in range(5):
        c.turns.append({'run_id':str(i),'items':[{'role':'user','content':str(i)},
            {'type':'function_call','call_id':str(i)}, {'type':'function_call_output','call_id':str(i)}]})
    selection=c.select()
    assert selection['selected_run_ids']==['2','3','4'] and selection['omitted_turns']==2
    assert len(selection['history'])==9
    assert c.select(char_limit=1)['history']==[]
    selection['history'][0]['content']='mutated'
    assert c.turns[2]['items'][0]['content']=='2'
    c.turns.append({'run_id':'large','items':[{'role':'user','content':'x'*25000}]})
    assert c.select()['history']==[] and c.select()['omitted_turns']==6


def test_cancelled_turn_is_discarded_and_next_turn_can_continue(monkeypatch,tmp_path):
    async def scenario():
        worker,manager,_=setup(monkeypatch,tmp_path);c=worker.create()
        first=manager.submit('first',conversation_id=c.conversation_id)
        assert first.task is not None
        await first.task
        entered=asyncio.Event()
        class Waiting(ConversationModel):
            async def get_response(self,*args,**kwargs):
                entered.set();await asyncio.Event().wait()
                raise AssertionError("Unexpected release")
        monkeypatch.setattr('repo_agent.model.create_model',lambda:Waiting())
        cancelled=manager.submit('cancelled-question',conversation_id=c.conversation_id)
        await entered.wait()
        manager.cancel(cancelled)
        assert cancelled.task is not None
        await cancelled.task
        assert cancelled.state=='cancelled' and len(c.turns)==1 and not c.busy
        monkeypatch.setattr('repo_agent.model.create_model',lambda:ConversationModel())
        final=manager.submit('continue',conversation_id=c.conversation_id)
        assert final.task is not None
        await final.task
        assert final.state=='completed' and len(c.turns)==2
        record=json.loads((tmp_path/f'{final.run_id}.json').read_text())
        assert 'cancelled-question' not in json.dumps(record['conversation']['history'])
        await manager.close()
    asyncio.run(scenario())


def test_http_conflict_and_invalid_conversation(monkeypatch,tmp_path):
    worker,manager,_=setup(monkeypatch,tmp_path)
    class Waiting(ConversationModel):
        async def get_response(self,*args,**kwargs):
            await asyncio.Event().wait()
            raise AssertionError("Unexpected release")
    monkeypatch.setattr('repo_agent.model.create_model',lambda:Waiting())
    with TestClient(create_app(manager)) as client:
        assert client.post('/runs',json={'question':'q'}).status_code==422
        assert client.post('/runs',json={'question':'q','conversation_id':'unknown'}).status_code==404
        cid=client.post('/conversations').json()['conversation_id']
        data={'question':'q','conversation_id':cid}
        first=client.post('/runs',json=data)
        assert first.status_code==202
        assert client.post('/runs',json=data).status_code==409
        rid=first.json()['run_id']
        client.post(f'/runs/{rid}/cancel')
        assert 'cancelled' in client.get(f'/runs/{rid}/events').text


def test_changed_source_scope_is_rejected(monkeypatch,tmp_path):
    async def scenario():
        source=tmp_path/'source';source.mkdir();(source/'a.md').write_text('one')
        worker=ConversationWorker({'input':source},business_url='http://127.0.0.1',business_token='test')
        c=worker.create();(source/'a.md').write_text('changed')
        with pytest.raises(ValueError,match='sources changed'):
            await worker('question',tmp_path/'run.json',conversation=c,run_id='r',emit=lambda *a:None)
        assert not c.turns
    asyncio.run(scenario())


def test_long_conversation_selection_and_context_budget_failure(monkeypatch,tmp_path):
    async def scenario():
        worker,manager,models=setup(monkeypatch,tmp_path);c=worker.create();ids=[]
        for i in range(5):
            run=manager.submit(f'question-{i}',conversation_id=c.conversation_id)
            assert run.task is not None
            await run.task
            assert run.state=='completed'
            ids.append(run.run_id)
        record=json.loads((tmp_path/f'{ids[-1]}.json').read_text())
        assert record['conversation']['selected_run_ids']==ids[1:4]
        assert record['conversation']['omitted_turns']==1
        assert 'question-0' not in json.dumps(models[-1].inputs[0])
        assert check_record(record)['ok']
        from repo_agent import application
        original=application.run_repository_async
        async def limited(*args,**kwargs):
            return await original(*args,**kwargs,context_char_limit=1)
        monkeypatch.setattr(application,'run_repository_async',limited)
        run=manager.submit('will exceed budget',conversation_id=c.conversation_id)
        assert run.task is not None
        await run.task
        assert run.state=='failed' and len(c.turns)==5 and not c.busy
        blocked=json.loads((tmp_path/f'{run.run_id}.json').read_text())
        assert blocked['stop_reason']=='context_budget_exhausted'
        assert check_record(blocked)['ok']
        await manager.close()
    asyncio.run(scenario())


def test_repeated_provider_placeholder_ids_remain_distinct_items():
    c=Conversation()
    c.turns.append({'run_id':'r','items':[
        {'type':'reasoning','id':'__fake_id__','summary':[]},
        {'type':'message','id':'__fake_id__','role':'assistant','content':[]},
        {'type':'reasoning','id':'__fake_id__','summary':[]}]})
    history=c.select()['history']
    assert len({i['id'] for i in history})==3


def test_sdk_retains_repeated_reasoning_placeholder_items_across_turns(monkeypatch,tmp_path):
    async def scenario():
        from openai.types.responses import ResponseReasoningItem
        worker,manager,_=setup(monkeypatch,tmp_path)
        class ReasoningModel(ConversationModel):
            async def get_response(self,*args,**kwargs):
                response=await super().get_response(*args,**kwargs)
                response.output.insert(0,ResponseReasoningItem(id='__fake_id__',summary=[],type='reasoning'))
                return response
        monkeypatch.setattr('repo_agent.model.create_model',lambda:ReasoningModel())
        c=worker.create()
        for question in ('first','follow-up'):
            run=manager.submit(question,conversation_id=c.conversation_id)
            assert run.task is not None
            await run.task
            assert run.state=='completed'
            record=json.loads((tmp_path/f'{run.run_id}.json').read_text())
            assert check_record(record)['ok']
        assert len(c.turns)==2
        await manager.close()
    asyncio.run(scenario())
