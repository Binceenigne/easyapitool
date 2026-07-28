import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image

import image_editor


class ImageEditorTests(unittest.TestCase):
    def test_service_decodes_and_writes_generated_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "reference.png"
            Image.new("RGB", (8, 8), "red").save(reference, format="PNG")
            encoded = base64.b64encode(reference.read_bytes()).decode("ascii")
            client = SimpleNamespace(
                edit_images=Mock(
                    return_value={
                        "data": [{"b64_json": encoded}],
                        "output_format": "png",
                        "quality": "auto",
                        "size": "8x8",
                    }
                )
            )
            service = image_editor.ImageEditorService(client)
            request = image_editor.prepare_image_edit(
                "Combine this reference",
                [str(reference)],
                {"size": "1024x1024", "quality": "low"},
            )

            result = service.edit(
                "https://example.test/v1",
                "secret",
                request,
                root / "output",
            )

            self.assertTrue(Path(result["path"]).is_file())
            self.assertEqual((result["width"], result["height"]), (8, 8))
            self.assertEqual(result["actualSize"], "8x8")
            self.assertEqual(result["quality"], "auto")

    def test_prepare_rejects_missing_reference_images(self):
        with self.assertRaisesRegex(ValueError, "1 到 16"):
            image_editor.prepare_image_edit("Combine images", [], {})


if __name__ == "__main__":
    unittest.main()