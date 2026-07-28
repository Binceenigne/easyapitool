import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image

import image_editor


class ImageEditorTests(unittest.TestCase):
    def test_client_posts_all_supported_generation_parameters_as_json(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"data":[{"b64_json":"aW1hZ2U="}]}'

        opener = SimpleNamespace(open=Mock(return_value=FakeResponse()))
        fields = {
            "model": "gpt-image-2",
            "prompt": "Generate a test image",
            "size": "768x1024",
            "quality": "low",
            "output_format": "webp",
            "output_compression": 73,
            "background": "opaque",
            "moderation": "low",
            "stream": False,
        }

        with __import__("unittest.mock").mock.patch.object(
            image_editor.ImageGenerationClient,
            "_opener",
            return_value=opener,
        ):
            result = image_editor.ImageGenerationClient.generate_image(
                "https://example.test/v1",
                "secret",
                fields,
            )

        request = opener.open.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://example.test/v1/images/generations")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertEqual(payload, fields)
        self.assertNotIn("n", payload)
        self.assertNotIn("user", payload)
        self.assertEqual(result["data"][0]["b64_json"], "aW1hZ2U=")

    def test_edit_images_requests_event_stream_when_streaming(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "reference.png"
            Image.new("RGB", (8, 8), "red").save(image_path)

            class FakeResponse:
                headers = {"Content-Type": "text/event-stream"}

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def __iter__(self):
                    return iter([b'data: {"type":"image_generation.completed","b64_json":"aW1hZ2U="}\n'])

            opener = SimpleNamespace(open=Mock(return_value=FakeResponse()))
            with __import__("unittest.mock").mock.patch.object(
                image_editor.ImageGenerationClient,
                "_opener",
                return_value=opener,
            ):
                image_editor.ImageGenerationClient.edit_images(
                    "https://example.test/v1",
                    "secret",
                    (image_path,),
                    {"prompt": "Combine", "stream": True, "partial_images": 1},
                )

            request = opener.open.call_args.args[0]
            self.assertEqual(request.headers["Accept"], "text/event-stream")

    def test_service_routes_without_references_to_generations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result_image = root / "result.png"
            Image.new("RGB", (8, 8), "red").save(result_image, format="PNG")
            encoded = base64.b64encode(result_image.read_bytes()).decode("ascii")
            client = SimpleNamespace(
                generate_image=Mock(
                    return_value={
                        "data": [{"b64_json": encoded}],
                        "output_format": "webp",
                        "quality": "auto",
                        "size": "1254x1254",
                        "background": "opaque",
                    }
                ),
                edit_images=Mock(),
            )
            service = image_editor.ImageGenerationService(client)
            request = image_editor.prepare_image_generation(
                "Generate a test image",
                [],
                {
                    "size": "768x1024",
                    "quality": "low",
                    "outputFormat": "webp",
                    "outputCompression": 73,
                    "background": "opaque",
                    "moderation": "low",
                },
            )

            result = service.generate(
                "https://example.test/v1",
                "secret",
                request,
                root / "output",
            )

            self.assertTrue(Path(result["path"]).is_file())
            self.assertEqual((result["width"], result["height"]), (8, 8))
            self.assertEqual(result["requestedSize"], "768x1024")
            self.assertEqual(result["actualSize"], "1254x1254")
            self.assertEqual(result["requestedQuality"], "low")
            self.assertEqual(result["quality"], "auto")
            self.assertEqual(result["referenceCount"], 0)
            self.assertEqual(request.fields["output_format"], "webp")
            self.assertEqual(request.fields["output_compression"], "73")
            self.assertEqual(request.fields["background"], "opaque")
            self.assertEqual(request.fields["moderation"], "low")
            client.generate_image.assert_called_once()
            client.edit_images.assert_not_called()

    def test_prepare_accepts_stream_and_partial_images(self):
        request = image_editor.prepare_image_generation(
            "Generate with previews",
            [],
            {"stream": True, "partialImages": 1},
        )

        self.assertTrue(request.stream)
        self.assertEqual(request.partial_images, 1)
        self.assertTrue(request.fields["stream"])
        self.assertEqual(request.fields["partial_images"], 1)

    def test_stream_parser_reports_actual_partial_count(self):
        final_image = base64.b64encode(b"image-bytes").decode("ascii")
        lines = [
            b'data: {"type":"image_generation.partial_image","b64_json":"part-1"}\n',
            b'data: {"type":"image_generation.partial_image","b64_json":"part-2"}\n',
            (
                'data: {"type":"image_generation.completed","b64_json":"'
                + final_image
                + '","output_format":"webp","quality":"auto","size":"1254x1254"}\n'
            ).encode("utf-8"),
            b"data: [DONE]\n",
        ]

        result = image_editor.ImageGenerationClient._read_response(lines, stream=True)

        self.assertEqual(result["partial_images_received"], 2)
        self.assertEqual(result["output_format"], "webp")
        self.assertEqual(result["quality"], "auto")


if __name__ == "__main__":
    unittest.main()