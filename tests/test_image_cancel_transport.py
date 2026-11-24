import base64
import contextlib
import datetime
import ipaddress
import io
import json
import socket
import ssl
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from PIL import Image

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from backend.client import EasyClinClient
from backend.controller_mixins.image_reasoning import ImageGenerationCancelled, ImageReasoningMixin, ImageTaskContext
from backend.image_editor import ImageGenerationClient
from backend.transport import abort_response, transport_details
from mirra_agents.retry import AgentCancelled, AgentRunFailure, retry_sync


def fixture_png():
    buffer = io.BytesIO()
    Image.new("RGB", (96, 64), "orange").save(buffer, format="PNG")
    return buffer.getvalue()


class HangingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.server.connections.append(self.connection)

    def handle(self):
        if self.server.mode == "handshake":
            self.server.ready.set()
            self.wait_for_cancel()
        else:
            super().handle()

    def wait_for_cancel(self):
        self.connection.settimeout(10)
        try:
            while self.connection.recv(16384):
                pass
        except (OSError, ssl.SSLError):
            pass
        self.server.peer_closed.set()
        self.close_connection = True

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.requests.append(self.path)
        if self.server.mode == "headers":
            self.server.ready.set()
            self.wait_for_cancel()
            return
        if self.server.mode == "complete":
            body = b'{"data":[{"b64_json":"completed-before-stop"}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True
            self.server.ready.set()
            return
        if self.server.mode in {"json", "error_body"}:
            self.send_response(403 if self.server.mode == "error_body" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100000")
            self.end_headers()
            self.wfile.write(b'{"data":[{"b64_json":"')
            self.wfile.flush()
            self.server.ready.set()
            self.wait_for_cancel()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        partial = ({"type": "response.output_text.delta", "delta": "partial response"}
                   if self.path.endswith("/responses") else
                   {"type": "image_generation.partial_image", "b64_json": base64.b64encode(fixture_png()).decode()})
        self.wfile.write(("data: " + json.dumps(partial) + "\n\n").encode())
        # Hold a real HTTPResponse.readline/SSL_read mid-line, with no
        # completion event and no timeout shorter than the normal request.
        self.wfile.write(b'data: {"type":"unfinished","value":"')
        self.wfile.flush()
        self.server.ready.set()
        self.wait_for_cancel()

    def log_message(self, *_args):
        pass


class ImageCancelTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = tempfile.TemporaryDirectory()
        cls.root = Path(cls.files.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
                .not_valid_after(now + datetime.timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                .sign(key, hashes.SHA256()))
        cls.cert_path = cls.root / "localhost-cert.pem"
        cls.key_path = cls.root / "localhost-key.pem"
        cls.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        cls.key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cls.input_path = cls.root / "reference.png"
        cls.input_path.write_bytes(fixture_png())

    @classmethod
    def tearDownClass(cls):
        cls.files.cleanup()

    @contextlib.contextmanager
    def server(self, mode, tls=False):
        server = ThreadingHTTPServer(("127.0.0.1", 0), HangingHandler)
        server.mode = mode
        server.ready = threading.Event()
        server.peer_closed = threading.Event()
        server.requests = []
        server.connections = []
        if tls and mode != "handshake":
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.cert_path, self.key_path)
            server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        try:
            yield server, f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}/v1"
        finally:
            for connection in server.connections:
                try:
                    socket.socket.shutdown(connection, socket.SHUT_RDWR)
                except OSError:
                    pass
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def opener(self):
        # Trust only this test certificate; production certificate validation
        # and user proxy settings are never disabled by the implementation.
        context = ssl.create_default_context(cafile=str(self.cert_path))
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))

    def assert_stops(self, mode, *, tls=False, operation="generation"):
        controller = ImageReasoningMixin()
        context = controller._register_image_task("hanging-request")
        partials = []
        partial_seen = threading.Event()
        retries = []
        outcome = []
        finished = threading.Event()
        attempts = []
        with self.server(mode, tls) as (server, base_url):
            def partial(*values):
                partials.append(values)
                partial_seen.set()
            def attempt(_number):
                attempts.append(_number)
                if operation == "responses":
                    return EasyClinClient.stream_response(base_url, "test-only-secret", "test-model", "", "test",
                        timeout=30, task_context=context, on_delta=partial)
                method = ImageGenerationClient.edit_images if operation == "edit" else ImageGenerationClient.generate_image
                arguments = (base_url, "test-only-secret", (self.input_path,)) if operation == "edit" else (base_url, "test-only-secret")
                return method(*arguments, {"model": "test-image", "stream": True},
                              timeout=30, task_context=context, on_partial=partial)
            def run():
                try:
                    outcome.append(retry_sync(attempt, stage="image", cancel_event=context.cancel_event,
                                              attempts=3, on_retry=retries.append))
                except (ImageGenerationCancelled, AgentCancelled):
                    outcome.append({"code": "cancelled"})
                except AgentRunFailure as error:
                    outcome.append(error.details)
                except BaseException as error:
                    outcome.append({"unexpected": repr(error)})
                finally:
                    finished.set()
            opener = self.opener()
            with patch.object(ImageGenerationClient, "_opener", return_value=opener), patch("urllib.request._opener", opener):
                worker = threading.Thread(target=run, name="real-cancel-test", daemon=True)
                worker.start()
                try:
                    self.assertTrue(server.ready.wait(3), "real server never received a connection/request")
                    if mode == "body":
                        self.assertTrue(partial_seen.wait(2), "client did not consume its real partial event")
                    time.sleep(.03)
                    start = time.monotonic()
                    response = controller.cancel_image_generation("hanging-request")
                    self.assertLess(time.monotonic() - start, 1, "stop RPC waited for the peer")
                    self.assertTrue(response["ok"])
                    self.assertTrue(finished.wait(1), "generation thread did not exit after socket abort")
                    worker.join(timeout=.2)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(outcome[-1].get("code"), "cancelled", outcome)
                    self.assertEqual(attempts, [1])
                    self.assertEqual(retries, [])
                    self.assertTrue(server.peer_closed.wait(1), "upstream connection was not actually closed")
                    if mode == "body":
                        self.assertEqual(len(partials), 1, "a delivered partial was lost or duplicated")
                finally:
                    context.cancel()

    def test_tcp_before_response_headers_cancels_without_waiting_for_600_second_timeout(self):
        self.assert_stops("headers")

    def test_tls_before_response_headers_cancels_without_waiting_for_600_second_timeout(self):
        self.assert_stops("headers", tls=True)

    def test_tcp_stream_read_is_interrupted_and_completed_partial_is_preserved(self):
        self.assert_stops("body")

    def test_tls_stream_read_is_interrupted_and_completed_partial_is_preserved(self):
        self.assert_stops("body", tls=True)

    def test_edit_upload_can_stop_before_tls_response_headers(self):
        self.assert_stops("headers", tls=True, operation="edit")

    def test_legacy_responses_can_stop_before_tls_headers(self):
        self.assert_stops("headers", tls=True, operation="responses")

    def test_legacy_responses_can_stop_in_tls_body(self):
        self.assert_stops("body", tls=True, operation="responses")

    def test_tls_handshake_itself_can_be_cancelled(self):
        self.assert_stops("handshake", tls=True)

    def test_json_image_body_can_be_cancelled_on_tcp(self):
        self.assert_stops("json")

    def test_json_image_body_can_be_cancelled_on_tls(self):
        self.assert_stops("json", tls=True)

    def test_error_response_body_can_be_cancelled_without_a_retry(self):
        self.assert_stops("error_body", tls=True)

    def test_legacy_error_response_body_can_be_cancelled_without_a_retry(self):
        self.assert_stops("error_body", tls=True, operation="responses")

    def test_completed_result_is_unchanged_by_a_later_cancel(self):
        context = ImageTaskContext()
        with self.server("complete") as (_, base_url), patch.object(ImageGenerationClient, "_opener", return_value=self.opener()):
            result = ImageGenerationClient.generate_image(base_url, "test-only", {"stream": False}, task_context=context)
            context.cancel()
            self.assertEqual(result["data"][0]["b64_json"], "completed-before-stop")
            self.assertEqual(context._responses, {})

    def test_a_slow_foreign_close_does_not_block_cancel_or_response_registration(self):
        started, release = threading.Event(), threading.Event()
        class SlowClose:
            def close(self):
                started.set()
                release.wait(3)
        context = ImageTaskContext()
        response = SlowClose()
        context.add_response(response)
        try:
            start = time.monotonic()
            context.cancel()
            self.assertLess(time.monotonic() - start, .5)
            self.assertTrue(started.wait(1))
            start = time.monotonic()
            with self.assertRaises(ImageGenerationCancelled):
                context.add_response(SlowClose())
            self.assertLess(time.monotonic() - start, .5)
        finally:
            release.set()

    def test_log_transport_strategy_distinguishes_managed_direct_from_configured_route(self):
        managed = urllib.request.Request("https://39.105.74.222/openai/v1/images/generations")
        self.assertEqual(transport_details(managed), {"transport": "direct", "proxyStrategy": "direct", "effectiveProxy": ""})
        self.assertEqual(transport_details(urllib.request.Request("https://api.openai.com/v1/images/generations"))["transport"], "configured")


if __name__ == "__main__":
    unittest.main()
