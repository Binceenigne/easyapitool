import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

from backend.agent_references import VisualReferenceSession
from backend.web_search import WebSearchService
from mirra_agents.reference_policy import VISUAL_REFERENCE_POLICY, reference_download_urls
from tests.test_agent_visual_references import fixture_png, image_parts, official_results, output_json


class ReferenceReviewStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = WebSearchService()
        self.download = Mock(return_value=fixture_png())
        self.enterContext(patch.object(self.service, "_public_image_bytes", self.download))
        self.events = []
        self.session = VisualReferenceSession(self.service, inspect_limit=4,
            vision_limit=lambda: 4, adopt_limit=lambda: 2,
            emit=lambda kind, **fields: self.events.append({"type": kind, **fields}))
        self.one = "https://images.example.com/one.png"
        self.two = "https://images.example.com/two.png"

    async def inspect(self, *urls):
        return await self.session.inspect(None, {"image_urls": list(urls)})

    async def select(self, *identifiers, rationale="Use the actual inspected shape"):
        return await self.session.select(None, {"candidate_ids": list(identifiers), "rationale": rationale})

    async def test_agent_can_decline_before_inspection_and_later_review_is_recorded_without_forcing_a_loop(self):
        self.session.observe(official_results(self.one))
        early = output_json(await self.select(rationale="Unfamiliar source"))
        self.assertEqual(early["selected_candidate_ids"], [])
        self.assertTrue(self.session.selection_made)
        self.assertEqual(self.session.rationale, "Unfamiliar source")
        self.assertIsNone(self.session.feedback("final"))
        inspected = output_json(await self.inspect())
        self.assertFalse(self.session.selection_made)
        self.assertEqual(self.session.metadata["webSelectionStatus"], "pending")
        self.assertIsNone(self.session.feedback("final"))
        await self.select(inspected["candidates"][0]["id"])
        self.assertIsNone(self.session.feedback("final"))
        self.assertEqual(len(self.session.adopted), 1)

    async def test_new_candidates_invalidate_old_decline_without_replaying_failed_downloads(self):
        def download(url, *args):
            if url.startswith(self.one):
                raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
            return fixture_png()
        self.download.side_effect = download
        self.session.observe(official_results(self.one))
        await self.inspect()
        await self.select(rationale="The only candidate returned HTTP 403")
        self.assertIsNone(self.session.feedback("final"))
        old_revision = self.session.candidate_revision
        self.session.observe(official_results(self.two))
        self.assertGreater(self.session.candidate_revision, old_revision)
        self.assertFalse(self.session.selection_made)
        self.assertIsNone(self.session.feedback("final"))
        self.download.reset_mock()
        result = output_json(await self.inspect())
        self.assertTrue(self.download.call_args_list)
        self.assertTrue(all(call.args[0] == self.two for call in self.download.call_args_list))
        await self.select(result["candidates"][0]["id"])
        self.assertEqual(self.session.adopted[0]["imageUrl"], self.two)
        self.assertEqual(self.session.metadata["webReferenceFailures"][0]["status"], 403)

    async def test_new_inspection_requires_reconfirmation_and_preserves_old_selected_images(self):
        self.session.observe(official_results(self.one, self.two))
        result = output_json(await self.inspect(self.one))
        chosen = result["candidates"][0]["id"]
        await self.select(chosen)
        old_revision = self.session.inspection_revision
        self.download.side_effect = urllib.error.HTTPError(self.two, 403, "Forbidden", {}, None)
        await self.inspect(self.two)
        self.assertGreater(self.session.inspection_revision, old_revision)
        self.assertFalse(self.session.selection_made)
        self.assertEqual(self.session.selected_ids, [chosen])
        self.assertEqual(self.session.adopted[0]["imageUrl"], self.one)
        self.assertIsNone(self.session.feedback("final"))
        await self.select(chosen)
        self.assertIsNone(self.session.feedback("final"))

    async def test_repeated_existing_candidates_do_not_create_a_feedback_loop(self):
        self.session.observe(official_results(self.one))
        result = output_json(await self.inspect())
        await self.select(result["candidates"][0]["id"])
        revision = self.session.candidate_revision
        self.session.observe(official_results(self.one))
        await self.inspect(self.one)
        self.assertEqual(self.session.candidate_revision, revision)
        self.assertTrue(self.session.selection_made)
        self.assertIsNone(self.session.feedback("final"))

    async def test_all_real_download_failures_can_be_declined_with_explicit_provenance(self):
        self.session.observe(official_results(self.one))
        self.download.side_effect = urllib.error.HTTPError(self.one, 403, "Forbidden", {}, None)
        result = await self.inspect()
        self.assertEqual(image_parts(result), [])
        selected = output_json(await self.select(rationale="The actual download was forbidden (HTTP 403)"))
        self.assertEqual(selected["selected_candidate_ids"], [])
        self.assertEqual(self.session.metadata["webSelectionStatus"], "declined")
        self.assertEqual(self.session.metadata["webReferenceFailures"][0]["status"], 403)
        self.assertIsNone(self.session.feedback("final"))

    async def test_relatedness_decision_can_decline_verified_but_unrelated_images(self):
        self.session.observe(official_results(self.one))
        await self.inspect()
        await self.select(rationale="The inspected candidate shows a different product and would distort the requested subject")
        self.assertEqual(self.session.adopted, [])
        self.assertEqual(len(self.session.inspected_candidates), 1)
        self.assertIsNone(self.session.feedback("final"))

    async def test_public_metadata_contains_full_review_and_reason_without_image_payloads(self):
        self.session.observe(official_results(self.one, self.two))
        def download(url, *args):
            if url.startswith(self.two):
                raise RuntimeError("failed via https://proxy-user:proxy-password@proxy.test sk-private-123456789")
            return fixture_png()
        self.download.side_effect = download
        result = output_json(await self.inspect())
        await self.select(result["candidates"][0]["id"], rationale="Preserve its visible proportions")
        metadata = self.session.metadata
        encoded = json.dumps(metadata)
        self.assertEqual(metadata["webImageCandidateCount"], 2)
        self.assertEqual(len(metadata["webInspectedReferences"]), 1)
        self.assertEqual(len(metadata["webReferenceFailures"]), 1)
        self.assertEqual(len(metadata["webInspectionRecords"]), 2)
        self.assertEqual(metadata["webSelectionRationale"], "Preserve its visible proportions")
        for value in ("data:image", "_imageBytes", "proxy-password", "sk-private"):
            self.assertNotIn(value, encoded)
        self.assertTrue(self.events[-1]["webInspectedReferences"][0]["previewUri"].startswith("data:image/jpeg;base64,"))
        self.assertTrue(self.session.inspected_candidates[0]["_imageBytes"])

    async def test_http_canonical_uses_only_official_https_thumbnail_and_keeps_source_identity(self):
        canonical = "http://catalog.example.com/product.png"
        thumbnail = "https://thumbs.example.com/official.jpg"
        self.session.observe({"output": [{"type": "web_search_call", "results": [
            {"type": "image_result", "image_url": canonical, "thumbnail_url": thumbnail,
             "source_website_url": "http://catalog.example.com/product"}]}]})
        result = await self.inspect()
        self.download.assert_called_once()
        self.assertEqual(self.download.call_args.args[0], thumbnail)
        candidate = output_json(result)["candidates"][0]
        self.assertEqual(candidate["imageUrl"], canonical)
        self.assertEqual(candidate["downloadUrl"], thumbnail)
        await self.select(candidate["id"])
        self.assertEqual(self.session.adopted[0]["_imageBytes"], self.service._normalized_image_bytes(fixture_png()))

    async def test_http_without_safe_thumbnail_and_obviously_private_urls_are_not_downloaded(self):
        invalid = [
            ("http://catalog.example.com/image.png", "http://thumbs.example.com/plain.jpg"),
            ("http://127.0.0.1/image.png", "https://thumbs.example.com/public.jpg"),
            ("http://user:secret@catalog.example.com/image.png", "https://thumbs.example.com/public.jpg"),
            ("http://catalog.example.com:8000/image.png", "https://thumbs.example.com/public.jpg"),
            ("http://catalog.example.com/image.png", "https://localhost/private.jpg"),
        ]
        self.session.observe({"output": [{"type": "web_search_call", "results": [
            {"type": "image_result", "image_url": original, "thumbnail_url": thumb} for original, thumb in invalid]}]})
        self.assertFalse(self.session.candidates)
        await self.inspect()
        self.download.assert_not_called()

    async def test_new_official_thumbnail_can_recover_a_failed_canonical_without_reusing_old_decision(self):
        self.session.observe(official_results(self.one))
        self.download.side_effect = urllib.error.HTTPError(self.one, 403, "Forbidden", {}, None)
        await self.inspect()
        await self.select(rationale="Both returned endpoints are forbidden")
        self.session.observe({"output": [{"type": "web_search_call", "results": [
            {"type": "image_result", "image_url": self.one, "thumbnail_url": "https://thumbs.example.com/new.jpg"}]}]})
        self.assertFalse(self.session.selection_made)
        self.assertNotIn(self.one, self.session.attempted)
        self.download.side_effect = lambda url, *_: fixture_png() if url.startswith("https://thumbs.example.com") else (_ for _ in ()).throw(urllib.error.HTTPError(url, 403, "Forbidden", {}, None))
        result = output_json(await self.inspect())
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["downloadUrl"], "https://thumbs.example.com/new.jpg")
        self.assertEqual(result["candidates"][0]["downloadAttempts"][0]["status"], 403)
        self.assertFalse(self.session.failures)


class VisualReferencePolicyTests(unittest.TestCase):
    def test_policy_leaves_reference_decisions_to_the_agent(self):
        self.assertIn("按任务需要自行", VISUAL_REFERENCE_POLICY)
        self.assertIn("不需要时也可以不选", VISUAL_REFERENCE_POLICY)
        self.assertNotIn("版权", VISUAL_REFERENCE_POLICY)
        self.assertNotIn("许可", VISUAL_REFERENCE_POLICY)
        self.assertEqual(reference_download_urls("http://example.com/a.png", "https://thumbs.example.com/a.jpg"),
                         ("https://thumbs.example.com/a.jpg",))

    def test_prose_does_not_force_a_reference_repair_turn(self):
        session = VisualReferenceSession(WebSearchService(), inspect_limit=4,
            vision_limit=lambda: 4, adopt_limit=lambda: 2, emit=lambda *args, **kwargs: None)
        session.observe(official_results("https://images.example.com/one.png"))
        for text in ("本次不采用参考图", "已采用参考图", "No reference image is necessary", "I have selected reference images"):
            self.assertIsNone(session.feedback(text))


if __name__ == "__main__":
    unittest.main()
