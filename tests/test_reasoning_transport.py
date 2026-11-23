import io
import json
import threading
import tempfile
from pathlib import Path
from PIL import Image
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app
from backend.client import EasyClinClient
from backend.prompt_targets import (
    apply_reasoning_profile, choose_reasoning_pair, format_prompt_instructions,
)
from backend.transport import (
    UnsentConnectionError, connection_was_refused, is_mirra_request, open_request,
)


class ReasoningTransportTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("backend.client.log_network_error")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_gateway_connects_without_a_running_desktop_proxy(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.headers.get("Authorization"), self.rfile.read(int(self.headers["Content-Length"]))))
                data = b'{"output_text":"connected"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            refused = urllib.error.URLError(ConnectionRefusedError(10061, "desktop proxy stopped"))
            with patch("urllib.request.urlopen", side_effect=refused) as system_proxy:
                result = EasyClinClient.stream_response(
                    f"http://127.0.0.1:{server.server_port}/openai/v1", "sk-mirra-test-only",
                    "model", "instructions", "keep this input", reasoning_mode="continuation",
                )
            self.assertEqual(result, "connected")
            system_proxy.assert_not_called()
            self.assertEqual(len(received), 1)
            self.assertEqual(json.loads(received[0][1])["input"], "keep this input")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_gateway_does_not_follow_authorized_redirects(self):
        visited = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                visited.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/credential-sink")
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/start")
            request.add_unredirected_header("Authorization", "Bearer sk-mirra-test-only")
            with self.assertRaises(urllib.error.HTTPError) as error:
                open_request(request, timeout=3)
            self.assertEqual(error.exception.code, 302)
            self.assertEqual(visited, ["/start"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_direct_openai_keeps_the_configured_opener(self):
        request = urllib.request.Request("https://api.openai.com/v1/responses")
        request.add_unredirected_header("Authorization", "Bearer sk-proj-test-only")
        configured_proxy = Mock(return_value="response")
        self.assertFalse(is_mirra_request(request))
        self.assertEqual(open_request(request, timeout=42, opener=configured_proxy), "response")
        configured_proxy.assert_called_once_with(request, timeout=42)

    def test_only_a_proven_unsent_refusal_gets_the_retry_marker(self):
        opener = SimpleNamespace(open=Mock(side_effect=urllib.error.URLError(ConnectionRefusedError(10061, "refused"))))
        with patch("backend.transport.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(UnsentConnectionError):
                EasyClinClient.stream_response("https://39.105.74.222/openai/v1", "sk-mirra-test-only", "model", "instructions", "input")
        self.assertEqual(opener.open.call_count, 1)
        self.assertFalse(connection_was_refused(TimeoutError("no response after upload")))
        self.assertFalse(connection_was_refused(OSError("unexpected EOF")))

    def test_partial_response_never_becomes_a_completed_prompt_or_retries(self):
        class Response:
            headers = {"Content-Type": "text/event-stream"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                return iter([b'data: {"type":"response.output_text.delta","delta":"partial"}\n', b"\n"])

        with patch("urllib.request.urlopen", return_value=Response()) as post:
            with self.assertRaisesRegex(RuntimeError, "响应中断"):
                EasyClinClient.stream_response("https://provider.test/v1", "test-only", "model", "instructions", "input")
        self.assertEqual(post.call_count, 1)

    def test_response_request_carries_permission_context_as_a_header(self):
        class Response:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"output_text":"done"}'

        with patch("urllib.request.urlopen", return_value=Response()) as post:
            EasyClinClient.stream_response("https://provider.test/v1", "test-only", "model", "instructions", "untouched", reasoning_mode="polish")
        request = post.call_args.args[0]
        self.assertEqual(request.get_header("X-mirra-reasoning-mode"), "polish")
        self.assertEqual(json.loads(request.data)["input"], "untouched")

    def test_json_incomplete_response_never_returns_partial_text(self):
        class Response:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"status":"incomplete","output_text":"partial"}'

        with patch("urllib.request.urlopen", return_value=Response()) as post:
            with self.assertRaisesRegex(RuntimeError, "未完成"):
                EasyClinClient.stream_response("https://provider.test/v1", "test-only", "model", "instructions", "input")
        self.assertEqual(post.call_count, 1)


class PromptTargetTests(unittest.TestCase):
    def test_format_controls_add_creative_blocks_and_keep_static_svg(self):
        svg = format_prompt_instructions({"output_format": "svg"})
        gif = format_prompt_instructions({"output_format": "gif", "frame_count": 12})
        self.assertIn("静态 SVG", svg)
        self.assertIn("路径与分层", svg)
        self.assertIn("不输出 SVG 代码", svg)
        self.assertIn("12 帧", gif)
        self.assertIn("运动脚本", gif)
        self.assertIn("循环衔接", gif)
        self.assertIn("独立 API 字段", svg)
        self.assertIn("独立 API 字段", gif)

    def test_polish_uses_the_gateway_pair_and_keeps_the_original_input(self):
        profile = {
            "tier": {"promptPolish": True, "reasoningModels": ["allowed-model"], "reasoningEfforts": ["low"]},
            "polish": {"model": "allowed-model", "effort": "low"},
        }
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(
            get_key_record=lambda key: {"id": key, "base_url": "https://provider.test/v1"},
            get_secret=lambda _key: "test-only", latest_payload=lambda _key: {"appModels": profile},
        )
        controller.client = SimpleNamespace(stream_response=Mock(return_value="GIF motion prompt"))
        result = controller.polish_prompt("key", "a dog jumps", {"output_format": "gif", "frame_count": 4})
        self.assertTrue(result["ok"])
        call = controller.client.stream_response.call_args
        self.assertEqual(call.args[2], "allowed-model")
        self.assertEqual(call.args[4], "a dog jumps")
        self.assertIn("4 帧", call.args[3])
        self.assertIn("运动脚本", call.args[3])
        self.assertEqual(call.kwargs["reasoning_effort"], "low")
        self.assertEqual(call.kwargs["reasoning_mode"], "polish")

    def test_reasoning_agent_receives_the_gif_target_in_system_instructions(self):
        controller = app.AppController.__new__(app.AppController)

        def response(*_args, **kwargs):
            text = "动作设计完成\n<<<FINAL_PROMPT>>>【运动脚本】跳起后自然落地并循环"
            kwargs["on_delta"](text)
            return text

        controller.client = SimpleNamespace(stream_response=Mock(side_effect=response))
        config = app.resolve_image_reasoning_config("flash")
        config["outputOptions"] = {"output_format": "gif", "frame_count": 9}
        result = controller._run_image_prompt_agent(
            {"id": "key", "base_url": "https://provider.test/v1"}, "test-only", "a dog jumps", (),
            "flash", False, lambda *_args, **_kwargs: None, reasoning_config=config,
        )
        self.assertIn("运动脚本", result["prompt"])
        call = controller.client.stream_response.call_args
        self.assertIn("循环衔接", call.args[3])
        self.assertEqual(call.args[4][0]["content"], "a dog jumps")
        self.assertEqual(call.kwargs["reasoning_mode"], "flash")

    def test_continuation_uses_the_authorized_pair_and_static_vision_image(self):
        profile = {
            "tier": {"imageEditing": True, "maxReferences": 4,
                     "reasoningModels": ["allowed-planner"], "reasoningEfforts": ["medium"]},
            "continuation": {"model": "allowed-planner", "effort": "medium"},
        }
        controller = app.AppController.__new__(app.AppController)
        controller.store = SimpleNamespace(latest_payload=lambda _key: {"appModels": profile})
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "motion.gif"
            Image.new("RGB", (8, 8), "red").save(image, save_all=True,
                append_images=[Image.new("RGB", (8, 8), "blue")], duration=200, loop=0)
            asset = app.image_asset_record(image)
            answer = {"operation": "edit", "selected_asset_ids": [asset["assetId"]],
                      "descriptions": [{"asset_id": asset["assetId"], "description": "a red square"}], "rationale": "preserve the square"}
            controller.client = SimpleNamespace(stream_response=Mock(return_value=json.dumps(answer)))
            controller._run_instant_image_continuation_planner(
                {"id": "key", "base_url": "https://provider.test/v1"}, "test-only", "turn it green",
                {"assets": [asset]}, [asset],
            )
        call = controller.client.stream_response.call_args
        self.assertEqual(call.args[2], "allowed-planner")
        self.assertEqual(call.kwargs["reasoning_effort"], "medium")
        self.assertEqual(call.kwargs["reasoning_mode"], "continuation")
        self.assertTrue(call.args[4][0]["content"][1]["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(controller._tier_reference_limit({"id": "key"}), 4)

    def test_rpc_passes_native_prompt_options_and_folder_opening(self):
        from backend.runtime import RemoteWebApi
        from backend.web_api import WebApi
        rpc = SimpleNamespace(call_with_events=Mock(return_value={"ok": True}))
        folder = Mock(return_value={"ok": True, "path": "project"})
        controller = SimpleNamespace(push_image_generation_event=Mock(), open_image_session_folder=folder,
                                     polish_prompt=Mock(return_value={"ok": True}))
        options = {"output_format": "svg"}
        api = RemoteWebApi(controller, rpc)
        api.polish_prompt("key", "prompt", options)
        self.assertEqual(rpc.call_with_events.call_args.args, ("polish_prompt", "key", "prompt", options))
        self.assertIs(rpc.call_with_events.call_args.kwargs["on_event"], controller.push_image_generation_event)
        self.assertEqual(api.open_image_session_folder("session", "set")["path"], "project")
        folder.assert_called_once_with("session", "set")
        WebApi(controller).polish_prompt("key", "prompt", options)
        controller.polish_prompt.assert_called_once_with("key", "prompt", options)

    def test_tier_cannot_reenable_hidden_presets_or_advanced_modes(self):
        profile = {
            "tier": {"advanced": False, "reasoningModels": ["gpt-6-luna"], "reasoningEfforts": ["medium"]},
            "reasoning": {"luna": {"model": "gpt-6-luna", "effort": "medium"}},
            "reasoningModes": {"flash": {"model": "gpt-6-luna", "effort": "medium"}},
            "reasoningCapabilities": {"gpt-6-luna": {"efforts": ["medium"], "default": "medium"}},
        }
        with self.assertRaisesRegex(ValueError, "高级"):
            apply_reasoning_profile(profile, "advanced", app.resolve_image_reasoning_config("advanced"), app.resolve_image_reasoning_config("flash"))
        with self.assertRaisesRegex(ValueError, "预设"):
            apply_reasoning_profile(profile, "max", app.resolve_image_reasoning_config("max"), app.resolve_image_reasoning_config("flash"))
        model, effort = choose_reasoning_pair({**profile, "tier": {**profile["tier"], "imageEditing": True}, "continuation": {"model": "gpt-6-luna", "effort": "medium"}}, "not-allowed", "max", purpose="continuation")
        self.assertEqual((model, effort), ("gpt-6-luna", "medium"))


if __name__ == "__main__":
    unittest.main()
