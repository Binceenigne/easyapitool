import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app
import backend.controller_mixins.image_files as image_files
import backend.controller_mixins.image_reasoning as image_reasoning
from backend.image_editor import image_resolution_for_size


class ResolutionSnapshotTests(unittest.TestCase):
    def test_concrete_sizes_identify_the_actual_requested_resolution(self):
        for size, expected in (("1536x1536", "sd"), ("2560x2560", "hd"), ("2880x2880", "uhd"), ("3840x2560", "uhd")):
            with self.subTest(size=size):
                self.assertEqual(image_resolution_for_size(size, "sd"), expected)
        self.assertEqual(image_resolution_for_size("auto"), "")
        self.assertEqual(image_resolution_for_size("auto", "hd"), "hd")
        self.assertEqual(image_resolution_for_size("", {"invalid": "value"}), "")

    def test_old_square_uhd_record_with_sd_label_is_restored_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            store = app.ImageSessionStore(Path(directory))
            store.begin_round("session", "set", "vector illustration", 1, 0,
                              {"size": "2880x2880", "resolution": "uhd", "quality": "max", "output_format": "svg"})
            store.complete_round("session", "set")
            file = store._manifest_path("session")
            data = json.loads(file.read_text(encoding="utf-8"))
            data["rounds"][0]["options"]["resolution"] = "sd"  # Existing 1.2.7 metadata.
            file.write_text(json.dumps(data), encoding="utf-8")
            previous = file.read_bytes()
            restored = store.list_sets()[0]
            self.assertEqual((restored["size"], restored["resolution"]), ("2880x2880", "uhd"))
            self.assertEqual(restored["quality"], "max")
            self.assertEqual(restored["outputFormat"], "svg")
            self.assertEqual(file.read_bytes(), previous)

    def test_svg_request_keeps_size_quality_and_author_depth_while_metadata_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); final = root / "vector.svg"
            final.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><circle cx="32" cy="32" r="24" fill="orange"/></svg>')
            profile = {"defaultImage": "gpt-image-2.6-flare", "imageModels": ["gpt-image-2.6-flare"],
                       "svg": {"enabled": True, "model": "gpt-6.2-sol", "effort": "xhigh"}}
            controller = app.AppController.__new__(app.AppController)
            controller.store = SimpleNamespace(latest_payload=lambda key: {"appModels": profile},
                get_key_record=lambda key: {"id": key, "base_url": "https://provider.test/v1"}, get_secret=lambda key: "test-secret")
            requests = []; events = []
            def generate(_base, _secret, request, output_dir, **kwargs):
                requests.append(request)
                return {"ok": True, "path": str(final), "uri": final.as_uri(), "format": "svg", "width": 64, "height": 64}
            controller.image_generator = SimpleNamespace(generate=generate)
            with patch.object(image_files, "generated_pictures_dir", return_value=root / "Pictures"), patch.object(image_reasoning, "app_data_dir", return_value=root / "data"):
                result = controller.generate_image("key", "vector illustration", [], {"requestId": "svg-request",
                    "imageModel": "gpt-image-2.6-flare", "size": "2880x2880", "resolution": "uhd", "quality": "max",
                    "complexity": "high", "output_format": "svg", "reasoningMode": "instant"}, events.append)
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(requests[0].fields["size"], "2880x2880")
            self.assertEqual(requests[0].fields["quality"], "max")
            self.assertEqual(requests[0].fields["complexity"], "high")
            self.assertEqual(requests[0].fields["svg"]["reasoning"]["effort"], "xhigh")
            self.assertFalse(requests[0].fields["stream"])
            self.assertEqual((result["size"], result["resolution"], result["outputFormat"]), ("2880x2880", "uhd", "svg"))
            started = next(event for event in events if event["type"] == "set_started")
            self.assertEqual((started["resolution"], started["quality"], started["complexity"]), ("uhd", "max", "high"))
            restored = app.ImageSessionStore(root / "Pictures").list_sets()[0]
            self.assertEqual((restored["resolution"], restored["quality"], restored["complexity"], restored["outputFormat"]), ("uhd", "max", "high", "svg"))


if __name__ == "__main__":
    unittest.main()
