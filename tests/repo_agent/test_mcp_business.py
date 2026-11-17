import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('mode,token',[('normal','local-test'),('forbidden','local-test'),('normal','wrong')])
def test_real_stdio_bridge_matches_http_and_retains_catalog(tmp_path,mode,token):
    from repo_agent.mcp_business import MCPBusinessClient
    from repo_agent.business import BusinessClient
    process = subprocess.Popen([sys.executable, 'fixtures/business-api/server.py','--port','0',
        '--log',str(tmp_path/'http.jsonl'),'--mode',mode],env={**os.environ,'BUSINESS_API_TOKEN':'local-test'},stdout=subprocess.PIPE,text=True)
    try:
        port=int(process.stdout.readline().strip().rsplit(':',1)[1])
        async def scenario():
            url=f'http://127.0.0.1:{port}'
            async with MCPBusinessClient(url,token) as client:
                for kind,oid in [('status','o-999'),('events','o-999'),('status','o-404'),('status','o-123'),('status','../secret')]:
                    got=await client.query(kind,oid)
                    direct=await BusinessClient(url,token).query(kind,oid)
                    assert {k:v for k,v in got.items() if k!='observed_at'}=={k:v for k,v in direct.items() if k!='observed_at'}
                assert [t['name'] for t in client.catalog]==['get_order_events','get_order_status']
                assert 'local-test' not in json.dumps(client.catalog)
            assert client.server.session is None
            # Independent official client: no Agent, no application bridge.
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client, StdioServerParameters
            params=StdioServerParameters(command=sys.executable,args=['-m','repo_agent.mcp_business'],
                env={'BUSINESS_API_URL':url,'BUSINESS_API_TOKEN':token})
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    response=await session.call_tool('get_order_status',{'order_id':'o-999'})
                    remote=json.loads(response.content[0].text)
                    expected=await BusinessClient(url,token).query('status','o-999')
                    assert remote['status']==expected['status']
                    assert remote.get('data')==expected.get('data')

        asyncio.run(scenario())
    finally:
        process.terminate();process.wait(timeout=5)


def test_catalog_filters_unknown_and_rejects_missing_or_changed_inputs():
    from types import SimpleNamespace
    from repo_agent.mcp_business import checked_catalog
    schema={'type':'object','properties':{'order_id':{'type':'string'}},'required':['order_id']}
    tools=[SimpleNamespace(name=n,input_schema=schema) for n in ('get_order_events','get_order_status','write_order')]
    assert len(checked_catalog(tools))==2
    with pytest.raises(ValueError): checked_catalog(tools[:1])
    tools[1]=SimpleNamespace(name='get_order_status',input_schema={'type':'object','properties':{}})
    with pytest.raises(ValueError): checked_catalog(tools)


@pytest.mark.parametrize('fault',['disconnect','invalid','scope','error'])
def test_protocol_faults_never_become_business_success(monkeypatch,fault):
    from repo_agent.mcp_business import MCPBusinessClient
    from mcp.types import CallToolResult,TextContent
    c=MCPBusinessClient('http://127.0.0.1','test')
    async def call(*args):
        if fault=='disconnect': raise ConnectionError('DO-NOT-RETAIN')
        from tests.repo_agent.test_business import DATA
        remote={'status':'ok','scope':'outside','kind':'status','order_id':'o-999','source':'/api/v1/orders/o-999',
            'observed_at':'2026-11-16T00:00:00Z','http_status':200,'data':DATA['o-999']['status']}
        return CallToolResult(content=[TextContent(type='text',text='not-json' if fault=='invalid' else json.dumps(remote))],is_error=fault=='error')
    monkeypatch.setattr(c.server,'call_tool',call)
    result=asyncio.run(c.query('status','o-999'))
    assert result['status']==('unavailable' if fault=='disconnect' else 'invalid_response')
    assert 'data' not in result and 'DO-NOT-RETAIN' not in json.dumps(c.observations)


def test_same_agent_runner_and_persistent_restart(monkeypatch,tmp_path):
    from repo_agent.application import RunManager
    from repo_agent.conversation import ConversationWorker,ConversationScopeChanged
    from repo_agent.persistence import LocalStore
    from repo_agent.checks import check_record
    from tests.repo_agent.test_conversation import ConversationModel
    monkeypatch.setattr('repo_agent.model.create_model',ConversationModel)
    process=subprocess.Popen([sys.executable,'fixtures/business-api/server.py','--port','0',
        '--log',str(tmp_path/'http.jsonl')],env={**os.environ,'BUSINESS_API_TOKEN':'test'},stdout=subprocess.PIPE,text=True)
    try:
        port=int(process.stdout.readline().strip().rsplit(':',1)[1])
        async def scenario():
            url=f'http://127.0.0.1:{port}'
            def start(transport):
                store=LocalStore(tmp_path/'data')
                worker=ConversationWorker({'policy':Path('fixtures/order-policy')},business_url=url,business_token='test',
                    store=store,business_transport=transport)
                return store,worker,RunManager(store.records,worker)
            store,w,m=start('mcp');cid=w.create().conversation_id
            first=m.submit('o-999状态',conversation_id=cid);await first.task
            assert first.state=='completed',first.result
            r=json.loads((store.records/(first.run_id+'.json')).read_text())
            assert check_record(r)['ok'],check_record(r)
            assert len(r['mcp']['tools'])==2
            assert r['answer_checks']['evidence'][0]['observation_id']==first.run_id+'/obs-1'
            await m.close();store.close()
            store,w,m=start('mcp')
            assert m.runs[first.run_id].result==first.result
            second=m.submit('现在呢',conversation_id=cid);await second.task
            assert second.state=='completed',second.result
            r=json.loads((store.records/(second.run_id+'.json')).read_text())
            assert r['conversation']['selected_run_ids']==[first.run_id]
            assert check_record(r)['ok'],check_record(r)
            await m.close();store.close()
            store,w,m=start('http')
            with pytest.raises(ConversationScopeChanged):m.submit('changed',conversation_id=cid)
            await m.close();store.close()
        asyncio.run(scenario())
    finally:process.terminate();process.wait(timeout=5)


def test_cancel_during_live_mcp_call_releases_connection(tmp_path):
    from repo_agent.mcp_business import MCPBusinessClient
    process=subprocess.Popen([sys.executable,'fixtures/business-api/server.py','--port','0','--mode','slow',
        '--log',str(tmp_path/'slow.jsonl')],env={**os.environ,'BUSINESS_API_TOKEN':'test'},stdout=subprocess.PIPE,text=True)
    try:
        port=int(process.stdout.readline().strip().rsplit(':',1)[1])
        async def scenario():
            async with MCPBusinessClient(f'http://127.0.0.1:{port}','test') as c:
                task=asyncio.create_task(c.query('status','o-999'))
                await asyncio.sleep(.1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
                assert c.observations==[]
            assert c.server.session is None
        asyncio.run(scenario())
    finally:process.terminate();process.wait(timeout=5)


def test_catalog_rejects_hidden_validation_constraints():
    from types import SimpleNamespace
    from repo_agent.mcp_business import checked_catalog
    for change in ('allOf','enum'):
        schema={'type':'object','properties':{'order_id':{'type':'string'}},'required':['order_id']}
        if change=='allOf':schema['allOf']=[{'not':{}}]
        else:schema['properties']['order_id']['enum']=['never-allowed']
        with pytest.raises(ValueError):
            checked_catalog([SimpleNamespace(name=n,input_schema=schema) for n in ('get_order_events','get_order_status')])


def test_remote_http_status_cannot_carry_arbitrary_content(monkeypatch):
    from repo_agent.mcp_business import MCPBusinessClient
    from mcp.types import CallToolResult,TextContent
    c=MCPBusinessClient('http://127.0.0.1','test')
    async def call(*args):
        return CallToolResult(content=[TextContent(type='text',text=json.dumps({'status':'timeout','scope':'business_readonly',
            'kind':'status','order_id':'o-999','source':'/api/v1/orders/o-999','observed_at':'2026-11-16T00:00:00Z',
            'http_status':{'remote_text':'DO-NOT-RETAIN'}}))])
    monkeypatch.setattr(c.server,'call_tool',call)
    result=asyncio.run(c.query('status','o-999'))
    assert result['status']=='invalid_response' and result['http_status'] is None
    assert 'DO-NOT-RETAIN' not in json.dumps(result)


@pytest.mark.parametrize('name',['description','title'])
def test_annotation_names_are_not_removed_from_parameter_names(name):
    from types import SimpleNamespace
    from repo_agent.mcp_business import checked_catalog
    schema={'type':'object','properties':{'order_id':{'type':'string'},name:{'type':'string'}},'required':['order_id']}
    with pytest.raises(ValueError):
        checked_catalog([SimpleNamespace(name=n,input_schema=schema) for n in ('get_order_events','get_order_status')])
