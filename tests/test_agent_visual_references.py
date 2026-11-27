import asyncio
import base64
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx2
from PIL import Image
from agents.tool import ToolOutputImage, ToolOutputText

import app
from backend.agent_references import VisualReferenceSession
from backend.controller_mixins.image_reasoning import ImageGenerationCancelled, ImageTaskContext
from backend.web_search import WebSearchService
from mirra_agents import AgentRequest, run_agent
from mirra_agents.retry import AgentCancelled
from mirra_agents.visual_tools import make_visual_reference_tools
from tests.test_agents_runtime import response_payload, search_item, sse_response


def official_results(*urls):
    return {"output": [{"type": "web_search_call", "id": "web-images", "status": "completed", "results": [
        {"type": "image_result", "image_url": url, "thumbnail_url": url + "?thumbnail=1",
         "source_website_url": "https://example.com/product", "caption": "Actual product photo"}
        for url in urls]}]}


def output_json(output):
    text = output if isinstance(output, str) else next(item.text for item in output if isinstance(item, ToolOutputText))
    return json.loads(text)


def image_parts(output):
    return [] if isinstance(output, str) else [item for item in output if isinstance(item, ToolOutputImage)]


def fixture_png():
    payload = io.BytesIO()
    Image.new("RGB", (96, 64), "orange").save(payload, format="PNG")
    return payload.getvalue()


class VisualReferenceSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = WebSearchService()
        self.download = Mock(return_value=fixture_png())
        self.enterContext(patch.object(self.service, "_public_image_bytes", self.download))
        self.events = []
        self.limits = {"vision": 4, "adopt": 2}
        self.context = ImageTaskContext()
        self.session = VisualReferenceSession(self.service, inspect_limit=4,
            vision_limit=lambda: self.limits["vision"], adopt_limit=lambda: self.limits["adopt"],
            emit=lambda kind, **details: self.events.append({"type": kind, **details}), task_context=self.context)
        self.urls = ["https://images.example.com/one.png", "https://images.example.com/two.png"]

    async def test_only_actual_official_https_image_results_enter_candidate_registry(self):
        self.session.observe(official_results(self.urls[0], self.urls[0], "http://images.example.com/plain.png",
            "https://user:password@images.example.com/private.png", "file:///local.png",
            "https://images.example.com:8443/nonstandard.png", "https://images.example.com/control\r.png"))
        self.session.observe({"output": [{"type": "message", "content": [], "results": [
            {"type": "image_result", "image_url": self.urls[1]}]}]})
        self.assertEqual(list(self.session.candidates), [self.urls[0]])
        self.assertEqual(self.session.candidates[self.urls[0]]["sourceUrl"], "https://example.com/product")
        unknown = await self.session.inspect(None, {"image_urls": ["https://other.example.com/not-searched.png"]})
        self.assertEqual(output_json(unknown)["error"]["code"], "unknown_search_image")
        self.download.assert_not_called()

    async def test_verified_bytes_reach_sdk_and_duplicate_inspection_does_not_repeat_images(self):
        self.session.observe(official_results(self.urls[0]))
        first = await self.session.inspect(None, {"image_urls": []})
        self.assertEqual(len(image_parts(first)), 1)
        image_url = image_parts(first)[0].image_url
        with Image.open(io.BytesIO(base64.b64decode(image_url.split(",", 1)[1]))) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (96, 64)))
        identifier = output_json(first)["candidates"][0]["id"]
        self.assertIn(identifier, self.session.inspected)
        second = await self.session.inspect(None, {"image_urls": [self.urls[0]]})
        self.assertEqual(image_parts(second), [])
        self.assertEqual(self.download.call_count, 1)
        self.assertTrue(self.events[0]["candidates"][0]["previewUri"].startswith("data:image/jpeg;base64,"))

    async def test_selection_accepts_only_inspected_ids_and_current_adoption_limit(self):
        self.session.observe(official_results(*self.urls))
        early = await self.session.select(None, {"candidate_ids": ["unseen-id"], "rationale": "Use it"})
        self.assertEqual(output_json(early)["error"]["code"], "uninspected_reference")
        self.assertFalse(self.session.selection_made)
        inspected = await self.session.inspect(None, {"image_urls": []})
        identifiers = [item["id"] for item in output_json(inspected)["candidates"]]
        self.limits["adopt"] = 1
        over = await self.session.select(None, {"candidate_ids": identifiers, "rationale": "Use both"})
        self.assertEqual(output_json(over)["error"]["code"], "reference_limit")
        self.assertFalse(self.session.selection_made)
        selected = await self.session.select(None, {"candidate_ids": [identifiers[0]], "rationale": "Use the shape"})
        self.assertEqual(output_json(selected)["selected_candidate_ids"], [identifiers[0]])
        self.assertEqual([item["id"] for item in self.session.adopted], [identifiers[0]])
        self.assertEqual(self.events[-1]["webReferenceCount"], 1)
        self.assertIsNone(self.session.feedback("complete"))

    async def test_failed_downloads_can_be_explicitly_declined_without_fake_reference(self):
        self.session.observe(official_results(self.urls[0]))
        self.download.side_effect = RuntimeError("unavailable sk-private-123456789 via https://proxy-user:proxy-password@proxy.test")
        failed = await self.session.inspect(None, {"image_urls": []})
        payload = output_json(failed)
        self.assertEqual(payload["candidates"], []); self.assertEqual(image_parts(failed), [])
        self.assertTrue(payload["errors"])
        self.assertNotIn("sk-private", json.dumps(payload)); self.assertNotIn("proxy-password", json.dumps(payload))
        self.assertIsNone(self.session.feedback("final"))
        await self.session.select(None, {"candidate_ids": [], "rationale": "The retrieved image could not be verified"})
        self.assertIsNone(self.session.feedback("final")); self.assertEqual(self.session.adopted, [])
        self.assertEqual(self.events[-1]["webReferenceCount"], 0)

    async def test_no_image_results_do_not_force_inspection_or_selection(self):
        self.session.observe({"output": [search_item("text-only")]})
        self.assertIsNone(self.session.feedback("text-only plan"))
        self.assertEqual(self.session.adopted, [])
        self.download.assert_not_called()

    async def test_tier_vision_cap_prevents_extra_image_parts_across_calls(self):
        self.limits["vision"] = 1
        self.session.observe(official_results(*self.urls))
        first = await self.session.inspect(None, {"image_urls": [self.urls[0]]})
        second = await self.session.inspect(None, {"image_urls": [self.urls[1]]})
        self.assertEqual(len(image_parts(first)), 1); self.assertEqual(image_parts(second), [])
        self.assertEqual(len(self.session.inspected), 1); self.assertEqual(self.download.call_count, 1)

    async def test_pending_inspection_respects_vision_slots_consumed_by_parallel_continuation(self):
        started, release = threading.Event(), threading.Event()
        original = self.service.inspect_image_results
        def pending(*args):
            started.set()
            if not release.wait(timeout=3): raise RuntimeError("test synchronization timeout")
            return original(*args)
        self.session.observe(official_results(*self.urls))
        with patch.object(self.service, "inspect_image_results", side_effect=pending):
            task = asyncio.create_task(self.session.inspect(None, {"image_urls": []}))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                self.limits["vision"] = 0  # A concurrent continuation tool filled the remaining visual slots.
            finally:
                release.set()
            output = await task
        self.assertEqual(image_parts(output), [])
        self.assertEqual(self.session.inspected, {})

    async def test_cancellation_prevents_download_and_late_inspection_output(self):
        self.session.observe(official_results(self.urls[0]))
        self.context.cancel()
        with self.assertRaises(ImageGenerationCancelled):
            await self.session.inspect(None, {"image_urls": []})
        self.download.assert_not_called()
        self.context = ImageTaskContext(); self.session.task_context = self.context
        original = self.service.inspect_image_results
        def cancelled_result(*args):
            result = original(*args); self.context.cancel(); return result
        with patch.object(self.service, "inspect_image_results", side_effect=cancelled_result):
            with self.assertRaises(ImageGenerationCancelled):
                await self.session.inspect(None, {"image_urls": []})
        self.assertEqual(self.session.inspected, {})
        self.assertFalse(any(event["type"] == "react_visual_selected" for event in self.events))

    async def test_staging_adopted_cache_uses_exact_verified_bytes_without_redownload(self):
        self.session.observe(official_results(self.urls[0]))
        await self.session.inspect(None, {"image_urls": []})
        identifier = next(iter(self.session.inspected))
        await self.session.select(None, {"candidate_ids": [identifier], "rationale": "Preserve the product"})
        self.download.reset_mock(side_effect=True)
        self.download.side_effect = AssertionError("verified reference should be reused")
        with tempfile.TemporaryDirectory() as temp:
            staged = self.service.stage_reference_records(self.session.adopted, Path(temp), 1, self.context)
            self.assertEqual(len(staged), 1)
            self.assertEqual(Path(staged[0]["path"]).read_bytes(), self.session.inspected[identifier]["_imageBytes"])
        self.download.assert_not_called()

    async def test_factory_rejects_invalid_fields_before_host_callback(self):
        inspect_callback, select_callback = Mock(return_value="ok"), Mock(return_value="ok")
        inspect_tool, select_tool = make_visual_reference_tools(inspect_callback, select_callback)
        for fields in ({"image_urls": ["https://example.com/x"], "extra": True}, {"image_urls": [""]}, {"image_urls": ["https://example.com/x"] * 13}, {"image_urls": "not-an-array"}):
            result = await inspect_tool.on_invoke_tool(None, json.dumps(fields))
            self.assertEqual(output_json(result)["error"]["code"], "invalid_reference_arguments")
        result = await select_tool.on_invoke_tool(None, json.dumps({"candidate_ids": [], "rationale": ""}))
        self.assertEqual(output_json(result)["error"]["code"], "invalid_reference_arguments")
        inspect_callback.assert_not_called(); select_callback.assert_not_called()
        self.assertEqual(await inspect_tool.on_invoke_tool(None, '{"image_urls":[]}'), "ok")
        inspect_callback.assert_called_once_with(None, {"image_urls": []})


class FlashVisualWorkflowTests(unittest.TestCase):
    def test_text_only_identity_search_can_recover_an_inspected_reference(self):
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(latest_payload=lambda _key: None)
        controller.web_search = WebSearchService()
        url = "https://images.example.com/naiwa.png"
        identifier = controller.web_search._candidate_id(url)
        bodies = []

        def tool_call(name, arguments):
            return {"type": "function_call", "id": "fc-" + name, "call_id": "call-" + name,
                    "name": name, "arguments": json.dumps(arguments), "status": "completed"}

        def handler(request):
            bodies.append(json.loads(request.content))
            if len(bodies) == 1:
                text = "确认了来源，图片搜索尚未返回角色。\n<<<FINAL_PROMPT>>>时代广场上的角色"
                return sse_response(text, [search_item("identity", "奶蛙的出处"), *response_payload(text)["output"]])
            if len(bodies) == 2:
                return sse_response("", [official_results(url)["output"][0],
                                         tool_call("inspect_visual_references", {"image_urls": [url]})])
            if len(bodies) == 3:
                return sse_response("", [tool_call("select_visual_references", {
                    "candidate_ids": [identifier], "rationale": "采用来源中已检视的角色外观"})])
            return sse_response("已检视并采用角色参考图。\n<<<FINAL_PROMPT>>>保留奶蛙外观的时代广场屏幕")

        def sdk(request, **kwargs):
            request.http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
            return run_agent(request, **kwargs)

        controller._run_sdk_agent = sdk
        with patch.object(controller.web_search, "_public_image_bytes", return_value=fixture_png()):
            result = controller._run_image_prompt_agent(
                {"id": "key", "base_url": "https://provider.test/v1"}, "test-key", "奶娃在纽约时代广场", (), "flash",
                True, lambda *args, **kwargs: None,
            )
        self.assertEqual(len(bodies), 4)
        self.assertIn("复核上一轮的参考图决定", json.dumps(bodies[1], ensure_ascii=False))
        self.assertEqual([item["id"] for item in result["webCandidates"]], [identifier])
        self.assertEqual(result["webReferenceCount"], 1)

    def test_sdk_cancellation_returns_before_pending_inspection_is_released(self):
        started, release, finished, watchdog_fired = (threading.Event() for _ in range(4))
        context = ImageTaskContext()
        class PendingInspection(WebSearchService):
            def inspect_image_results(self, candidates, max_count=6, task_context=None):
                started.set()
                try:
                    if not release.wait(timeout=5): raise RuntimeError("test watchdog failed to release inspection")
                    if task_context is not None: task_context.check_cancelled()
                    return {"candidates": [], "errors": []}
                finally:
                    finished.set()
        session = VisualReferenceSession(PendingInspection(), inspect_limit=2,
            vision_limit=lambda: 2, adopt_limit=lambda: 2, emit=lambda *args, **kwargs: None, task_context=context)
        url = "https://images.example.com/pending.png"
        web = official_results(url)["output"][0]
        def handler(_request):
            return sse_response("", [web, {"type": "function_call", "id": "fc-pending", "call_id": "call-pending",
                "name": "inspect_visual_references", "arguments": '{"image_urls":[]}', "status": "completed"}])
        def trigger_cancel():
            if started.wait(timeout=3): context.cancel()
        def watchdog():
            watchdog_fired.set(); release.set()
        trigger = threading.Thread(target=trigger_cancel, daemon=True)
        timer = threading.Timer(3, watchdog); timer.daemon = True
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        request = AgentRequest(base_url="https://provider.test/v1", api_key="test-key", model="test", instructions="",
            input="Inspect the sourced image", web_search_enabled=True, http_client=client, response_observer=session.observe)
        trigger.start(); timer.start()
        try:
            with self.assertRaises(AgentCancelled):
                run_agent(request, tools=make_visual_reference_tools(session.inspect, session.select), cancel_event=context.cancel_event)
            # With asyncio.to_thread here, asyncio.run would wait for the
            # watchdog to release the default-executor work before returning.
            self.assertTrue(started.is_set())
            self.assertFalse(release.is_set(), "cancel waited for the pending image download")
            self.assertFalse(watchdog_fired.is_set())
            self.assertEqual(session.inspected, {})
        finally:
            release.set(); timer.cancel(); trigger.join(timeout=1)
            self.assertTrue(finished.wait(timeout=2), "background inspection did not clean up")

    def test_flash_budget_allows_agent_chosen_inspect_select_and_final(self):
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(latest_payload=lambda _key: None)
        controller.web_search = WebSearchService()
        url = "https://images.example.com/product.png"
        identifier = controller.web_search._candidate_id(url)
        image = official_results(url)["output"][0]
        bodies, requests, events = [], [], []
        def handler(request):
            bodies.append(json.loads(request.content))
            if len(bodies) == 1:
                return sse_response("", [image, {"type": "function_call", "id": "fc-inspect", "call_id": "call-inspect", "name": "inspect_visual_references", "arguments": json.dumps({"image_urls": [url]}), "status": "completed"}])
            if len(bodies) == 2:
                return sse_response("", [{"type": "function_call", "id": "fc-select", "call_id": "call-select", "name": "select_visual_references", "arguments": json.dumps({"candidate_ids": [identifier], "rationale": "Use the real product shape"}), "status": "completed"}])
            return sse_response("Checked the real image.\n<<<FINAL_PROMPT>>>revised prompt based on the real product")
        def sdk(request, **kwargs):
            requests.append(request)
            request.http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
            return run_agent(request, **kwargs)
        controller._run_sdk_agent = sdk
        with patch.object(controller.web_search, "_public_image_bytes", return_value=fixture_png()):
            result = controller._run_image_prompt_agent({"id": "key", "base_url": "https://provider.test/v1"}, "test-key", "Product render", (), "flash", True,
                lambda kind, **details: events.append({"type": kind, **details}))
        self.assertEqual(len(bodies), 3)
        self.assertEqual(requests[0].max_turns, 6)
        self.assertEqual(result["prompt"], "revised prompt based on the real product")
        self.assertEqual(result["summary"], "Checked the real image.")
        self.assertEqual([item["id"] for item in result["webCandidates"]], [identifier])
        self.assertTrue(any(item.get("type") == "function_call_output" and any(part.get("type") == "input_image" for part in item.get("output", []) if isinstance(part, dict)) for item in bodies[1]["input"]))
        self.assertEqual((result["turnCount"], result["requestCount"], result["webSearchCallCount"], result["localToolCallCount"]), (3, 3, 1, 2))

    def test_search_candidates_do_not_force_extra_inspection_or_selection_turns(self):
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(latest_payload=lambda _key: None)
        controller.web_search = WebSearchService()
        bodies = []
        def handler(request):
            bodies.append(json.loads(request.content))
            text = "Textual facts are enough for this scene.\n<<<FINAL_PROMPT>>>An independently composed landscape"
            return sse_response(text, [official_results("https://images.example.com/optional.png")["output"][0], *response_payload(text)["output"]])
        def sdk(request, **kwargs):
            self.assertIsNone(request.final_output_feedback)
            request.http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
            return run_agent(request, **kwargs)
        controller._run_sdk_agent = sdk
        with patch.object(controller.web_search, "_public_image_bytes", side_effect=AssertionError("The Agent did not ask to inspect")):
            result = controller._run_image_prompt_agent({"id": "key", "base_url": "https://provider.test/v1"}, "test-key", "Landscape", (), "flash", True, lambda *args, **kwargs: None)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(result["webCandidates"], [])
        self.assertEqual(result["inspectedCandidates"], [])
        self.assertEqual(result["webReferenceCount"], 0)
        self.assertEqual(result["webImageCandidateCount"], 1)


if __name__ == "__main__":
    unittest.main()
