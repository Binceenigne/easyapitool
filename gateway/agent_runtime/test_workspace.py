from __future__ import annotations
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workspace import WorkerWorkspaceSession


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_controls_workspace_tools_and_preserves_actual_image(self):
        import httpx2
        from mirra_agents import AgentRequest, run_agent_stream
        session = WorkerWorkspaceSession({'base_url':'http://127.0.0.1/internal/agent/v1','api_key':'lease-fixture'}, threading.Event())
        host_calls, models = [], []
        async def host(body):
            host_calls.append(body)
            if body['operation'] == 'view':
                return {'assets':[{'assetId':'older','previewDataUrl':'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6D0YAAAAASUVORK5CYII='}],'errors':[]}
            if body['operation'] == 'select':
                return {'operation':'edit','selectedAssetIds':body['asset_ids'],'rationale':body['rationale']}
            return {'workspaceId':'one','operation':'edit','selectedAssetIds':['parent'],'items':[{'assetId':'older','path':'workspace://older','viewed':False}]}
        session.transport.request = host
        await session.initialize()
        def provider(request):
            body = json.loads(request.content); models.append(body); turn = len(models)
            specs = [('list_image_workspace',{'kind':'assets','offset':0,'limit':10}),('view_image_workspace_assets',{'asset_ids':['older']}),('select_image_workspace_inputs',{'operation':'edit','asset_ids':['older'],'rationale':'Use the earlier product photo'})]
            if turn <= len(specs):
                name, args = specs[turn-1]
                output = [{'type':'function_call','id':f'fc-{turn}','call_id':f'call-{turn}','name':name,'arguments':json.dumps(args),'status':'completed'}]
            else:
                output = [{'type':'message','id':'msg-final','role':'assistant','status':'completed','content':[{'type':'output_text','text':'Continue from the selected product photo','annotations':[]}]}]
            response = {'id':f'resp-{turn}','object':'response','created_at':1,'model':'gpt-6-luna','status':'completed','output':output,'usage':{'input_tokens':10,'output_tokens':3,'total_tokens':13}}
            return httpx2.Response(200, text='data: '+json.dumps({'type':'response.completed','sequence_number':0,'response':response})+'\n\n', headers={'Content-Type':'text/event-stream'})
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(provider))
        request = AgentRequest(base_url='http://127.0.0.1/v1',api_key='fixture',model='gpt-6-luna',instructions='Continue the image',input='Keep the product',http_client=client,max_turns=6)
        events = [event async for event in run_agent_stream(request,tools=session.tools())]
        self.assertEqual(events[-1]['type'],'result')
        self.assertEqual(len(models),4)
        self.assertIn('data:image/png;base64,',json.dumps(models[2]['input']))
        self.assertEqual(session.metadata()['continuationPlan']['selectedAssetIds'],['older'])
        self.assertNotIn('plan_image_continuation',json.dumps(models[0]['tools']))
        self.assertEqual([body['operation'] for body in host_calls],['list','list','view','select'])


if __name__ == '__main__': unittest.main()
