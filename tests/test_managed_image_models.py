import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from backend.image_editor import ImageGenerationService, prepare_image_generation


class ManagedImageModelTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "defaultImage": "image-fixture",
            "imageModels": ["image-fixture", "image-other"],
            "svg": {"enabled": True, "model": "vector-fixture", "effort": "high"},
        }

    def test_svg_uses_gateway_model_settings_and_keeps_the_vector(self):
        request = prepare_image_generation(
            "生成矢量图", [], {"output_format": "svg", "stream": True}, app_models=self.profile
        )
        self.assertEqual(request.fields["model"], "image-fixture")
        self.assertEqual(request.fields["svg"], {"model": "vector-fixture", "reasoning": {"effort": "high"}})
        self.assertFalse(request.stream)
        vector = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 48"><rect width="96" height="48"/></svg>'
        client = Mock()
        client.generate_image.return_value = {"data": [{"b64_json": base64.b64encode(vector).decode()}]}
        with tempfile.TemporaryDirectory() as folder:
            generated = ImageGenerationService(client).generate("https://example.test/openai/v1", "fixture", request, Path(folder))
            self.assertEqual(Path(generated["path"]).suffix, ".svg")
            self.assertEqual(Path(generated["path"]).read_bytes(), vector)
            self.assertEqual((generated["width"], generated["height"]), (96, 48))
            self.assertTrue(generated["previewUri"].startswith("data:image/svg+xml;base64,"))

    def test_gateway_restrictions_apply_to_desktop_requests(self):
        with self.assertRaisesRegex(ValueError, "未开放"):
            prepare_image_generation("提示词", [], {"imageModel": "image-disabled"}, app_models=self.profile)
        with self.assertRaisesRegex(ValueError, "仅支持单张"):
            prepare_image_generation("提示词", [], {"output_format": "svg", "imageCount": 2}, app_models=self.profile)
