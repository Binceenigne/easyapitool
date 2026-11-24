"""Private loopback HTTP bridge to the shared official OpenAI Agents SDK core.

Run in a dedicated virtualenv. No upstream key is accepted here: api_key is an
ephemeral Go gateway lease and base_url is the configured local metered proxy.
"""
from __future__ import annotations

import asyncio
import hmac
import importlib.util
import ipaddress
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MAX_BODY = 4 * 1024 * 1024
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,100}\Z")
SECRET = re.compile(r"(?i)(?:sk-[a-z0-9_-]{8,}|lease-[a-z0-9_-]{12,}|bearer\s+[^\s,;]+)")


def local_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return (url.scheme == "http" and ipaddress.ip_address(url.hostname).is_loopback
                and bool(url.port) and not (url.username or url.password or url.query or url.fragment))
    except (ValueError, TypeError):
        return False


def clean(value, forbidden=()):
    if isinstance(value, dict):
        return {key: clean(item, forbidden) for key, item in value.items() if key not in ("api_key", "headers")}
    if isinstance(value, list):
        return [clean(item, forbidden) for item in value]
    if isinstance(value, str):
        for secret in forbidden:
            if secret:
                value = value.replace(secret, "[redacted]")
        return SECRET.sub("[redacted]", value)
    return value


class WorkerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, token, proxy_base, *, runner=None, concurrency=2):
        if not ipaddress.ip_address(address[0]).is_loopback or len(token) < 32 or not local_url(proxy_base):
            raise ValueError("Worker requires loopback bind/proxy and a random token of at least 32 characters")
        if urlsplit(proxy_base).path != "/internal/agent/v1":
            raise ValueError("Worker proxy must be the private metered Agent route")
        self.token, self.proxy_base, self.runner = token, proxy_base.rstrip("/"), runner
        self.jobs, self.jobs_lock = {}, threading.Lock()
        self.capacity = threading.BoundedSemaphore(concurrency)
        self.connections = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\nRetry-After: 1\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_args):
        pass  # HTTP payloads, credentials and raw exception representations stay out of logs.

    def authenticated(self):
        return hmac.compare_digest(self.headers.get("Authorization", "").encode("utf-8"), ("Bearer " + self.server.token).encode("utf-8"))

    def respond(self, status, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status in (429, 503):
            self.send_header("Retry-After", "1")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.authenticated():
            return self.respond(401, {"error": "unauthorized"})
        if self.path != "/healthz":
            return self.respond(404, {"error": "not_found"})
        ready = self.server.runner is not None or importlib.util.find_spec("mirra_agents") is not None
        return self.respond(200 if ready else 503, {"ok": ready, "runtime": "openai-agents-sdk"})

    def body(self):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Streaming request bodies are not accepted")
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= MAX_BODY:
            raise ValueError("Invalid request size")
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("Expected an object")
        return value

    def do_POST(self):
        if not self.authenticated():
            self.close_connection = True
            return self.respond(401, {"error": "unauthorized"})
        try:
            payload = self.body()
            request_id = payload.get("requestId", "")
            if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
                raise ValueError("Invalid request ID")
            if self.path == "/cancel":
                with self.server.jobs_lock:
                    cancellation = self.server.jobs.get(request_id)
                if cancellation is not None:
                    cancellation.set()
                return self.respond(200, {"ok": True, "cancelled": cancellation is not None})
            if self.path != "/run":
                return self.respond(404, {"error": "not_found"})
            parameters = self.validate(payload.get("request"))
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            self.close_connection = True
            return self.respond(400, {"error": "invalid_agent_request"})
        if not self.server.capacity.acquire(blocking=False):
            return self.respond(429, {"error": "agent_worker_busy"})
        cancellation = threading.Event()
        with self.server.jobs_lock:
            duplicate = request_id in self.server.jobs
            if not duplicate:
                self.server.jobs[request_id] = cancellation
        if duplicate:
            self.server.capacity.release()
            return self.respond(409, {"error": "duplicate_request_id"})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()  # A 200 commits this run before any SDK/model work.
        self.close_connection = True

        def emit(event):
            safe = clean(event, (self.server.token, parameters["api_key"]))
            self.wfile.write(json.dumps(safe, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n")
            self.wfile.flush()

        async def stream():
            if self.server.runner is None:
                from mirra_agents import AgentRequest, run_agent_stream
                from visual import WorkerVisualSession
                options = dict(parameters)
                visual = WorkerVisualSession(options, cancellation)
                if visual.limit:
                    options["instructions"] += ("\n联网使用 OpenAI 官方图片与网页搜索。检索得到图片候选后，使用 inspect_visual_references 检视真实画面，再使用 select_visual_references 明确采用或不采用。"
                        f"最多可检视 {visual.limit} 张、采用 {visual.adopt_limit} 张；已附的用户图片占用参考槽位。只把实际采用的图作为图片生成参考，SVG等不允许参考图的输出可检视但应选择空数组。")
                    options["response_observer"] = visual.observe
                    options["final_output_feedback"] = visual.feedback
                events = run_agent_stream(AgentRequest(**options), cancel_event=cancellation, tools=visual.tools())
            else:
                events = self.server.runner(parameters, cancellation)
            queue = asyncio.Queue()

            async def produce():
                try:
                    async for event in events:
                        queue.put_nowait(event)
                except BaseException as error:
                    queue.put_nowait(error)
                finally:
                    queue.put_nowait(None)

            producer = asyncio.create_task(produce())
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=2)
                    except TimeoutError:
                        emit({"type": "heartbeat"})
                        continue
                    if event is None:
                        break
                    if isinstance(event, BaseException):
                        raise event
                    emit(event)
            finally:
                cancellation.set()
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)

        try:
            asyncio.run(stream())
        except (BrokenPipeError, ConnectionError, OSError):
            cancellation.set()
        except Exception:
            # SDK core supplies safe, detailed errors for model failures. An
            # unexpected wrapper failure must not echo request data or keys.
            try:
                emit({"type": "error", "error": {"stage": "agent", "code": "agent_worker_failure",
                     "message": "Agent SDK worker failed before completing the operation", "lastCause": "Agent SDK worker internal failure",
                     "attempts": 0, "maxAttempts": parameters["max_attempts"], "status": 503,
                     "diagnosticId": request_id, "retryable": False}})
            except OSError:
                pass
        finally:
            cancellation.set()
            with self.server.jobs_lock:
                self.server.jobs.pop(request_id, None)
            self.server.capacity.release()

    def validate(self, request):
        if not isinstance(request, dict):
            raise ValueError("Missing request")
        allowed = {"base_url", "api_key", "model", "instructions", "input", "effort", "mode", "web_search_enabled",
                   "max_turns", "max_output_tokens", "max_attempts", "timeout", "visual_reference_limit", "visual_reference_adopt_limit"}
        if (set(request) - allowed or not isinstance(request.get("base_url"), str)
                or request["base_url"].rstrip("/") != self.server.proxy_base):
            raise ValueError("Invalid proxy")
        if not isinstance(request.get("api_key"), str) or not request["api_key"].startswith("lease-") or len(request["api_key"]) > 200:
            raise ValueError("Only ephemeral Go leases are accepted")
        if not isinstance(request.get("model"), str) or not MODEL.fullmatch(request["model"]):
            raise ValueError("Invalid model")
        user_input = request.get("input")
        if isinstance(user_input, str):
            if not 0 < len(user_input) <= 32000:
                raise ValueError("Invalid prompt")
        elif isinstance(user_input, list) and len(user_input) == 1 and isinstance(user_input[0], dict) and user_input[0].get("role") == "user":
            content = user_input[0].get("content")
            if not isinstance(content, list) or not 1 <= len(content) <= 17:
                raise ValueError("Invalid prompt content")
            for part in content:
                if not isinstance(part, dict):
                    raise ValueError("Invalid prompt content")
                if part.get("type") == "input_text":
                    if not isinstance(part.get("text"), str) or len(part["text"]) > 32000:
                        raise ValueError("Invalid prompt text")
                elif part.get("type") == "input_image":
                    image = part.get("image_url")
                    if not isinstance(image, str) or not image.startswith("data:image/jpeg;base64,") or len(image) > 132000:
                        raise ValueError("Invalid preview image")
                else:
                    raise ValueError("Unsupported prompt content")
        else:
            raise ValueError("Invalid prompt")
        if not isinstance(request.get("instructions"), str) or len(request["instructions"]) > 48000:
            raise ValueError("Invalid instructions")
        if request.get("effort", "") not in ("", "none", "minimal", "low", "medium", "high", "xhigh", "max"):
            raise ValueError("Invalid effort")
        if not isinstance(request.get("mode", ""), str) or len(request.get("mode", "")) > 32:
            raise ValueError("Invalid mode")
        if not isinstance(request.get("web_search_enabled", False), bool):
            raise ValueError("Invalid search setting")
        for field, minimum, maximum, default in (("max_turns", 1, 32, 12), ("max_output_tokens", 1, 8192, 8192), ("max_attempts", 1, 5, 3)):
            value = request.get(field, default)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError("Invalid runtime limit")
            request[field] = value
        for field, maximum in (("visual_reference_limit", 12), ("visual_reference_adopt_limit", 8)):
            value = request.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                raise ValueError("Invalid reference limit")
            request[field] = value
        timeout = request.get("timeout", 720)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 1200:
            raise ValueError("Invalid timeout")
        # SDK calls must go straight to the Go loopback proxy. Egress proxying
        # belongs to Go and remains consistent for direct and distributed keys.
        return {**request, "trust_env": False}


if __name__ == "__main__":
    host = os.environ.get("MIRRA_AGENT_WORKER_HOST", "127.0.0.1")
    port = int(os.environ.get("MIRRA_AGENT_WORKER_PORT", "8774"))
    token = os.environ.get("MIRRA_AGENT_WORKER_TOKEN", "")
    proxy = os.environ.get("MIRRA_AGENT_PROXY_BASE_URL", "http://127.0.0.1:8767/internal/agent/v1")
    server = WorkerServer((host, port), token, proxy)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
