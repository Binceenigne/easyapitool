import asyncio
import json
import threading
import unittest

import httpx2
from agents import FunctionTool

from mirra_agents import AgentRequest, run_agent_stream
from mirra_agents.retry import AgentRunFailure, ProviderResponseError, failure_details, retry_sync


def response_payload(text="ready", output=None):
    return {"id":"resp-test","created_at":1,"object":"response","model":"gpt-6-luna","status":"completed","error":None,"incomplete_details":None,"instructions":None,"metadata":{},"parallel_tool_calls":True,"temperature":1,"tool_choice":"auto","tools":[],"top_p":1,"output":output if output is not None else [{"id":"msg-1","type":"message","role":"assistant","status":"completed","content":[{"type":"output_text","text":text,"annotations":[]}]}],"usage":{"input_tokens":10,"output_tokens":3,"total_tokens":13,"input_tokens_details":{"cached_tokens":2},"output_tokens_details":{"reasoning_tokens":0}}}


def sse_response(text="ready", output=None):
    payload=response_payload(text,output)
    chunks=[{"type":"response.created","response":{**payload,"status":"in_progress","output":[]},"sequence_number":0}]
    if text:chunks.append({"type":"response.output_text.delta","delta":text,"content_index":0,"output_index":0,"item_id":"msg-1","sequence_number":1})
    chunks.append({"type":"response.completed","response":payload,"sequence_number":2})
    return httpx2.Response(200,headers={"content-type":"text/event-stream"},content="".join("data: "+json.dumps(chunk)+"\n\n" for chunk in chunks))


class AgentsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def consume(self,handler,**kwargs):
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        request=AgentRequest(base_url="http://127.0.0.1:1234/v1",api_key="sk-test-super-secret-123456789",model="gpt-6-luna",instructions="Return a prompt",input="orange",http_client=client,**kwargs)
        events=[event async for event in run_agent_stream(request)]
        return events

    async def test_real_sdk_owns_function_tool_loop_and_official_search(self):
        bodies=[];tools_called=[]
        async def invoke(context,arguments):
            tools_called.append(json.loads(arguments));return "reference selected"
        tool=FunctionTool(name="choose_reference",description="Choose a reference",params_json_schema={"type":"object","properties":{"id":{"type":"string"}},"required":["id"],"additionalProperties":False},on_invoke_tool=invoke)
        def handler(request):
            bodies.append(json.loads(request.content))
            if len(bodies)==1:return sse_response("",[{"type":"function_call","id":"fc-1","call_id":"call-1","name":"choose_reference","arguments":"{\"id\":\"existing-asset\"}","status":"completed"}])
            return sse_response("final prompt")
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        request=AgentRequest(base_url="http://127.0.0.1:1234/v1",api_key="sk-test-123456789",model="gpt-6-luna",instructions="test",input="user",web_search_enabled=True,http_client=client,mode="high",effort="high")
        events=[event async for event in run_agent_stream(request,tools=[tool])]
        self.assertEqual(tools_called,[{"id":"existing-asset"}]);self.assertEqual(len(bodies),2)
        self.assertTrue(any(tool["type"]=="web_search" for tool in bodies[0]["tools"]))
        self.assertFalse(any(tool.get("name")=="search_web" for tool in bodies[0]["tools"]))
        self.assertFalse(bodies[0]["store"])
        self.assertEqual(bodies[0]["max_tool_calls"],3)
        self.assertTrue(any(item.get("type")=="function_call_output" for item in bodies[1]["input"]))
        self.assertEqual(events[-1]["type"],"result");self.assertEqual(events[-1]["text"],"final prompt")
        self.assertEqual(events[-1]["usage"]["input_tokens"],20)

    async def test_transient_failure_retries_only_failed_turn(self):
        calls=[]
        def handler(request):
            calls.append(request)
            if len(calls)<3:return httpx2.Response(503,json={"error":{"code":"server_error","message":"temporary unavailable"}},headers={"retry-after":"0","x-request-id":"upstream-503"})
            return sse_response("recovered")
        events=await self.consume(handler)
        self.assertEqual(len(calls),3);self.assertEqual([event["attempt"] for event in events if event["type"]=="retry"],[2,3]);self.assertEqual(events[-1]["text"],"recovered")

    async def test_exhausted_error_retains_specific_sanitized_cause(self):
        calls=[]
        def handler(request):
            calls.append(request);return httpx2.Response(502,json={"error":{"code":"bad_gateway","message":"upstream refused sk-test-super-secret-123456789"}},headers={"retry-after":"0","x-request-id":"upstream-id"})
        events=await self.consume(handler)
        self.assertEqual(len(calls),3)
        failure=events[-1]["error"]
        self.assertEqual(failure["attempts"],3);self.assertEqual(failure["status"],502);self.assertEqual(failure["diagnosticId"],"upstream-id")
        self.assertIn("upstream refused",failure["cause"]);self.assertNotIn("sk-test",json.dumps(failure));self.assertIn("重试已用尽",failure["message"])

    async def test_permissions_and_local_budget_limits_do_not_retry(self):
        for status,code in [(403,"tier_denied"),(429,"rpm_limit_exceeded"),(429,"insufficient_quota"),(400,"invalid_request_error")]:
            with self.subTest(status=status,code=code):
                calls=[]
                def handler(request):
                    calls.append(request);return httpx2.Response(status,json={"error":{"code":code,"message":"request rejected"}},headers={"retry-after":"0"})
                events=await self.consume(handler)
                self.assertEqual(len(calls),1);self.assertEqual(events[-1]["type"],"error");self.assertFalse(events[-1]["error"]["retryable"])

    async def test_partial_stream_retry_does_not_duplicate_sdk_output(self):
        class Interrupted(httpx2.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"discard this","content_index":0,"output_index":0,"item_id":"bad-msg","sequence_number":0}\n\n'
                raise httpx2.ReadError("stream interrupted")
        calls=[]
        def handler(request):
            calls.append(request)
            if len(calls)==1:return httpx2.Response(200,headers={"content-type":"text/event-stream"},stream=Interrupted())
            return sse_response("complete")
        events=await self.consume(handler)
        self.assertEqual(len(calls),2);self.assertEqual(events[-1]["text"],"complete")
        self.assertTrue(any(e["type"]=="turn_started" and e.get("reset") for e in events))
        self.assertEqual(len(events[-1]["responses"]),1)

    async def test_cancel_during_retry_wait_prevents_next_request(self):
        calls=[];cancel=threading.Event()
        def handler(request):
            calls.append(request);return httpx2.Response(503,json={"error":{"code":"server_error","message":"temporary"}})
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        req=AgentRequest(base_url="http://127.0.0.1/v1",api_key="test",model="test",instructions="",input="test",http_client=client)
        events=[]
        async for event in run_agent_stream(req,cancel_event=cancel):
            events.append(event)
            if event["type"]=="retry":cancel.set()
        self.assertEqual(len(calls),1);self.assertEqual(events[-1]["error"]["code"],"cancelled")

    async def test_terminal_failed_event_preserves_cause_and_retries_transient_error(self):
        calls=[]
        def handler(request):
            calls.append(request)
            if len(calls)>1:return sse_response("recovered after provider error")
            payload=response_payload()
            payload.update(status="failed",id="resp-provider-failed",error={"code":"server_error","message":"GPU pool temporarily unavailable"})
            return httpx2.Response(200,headers={"content-type":"text/event-stream"},content="data: "+json.dumps({"type":"response.failed","response":payload,"sequence_number":0})+"\n\n")
        events=await self.consume(handler)
        retry=next(event for event in events if event["type"]=="retry")
        self.assertEqual(len(calls),2);self.assertEqual(retry["code"],"server_error")
        self.assertEqual(retry["cause"],"GPU pool temporarily unavailable")
        self.assertEqual(retry["diagnosticId"],"resp-provider-failed")
        self.assertEqual(events[-1]["type"],"result")

    async def test_incomplete_output_token_limit_is_specific_and_not_retried(self):
        calls=[]
        def handler(request):
            calls.append(request);payload=response_payload()
            payload.update(status="incomplete",incomplete_details={"reason":"max_output_tokens"})
            return httpx2.Response(200,headers={"content-type":"text/event-stream"},content="data: "+json.dumps({"type":"response.incomplete","response":payload,"sequence_number":0})+"\n\n")
        events=await self.consume(handler)
        self.assertEqual(len(calls),1);self.assertEqual(events[-1]["error"]["code"],"max_output_tokens")
        self.assertIn("令牌上限",events[-1]["error"]["cause"])

    async def test_completed_function_tool_is_not_repeated_when_next_turn_retries(self):
        calls=[];tool_calls=[]
        async def invoke(context,arguments):tool_calls.append(arguments);return "loaded"
        tool=FunctionTool(name="load_asset",description="Load asset",params_json_schema={"type":"object","properties":{},"additionalProperties":False,"required":[]},on_invoke_tool=invoke)
        def handler(request):
            calls.append(json.loads(request.content))
            if len(calls)==1:return sse_response("",[{"type":"function_call","id":"fc-1","call_id":"call-1","name":"load_asset","arguments":"{}","status":"completed"}])
            if len(calls)==2:return httpx2.Response(503,json={"error":{"code":"server_error","message":"temporary"}},headers={"retry-after":"0"})
            return sse_response("final")
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        req=AgentRequest(base_url="http://127.0.0.1/v1",api_key="test",model="test",instructions="",input="test",http_client=client)
        events=[event async for event in run_agent_stream(req,tools=[tool])]
        self.assertEqual(len(calls),3);self.assertEqual(tool_calls,["{}"])
        self.assertEqual(events[-1]["text"],"final");self.assertEqual(len(events[-1]["responses"]),2)

    async def test_final_output_validation_retries_only_malformed_model_turn(self):
        calls=[]
        def handler(request):
            calls.append(request);return sse_response("missing delimiter" if len(calls)==1 else "complete | prompt")
        def validate(response):
            if " | " not in response["output"][0]["content"][0]["text"]:raise ConnectionError("Agent 输出格式不完整")
        events=await self.consume(handler,validate_output=validate)
        self.assertEqual(len(calls),2);self.assertEqual(events[-1]["text"],"complete | prompt")

    async def test_client_close_failure_cannot_hide_result_or_leave_stream_waiting(self):
        class FailingCloseClient(httpx2.AsyncClient):
            async def aclose(self):
                await super().aclose()
                raise RuntimeError("synthetic close failure")
        for reject in (False,True):
            with self.subTest(reject=reject):
                def handler(request):
                    return httpx2.Response(403,json={"error":{"code":"tier_denied","message":"not permitted"}}) if reject else sse_response("final result")
                client=FailingCloseClient(transport=httpx2.MockTransport(handler))
                request=AgentRequest(base_url="http://127.0.0.1/v1",api_key="test",model="test",instructions="",input="test",http_client=client)
                async def consume():return [event async for event in run_agent_stream(request)]
                events=await asyncio.wait_for(consume(),timeout=2)
                self.assertTrue(client.is_closed)
                self.assertEqual(events[-1]["type"],"error" if reject else "result")
                if reject:self.assertEqual(events[-1]["error"]["code"],"tier_denied")
                else:self.assertEqual(events[-1]["text"],"final result")


class RetryBoundaryTests(unittest.TestCase):
    def test_an_exhausted_sdk_budget_is_not_multiplied_by_outer_retry(self):
        calls=[]
        details=failure_details(ProviderResponseError("server_error","capacity exhausted",status=503),"agent",3,3)
        def operation(attempt):calls.append(attempt);raise AgentRunFailure(details)
        with self.assertRaises(AgentRunFailure) as caught:retry_sync(operation,stage="agent")
        self.assertEqual(calls,[1]);self.assertEqual(caught.exception.details,details)


if __name__=="__main__":unittest.main()
