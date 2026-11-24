import asyncio
import json
import ssl
import threading
import unittest
from unittest.mock import patch

import httpx2
from agents import FunctionTool
from agents.tool import ToolOutputImage, ToolOutputText

from mirra_agents import AgentRequest, run_agent_stream
from mirra_agents.runtime import _tool_result_summary
from mirra_agents.retry import AgentRunFailure, ProviderResponseError, failure_details, retry_sync


def response_payload(text="ready", output=None):
    return {"id":"resp-test","created_at":1,"object":"response","model":"gpt-6-luna","status":"completed","error":None,"incomplete_details":None,"instructions":None,"metadata":{},"parallel_tool_calls":True,"temperature":1,"tool_choice":"auto","tools":[],"top_p":1,"output":output if output is not None else [{"id":"msg-1","type":"message","role":"assistant","status":"completed","content":[{"type":"output_text","text":text,"annotations":[]}]}],"usage":{"input_tokens":10,"output_tokens":3,"total_tokens":13,"input_tokens_details":{"cached_tokens":2},"output_tokens_details":{"reasoning_tokens":0}}}


def sse_response(text="ready", output=None):
    payload=response_payload(text,output)
    chunks=[{"type":"response.created","response":{**payload,"status":"in_progress","output":[]},"sequence_number":0}]
    if text:chunks.append({"type":"response.output_text.delta","delta":text,"content_index":0,"output_index":0,"item_id":"msg-1","sequence_number":1})
    chunks.append({"type":"response.completed","response":payload,"sequence_number":2})
    return httpx2.Response(200,headers={"content-type":"text/event-stream"},content="".join("data: "+json.dumps(chunk)+"\n\n" for chunk in chunks))


def search_item(identifier, query="visual references", *, results=None, status="completed"):
    return {"id":identifier,"type":"web_search_call","status":status,
            "action":{"type":"search","query":query,"sources":[{"type":"url","url":f"https://example.com/{identifier}"}]},
            **({"results":results} if results is not None else {})}


def search_events(kind, identifier, index=0, item=None):
    return {"type":kind,"item_id":identifier,"output_index":index,"sequence_number":index,
            **({"item":item} if item is not None else {})}


def streamed_fixture(events):
    return httpx2.Response(200,headers={"content-type":"text/event-stream"},
                          content="".join("data: "+json.dumps(event)+"\n\n" for event in events))


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
        for status,code in [(403,"tier_denied"),(429,"rpm_limit_exceeded"),(429,"insufficient_quota"),(400,"invalid_request_error"),(502,"tls_certificate_invalid")]:
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

    async def test_real_sdk_connection_failure_preserves_specific_sanitized_root_cause(self):
        calls=[]
        def handler(request):
            calls.append(request)
            try:
                raise ConnectionRefusedError(10061,"target refused sk-test-super-secret-123456789 via socks5h://proxy-user:proxy-password@proxy.test:8001")
            except ConnectionRefusedError as cause:
                raise httpx2.ConnectError("All connection attempts failed",request=request) from cause
        with patch("mirra_agents.runtime.retry_delay",return_value=0):
            events=await self.consume(handler)
        details=events[-1]["error"]
        self.assertEqual(len(calls),3);self.assertEqual(details["attempts"],3)
        self.assertIn("10061",details["cause"]);self.assertIn("target refused",details["cause"])
        self.assertIn("All connection attempts failed",details["cause"])
        self.assertNotIn("sk-test",json.dumps(details));self.assertNotIn("proxy-user",json.dumps(details))
        self.assertNotIn("proxy-password",json.dumps(details))
        self.assertEqual(details["cause"],details["lastCause"])

    async def test_real_sdk_read_timeout_preserves_low_level_reason(self):
        calls=[]
        def handler(request):
            calls.append(request)
            try:
                raise TimeoutError("upstream read timed out after 12 seconds")
            except TimeoutError as cause:
                raise httpx2.ReadTimeout("response headers were not received",request=request) from cause
        with patch("mirra_agents.runtime.retry_delay",return_value=0):
            events=await self.consume(handler)
        details=events[-1]["error"]
        self.assertEqual(len(calls),3);self.assertIn("12 seconds",details["cause"])
        self.assertIn("response headers were not received",details["cause"])

    async def test_real_sdk_certificate_verification_failure_stops_after_one_request(self):
        calls=[]
        def handler(request):
            calls.append(request)
            try:
                raise ssl.SSLCertVerificationError(1,"certificate verify failed: unable to get local issuer certificate")
            except ssl.SSLCertVerificationError as cause:
                raise httpx2.ConnectError("TLS handshake failed",request=request) from cause
        events=await self.consume(handler)
        details=events[-1]["error"]
        self.assertEqual(len(calls),1);self.assertEqual(details["code"],"tls_certificate_invalid")
        self.assertFalse(details["retryable"]);self.assertEqual(details["attempts"],1)
        self.assertIn("unable to get local issuer certificate",details["cause"])
        self.assertFalse(any(event["type"]=="retry" for event in events))

    async def test_each_hosted_search_has_matching_terminal_event_with_official_images(self):
        bodies=[]
        image={"type":"image_result","image_url":"https://images.example.com/product.png",
               "source_website_url":"https://example.com/product","thumbnail_url":"https://images.example.com/product-small.png",
               "caption":"Official product reference","provider_extra":{"rank":1}}
        web=[search_item("web-a","product front",results=[image]),
             search_item("web-b","product side",results=[dict(image),{"type":"text_result","image_url":"https://images.example.com/ignored.png"}]),
             search_item("web-c","product material",results=[{"type":"image_result","image_url":"file:///private/image.png"}])]
        def handler(request):
            bodies.append(json.loads(request.content))
            payload=response_payload("final plan",[*web,*response_payload("final plan")["output"]])
            events=[]
            for index,item in enumerate(web):
                events.extend([search_events("response.web_search_call.in_progress",item["id"],index),
                               search_events("response.web_search_call.searching",item["id"],index),
                               search_events("response.web_search_call.completed",item["id"],index)])
            events.append({"type":"response.completed","response":payload,"sequence_number":10})
            return streamed_fixture(events)
        events=await self.consume(handler,web_search_enabled=True,web_image_max_results=4)
        started=[event for event in events if event["type"]=="web_search_started"]
        finished=[event for event in events if event["type"]=="web_search_completed"]
        self.assertEqual([event["callId"] for event in started],["web-a","web-b","web-c"])
        self.assertEqual([event["callId"] for event in finished],["web-a","web-b","web-c"])
        self.assertEqual([event["query"] for event in finished],["product front","product side","product material"])
        self.assertTrue(all(event["action"]=="search" and event["turn"]==1 for event in finished))
        self.assertEqual(finished[0]["images"],[{**image,"callId":"web-a"}])
        self.assertEqual(events[-1]["webImages"],[{**image,"callId":"web-a"}])
        self.assertEqual((events[-1]["requestCount"],events[-1]["turnCount"],events[-1]["toolCallCount"],events[-1]["webSearchCallCount"]),(1,1,3,3))
        self.assertEqual(bodies[0]["tools"][0]["search_content_types"],["image","text"])
        self.assertEqual(bodies[0]["tools"][0]["image_settings"],{"max_results":4,"caption":True})
        self.assertIn("web_search_call.results",bodies[0]["include"])

    async def test_search_output_item_completion_is_not_closed_twice_at_response_end(self):
        item=search_item("web-item","current product")
        def handler(_request):
            return streamed_fixture([search_events("response.output_item.added",item["id"],item={**item,"status":"in_progress"}),
                                     search_events("response.web_search_call.searching",item["id"]),
                                     search_events("response.output_item.done",item["id"],item=item),
                                     {"type":"response.completed","response":response_payload("done",[item,*response_payload("done")["output"]]),"sequence_number":4}])
        events=await self.consume(handler,web_search_enabled=True)
        self.assertEqual([event["callId"] for event in events if event["type"]=="web_search_started"],["web-item"])
        self.assertEqual([event["callId"] for event in events if event["type"]=="web_search_completed"],["web-item"])

    async def test_interrupted_search_tools_close_before_model_turn_retry(self):
        class Interrupted(httpx2.AsyncByteStream):
            async def __aiter__(self):
                yield ("data: "+json.dumps(search_events("response.web_search_call.in_progress","web-interrupted"))+"\n\n").encode()
                raise httpx2.ReadError("search stream disconnected")
        calls=[]
        def handler(request):
            calls.append(request)
            if len(calls)==1:return httpx2.Response(200,headers={"content-type":"text/event-stream"},stream=Interrupted())
            return sse_response("recovered",[search_item("web-recovered"),*response_payload("recovered")["output"]])
        with patch("mirra_agents.runtime.retry_delay",return_value=0):
            events=await self.consume(handler,web_search_enabled=True)
        failed=next(event for event in events if event["type"]=="web_search_failed")
        retry=next(event for event in events if event["type"]=="retry")
        self.assertEqual(failed["callId"],"web-interrupted");self.assertTrue(failed["retrying"])
        self.assertLess(events.index(failed),events.index(retry))
        self.assertEqual([event["callId"] for event in events if event["type"]=="web_search_completed"],["web-recovered"])
        self.assertEqual((events[-1]["requestCount"],events[-1]["turnCount"],events[-1]["webSearchCallCount"]),(2,1,2))
        self.assertEqual(events[-1]["usage"]["requests"],1)

    async def test_cancel_during_hosted_search_closes_its_running_tool(self):
        cancellation=threading.Event()
        class Pending(httpx2.AsyncByteStream):
            async def __aiter__(self):
                yield ("data: "+json.dumps(search_events("response.web_search_call.in_progress","web-cancel"))+"\n\n").encode()
                await asyncio.sleep(10)
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request:httpx2.Response(200,headers={"content-type":"text/event-stream"},stream=Pending())))
        request=AgentRequest(base_url="http://127.0.0.1/v1",api_key="test",model="test",instructions="",input="search",web_search_enabled=True,http_client=client)
        events=[]
        async for event in run_agent_stream(request,cancel_event=cancellation):
            events.append(event)
            if event["type"]=="web_search_started":cancellation.set()
        terminal=next(event for event in events if event["type"]=="web_search_failed")
        self.assertEqual(terminal["callId"],"web-cancel");self.assertEqual(terminal["code"],"cancelled")
        self.assertEqual(events[-1]["error"]["code"],"cancelled")
        self.assertFalse(any(event["type"]=="web_search_completed" for event in events))

    async def test_response_observer_registers_official_images_before_real_function_tool_loop(self):
        calls=[];observed={};tool_inputs=[]
        image={"type":"image_result","image_url":"https://images.example.com/ref.png","caption":"Reference","source_website_url":"https://example.com/ref"}
        def observer(response):
            for item in response["output"]:
                if item["type"]=="web_search_call":
                    observed.update({result["image_url"]:result for result in item.get("results") or []})
        async def invoke(context,arguments):
            self.assertEqual(context.tool_call_id,"call-inspect")
            self.assertIn(image["image_url"],observed)
            tool_inputs.append(json.loads(arguments))
            return [ToolOutputText(text=json.dumps({"candidates":[{"id":"one"},{"id":"two"}],"failures":[{"error":"unavailable"}]})),
                    ToolOutputImage(image_url="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jL6sAAAAASUVORK5CYII=")]
        tool=FunctionTool(name="inspect_visual_references",description="Inspect a visual reference",params_json_schema={"type":"object","properties":{"url":{"type":"string"}},"required":["url"],"additionalProperties":False},on_invoke_tool=invoke)
        def handler(request):
            calls.append(json.loads(request.content))
            if len(calls)==1:
                return sse_response("",[search_item("web-reference",results=[image]),{"type":"function_call","id":"fc-inspect","call_id":"call-inspect","name":"inspect_visual_references","arguments":json.dumps({"url":image["image_url"]}),"status":"completed"}])
            return sse_response("refined final prompt")
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        request=AgentRequest(base_url="http://127.0.0.1/v1",api_key="test",model="test",instructions="",input="visual task",web_search_enabled=True,http_client=client,response_observer=observer)
        events=[event async for event in run_agent_stream(request,tools=[tool])]
        self.assertEqual(len(calls),2);self.assertEqual(tool_inputs,[{"url":image["image_url"]}])
        self.assertEqual([(event["type"],event["callId"]) for event in events if event["type"] in {"tool_started","tool_completed"}],[("tool_started","call-inspect"),("tool_completed","call-inspect")])
        completed=next(event for event in events if event["type"]=="tool_completed")
        self.assertEqual(completed["resultCount"],2)
        self.assertEqual(completed["result"],{"candidateCount":2,"failureCount":1})
        self.assertNotIn("base64",json.dumps(completed))
        self.assertTrue(any(item.get("type")=="function_call_output" and item.get("call_id")=="call-inspect" for item in calls[1]["input"]))
        self.assertEqual((events[-1]["turnCount"],events[-1]["requestCount"],events[-1]["webSearchCallCount"],events[-1]["localToolCallCount"],events[-1]["toolCallCount"]),(2,2,1,1,2))

    async def test_local_tool_exception_closes_actual_call_id_without_restarting_runner(self):
        calls=[]
        async def invoke(context,arguments):raise ValueError("reference was unavailable")
        tool=FunctionTool(name="inspect_reference",description="Inspect a reference",params_json_schema={"type":"object","properties":{},"required":[],"additionalProperties":False},on_invoke_tool=invoke,_use_default_failure_error_function=False)
        def handler(request):
            calls.append(request)
            return sse_response("",[{"type":"function_call","id":"fc-failure","call_id":"call-failure","name":"inspect_reference","arguments":"{}","status":"completed"}])
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        request=AgentRequest(base_url="http://127.0.0.1/v1",api_key="test",model="test",instructions="",input="task",http_client=client)
        events=[event async for event in run_agent_stream(request,tools=[tool])]
        self.assertEqual(len(calls),1)
        failed=next(event for event in events if event["type"]=="tool_failed")
        self.assertEqual(failed["callId"],"call-failure");self.assertEqual(failed["tool"],"inspect_reference")
        self.assertIn("reference was unavailable",failed["cause"])
        self.assertEqual(events[-1]["type"],"error")
        self.assertFalse(any(event["type"]=="tool_completed" for event in events))

    async def test_image_search_can_be_explicitly_disabled_for_text_only_planning(self):
        bodies=[]
        def handler(request):bodies.append(json.loads(request.content));return sse_response("text-only plan")
        events=await self.consume(handler,web_search_enabled=True,web_image_search_enabled=False)
        self.assertEqual(events[-1]["type"],"result")
        web=next(tool for tool in bodies[0]["tools"] if tool["type"]=="web_search")
        self.assertNotIn("search_content_types",web);self.assertNotIn("image_settings",web)
        self.assertNotIn("web_search_call.results",bodies[0]["include"])

    async def test_workflow_feedback_continues_transcript_within_one_global_turn_budget(self):
        calls=[];inspected=[];feedback_checks=[]
        image={"type":"image_result","image_url":"https://images.example.com/reference.png","source_website_url":"https://example.com/reference","caption":"Visual reference"}
        async def invoke(context,arguments):
            inspected.append(context.tool_call_id);return "Verified image supplied; selected for image generation"
        tool=FunctionTool(name="inspect_and_select",description="Inspect and select the reference",params_json_schema={"type":"object","properties":{},"required":[],"additionalProperties":False},on_invoke_tool=invoke)
        def feedback(text):
            feedback_checks.append(text)
            return "Inspect and choose the existing image before finalizing; do not repeat the completed search." if not inspected else None
        def handler(request):
            calls.append(json.loads(request.content))
            if len(calls)==1:return sse_response("premature final",[search_item("web-original",results=[image]),*response_payload("premature final")["output"]])
            if len(calls)==2:return sse_response("",[{"type":"function_call","id":"fc-select","call_id":"call-select","name":"inspect_and_select","arguments":"{}","status":"completed"}])
            return sse_response("refined using the inspected image")
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        request=AgentRequest(base_url="http://127.0.0.1/v1",api_key="test",model="test",instructions="",input="visual task",web_search_enabled=True,http_client=client,max_turns=3,tool_choice="none",final_output_feedback=feedback)
        events=[event async for event in run_agent_stream(request,tools=[tool])]
        self.assertEqual(len(calls),3);self.assertEqual(inspected,["call-select"])
        self.assertEqual(feedback_checks,["premature final","refined using the inspected image"])
        self.assertEqual(calls[0]["tool_choice"],"none");self.assertNotEqual(calls[1].get("tool_choice"),"none")
        self.assertTrue(any(item.get("type")=="web_search_call" and item.get("id")=="web-original" for item in calls[1]["input"]))
        self.assertTrue(any(item.get("role")=="user" and "do not repeat" in str(item.get("content")) for item in calls[1]["input"]))
        self.assertEqual([event["turn"] for event in events if event["type"]=="turn_started"],[1,2,3])
        self.assertEqual(len([event for event in events if event["type"]=="workflow_feedback"]),1)
        self.assertEqual(events[-1]["text"],"refined using the inspected image")
        self.assertEqual((events[-1]["turnCount"],events[-1]["requestCount"],events[-1]["webSearchCallCount"],events[-1]["localToolCallCount"]),(3,3,1,1))
        self.assertEqual(events[-1]["usage"]["input_tokens"],30)

    async def test_unfinished_semantic_workflow_exhausts_budget_without_network_retry(self):
        calls=[]
        def handler(request):calls.append(request);return sse_response("missing reference selection")
        events=await self.consume(handler,max_turns=1,final_output_feedback=lambda text:"Must inspect and select the candidate image")
        self.assertEqual(len(calls),1);self.assertEqual(events[-1]["type"],"error")
        self.assertEqual(events[-1]["error"]["code"],"max_turns")
        self.assertIn("Must inspect",events[-1]["error"]["cause"])
        self.assertFalse(events[-1]["error"]["retryable"])
        self.assertFalse(any(event["type"]=="retry" for event in events))
        self.assertEqual((events[-1]["turnCount"],events[-1]["requestCount"]),(1,1))

    async def test_complete_workflow_does_not_force_extra_model_calls(self):
        calls=[]
        def handler(request):calls.append(request);return sse_response("complete plan")
        events=await self.consume(handler,final_output_feedback=lambda text:None)
        self.assertEqual(len(calls),1);self.assertEqual(events[-1]["text"],"complete plan")
        self.assertFalse(any(event["type"]=="workflow_feedback" for event in events))

    async def test_repeated_function_turns_obey_sdk_global_budget_and_fail_explicitly(self):
        calls=[];invocations=[]
        async def invoke(context,arguments):invocations.append(context.tool_call_id);return "checked"
        tool=FunctionTool(name="inspect_reference",description="Inspect a reference",params_json_schema={"type":"object","properties":{},"required":[],"additionalProperties":False},on_invoke_tool=invoke)
        def handler(request):
            calls.append(request)
            return sse_response("",[{"type":"function_call","id":f"fc-{len(calls)}","call_id":f"call-{len(calls)}","name":"inspect_reference","arguments":"{}","status":"completed"}])
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        request=AgentRequest(base_url="http://127.0.0.1/v1",api_key="test",model="test",instructions="",input="task",http_client=client,max_turns=2)
        events=[event async for event in run_agent_stream(request,tools=[tool])]
        self.assertEqual(len(calls),2);self.assertEqual(invocations,["call-1","call-2"])
        self.assertEqual(events[-1]["type"],"error");self.assertEqual(events[-1]["error"]["code"],"max_turns")
        self.assertFalse(events[-1]["error"]["retryable"])
        self.assertEqual([event["callId"] for event in events if event["type"]=="tool_completed"],invocations)


class RetryBoundaryTests(unittest.TestCase):
    def test_tool_summary_supports_visual_reference_aliases_without_raw_payloads(self):
        summary=_tool_result_summary([ToolOutputText(text=json.dumps({"candidates":[{"id":"one","previewDataUrl":"data:image/png;base64,private"}],"errors":[{"message":"unavailable"}]}))],())
        self.assertEqual(summary,{"resultCount":1,"result":{"candidateCount":1,"failureCount":1}})
        summary=_tool_result_summary(json.dumps({"selected_candidate_ids":["one"],"rationale":"use the shape"}),())
        self.assertEqual(summary,{"resultCount":1,"result":{"selectedIds":["one"],"rationale":"use the shape"}})
        summary=_tool_result_summary(json.dumps({"error":{"code":"invalid_reference_arguments","message":"Do not expose sk-private-123456789 or https://proxy-user:proxy-password@proxy.test"}}),())
        self.assertFalse(summary["result"]["ok"]);self.assertEqual(summary["result"]["code"],"invalid_reference_arguments")
        self.assertNotIn("sk-private",json.dumps(summary));self.assertNotIn("proxy-password",json.dumps(summary))

    def test_an_exhausted_sdk_budget_is_not_multiplied_by_outer_retry(self):
        calls=[]
        details=failure_details(ProviderResponseError("server_error","capacity exhausted",status=503),"agent",3,3)
        def operation(attempt):calls.append(attempt);raise AgentRunFailure(details)
        with self.assertRaises(AgentRunFailure) as caught:retry_sync(operation,stage="agent")
        self.assertEqual(calls,[1]);self.assertEqual(caught.exception.details,details)

    def test_exception_chains_are_bounded_and_cycles_do_not_loop(self):
        errors=[RuntimeError(f"layer-{index}") for index in range(12)]
        for outer,inner in zip(errors,errors[1:]):outer.__cause__=inner
        details=failure_details(errors[0],"agent",1,3)
        self.assertIn("layer-7",details["cause"]);self.assertNotIn("layer-8",details["cause"])
        errors[2].__cause__=errors[0]
        details=failure_details(errors[0],"agent",1,3)
        self.assertEqual(details["cause"].count("layer-0"),1)
        self.assertIn("layer-2",details["cause"])

    def test_implicit_exception_context_is_preserved_and_provider_message_stays_primary(self):
        try:
            try:raise OSError("socket reset by peer")
            except OSError:raise RuntimeError("transport failed")
        except RuntimeError as error:
            details=failure_details(error,"agent",1,3)
        self.assertIn("socket reset by peer",details["cause"])
        provider=ProviderResponseError("rate_limit_exceeded","precise provider rejection",status=429)
        provider.__cause__=RuntimeError("HTTP wrapper")
        self.assertEqual(failure_details(provider,"agent",1,3)["cause"],"precise provider rejection")

    def test_proxy_url_authentication_is_redacted_for_every_supported_scheme(self):
        for scheme in ("http","https","socks5","socks5h"):
            with self.subTest(scheme=scheme):
                error=RuntimeError(f"Cannot connect through {scheme}://proxy-user:proxy-password@proxy.test:8001; custom-secret")
                details=failure_details(error,"agent",1,3,("custom-secret",))
                self.assertIn(f"{scheme}://[REDACTED]@proxy.test:8001",details["cause"])
                for secret in ("proxy-user","proxy-password","custom-secret"):
                    self.assertNotIn(secret,json.dumps(details))


if __name__=="__main__":unittest.main()
