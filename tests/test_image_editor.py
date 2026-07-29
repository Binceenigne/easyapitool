import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image

import image_editor


class ImageEditorTests(unittest.TestCase):
    def test_output_presets_use_fixed_png_api_requests(self):
        self.assertEqual(
            image_editor.OUTPUT_PRESETS,
            {
                "lossless": ("png", None),
                "large": ("jpeg", 90),
                "medium": ("jpeg", 75),
                "small": ("jpeg", 55),
            },
        )
        for preset, (output_format, _quality) in image_editor.OUTPUT_PRESETS.items():
            request = image_editor.prepare_image_generation(
                "Generate an image",
                [],
                {"outputPreset": preset},
            )
            self.assertEqual(request.fields["output_format"], "png")
            self.assertEqual(request.output_format, output_format)
            self.assertEqual(request.output_preset, preset)

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
            "output_format": "png",
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
                    "outputPreset": "medium",
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
            self.assertEqual(request.fields["output_format"], "png")
            self.assertNotIn("output_compression", request.fields)
            self.assertEqual(request.fields["background"], "opaque")
            self.assertEqual(request.fields["moderation"], "low")
            self.assertEqual(result["format"], "jpeg")
            self.assertEqual(result["outputPreset"], "medium")
            self.assertEqual(Path(result["path"]).suffix, ".jpg")
            with Image.open(result["path"]) as saved_image:
                self.assertEqual(saved_image.format, "JPEG")
            client.generate_image.assert_called_once()
            client.edit_images.assert_not_called()

    def test_jpeg_presets_use_backend_compression_quality(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = io.BytesIO()
            Image.new("RGBA", (64, 64), (20, 40, 60, 128)).save(source, format="PNG")
            encoded = base64.b64encode(source.getvalue()).decode("ascii")
            client = SimpleNamespace(
                generate_image=Mock(return_value={"data": [{"b64_json": encoded}]}),
                edit_images=Mock(),
            )
            service = image_editor.ImageGenerationService(client)

            for preset, expected_quality in {"large": 90, "medium": 75, "small": 55}.items():
                request = image_editor.prepare_image_generation(
                    "Generate an image",
                    [],
                    {"outputPreset": preset},
                )
                with __import__("unittest.mock").mock.patch.object(
                    Image.Image,
                    "save",
                    autospec=True,
                    wraps=Image.Image.save,
                ) as save_image:
                    result = service.generate(
                        "https://example.test/v1",
                        "secret",
                        request,
                        root / preset,
                    )

                jpeg_calls = [
                    call for call in save_image.call_args_list
                    if call.kwargs.get("format") == "JPEG"
                ]
                self.assertEqual(
                    [call.kwargs["quality"] for call in jpeg_calls],
                    [expected_quality, 78],
                )
                self.assertEqual(result["format"], "jpeg")
                self.assertEqual(result["outputPreset"], preset)

    def test_transparent_background_requires_lossless_output(self):
        with self.assertRaisesRegex(ValueError, "透明背景只能使用无损 PNG 输出"):
            image_editor.prepare_image_generation(
                "Generate a transparent image",
                [],
                {"background": "transparent", "outputPreset": "large"},
            )

        request = image_editor.prepare_image_generation(
            "Generate a transparent image",
            [],
            {"background": "transparent", "outputPreset": "lossless"},
        )
        self.assertEqual(request.fields["background"], "transparent")
        self.assertEqual(request.fields["output_format"], "png")
        self.assertEqual(request.output_format, "png")

    def test_moderation_defaults_to_low(self):
        request = image_editor.prepare_image_generation("Generate an image", [], {})

        self.assertEqual(request.fields["moderation"], "low")

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

        partials = []
        result = image_editor.ImageGenerationClient._read_response(
            lines,
            stream=True,
            on_partial=lambda image_data, index: partials.append((index, image_data)),
        )

        self.assertEqual(result["partial_images_received"], 2)
        self.assertEqual(partials, [(1, "part-1"), (2, "part-2")])
        self.assertEqual(result["output_format"], "webp")
        self.assertEqual(result["quality"], "auto")

    def test_stream_parser_accepts_nested_completed_image(self):
        final_image = base64.b64encode(b"nested-final").decode("ascii")
        lines = [
            (
                'data: {"type":"image_generation.completed","data":[{"b64_json":"'
                + final_image
                + '"}],"output_format":"png"}\n'
            ).encode("utf-8"),
            b"data: [DONE]\n",
        ]

        result = image_editor.ImageGenerationClient._read_response(lines, stream=True)

        self.assertEqual(result["data"][0]["b64_json"], final_image)
        self.assertEqual(result["output_format"], "png")

    def test_stream_parser_accepts_plain_json_final_payload(self):
        final_image = base64.b64encode(b"plain-final").decode("ascii")
        lines = [
            ('{"data":[{"b64_json":"' + final_image + '"}]}\n').encode("utf-8")
        ]

        result = image_editor.ImageGenerationClient._read_response(lines, stream=True)

        self.assertEqual(result["data"][0]["b64_json"], final_image)

    def test_stream_parser_accepts_named_sse_completed_event(self):
        final_image = base64.b64encode(b"named-final").decode("ascii")
        lines = [
            b"event: image_generation.completed\n",
            ('data: {"data":[{"b64_json":"' + final_image + '"}]}\n').encode("utf-8"),
            b"\n",
        ]

        result = image_editor.ImageGenerationClient._read_response(lines, stream=True)

        self.assertEqual(result["data"][0]["b64_json"], final_image)

    def test_stream_parser_never_uses_partial_as_final_image(self):
        partial_image = base64.b64encode(b"last-partial").decode("ascii")
        lines = [
            (
                'data: {"type":"image_generation.partial_image","b64_json":"'
                + partial_image
                + '"}\n'
            ).encode("utf-8"),
            b"data: [DONE]\n",
        ]

        with self.assertRaisesRegex(RuntimeError, "未返回最终图片"):
            image_editor.ImageGenerationClient._read_response(lines, stream=True)

    def test_stream_parser_promotes_last_partial_after_explicit_completion(self):
        final_image = base64.b64encode(b"completed-partial").decode("ascii")
        lines = [
            (
                'data: {"type":"image_generation.partial_image","b64_json":"'
                + final_image
                + '"}\n'
            ).encode("utf-8"),
            b'event: image_generation.completed\n',
            b'data: {"usage":{"total_tokens":42}}\n',
            b"\n",
        ]

        result = image_editor.ImageGenerationClient._read_response(lines, stream=True)

        self.assertEqual(result["data"][0]["b64_json"], final_image)
        self.assertEqual(result["usage"], {"total_tokens": 42})

    def test_stream_parser_accepts_named_completion_with_done_payload(self):
        final_image = base64.b64encode(b"named-done-final").decode("ascii")
        lines = [
            (
                'data: {"type":"image_generation.partial_image","b64_json":"'
                + final_image
                + '"}\n'
            ).encode("utf-8"),
            b"event: image_generation.completed\n",
            b"data: [DONE]\n",
            b"\n",
        ]

        result = image_editor.ImageGenerationClient._read_response(lines, stream=True)

        self.assertEqual(result["data"][0]["b64_json"], final_image)

    def test_service_persists_partial_before_returning_final_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            partial_buffer = io.BytesIO()
            final_buffer = io.BytesIO()
            Image.new("RGB", (4, 4), "blue").save(partial_buffer, format="PNG")
            Image.new("RGB", (8, 8), "green").save(final_buffer, format="PNG")
            partial_encoded = base64.b64encode(partial_buffer.getvalue()).decode("ascii")
            final_encoded = base64.b64encode(final_buffer.getvalue()).decode("ascii")

            def generate_image(_base_url, _secret, _fields, on_partial=None, **_kwargs):
                on_partial(partial_encoded, 1)
                return {"data": [{"b64_json": final_encoded}], "partial_images_received": 1}

            service = image_editor.ImageGenerationService(
                SimpleNamespace(generate_image=generate_image, edit_images=Mock())
            )
            request = image_editor.prepare_image_generation(
                "Generate with preview",
                [],
                {"stream": True, "partialImages": 3},
            )
            partial_events = []

            result = service.generate(
                "https://example.test/v1",
                "secret",
                request,
                root,
                on_partial=partial_events.append,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(len(partial_events), 1)
            self.assertEqual(partial_events[0]["partialIndex"], 1)
            self.assertTrue(Path(partial_events[0]["path"]).is_file())
            self.assertTrue(partial_events[0]["uri"].startswith("file:"))
            self.assertTrue(partial_events[0]["previewUri"].startswith("data:image/jpeg;base64,"))
            self.assertTrue(result["previewUri"].startswith("data:image/jpeg;base64,"))

    def test_session_store_persists_restores_and_deletes_rounds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.png"
            Image.new("RGB", (32, 24), "purple").save(source)
            store = image_editor.ImageSessionStore(root / "pictures")

            first = store.begin_round(
                "session-1",
                "set-1",
                "初始生成",
                1,
                0,
                {"size": "1024x1024", "quality": "auto", "outputPreset": "lossless"},
            )
            first_result = store.persist_result(
                "session-1",
                "set-1",
                0,
                source,
                {"width": 32, "height": 24, "format": "png", "actualSize": "32x24"},
            )
            store.complete_round("session-1", "set-1")
            second = store.begin_round(
                "session-1",
                "set-2",
                "追加霓虹灯",
                1,
                1,
                {"size": "2048x2048", "quality": "high", "outputPreset": "large"},
                parent_set_id="set-1",
            )
            store.persist_result(
                "session-1",
                "set-2",
                0,
                source,
                {"width": 32, "height": 24, "format": "png", "actualSize": "32x24"},
            )
            store.complete_round("session-1", "set-2")

            manifest_path = root / "pictures" / "sessions" / "session-1" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            restored = store.list_sets()

            self.assertEqual((first["roundNumber"], second["roundNumber"]), (1, 2))
            self.assertEqual(manifest["roundCount"], 2)
            self.assertEqual([item["prompt"] for item in manifest["rounds"]], ["初始生成", "追加霓虹灯"])
            self.assertEqual(manifest["rounds"][1]["parentSetId"], "set-1")
            self.assertTrue(Path(first_result["path"]).is_file())
            self.assertTrue(Path(first_result["previewPath"]).is_file())
            self.assertEqual([item["setId"] for item in restored], ["set-2", "set-1"])
            self.assertTrue(restored[0]["items"][0]["result"]["previewUri"].startswith("data:image/jpeg;base64,"))

            self.assertTrue(store.delete_set("session-1", "set-1"))
            remaining_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(remaining_manifest["roundCount"], 1)
            self.assertFalse(store.delete_set("session-1", "missing"))
            self.assertTrue(store.delete_set("session-1", "set-2"))
            self.assertFalse(manifest_path.parent.exists())

    def test_session_store_allows_removing_stale_running_round(self):
        with tempfile.TemporaryDirectory() as temp:
            store = image_editor.ImageSessionStore(Path(temp) / "pictures")
            store.begin_round("session-stale", "set-stale", "未完成", 1, 0, {})

            self.assertTrue(store.delete_set("session-stale", "set-stale"))
            self.assertFalse((store.root / "session-stale").exists())


if __name__ == "__main__":
    unittest.main()