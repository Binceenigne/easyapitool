from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from backend.image_editor import ImageSessionStore, prepare_image_generation, reference_preview_data_url
from backend.controller_mixins.image_files import ImageFilesMixin
from backend.platform import clipboard_image_payloads, maximized_work_area_bounds, update_window_maximized_bounds


def animated_gif() -> bytes:
    buffer = io.BytesIO()
    frames = [Image.new("RGB", (24, 16), color) for color in ("red", "blue")]
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=200, loop=0)
    return buffer.getvalue()


class ProjectFilesTests(unittest.TestCase):
    def test_vector_and_animation_references_are_rasterized_without_changing_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gif = root / "source.gif"
            gif.write_bytes(animated_gif())
            svg = root / "source.svg"
            svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="16"><rect width="32" height="16" fill="red"/></svg>', encoding="utf-8")
            for source in (gif, svg):
                before = source.read_bytes()
                with patch.dict("os.environ", {"LOCALAPPDATA": str(root / "appdata")}):
                    request = prepare_image_generation("保持主体并修改背景", [str(source)], {"operation": "edit"})
                self.assertEqual(request.image_paths[0].suffix, ".png")
                self.assertNotEqual(request.image_paths[0], source)
                self.assertEqual(source.read_bytes(), before)
                self.assertTrue(reference_preview_data_url(before).startswith("data:image/png;base64,"))
                with Image.open(request.image_paths[0]) as image:
                    self.assertEqual(getattr(image, "n_frames", 1), 1)
                    self.assertEqual(image.convert("RGB").getpixel((0, 0)), (255, 0, 0))

    def test_prompt_folders_have_separate_outputs_and_survive_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "sample.gif"
            source.write_bytes(animated_gif())
            store = ImageSessionStore(root / "pictures")
            store.begin_round("session-a", "set-a", '灯塔：海浪 / 黄昏?', 1, 0, {})
            result = store.persist_result("session-a", "set-a", 0, source, {"format": "gif"})
            store.complete_round("session-a", "set-a")
            folder = store.folder_for_set("session-a", "set-a")
            self.assertIn("灯塔", folder.name)
            self.assertNotIn("set-a", folder.name)
            self.assertEqual(Path(result["path"]).parent, folder / "Output")
            self.assertNotEqual(Path(result["previewPath"]).parent, folder / "Output")
            self.assertEqual(Path(result["path"]).read_bytes(), source.read_bytes())
            ImageSessionStore._directory_cache.clear()
            fresh = ImageSessionStore(root / "pictures")
            loaded = fresh.list_sets()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(fresh.original_paths_for_set("session-a", "set-a")[0]["path"], result["path"])
            self.assertTrue(Path(loaded[0]["items"][0]["result"]["path"]).is_file())
            fresh.begin_round("session-b", "set-b", '灯塔：海浪 / 黄昏?', 1, 0, {})
            self.assertNotEqual(fresh.folder_for_set("session-b"), fresh.folder_for_set("session-a"))

    def test_history_uses_original_when_preview_is_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "sample.svg"
            source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><path d="M0 0H16V16H0Z" fill="blue"/></svg>', encoding="utf-8")
            store = ImageSessionStore(root / "pictures")
            store.begin_round("svg-session", "svg-set", "蓝色矢量", 1, 0, {})
            result = store.persist_result("svg-session", "svg-set", 0, source, {"format": "svg"})
            store.complete_round("svg-session", "svg-set")
            Path(result["previewPath"]).unlink()
            restored = store.list_sets()[0]["items"][0]["result"]
            self.assertEqual(restored["previewPath"], restored["path"])
            context = store.continuation_context("svg-session", "svg-set")
            self.assertEqual(Path(context["assets"][0]["previewPath"]).suffix, ".svg")

    def _legacy_project(self, root: Path) -> tuple[ImageSessionStore, bytes]:
        store = ImageSessionStore(root / "pictures")
        old = store.root / "legacy-session"
        old_round = old / "round-001-set-legacy"
        old_round.mkdir(parents=True)
        data = animated_gif()
        (old_round / "image-01-original.gif").write_bytes(data)
        (old_round / "image-01-preview.gif").write_bytes(data)
        manifest = {
            "schemaVersion": 3, "sessionId": "legacy-session", "roundCount": 1, "assets": [],
            "rounds": [{"setId": "set-legacy", "roundNumber": 1, "parentSetId": "", "prompt": "海边风车", "status": "completed",
                        "directory": old_round.name, "options": {}, "items": [{"status": "completed", "itemIndex": 0,
                        "original": old_round.name + "/image-01-original.gif", "preview": old_round.name + "/image-01-preview.gif"}]}],
        }
        (old / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return store, data

    def test_existing_numbered_projects_migrate_without_losing_animation(self):
        with tempfile.TemporaryDirectory() as temp:
            store, data = self._legacy_project(Path(temp))
            restored = store.list_sets()
            self.assertEqual(len(restored), 1)
            self.assertFalse((store.root / "legacy-session").exists())
            self.assertIn("海边风车", store.folder_for_set("legacy-session").name)
            result = restored[0]["items"][0]["result"]
            self.assertEqual(Path(result["path"]).parent.name, "Output")
            self.assertEqual(Path(result["path"]).read_bytes(), data)
            with Image.open(result["path"]) as image:
                self.assertEqual(image.n_frames, 2)

    def test_prompt_equal_to_another_session_id_does_not_merge_projects(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ImageSessionStore(Path(temp) / "pictures")
            store.begin_round("session-a", "set-a", "session-b", 1, 0, {})
            store.begin_round("session-b", "set-b", "独立项目", 1, 0, {})
            self.assertNotEqual(store.folder_for_set("session-a"), store.folder_for_set("session-b"))
            self.assertEqual({item["sessionId"] for item in store.list_sets()}, {"session-a", "session-b"})

    def test_migration_resumes_after_manifest_commit_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            store, data = self._legacy_project(Path(temp))
            with patch.object(store, "_write_manifest", side_effect=OSError("disk temporarily unavailable")):
                self.assertEqual(store.list_sets(), [])
            ImageSessionStore._directory_cache.clear()
            restored = ImageSessionStore(Path(temp) / "pictures").list_sets()
            self.assertEqual(len(restored), 1)
            result = restored[0]["items"][0]["result"]
            self.assertEqual(Path(result["path"]).read_bytes(), data)
            self.assertEqual(len(list((Path(temp) / "pictures" / "sessions").glob("*/第*"))), 1)

    def test_conflicting_outputs_remain_distinct_after_interrupted_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            store, data = self._legacy_project(Path(temp))
            old = store.root / "legacy-session" / "round-001-set-legacy"
            (old / "Output").mkdir()
            unrelated = b"another existing output"
            (old / "Output" / "image-01-original.gif").write_bytes(unrelated)
            with patch.object(store, "_write_manifest", side_effect=OSError("disk temporarily unavailable")):
                self.assertEqual(store.list_sets(), [])
            ImageSessionStore._directory_cache.clear()
            fresh = ImageSessionStore(Path(temp) / "pictures")
            restored = fresh.list_sets()
            self.assertEqual(len(restored), 1)
            result_path = Path(restored[0]["items"][0]["result"]["path"])
            self.assertEqual(result_path.read_bytes(), data)
            self.assertEqual((result_path.parent / "image-01-original.gif").read_bytes(), unrelated)
            self.assertNotEqual(result_path.name, "image-01-original.gif")

    def test_folder_lookup_and_cleanup_reject_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            store, _ = self._legacy_project(Path(temp))
            marker = Path(temp) / "outside.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                store._cleanup_committed_paths([marker])
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            with self.assertRaises(ValueError):
                store.folder_for_set("legacy-session", "missing")

    def test_all_output_formats_reload_and_can_be_copied(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pictures = root / "pictures"
            pictures.mkdir()
            sources = {}
            for suffix, format_name in (("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP")):
                path = pictures / ("成品." + suffix)
                Image.new("RGB", (16, 16), "green").save(path, format=format_name)
                sources[suffix] = path
            sources["gif"] = pictures / "成品.gif"
            sources["gif"].write_bytes(animated_gif())
            sources["svg"] = pictures / "成品.svg"
            sources["svg"].write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="red"/></svg>', encoding="utf-8")
            reader = ImageFilesMixin()
            with patch("backend.controller_mixins.image_files.generated_pictures_dir", return_value=pictures), patch("backend.controller_mixins.image_files.app_data_dir", return_value=root):
                for suffix, path in sources.items():
                    with self.subTest(suffix=suffix):
                        loaded = reader.load_generated_image(str(path))
                        self.assertTrue(loaded["ok"], loaded)
                        self.assertTrue(loaded["dataUrl"].startswith("data:image/"))
                        with patch("backend.controller_mixins.image_files.copy_image_to_windows_clipboard") as copied:
                            self.assertTrue(reader.copy_generated_image(str(path))["ok"])
                            copied.assert_called_once_with(path)
                for suffix in ("gif", "svg"):
                    legacy_preview = pictures / f"{suffix}-asset-preview.jpg"
                    legacy_preview.write_bytes(sources[suffix].read_bytes())
                    loaded = reader.load_generated_image(str(legacy_preview))
                    self.assertTrue(loaded["ok"], loaded)
                    expected = "image/gif" if suffix == "gif" else "image/svg+xml"
                    self.assertTrue(loaded["dataUrl"].startswith(f"data:{expected};"))
            for suffix in ("gif", "svg"):
                payloads = dict(clipboard_image_payloads(sources[suffix]))
                self.assertEqual(payloads["image/gif" if suffix == "gif" else "image/svg+xml"], sources[suffix].read_bytes())
                self.assertIn(str(sources[suffix]), payloads[15][20:].decode("utf-16-le"))
            self.assertIn(8, dict(clipboard_image_payloads(sources["gif"])))

    def test_maximize_uses_monitor_relative_work_area_including_taskbar_edges(self):
        self.assertEqual(maximized_work_area_bounds((0, 0, 1920, 1080), (0, 0, 1920, 1032)), (0, 0, 1920, 1032))
        self.assertEqual(maximized_work_area_bounds((-2560, -200, 0, 1240), (-2500, -200, 0, 1240)), (60, 0, 2500, 1440))
        self.assertEqual(maximized_work_area_bounds((1920, 0, 3840, 1080), (1920, 40, 3840, 1080)), (0, 40, 1920, 1040))

    def test_work_area_is_applied_to_hidden_native_form(self):
        import clr
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        from System.Reflection import BindingFlags
        from System.Windows.Forms import Form, Screen
        form = Form()
        try:
            form.ShowInTaskbar = False
            form.CreateControl()
            self.assertTrue(update_window_maximized_bounds(SimpleNamespace(native=form)))
            screen = Screen.FromHandle(form.Handle)
            prop = form.GetType().GetProperty("MaximizedBounds", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            actual = prop.GetValue(form, None)
            self.assertEqual(actual.Width, screen.WorkingArea.Width)
            self.assertEqual(actual.Height, screen.WorkingArea.Height)
        finally:
            form.Dispose()


if __name__ == "__main__":
    unittest.main()
