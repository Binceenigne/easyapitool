from __future__ import annotations

import asyncio
import http.client
import importlib.util
import json
from pathlib import Path
import threading
import time
import unittest


spec = importlib.util.spec_from_file_location("mirra_private_worker", Path(__file__).with_name("worker.py"))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        async def runner(parameters, cancel):
            if parameters["input"] == "wait":
                while not cancel.is_set():
                    await asyncio.sleep(.01)
                yield {"type": "error", "error": {"code": "cancelled"}}
                return
            yield {"type": "text_delta", "delta": "hello " + parameters["api_key"]}
            yield {"type": "result", "text": "finished", "usage": {}, "webSearchUsed": False}

        self.server = worker.WorkerServer(("127.0.0.1", 0), "t" * 48,
            "http://127.0.0.1:18767/internal/agent/v1", runner=runner)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, payload=None, token=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request("GET" if payload is None else "POST", path,
            None if payload is None else json.dumps(payload),
            {"Authorization": "Bearer " + (token if token is not None else self.server.token), "Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), data
        connection.close()
        return result

    def request(self, request_id="runtime-request-1", prompt="hello"):
        return {"requestId": request_id, "request": {"base_url": self.server.proxy_base,
            "api_key": "lease-" + "k" * 43, "model": "gpt-6.1-terra", "instructions": "prepare an image prompt",
            "input": prompt, "effort": "high", "mode": "advanced", "web_search_enabled": False,
            "max_attempts": 3, "max_turns": 12, "max_output_tokens": 8192, "timeout": 60}}

    def test_authenticated_ndjson_and_secret_redaction(self):
        status, _, data = self.call("/run", self.request())
        self.assertEqual(status, 200)
        events = [json.loads(line) for line in data.splitlines()]
        self.assertEqual(events[-1]["text"], "finished")
        self.assertNotIn(b"kkkkkkkkkkkk", data)
        self.assertEqual(self.call("/healthz", token="incorrect")[0], 401)
        self.assertEqual(self.call("/healthz")[0], 200)

    def test_arbitrary_urls_real_keys_and_invalid_limits_are_rejected(self):
        for patch in ({"base_url": "https://api.openai.com/v1"}, {"api_key": "sk-proj-do-not-use"},
                      {"base_url": {}}, {"max_attempts": 100}, {"timeout": 0}, {"mode": {}},
                      {"input": "a" * 32001}, {"headers": {"Authorization": "unsafe"}}):
            request = self.request()
            request["request"].update(patch)
            self.assertEqual(self.call("/run", request)[0], 400, patch)

    def test_size_is_rejected_before_reading_a_large_body(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.putrequest("POST", "/run")
        connection.putheader("Authorization", "Bearer " + self.server.token)
        connection.putheader("Content-Length", str(worker.MAX_BODY + 1))
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        connection.close()

    def test_cancel_stops_active_runtime(self):
        results = []
        thread = threading.Thread(target=lambda: results.append(self.call("/run", self.request(prompt="wait"))), daemon=True)
        thread.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with self.server.jobs_lock:
                if self.server.jobs:
                    break
            time.sleep(.01)
        status, _, body = self.call("/cancel", {"requestId": "runtime-request-1"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["cancelled"])
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertIn(b"cancelled", results[0][2])

    def test_busy_admission_has_retry_after_without_running_model(self):
        self.assertTrue(self.server.capacity.acquire(False))
        self.assertTrue(self.server.capacity.acquire(False))
        try:
            status, headers, _ = self.call("/run", self.request())
            self.assertEqual(status, 429)
            self.assertEqual(headers["Retry-After"], "1")
            self.assertFalse(self.server.jobs)
        finally:
            self.server.capacity.release()
            self.server.capacity.release()

    def test_non_loopback_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            worker.WorkerServer(("0.0.0.0", 0), "t" * 48, self.server.proxy_base)


if __name__ == "__main__":
    unittest.main()
