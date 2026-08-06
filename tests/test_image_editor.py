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
    def test_session_branches_use_parent_depth_and_isolate_sibling_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = image_editor.ImageSessionStore(root / "pictures")
            source_paths = {}
            for name, color in (
                ("root-input", "white"),
                ("root-output", "red"),
                ("branch-a-output", "green"),
                ("branch-b-output", "blue"),
            ):
                path = root / f"{name}.png"
                Image.new("RGB", (16, 16), color).save(path)
                source_paths[name] = path

            root_round = store.begin_round(
                "branch-session",
                "set-root",
                "创建基础场景",
                1,
                1,
                {"inputReferencePaths": [str(source_paths["root-input"])]},
            )
            root_inputs = store.register_assets(
                "branch-session",
                [source_paths["root-input"]],
                "set-root",
                "input",
            )
            root_result = store.persist_result(
                "branch-session",
                "set-root",
                0,
                source_paths["root-output"],
                {"width": 16, "height": 16, "format": "png"},
            )
            store.complete_round("branch-session", "set-root")

            branch_a = store.begin_round(
                "branch-session", "set-a", "分支 A", 1, 1, {}, parent_set_id="set-root"
            )
            branch_a_result = store.persist_result(
                "branch-session",
                "set-a",
                0,
                source_paths["branch-a-output"],
                {"width": 16, "height": 16, "format": "png"},
            )
            store.complete_round("branch-session", "set-a")

            branch_b = store.begin_round(
                "branch-session", "set-b", "分支 B", 1, 1, {}, parent_set_id="set-root"
            )
            branch_b_result = store.persist_result(
                "branch-session",
                "set-b",
                0,
                source_paths["branch-b-output"],
                {"width": 16, "height": 16, "format": "png"},
            )
            store.complete_round("branch-session", "set-b")
            branch_a_child = store.begin_round(
                "branch-session", "set-a-child", "继续分支 A", 1, 1, {}, parent_set_id="set-a"
            )

            self.assertEqual(
                [
                    root_round["roundNumber"],
                    branch_a["roundNumber"],
                    branch_b["roundNumber"],
                    branch_a_child["roundNumber"],
                ],
                [1, 2, 2, 3],
            )

            context_a = store.continuation_context("branch-session", "set-a")
            context_b = store.continuation_context("branch-session", "set-b")
            assets_a = {asset["assetId"] for asset in context_a["assets"]}
            assets_b = {asset["assetId"] for asset in context_b["assets"]}
            shared_assets = {root_inputs[0]["assetId"], root_result["assetId"]}

            self.assertEqual([item["setId"] for item in context_a["history"]], ["set-root", "set-a"])
            self.assertEqual([item["setId"] for item in context_b["history"]], ["set-root", "set-b"])
            self.assertTrue(shared_assets <= assets_a)
            self.assertTrue(shared_assets <= assets_b)
            self.assertIn(branch_a_result["assetId"], assets_a)
            self.assertNotIn(branch_a_result["assetId"], assets_b)
            self.assertIn(branch_b_result["assetId"], assets_b)
            self.assertNotIn(branch_b_result["assetId"], assets_a)

            branch_b_asset_path = Path(
                next(
                    asset["path"]
                    for asset in context_b["assets"]
                    if asset["assetId"] == branch_b_result["assetId"]
                )
            )
            self.assertTrue(store.delete_set("branch-session", "set-b"))
            context_a_after_delete = store.continuation_context("branch-session", "set-a")
            self.assertEqual(
                {asset["assetId"] for asset in context_a_after_delete["assets"]},
                {*shared_assets, branch_a_result["assetId"]},
            )
            self.assertFalse(branch_b_asset_path.exists())

    def test_legacy_sequential_round_numbers_are_restored_as_tree_depths(self):
        with tempfile.TemporaryDirectory() as temp:
            store = image_editor.ImageSessionStore(Path(temp) / "pictures")
            store.begin_round("legacy-tree", "set-root", "根", 1, 0, {})
            store.begin_round(
                "legacy-tree", "set-a", "A", 1, 0, {}, parent_set_id="set-root"
            )
            store.begin_round(
                "legacy-tree", "set-b", "B", 1, 0, {}, parent_set_id="set-root"
            )
            store.begin_round(
                "legacy-tree", "set-a-child", "A3", 1, 0, {}, parent_set_id="set-a"
            )
            manifest_path = store.root / "legacy-tree" / "manifest.json"
            legacy_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            legacy_manifest["schemaVersion"] = 2
            for sequential_number, round_data in enumerate(legacy_manifest["rounds"], 1):
                round_data["roundNumber"] = sequential_number
            manifest_path.write_text(json.dumps(legacy_manifest), encoding="utf-8")

            restored = {
                item["setId"]: item["roundNumber"]
                for item in store.list_sets()
            }
            branch_history = store.continuation_context(
                "legacy-tree", "set-a-child"
            )["history"]
            branch_b_child = store.begin_round(
                "legacy-tree",
                "set-b-child",
                "B3",
                1,
                0,
                {},
                parent_set_id="set-b",
            )
            migrated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(
                restored,
                {"set-root": 1, "set-a": 2, "set-b": 2, "set-a-child": 3},
            )
            self.assertEqual(
                [item["roundNumber"] for item in branch_history],
                [1, 2, 3],
            )
            self.assertEqual(branch_b_child["roundNumber"], 3)
            self.assertEqual(migrated_manifest["schemaVersion"], 3)
            self.assertEqual(
                [item["roundNumber"] for item in migrated_manifest["rounds"]],
                [1, 2, 2, 3, 3],
            )

    def test_begin_round_rejects_missing_parent_without_changing_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            store = image_editor.ImageSessionStore(Path(temp) / "pictures")
            store.begin_round("parent-check", "set-root", "根", 1, 0, {})
            manifest_path = store.root / "parent-check" / "manifest.json"
            manifest_before = manifest_path.read_bytes()

            with self.assertRaisesRegex(ValueError, "来源轮次不存在"):
                store.begin_round(
                    "parent-check",
                    "set-child",
                    "无效续作",
                    1,
                    0,
                    {},
                    parent_set_id="set-missing",
                )

            self.assertEqual(manifest_path.read_bytes(), manifest_before)
            self.assertFalse(any(store._session_dir("parent-check").glob("*set-child*")))

    def test_reasoning_usage_rejects_legacy_unattributed_cost(self):
        legacy_usage = image_editor.ImageSessionStore._reasoning_usage(
            {
                "inputTokens": 6000,
                "outputTokens": 1828,
                "totalTokens": 7828,
                "callCount": 2,
                "costUsd": 1.487722,
                "hasTokenUsage": True,
                "hasCost": True,
            }
        )

        self.assertEqual(legacy_usage["costUsd"], 0)
        self.assertEqual(legacy_usage["costedCallCount"], 0)
        self.assertEqual(legacy_usage["costSource"], "")
        self.assertFalse(legacy_usage["hasCost"])

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
                        "usage": {
                            "input_tokens": 120,
                            "output_tokens": 240,
                        },
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
            self.assertEqual(result["operation"], "generate")
            self.assertEqual(result["transportOperation"], "generate")
            self.assertEqual(
                result["usage"],
                {"input_tokens": 120, "output_tokens": 240},
            )
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

    def test_semantic_generate_can_keep_reference_images(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference_path = root / "character.png"
            Image.new("RGB", (8, 8), "red").save(reference_path)
            encoded = base64.b64encode(reference_path.read_bytes()).decode("ascii")
            client = SimpleNamespace(
                generate_image=Mock(),
                edit_images=Mock(return_value={"data": [{"b64_json": encoded}]}),
            )
            request = image_editor.prepare_image_generation(
                "Create a new battle scene with this character",
                [str(reference_path)],
                {"operation": "generate"},
            )

            result = image_editor.ImageGenerationService(client).generate(
                "https://example.test/v1",
                "secret",
                request,
                root / "output",
            )

        self.assertEqual(request.operation, "generate")
        self.assertEqual(request.image_paths, (reference_path.resolve(),))
        self.assertEqual(result["operation"], "generate")
        self.assertEqual(result["transportOperation"], "edit")
        client.edit_images.assert_called_once()
        client.generate_image.assert_not_called()

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
            Image.new("RGB", (8, 4), "blue").save(partial_buffer, format="PNG")
            Image.new("RGB", (8, 8), "green").save(final_buffer, format="PNG")
            partial_encoded = base64.b64encode(partial_buffer.getvalue()).decode("ascii")
            final_encoded = base64.b64encode(final_buffer.getvalue()).decode("ascii")

            def generate_image(_base_url, _secret, _fields, on_partial=None, **_kwargs):
                on_partial(partial_encoded, 1)
                on_partial(partial_encoded, 4)
                return {"data": [{"b64_json": final_encoded}], "partial_images_received": 4}

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
            self.assertEqual([event["partialIndex"] for event in partial_events], [1, 4])
            self.assertTrue(all(event["partialTotal"] == 3 for event in partial_events))
            self.assertTrue(all(Path(event["path"]).is_file() for event in partial_events))
            self.assertTrue(any(root.glob("partials/*-4.png")))
            self.assertTrue(partial_events[0]["uri"].startswith("file:"))
            self.assertTrue(partial_events[0]["previewUri"].startswith("data:image/jpeg;base64,"))
            self.assertTrue(all(event["width"] == 8 for event in partial_events))
            self.assertTrue(all(event["height"] == 4 for event in partial_events))
            self.assertTrue(result["previewUri"].startswith("data:image/jpeg;base64,"))

    def test_service_persists_partial_without_frontend_callback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            partial_buffer = io.BytesIO()
            final_buffer = io.BytesIO()
            Image.new("RGB", (4, 4), "yellow").save(partial_buffer, format="PNG")
            Image.new("RGB", (8, 8), "green").save(final_buffer, format="PNG")
            partial_encoded = base64.b64encode(partial_buffer.getvalue()).decode("ascii")
            final_encoded = base64.b64encode(final_buffer.getvalue()).decode("ascii")

            def generate_image(_base_url, _secret, _fields, on_partial=None, **_kwargs):
                on_partial(partial_encoded, 1)
                return {"data": [{"b64_json": final_encoded}]}

            service = image_editor.ImageGenerationService(
                SimpleNamespace(generate_image=generate_image, edit_images=Mock())
            )
            request = image_editor.prepare_image_generation(
                "Generate with saved process image",
                [],
                {"stream": True, "partialImages": 3},
            )

            result = service.generate(
                "https://example.test/v1",
                "secret",
                request,
                root,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(len(list(root.glob("partials/*-1.png"))), 1)

    def test_session_store_persists_restores_and_deletes_rounds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.png"
            Image.new("RGB", (32, 24), "purple").save(source)
            web_reference = root / "web-reference.jpg"
            Image.new("RGB", (24, 32), "silver").save(web_reference, format="JPEG")
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
                {
                    "size": "2048x2048",
                    "quality": "high",
                    "outputPreset": "large",
                    "reasoningMode": "high",
                    "reasoningSummary": "先分析构图与灯光。",
                    "reasoningDurationMs": 12_345,
                    "reasoningUsage": {
                        "inputTokens": 900,
                        "outputTokens": 300,
                        "totalTokens": 1200,
                        "callCount": 2,
                        "costUsd": 0.01875,
                        "costedCallCount": 2,
                        "costSource": "response",
                        "hasTokenUsage": True,
                        "hasCost": True,
                    },
                    "webSearchEnabled": True,
                    "webSearchUsed": True,
                    "webSearchFailed": False,
                    "webSearchResultCount": 4,
                    "webReferenceCount": 1,
                },
                parent_set_id="set-1",
            )
            second_result = store.persist_result(
                "session-1",
                "set-2",
                0,
                source,
                {"width": 32, "height": 24, "format": "png", "actualSize": "32x24"},
            )
            persisted_references = store.persist_web_references(
                "session-1",
                "set-2",
                [{
                    "id": "webref-1",
                    "title": "Neon reference",
                    "caption": "Silver neon geometry",
                    "provider": "Bing Images",
                    "sourceUrl": "https://example.test/source",
                    "imageUrl": "https://example.test/image.jpg",
                    "path": str(web_reference),
                }],
            )
            store.complete_round("session-1", "set-2")

            manifest_path = root / "pictures" / "sessions" / "session-1" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            restored = store.list_sets()

            self.assertEqual((first["roundNumber"], second["roundNumber"]), (1, 2))
            self.assertEqual(manifest["roundCount"], 2)
            self.assertEqual([item["prompt"] for item in manifest["rounds"]], ["初始生成", "追加霓虹灯"])
            self.assertEqual(manifest["rounds"][1]["parentSetId"], "set-1")
            self.assertTrue(manifest["rounds"][1]["options"]["webSearchEnabled"])
            self.assertFalse(manifest["rounds"][1]["options"]["webSearchFailed"])
            self.assertEqual(manifest["rounds"][1]["options"]["webSearchResultCount"], 4)
            self.assertEqual(manifest["rounds"][1]["options"]["webReferenceCount"], 1)
            self.assertEqual(manifest["rounds"][1]["options"]["reasoningDurationMs"], 12_345)
            self.assertEqual(manifest["rounds"][1]["options"]["reasoningUsage"]["totalTokens"], 1200)
            self.assertEqual(manifest["rounds"][1]["options"]["reasoningUsage"]["callCount"], 2)
            self.assertAlmostEqual(manifest["rounds"][1]["options"]["reasoningUsage"]["costUsd"], 0.01875)
            self.assertEqual(manifest["rounds"][1]["options"]["reasoningUsage"]["costSource"], "response")
            self.assertEqual(manifest["rounds"][1]["webReferences"][0]["title"], "Neon reference")
            self.assertTrue(manifest["rounds"][1]["webReferences"][0]["assetId"].startswith("asset-"))
            self.assertIn(
                manifest["rounds"][1]["webReferences"][0]["assetId"],
                manifest["rounds"][1]["options"]["inputAssetIds"],
            )
            self.assertTrue(Path(persisted_references[0]["path"]).is_file())
            self.assertTrue(Path(persisted_references[0]["previewPath"]).is_file())
            self.assertTrue(Path(first_result["path"]).is_file())
            self.assertTrue(Path(first_result["previewPath"]).is_file())
            originals = store.original_paths_for_set("session-1", "set-2")
            self.assertEqual(len(originals), 1)
            self.assertEqual(Path(originals[0]["path"]).resolve(), Path(second_result["path"]).resolve())
            self.assertNotIn("preview", originals[0]["path"])
            self.assertTrue(first_result["assetId"].startswith("asset-"))
            self.assertEqual(manifest["rounds"][0]["items"][0]["assetId"], first_result["assetId"])
            self.assertEqual(manifest["assets"][0]["assetId"], first_result["assetId"])
            self.assertEqual([item["setId"] for item in restored], ["set-2", "set-1"])
            self.assertEqual(restored[0]["items"][0]["uri"], restored[0]["items"][0]["result"]["uri"])
            self.assertTrue(restored[0]["items"][0]["result"]["assetId"].startswith("asset-"))
            self.assertTrue(restored[0]["items"][0]["uri"].startswith("file:"))
            self.assertEqual(restored[0]["items"][0]["result"]["previewUri"], "")
            self.assertTrue(Path(restored[0]["items"][0]["previewPath"]).is_file())
            self.assertTrue(restored[0]["webSearchUsed"])
            self.assertFalse(restored[0]["webSearchFailed"])
            self.assertEqual(restored[0]["webSearchResultCount"], 4)
            self.assertEqual(restored[0]["reasoningDurationMs"], 12_345)
            self.assertEqual(restored[0]["reasoningUsage"]["totalTokens"], 1200)
            self.assertEqual(restored[0]["reasoningUsage"]["callCount"], 2)
            self.assertAlmostEqual(restored[0]["reasoningUsage"]["costUsd"], 0.01875)
            self.assertEqual(restored[0]["reasoningUsage"]["costSource"], "response")
            self.assertEqual(restored[0]["webReferenceCount"], 1)
            self.assertEqual(restored[0]["webReferences"][0]["provider"], "Bing Images")
            self.assertTrue(restored[0]["webReferences"][0]["assetId"].startswith("asset-"))
            self.assertTrue(Path(restored[0]["webReferences"][0]["previewPath"]).is_file())

            with self.assertRaisesRegex(ValueError, "先删除后续轮次"):
                store.delete_set("session-1", "set-1")
            self.assertFalse(store.delete_set("session-1", "missing"))
            self.assertTrue(store.delete_set("session-1", "set-2"))
            remaining_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(remaining_manifest["roundCount"], 1)
            self.assertTrue(store.delete_set("session-1", "set-1"))
            self.assertFalse(manifest_path.parent.exists())

    def test_legacy_context_infers_edit_operation_from_reference_count(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = image_editor.ImageSessionStore(root / "pictures")
            store.begin_round(
                "session-legacy",
                "set-legacy",
                "旧版编辑",
                1,
                1,
                {"originalPrompt": "修改旧图"},
            )
            manifest_path = store.root / "session-legacy" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["rounds"][0]["options"].pop("operation", None)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            context = store.continuation_context("session-legacy", "set-legacy")

            self.assertEqual(context["history"][0]["operation"], "edit")

    def test_session_store_allows_removing_stale_running_round(self):
        with tempfile.TemporaryDirectory() as temp:
            store = image_editor.ImageSessionStore(Path(temp) / "pictures")
            store.begin_round("session-stale", "set-stale", "未完成", 1, 0, {})

            self.assertTrue(store.delete_set("session-stale", "set-stale"))
            self.assertFalse((store.root / "session-stale").exists())

    def test_deleting_round_prunes_its_unused_assets_but_keeps_reused_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            unused_input = root / "unused.png"
            shared_output = root / "shared.png"
            Image.new("RGB", (8, 8), "green").save(unused_input)
            Image.new("RGB", (8, 8), "purple").save(shared_output)
            store = image_editor.ImageSessionStore(root / "pictures")
            store.begin_round(
                "session-prune",
                "set-1",
                "第一轮",
                1,
                0,
                {},
            )
            first_result = store.persist_result(
                "session-prune",
                "set-1",
                0,
                shared_output,
                {"width": 8, "height": 8, "format": "png"},
            )
            store.complete_round("session-prune", "set-1")
            store.begin_round(
                "session-prune",
                "set-2",
                "第二轮",
                1,
                1,
                {
                    "inputAssetIds": [first_result["assetId"]],
                    "inputReferencePaths": [str(unused_input)],
                },
                parent_set_id="set-1",
            )
            store.persist_result(
                "session-prune",
                "set-2",
                0,
                shared_output,
                {"width": 8, "height": 8, "format": "png"},
            )
            store.complete_round("session-prune", "set-2")
            before = store.continuation_context("session-prune", "set-2")
            unused_asset = next(
                asset
                for asset in before["assets"]
                if asset["assetId"] != first_result["assetId"]
            )

            self.assertTrue(store.delete_set("session-prune", "set-2"))
            after = store.continuation_context("session-prune", "set-1")

            self.assertEqual(
                [asset["assetId"] for asset in after["assets"]],
                [first_result["assetId"]],
            )
            self.assertFalse(Path(unused_asset["path"]).exists())
            self.assertTrue(Path(after["assets"][0]["path"]).is_file())

    def test_delete_keeps_manifest_and_files_when_atomic_write_permanently_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_output = root / "first.png"
            leaf_output = root / "leaf.png"
            Image.new("RGB", (8, 8), "purple").save(first_output)
            Image.new("RGB", (8, 8), "green").save(leaf_output)
            store = image_editor.ImageSessionStore(root / "pictures")
            store.begin_round("session-atomic", "set-1", "第一轮", 1, 0, {})
            store.persist_result(
                "session-atomic",
                "set-1",
                0,
                first_output,
                {"width": 8, "height": 8, "format": "png"},
            )
            store.complete_round("session-atomic", "set-1")
            store.begin_round(
                "session-atomic",
                "set-2",
                "第二轮",
                1,
                0,
                {},
                parent_set_id="set-1",
            )
            leaf_result = store.persist_result(
                "session-atomic",
                "set-2",
                0,
                leaf_output,
                {"width": 8, "height": 8, "format": "png"},
            )
            store.complete_round("session-atomic", "set-2")
            manifest_path = store.root / "session-atomic" / "manifest.json"
            manifest_before = manifest_path.read_bytes()
            manifest = json.loads(manifest_before)
            leaf_round_dir = manifest_path.parent / manifest["rounds"][1]["directory"]
            leaf_asset_path = Path(
                next(
                    asset["path"]
                    for asset in store.continuation_context("session-atomic", "set-2")["assets"]
                    if asset["assetId"] == leaf_result["assetId"]
                )
            )

            with __import__("unittest.mock").mock.patch.object(
                image_editor.os,
                "replace",
                side_effect=PermissionError(5, "permanently locked"),
            ), __import__("unittest.mock").mock.patch.object(image_editor.time, "sleep"):
                with self.assertRaises(PermissionError):
                    store.delete_set("session-atomic", "set-2")

            self.assertEqual(manifest_path.read_bytes(), manifest_before)
            self.assertTrue(leaf_round_dir.is_dir())
            self.assertTrue(Path(leaf_result["path"]).is_file())
            self.assertTrue(leaf_asset_path.is_file())
            self.assertEqual(
                [item["setId"] for item in store.list_sets()],
                ["set-2", "set-1"],
            )

    def test_delete_last_round_keeps_session_when_atomic_write_permanently_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "only.png"
            Image.new("RGB", (8, 8), "purple").save(output)
            store = image_editor.ImageSessionStore(root / "pictures")
            store.begin_round("session-last", "set-only", "唯一轮次", 1, 0, {})
            result = store.persist_result(
                "session-last",
                "set-only",
                0,
                output,
                {"width": 8, "height": 8, "format": "png"},
            )
            store.complete_round("session-last", "set-only")
            manifest_path = store.root / "session-last" / "manifest.json"
            manifest_before = manifest_path.read_bytes()

            with __import__("unittest.mock").mock.patch.object(
                image_editor.os,
                "replace",
                side_effect=PermissionError(5, "permanently locked"),
            ), __import__("unittest.mock").mock.patch.object(image_editor.time, "sleep"):
                with self.assertRaises(PermissionError):
                    store.delete_set("session-last", "set-only")

            self.assertEqual(manifest_path.read_bytes(), manifest_before)
            self.assertTrue(Path(result["path"]).is_file())
            self.assertEqual(
                [item["setId"] for item in store.list_sets()],
                ["set-only"],
            )

    def test_discarding_failed_round_source_removes_only_uncommitted_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            committed = root / "committed.png"
            pending = root / "pending.png"
            Image.new("RGB", (8, 8), "purple").save(committed)
            Image.new("RGB", (8, 8), "green").save(pending)
            store = image_editor.ImageSessionStore(root / "pictures")
            store.begin_round("session-rollback", "set-1", "第一轮", 1, 0, {})
            committed_result = store.persist_result(
                "session-rollback",
                "set-1",
                0,
                committed,
                {"width": 8, "height": 8, "format": "png"},
            )
            store.complete_round("session-rollback", "set-1")
            pending_asset = store.register_assets(
                "session-rollback",
                [str(pending)],
                "set-failed",
                "input",
            )[0]

            store.discard_asset_source("session-rollback", "set-failed")
            context = store.continuation_context("session-rollback", "set-1")

            self.assertEqual(
                [asset["assetId"] for asset in context["assets"]],
                [committed_result["assetId"]],
            )
            self.assertFalse(Path(pending_asset["original"]).exists() if "original" in pending_asset else Path(pending_asset["path"]).exists())

    def test_session_store_retries_transient_windows_manifest_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            store = image_editor.ImageSessionStore(Path(temp) / "pictures")
            real_replace = image_editor.os.replace
            attempts = 0

            def replace_with_transient_lock(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError(5, "temporarily locked")
                return real_replace(source, destination)

            with __import__("unittest.mock").mock.patch.object(
                image_editor.os,
                "replace",
                side_effect=replace_with_transient_lock,
            ), __import__("unittest.mock").mock.patch.object(image_editor.time, "sleep") as sleep:
                store.begin_round("session-lock", "set-lock", "测试写入", 1, 0, {})

            self.assertEqual(attempts, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.04, 0.08])
            self.assertTrue((store.root / "session-lock" / "manifest.json").is_file())

    def test_begin_round_never_overwrites_unreadable_or_corrupt_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            store = image_editor.ImageSessionStore(Path(temp) / "pictures")
            store.begin_round("session-read", "set-1", "第一轮", 1, 0, {})
            manifest_path = store.root / "session-read" / "manifest.json"
            manifest_before = manifest_path.read_bytes()

            real_read_text = Path.read_text

            def read_with_lock(path, *args, **kwargs):
                if path == manifest_path:
                    raise PermissionError(5, "temporarily unreadable")
                return real_read_text(path, *args, **kwargs)

            with __import__("unittest.mock").mock.patch.object(
                Path,
                "read_text",
                autospec=True,
                side_effect=read_with_lock,
            ):
                with self.assertRaises(PermissionError):
                    store.begin_round("session-read", "set-2", "第二轮", 1, 0, {})

            self.assertEqual(manifest_path.read_bytes(), manifest_before)

            corrupt_manifest = b'{"sessionId":"session-read","rounds":['
            manifest_path.write_bytes(corrupt_manifest)
            with self.assertRaisesRegex(RuntimeError, "清单已损坏"):
                store.begin_round("session-read", "set-2", "第二轮", 1, 0, {})
            self.assertEqual(manifest_path.read_bytes(), corrupt_manifest)

    def test_list_sets_skips_corrupt_session_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as temp:
            store = image_editor.ImageSessionStore(Path(temp) / "pictures")
            store.begin_round("session-good", "set-good", "正常会话", 1, 0, {})
            corrupt_path = store.root / "session-corrupt" / "manifest.json"
            corrupt_path.parent.mkdir(parents=True)
            corrupt_bytes = b"not-json"
            corrupt_path.write_bytes(corrupt_bytes)

            restored = store.list_sets()

            self.assertEqual([item["setId"] for item in restored], ["set-good"])
            self.assertEqual(corrupt_path.read_bytes(), corrupt_bytes)

    def test_session_store_builds_deduplicated_asset_pool_and_history_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character.png"
            Image.new("RGB", (24, 24), "purple").save(character)
            store = image_editor.ImageSessionStore(root / "pictures")
            store.begin_round(
                "world-session",
                "set-1",
                "角色设定图",
                1,
                1,
                {
                    "originalPrompt": "设计紫色披风角色",
                    "reasoningSummary": "确定角色轮廓与服装标识。",
                    "inputReferencePaths": [str(character)],
                },
            )
            store.persist_result(
                "world-session",
                "set-1",
                0,
                character,
                {"width": 24, "height": 24, "format": "png"},
            )
            store.complete_round("world-session", "set-1")
            context = store.continuation_context("world-session", "set-1")
            asset_ids = [asset["assetId"] for asset in context["assets"]]

            self.assertEqual(len(asset_ids), 1)
            self.assertEqual(context["parentOutputAssetIds"], asset_ids)
            self.assertEqual(context["history"][0]["userPrompt"], "设计紫色披风角色")
            self.assertEqual(
                context["history"][0]["reasoningSummary"],
                "确定角色轮廓与服装标识。",
            )
            self.assertEqual(context["history"][0]["inputAssetIds"], asset_ids)
            self.assertEqual(context["history"][0]["outputAssetIds"], asset_ids)
            self.assertTrue(Path(context["assets"][0]["path"]).is_file())

            store.update_asset_descriptions(
                "world-session",
                {asset_ids[0]: "紫色披风、银色护甲、短发角色正面设定图"},
            )
            store.update_asset_descriptions(
                "world-session",
                {asset_ids[0]: "不应覆盖首次缓存的第二份描述"},
            )
            refreshed = store.continuation_context("world-session", "set-1")
            self.assertEqual(
                refreshed["assets"][0]["description"],
                "紫色披风、银色护甲、短发角色正面设定图",
            )
            self.assertTrue(refreshed["assets"][0]["describedAt"])


if __name__ == "__main__":
    unittest.main()