from __future__ import annotations
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
spec = importlib.util.spec_from_file_location("mirra_worker_visual", Path(__file__).with_name("visual.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WorkerVisualTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        return module.WorkerVisualSession({"base_url": "http://127.0.0.1:1/internal/agent/v1", "api_key": "lease-test", "visual_reference_limit": 3, "visual_reference_adopt_limit": 2}, threading.Event())

    async def test_visual_feedback_and_tools_only_use_verified_candidates(self):
        session = self.session()
        self.assertIsNone(session.feedback("imagined image"))
        url = "https://cdn.example/photo.png"
        session.observe({"output": [{"id": "ws-1", "type": "web_search_call", "results": [{"type": "image_result", "image_url": url}]}]})
        self.assertIn("inspect_visual_references", session.feedback("premature final"))
        requests = []
        async def request(payload):
            requests.append(payload)
            if payload["operation"] == "select":
                return {"selectedIds": ["webref-real"], "webReferences": [{"id": "webref-real", "resourceId": "owner-scoped"}]}
            return {"candidates": [{"id": "webref-real", "imageUrl": url, "previewDataUrl": "data:image/jpeg;base64,eA=="}]}
        session.request = request
        invalid = await session.inspect(None, {"image_urls": ["https://fabricated.example/photo.png"]})
        self.assertIn("unknown_reference", invalid)
        self.assertEqual(requests, [])
        output = await session.inspect(None, {"image_urls": []})
        self.assertEqual(len(output), 2)
        self.assertEqual(output[1].type, "image")
        self.assertNotIn("base64", output[0].text)
        self.assertIn("select_visual_references", session.feedback("not selected"))
        duplicate = await session.inspect(None, {"image_urls": []})
        self.assertEqual(len(duplicate), 1, "do not append an image twice to SDK history")
        invalid = await session.select(None, {"candidate_ids": ["not-inspected"], "rationale": "invalid"})
        self.assertIn("invalid_reference_selection", invalid)
        await session.select(None, {"candidate_ids": ["webref-real"], "rationale": "preserve the real product"})
        self.assertIsNone(session.feedback("ready"))

    async def test_reference_http_is_cancellable_without_executor_shutdown(self):
        import httpx2
        original = httpx2.AsyncClient
        entered = asyncio.Event()
        async def handler(_request):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(httpx2, "AsyncClient", side_effect=lambda **kw: original(transport=httpx2.MockTransport(handler), **kw)):
            task = asyncio.create_task(self.session().request({"operation": "inspect", "image_urls": []}))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, .2)


if __name__ == "__main__":
    unittest.main()
