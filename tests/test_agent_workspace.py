import base64
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx2
from PIL import Image

import app
from backend.agent_workspace import ImageWorkspaceSession
from backend.prompt_targets import choose_reasoning_pair, validate_reasoning_config
import backend.controller_mixins.image_files as image_files
import backend.controller_mixins.image_reasoning as image_reasoning
from mirra_agents import run_agent
from tests.test_agents_runtime import sse_response


class WorkspaceToolsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.asset("parent", "purple", "output")
        self.old = self.asset("original-reference", "gold", "input")
        self.other = self.asset("inspected-web", "silver", "web-inspected")
        self.web_count = 0
        self.workspace = ImageWorkspaceSession({"workspaceId": "session", "parentSetId": "set-parent", "workspaceRoot": str(self.root),
            "assets": [self.parent, self.old, self.other], "parentOutputAssetIds": [self.parent["assetId"]],
            "history": [{"setId": "set-parent", "parentSetId": "set-old", "userPrompt": "purple character", "outputAssetIds": [self.parent["assetId"]]}]},
            [self.parent], reference_limit=3, emit=lambda *args, **kwargs: None,
            web_inspected_count=lambda: self.web_count, web_adopted_count=lambda: 0)

    def asset(self, name, color, role):
        path = self.root / (name + ".png")
        Image.new("RGB", (32, 24), color).save(path)
        return {**app.image_asset_record(path), "sourceRoles": [role], "sourceSetIds": ["set-old"]}

    async def test_catalog_pages_include_registered_paths_and_actual_source_roles(self):
        page = json.loads(await self.workspace.list(None, {"kind": "assets", "offset": 1, "limit": 1}))
        self.assertEqual(page["resultCount"], 1)
        self.assertTrue(page["hasMore"])
        self.assertEqual(page["items"][0]["path"], self.old["path"])
        self.assertEqual(page["items"][0]["sourceRoles"], ["input"])
        history = json.loads(await self.workspace.list(None, {"kind": "history", "offset": 0, "limit": 10}))
        self.assertEqual(history["items"][0]["parentSetId"], "set-old")
        self.assertEqual(self.workspace.plan["operation"], "edit")
        self.assertEqual(self.workspace.plan["selectedAssetIds"], [self.parent["assetId"]])

    async def test_view_returns_real_pixels_and_repeated_view_does_not_reload(self):
        output = await self.workspace.view(None, {"asset_ids": [self.old["assetId"]]})
        image = next(item for item in output if item.type == "image")
        with Image.open(io.BytesIO(base64.b64decode(image.image_url.split(",", 1)[1]))) as decoded:
            self.assertEqual(decoded.size, (32, 24))
        again = await self.workspace.view(None, {"asset_ids": [self.old["assetId"]]})
        self.assertFalse(any(item.type == "image" for item in again))
        self.assertTrue(json.loads(again[0].text)["assets"][0]["viewed"])

    async def test_agent_can_select_registered_assets_without_a_forced_view_step(self):
        value = json.loads(await self.workspace.select(None, {"operation": "generate", "asset_ids": [self.other["assetId"]], "rationale": "use the historical composition"}))
        self.assertEqual(value["selectedAssetIds"], [self.other["assetId"]])
        self.assertNotIn(self.other["assetId"], self.workspace.viewed)
        changed = json.loads(await self.workspace.select(None, {"operation": "generate", "asset_ids": [], "rationale": "create an independent scene"}))
        self.assertEqual(changed["selectedAssetIds"], [])

    async def test_unregistered_ids_and_external_registered_paths_are_unreadable(self):
        unknown = json.loads(await self.workspace.view(None, {"asset_ids": ["../secret"]}))
        self.assertEqual(unknown["error"]["code"], "unknown_workspace_asset")
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external) / "outside.png"
            Image.new("RGB", (4, 4), "red").save(outside)
            record = app.image_asset_record(outside)
            self.workspace.assets[record["assetId"]] = record
            output = await self.workspace.view(None, {"asset_ids": [record["assetId"]]})
            self.assertFalse(any(item.type == "image" for item in output))
            self.assertEqual(json.loads(output[0].text)["resultCount"], 0)
            selected = json.loads(await self.workspace.select(None, {"operation": "edit", "asset_ids": [record["assetId"]], "rationale": "outside file"}))
            self.assertEqual(selected["error"]["code"], "unknown_workspace_asset")

    async def test_mutated_registered_bytes_are_not_claimed_as_the_original_asset(self):
        Image.new("RGB", (4, 4), "blue").save(self.old["path"])
        output = await self.workspace.view(None, {"asset_ids": [self.old["assetId"]]})
        self.assertFalse(any(item.type == "image" for item in output))
        self.assertIn("已注册素材", json.loads(output[0].text)["errors"][0]["message"])

    async def test_final_reference_limit_is_shared_with_web_inputs(self):
        self.workspace.web_adopted_count = lambda: 2
        value = json.loads(await self.workspace.select(None, {"operation": "edit", "asset_ids": [self.parent["assetId"], self.old["assetId"]], "rationale": "two local images"}))
        self.assertEqual(value["error"]["code"], "workspace_reference_limit")
        self.assertEqual(self.workspace.selected, [self.parent["assetId"]])

    async def test_strict_tool_arguments_are_validated_at_the_host_boundary(self):
        listing = next(tool for tool in self.workspace.tools() if tool.name == "list_image_workspace")
        value = json.loads(await listing.on_invoke_tool(None, '{"kind":"assets","offset":false,"limit":1}'))
        self.assertEqual(value["error"]["code"], "invalid_workspace_arguments")


class MainAgentContinuationTests(unittest.TestCase):
    def exercise(self, *, replace_selection=False, sibling=False, profile=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "parent.png"; uploaded = root / "upload.png"; final = root / "final.png"
            Image.new("RGB", (16, 16), "purple").save(parent)
            Image.new("RGB", (16, 16), "gold").save(uploaded)
            Image.new("RGB", (16, 16), "navy").save(final)
            store = app.ImageSessionStore(root / "Pictures")
            store.begin_round("world", "parent", "create character", 1, 0, {})
            original = store.persist_result("world", "parent", 0, parent, {"format": "png", "width": 16, "height": 16})
            store.complete_round("world", "parent")
            sibling_id = None
            if sibling:
                sibling_file = root / "sibling.png"
                Image.new("RGB", (16, 16), "red").save(sibling_file)
                store.begin_round("world", "sibling", "alternate scene", 1, 0, {}, parent_set_id="parent")
                sibling_id = store.persist_result("world", "sibling", 0, sibling_file, {"format": "png"})["assetId"]
                store.complete_round("world", "sibling")
            controller = app.AppController.__new__(app.AppController)
            controller.store = SimpleNamespace(latest_payload=lambda key: {"appModels": profile} if profile is not None else None,
                get_key_record=lambda key: {"id": key, "base_url": "https://provider.test/v1"}, get_secret=lambda key: "test-secret")
            controller._cache_undescribed_image_assets = Mock(side_effect=AssertionError("old description prepass ran"))
            controller._run_instant_image_continuation_planner = Mock(side_effect=AssertionError("old planner ran"))
            bodies, image_requests, events = [], [], []
            def handler(request):
                self.assertEqual(request.headers["X-Mirra-Reasoning-Mode"], "continuation")
                body = json.loads(request.content); bodies.append(body)
                if len(bodies) == 1 and replace_selection:
                    return sse_response("", [{"type": "function_call", "id": "fc-select", "call_id": "call-select", "status": "completed",
                        "name": "select_image_workspace_inputs", "arguments": json.dumps({"operation": "generate", "asset_ids": [original["assetId"]], "rationale": "use the character only"})}])
                if len(bodies) == 1 and sibling:
                    return sse_response("", [{"type": "function_call", "id": "fc-list", "call_id": "call-list", "status": "completed",
                        "name": "list_image_workspace", "arguments": '{"kind":"assets","offset":0,"limit":50}'}])
                return sse_response("已继续处理。\n<<<FINAL_PROMPT>>>Agent proposed a rewritten prompt")
            def sdk(request, **kwargs):
                request.http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
                return run_agent(request, **kwargs)
            controller._run_sdk_agent = sdk
            def generate(_base, _secret, request, output_dir, **kwargs):
                image_requests.append(request)
                return {"ok": True, "path": str(final), "uri": final.as_uri(), "format": "png", "width": 16, "height": 16}
            controller.image_generator = SimpleNamespace(generate=generate)
            with patch.object(image_files, "generated_pictures_dir", return_value=root / "Pictures"), patch.object(image_reasoning, "app_data_dir", return_value=root / "data"):
                result = controller.generate_image("key", "let her enter a rainy station", [str(uploaded)], {
                    "requestId": "child", "sessionId": "world", "parentSetId": "parent", "continuation": True, "reasoningMode": "instant"}, events.append)
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(result["reasoningMode"], "instant")
            self.assertEqual(next(event for event in events if event["type"] == "react_started")["mode"], "instant")
            controller._cache_undescribed_image_assets.assert_not_called()
            controller._run_instant_image_continuation_planner.assert_not_called()
            self.assertEqual(result["prompt"], "let her enter a rainy station")
            self.assertFalse(any(tool.get("type") == "web_search" for tool in bodies[0]["tools"]))
            self.assertFalse(any(tool.get("name") == "plan_image_continuation" for tool in bodies[0]["tools"]))
            self.assertNotIn("tool_choice", bodies[0])
            self.assertEqual(sum(part["type"] == "input_image" for part in bodies[0]["input"][0]["content"]), 2)
            return result, bodies, image_requests, sibling_id

    def test_instant_continuation_uses_main_agent_and_default_parent_plus_upload(self):
        result, bodies, requests, _ = self.exercise()
        self.assertEqual(len(bodies), 1)
        self.assertEqual(result["operation"], "edit")
        self.assertEqual(len(requests[0].image_paths), 2)

    def test_main_agent_can_explicitly_replace_default_uploaded_references(self):
        result, bodies, requests, _ = self.exercise(replace_selection=True)
        self.assertEqual(len(bodies), 2)
        self.assertEqual(result["operation"], "generate")
        self.assertEqual(len(requests[0].image_paths), 1)

    def test_workspace_lists_sibling_history_without_automatically_submitting_it(self):
        result, bodies, requests, sibling_id = self.exercise(sibling=True)
        self.assertIn(sibling_id, json.dumps(bodies[1]["input"]))
        self.assertEqual(len(requests[0].image_paths), 2)
        self.assertNotIn(sibling_id, result["selectedAssetIds"])

    def test_basic_editing_tier_uses_exact_gateway_utility_without_enabling_planning(self):
        result, bodies, requests, _ = self.exercise(profile=ContinuationUtilityTests.profile())
        self.assertEqual(bodies[0]["model"], "gpt-6-luna")
        self.assertEqual(bodies[0]["reasoning"]["effort"], "low")
        self.assertEqual(len(bodies), 1)
        self.assertEqual(len(requests[0].image_paths), 2)


class ContinuationUtilityTests(unittest.TestCase):
    @staticmethod
    def profile():
        return {"tier": {"imageEditing": True, "maxReferences": 2, "reasoningModels": [], "reasoningEfforts": [],
                         "advanced": False, "webSearch": False},
                "reasoningModes": {"instant": {"model": "", "effort": ""}},
                "continuation": {"model": "gpt-6-luna", "effort": "low"}}

    def test_explicit_editing_pair_is_separate_from_selectable_reasoning_permissions(self):
        self.assertEqual(choose_reasoning_pair(self.profile(), "gpt-6.2-sol", "high", purpose="continuation"),
                         ("gpt-6-luna", "low"))

    def test_disabled_editing_or_missing_utility_cannot_use_an_arbitrary_fallback(self):
        profile = self.profile()
        profile["tier"]["imageEditing"] = False
        with self.assertRaises(ValueError):
            choose_reasoning_pair(profile, "gpt-6.2-sol", "high", purpose="continuation")
        profile = self.profile(); profile.pop("continuation")
        with self.assertRaises(ValueError):
            choose_reasoning_pair(profile, "gpt-6.2-sol", "high", purpose="continuation")

    def test_normal_and_advanced_planning_still_obey_the_original_tier(self):
        for mode in ("advanced", "flash", "max"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_reasoning_config(self.profile(), mode, {"model": "gpt-6.2-sol", "effort": "high"})
        profile = self.profile(); profile["polish"] = {"model": "gpt-6-luna", "effort": "low"}
        profile["tier"]["promptPolish"] = True
        with self.assertRaises(ValueError):
            choose_reasoning_pair(profile, "gpt-6-luna", "low", purpose="polish")

    def test_invalid_advertised_pair_is_not_replaced_by_a_caller_preference(self):
        for pair in ({"model": "../private", "effort": "low"}, {"model": "gpt-6-luna", "effort": "arbitrary"}):
            profile = self.profile(); profile["continuation"] = pair
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                choose_reasoning_pair(profile, "gpt-6.2-sol", "high", purpose="continuation")


if __name__ == "__main__":
    unittest.main()
