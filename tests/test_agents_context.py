import json
import unittest
from unittest.mock import patch

import httpx2
from agents import FunctionTool
from agents.tool import ToolOutputImage, ToolOutputText

from mirra_agents import AgentRequest, run_agent_stream
from tests.test_agents_runtime import response_payload, search_item, sse_response, streamed_fixture


def compact(identifier, opaque="private-encrypted-state", **extra):
    return {"id": identifier, "type": "compaction", "encrypted_content": opaque, **extra}


def function(identifier, name):
    return {"type": "function_call", "id": "fc-" + identifier, "call_id": "call-" + identifier,
            "name": name, "arguments": "{}", "status": "completed"}


def tool(name, invoke):
    return FunctionTool(name=name, description="Inspect the real reference", on_invoke_tool=invoke,
                        params_json_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False})


class AgentsContextTests(unittest.IsolatedAsyncioTestCase):
    async def consume(self, handler, *, model="gpt-6-luna", tools=None, **kwargs):
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        request = AgentRequest(base_url="http://127.0.0.1/v1", api_key="test", model=model,
                               instructions="Keep the reference, compose the final image prompt.",
                               input="original user request", http_client=client, **kwargs)
        return [event async for event in run_agent_stream(request, tools=tools)]

    async def test_actual_sdk_sends_full_output_and_server_threshold_on_every_turn(self):
        bodies = []
        async def invoke(context, arguments):
            return "loaded"
        def handler(request):
            body = json.loads(request.content); bodies.append(body)
            return sse_response("", [function("inspect", "inspect_reference")]) if len(bodies) == 1 else sse_response("final")
        events = await self.consume(handler, tools=[tool("inspect_reference", invoke)])
        self.assertEqual(events[-1]["type"], "result")
        for body in bodies:
            self.assertEqual(body["max_output_tokens"], 128_000)
            self.assertEqual(body["context_management"], [{"type": "compaction", "compact_threshold": 829_800}])
            self.assertFalse(body["store"])
            self.assertNotIn("truncation", body)
            self.assertIn("reasoning.encrypted_content", body["include"])
        self.assertEqual(events[-1]["modelContext"]["compactionBasis"], "usable_input_budget")

    async def test_unknown_model_omits_guessed_limits_and_keeps_upstream_control(self):
        bodies = []
        def handler(request):
            bodies.append(json.loads(request.content)); return sse_response("final")
        events = await self.consume(handler, model="private-text-model")
        self.assertNotIn("max_output_tokens", bodies[0])
        self.assertNotIn("context_management", bodies[0])
        self.assertFalse(events[-1]["modelContext"]["known"])
        self.assertFalse(events[-1]["modelContext"]["compactionEnabled"])

    async def test_explicit_lower_output_cap_is_preserved(self):
        bodies = []
        def handler(request):
            bodies.append(json.loads(request.content)); return sse_response("final")
        await self.consume(handler, max_output_tokens=4_096)
        self.assertEqual(bodies[0]["max_output_tokens"], 4_096)
        self.assertEqual(bodies[0]["context_management"][0]["compact_threshold"], 829_800)

    async def test_compaction_survives_real_tools_feedback_and_multiple_compactions(self):
        bodies = []; inspected = []; selected = []; observed = []
        first = compact("cmp-first", vendor_extension={"keep": True})
        second = compact("cmp-second")
        latest = compact("cmp-latest", "private-latest-state")
        config = {"id": "cfg-current", "type": "configuration_update", "reasoning": {"effort": "high"},
                  "vendor_extension": {"future": "retained"}}
        image = {"type": "image_result", "image_url": "https://example.com/actual-reference.jpg", "title": "Verified product"}
        async def inspect(context, arguments):
            inspected.append(context.tool_call_id)
            return [ToolOutputText(text=json.dumps({"candidateId": "actual-ref-1", "width": 640})),
                    ToolOutputImage(image_url="data:image/jpeg;base64,YWN0dWFsLXByZXZpZXc=")]
        async def select(context, arguments):
            self.assertTrue(inspected)
            selected.append("actual-ref-1")
            return {"selectedIds": selected, "rationale": "Use inspected subject"}
        def handler(request):
            body = json.loads(request.content); bodies.append(body)
            if len(bodies) == 1:
                return sse_response("", [search_item("web-actual", results=[image]), first, config, function("inspect", "inspect_reference")])
            if len(bodies) == 2:
                # The API can compact more than once and finish with compaction,
                # while an earlier message remains the turn's final output.
                return sse_response("premature result", [second, *response_payload("premature result")["output"], latest])
            if len(bodies) == 3:
                return sse_response("", [function("select", "select_reference")])
            return sse_response("final verified-reference prompt")
        feedback = lambda text: "Select the inspected reference before finishing." if not selected else None
        events = await self.consume(handler, tools=[tool("inspect_reference", inspect), tool("select_reference", select)],
                                    final_output_feedback=feedback, response_observer=observed.append, web_search_enabled=True)
        self.assertEqual(events[-1]["type"], "result", events[-1])
        self.assertEqual(len(bodies), 4)
        self.assertEqual(inspected, ["call-inspect"])
        self.assertEqual(selected, ["actual-ref-1"])
        self.assertEqual([item["type"] for item in bodies[1]["input"][:3]],
                         ["compaction", "configuration_update", "function_call"])
        self.assertEqual(bodies[1]["input"][0], first)
        self.assertEqual(bodies[1]["input"][1], config)
        inspected_output = next(item for item in bodies[1]["input"] if item.get("type") == "function_call_output")
        self.assertIn("data:image/jpeg;base64,YWN0dWFsLXByZXZpZXc=", json.dumps(inspected_output))
        self.assertEqual(bodies[2]["input"][0], latest)
        self.assertEqual(bodies[2]["input"][1]["role"], "user")
        for body in bodies[1:]:
            self.assertNotIn("original user request", json.dumps(body["input"]))
        self.assertEqual(bodies[3]["input"][0], latest)
        self.assertTrue(any(item.get("call_id") == "call-select" for item in bodies[3]["input"]))
        result = events[-1]
        self.assertEqual(result["text"], "final verified-reference prompt")
        self.assertEqual(result["modelContext"]["compactionCount"], 3)
        self.assertEqual(result["usage"]["input_tokens"], 40)
        self.assertEqual(result["modelContext"]["lastRequestInputTokens"], 10)
        self.assertEqual(result["webImages"][0]["image_url"], image["image_url"])
        self.assertEqual(observed[0]["output"][1]["encrypted_content"], first["encrypted_content"])
        self.assertNotIn("encrypted_content", json.dumps(events))
        self.assertNotIn("private-latest-state", json.dumps(events))

    async def test_failed_response_cannot_commit_a_compaction_or_erase_the_original_input(self):
        bodies = []
        def handler(request):
            bodies.append(json.loads(request.content))
            if len(bodies) == 1:
                return sse_response("malformed", [compact("cmp-discard"), *response_payload("malformed")["output"]])
            return sse_response("complete | prompt")
        def validate(response):
            text = "".join(part.get("text", "") for item in response["output"] for part in item.get("content", []))
            if " | " not in text:
                raise ConnectionError("Malformed final prompt")
        with patch("mirra_agents.runtime.retry_delay", return_value=0):
            events = await self.consume(handler, validate_output=validate)
        self.assertEqual(events[-1]["type"], "result")
        self.assertEqual(bodies[0]["input"], bodies[1]["input"])
        self.assertFalse(any(event["type"] == "context_compacted" for event in events))
        self.assertEqual(events[-1]["modelContext"]["compactionCount"], 0)

    async def test_interrupted_compaction_stream_is_retried_without_replaying_partial_state(self):
        class Interrupted(httpx2.AsyncByteStream):
            async def __aiter__(self):
                item = {"type": "response.output_item.added", "output_index": 0, "sequence_number": 0,
                        "item": compact("cmp-uncommitted", "private-partial-state")}
                yield ("data: " + json.dumps(item) + "\n\n").encode()
                raise httpx2.ReadError("stream interrupted during compaction")
        bodies = []
        def handler(request):
            bodies.append(json.loads(request.content))
            if len(bodies) == 1:
                return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=Interrupted())
            return sse_response("complete")
        with patch("mirra_agents.runtime.retry_delay", return_value=0):
            events = await self.consume(handler)
        self.assertEqual(events[-1]["type"], "result")
        self.assertEqual(bodies[0]["input"], bodies[1]["input"])
        self.assertNotIn("private-partial-state", json.dumps(events))
        self.assertEqual(events[-1]["modelContext"]["compactionCount"], 0)

    async def test_cumulative_billable_tokens_do_not_trim_an_uncompacted_window(self):
        bodies = []
        async def invoke(context, arguments):
            return "retain this inspected reference"
        def handler(request):
            bodies.append(json.loads(request.content))
            output = [function("inspect", "inspect_reference")] if len(bodies) == 1 else response_payload("final")["output"]
            payload = response_payload("", output)
            payload["usage"].update(input_tokens=600_000, total_tokens=600_003)
            return streamed_fixture([{"type": "response.completed", "response": payload, "sequence_number": 1}])
        events = await self.consume(handler, tools=[tool("inspect_reference", invoke)])
        self.assertEqual(events[-1]["type"], "result")
        self.assertEqual(events[-1]["usage"]["input_tokens"], 1_200_000)
        self.assertEqual(events[-1]["modelContext"]["lastRequestInputTokens"], 600_000)
        self.assertIn("original user request", json.dumps(bodies[1]["input"]))
        self.assertIn("retain this inspected reference", json.dumps(bodies[1]["input"]))


if __name__ == "__main__":
    unittest.main()
