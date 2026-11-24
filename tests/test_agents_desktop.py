import json
import base64
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx2
from PIL import Image

import app
import backend.controller_mixins.image_files as image_files
import backend.controller_mixins.image_reasoning as image_reasoning
from backend.image_editor import ImageGenerationClient, ImageGenerationService
from backend.web_search import WebSearchService
from mirra_agents import run_agent
from mirra_agents.retry import ProviderResponseError
from tests.test_agents_runtime import response_payload, sse_response


class DesktopAgentsTests(unittest.TestCase):
    def test_official_images_are_inspected_selected_submitted_and_restored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = self.controller()
            controller.web_search = WebSearchService()
            events, bodies, image_requests = [], [], []
            image_url = "https://cdn.reference.test/subject.png"
            source_url = "https://reference.test/subject"
            candidate_id = controller.web_search._candidate_id(image_url)
            source_image = io.BytesIO()
            Image.new("RGB", (256, 192), "orange").save(source_image, format="PNG")
            original_bytes = source_image.getvalue()
            expected_reference = controller.web_search._normalized_image_bytes(original_bytes)

            def function_call(name, arguments, suffix):
                return {"id": "fc-" + suffix, "call_id": "call-" + suffix, "type": "function_call", "status": "completed", "name": name, "arguments": json.dumps(arguments)}

            def handler(request):
                body = json.loads(request.content)
                bodies.append(body)
                if len(bodies) == 1:
                    tool = next(tool for tool in body["tools"] if tool["type"] == "web_search")
                    self.assertEqual(tool["search_content_types"], ["image", "text"])
                    self.assertIn("web_search_call.results", body["include"])
                    text = "尚未看图的初步方案。\n<<<FINAL_PROMPT>>>OUTDATED FIRST PROMPT"
                    web = {"id": "web-search-real", "type": "web_search_call", "status": "completed",
                           "action": {"type": "search", "query": "subject reference", "sources": [{"type": "url", "url": source_url}]},
                           "results": [{"type": "image_result", "image_url": image_url, "source_website_url": source_url, "caption": "Orange subject"}]}
                    return sse_response(text, [web, *response_payload(text)["output"]])
                if len(bodies) == 2:
                    self.assertTrue(any("inspect_visual_references" in str(item.get("content")) for item in body["input"]))
                    return sse_response("", [function_call("inspect_visual_references", {"image_urls": []}, "inspect")])
                if len(bodies) == 3:
                    output = next(item["output"] for item in body["input"] if item.get("type") == "function_call_output")
                    images = [part for part in output if part["type"] == "input_image"]
                    self.assertEqual(len(images), 1)
                    with Image.open(io.BytesIO(base64.b64decode(images[0]["image_url"].split(",", 1)[1]))) as actual:
                        self.assertEqual(actual.size, (256, 192))
                    return sse_response("", [function_call("select_visual_references", {"candidate_ids": [candidate_id], "rationale": "采用这张图保持主体外观"}, "select")])
                return sse_response("已看图并确认主体外观，补全光线与构图。\n<<<FINAL_PROMPT>>>FINAL VERIFIED SUBJECT PROMPT")

            self.sdk_transport(controller, handler)

            def generate(_base, _secret, request, output_dir, **_kwargs):
                image_requests.append(request)
                self.assertEqual(request.prompt, "FINAL VERIFIED SUBJECT PROMPT")
                self.assertEqual(len(request.image_paths), 1)
                self.assertEqual(request.image_paths[0].read_bytes(), expected_reference)
                output_dir.mkdir(parents=True, exist_ok=True)
                path = output_dir / "result.png"
                Image.new("RGB", (64, 64), "navy").save(path)
                return {"ok": True, "path": str(path), "uri": path.as_uri(), "format": "png", "width": 64, "height": 64}

            controller.image_generator = SimpleNamespace(generate=generate)
            with patch.object(image_reasoning, "app_data_dir", return_value=root / "data"), patch.object(image_files, "generated_pictures_dir", return_value=root / "Pictures"), patch.object(controller.web_search, "_public_image_bytes", return_value=original_bytes) as download:
                result = controller.generate_image("key", "Draw the subject faithfully", [], {
                    "requestId": "official-visual-loop", "reasoningMode": "max", "webSearchEnabled": True,
                }, events.append)
            self.assertTrue(result["ok"], result.get("error"))
            self.assertEqual(len(image_requests), 1)
            self.assertEqual(download.call_count, 1, "The adopted image must reuse the bytes the Agent actually inspected")
            self.assertEqual((result["requestCount"], result["toolCallCount"], result["webReferenceCount"]), (4, 3, 1))
            self.assertEqual(result["webReferences"][0]["sourceUrl"], source_url)
            saved = app.ImageSessionStore(root / "Pictures").list_sets()[0]
            self.assertEqual(saved["webReferenceCount"], 1)
            self.assertTrue(Path(saved["webReferences"][0]["path"]).is_file())
            self.assertTrue(Path(saved["webReferences"][0]["previewPath"]).is_file())
            self.assertEqual(saved["webSearchSources"][0]["url"], source_url)
            self.assertEqual((saved["requestCount"], len(saved["reasoningTurns"])), (4, 4))
            tools = [tool for turn in saved["reasoningTurns"] for tool in turn["tools"]]
            self.assertEqual(len(tools), 3)
            self.assertTrue(all(tool["status"] == "completed" for tool in tools))

    def controller(self):
        controller=app.AppController.__new__(app.AppController)
        controller.store=SimpleNamespace(latest_payload=lambda _key:None,
            get_key_record=lambda key:{"id":key,"base_url":"https://provider.test/v1"},
            get_secret=lambda _key:"test-only-secret")
        return controller

    def sdk_transport(self,controller,handler):
        def run(request,**kwargs):
            request.http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
            return run_agent(request,**kwargs)
        controller._run_sdk_agent=run

    def test_real_sdk_continuation_loads_only_selected_older_asset(self):
        with tempfile.TemporaryDirectory() as temp:
            parent=Path(temp)/"parent.png";older=Path(temp)/"older.png"
            Image.new("RGB",(8,8),"purple").save(parent)
            Image.new("RGB",(8,8),"silver").save(older)
            parent_asset=app.image_asset_record(parent)
            older_asset={**app.image_asset_record(older),"description":"silver hall"}
            controller=self.controller();bodies=[]
            def handler(request):
                body=json.loads(request.content);bodies.append(body)
                if len(bodies)==1:
                    arguments={"operation":"generate","selected_asset_ids":[older_asset["assetId"],parent_asset["assetId"]],
                        "descriptions":[{"asset_id":parent_asset["assetId"],"description":"purple character"}],"rationale":"preserve character and hall"}
                    return sse_response("",[{"id":"fc-plan","type":"function_call","status":"completed","call_id":"call-plan","name":"plan_image_continuation","arguments":json.dumps(arguments)}])
                return sse_response("保留角色和大厅。\n<<<FINAL_PROMPT>>>角色走入银色大厅")
            self.sdk_transport(controller,handler)
            result=controller._run_image_prompt_agent({"base_url":"https://provider.test/v1"},"test-only-secret",
                "continue in hall",(parent,),"medium",False,lambda *args,**kwargs:None,
                {"assets":[older_asset,parent_asset]},[parent_asset])
            self.assertEqual(len(bodies),2)
            self.assertEqual(bodies[0]["tool_choice"],{"type":"function","name":"plan_image_continuation"})
            self.assertEqual(sum(part["type"]=="input_image" for part in bodies[0]["input"][0]["content"]),1)
            output=next(item["output"] for item in bodies[1]["input"] if item.get("type")=="function_call_output")
            self.assertEqual(sum(part["type"]=="input_image" for part in output),1)
            self.assertEqual(result["continuationPlan"]["selectedAssetIds"],[older_asset["assetId"],parent_asset["assetId"]])
            self.assertEqual(result["prompt"],"角色走入银色大厅")

    def test_official_search_sources_are_not_fabricated_as_image_references(self):
        controller=self.controller();controller.web_search=Mock();bodies=[];events=[]
        def handler(request):
            bodies.append(json.loads(request.content))
            text="已核对产品规格。\n<<<FINAL_PROMPT>>>A faithful product render"
            search={"id":"web-1","type":"web_search_call","status":"completed","action":{"type":"search","query":"product specifications","sources":[{"type":"url","url":"https://example.com/spec"}]}}
            return sse_response(text,[search,*response_payload(text)["output"]])
        self.sdk_transport(controller,handler)
        result=controller._run_image_prompt_agent({"base_url":"https://provider.test/v1"},"test-only-secret",
            "product render",(),"flash",True,lambda kind,**details:events.append({"type":kind,**details}))
        self.assertTrue(any(tool["type"]=="web_search" for tool in bodies[0]["tools"]))
        self.assertIn("web_search_call.action.sources",bodies[0]["include"])
        self.assertTrue(result["webSearchUsed"]);self.assertEqual(result["webCandidates"],[])
        self.assertEqual(result["webReferenceCount"],0)
        self.assertEqual(result["webSearchSources"][0]["url"],"https://example.com/spec")
        self.assertFalse(controller.web_search.mock_calls)
        self.assertTrue(any(event["type"]=="react_tool_completed" and event.get("tool")=="web_search" for event in events))

    def test_only_failed_image_retries_and_partial_preview_is_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);controller=self.controller();counts={};events=[]
            def generate(base,secret,request,output_dir,**kwargs):
                name=output_dir.name;counts[name]=counts.get(name,0)+1
                if name=="item-002" and counts[name]==1:
                    kwargs["on_partial"]({"previewUri":"data:image/png;base64,preview","partialIndex":1})
                    raise ProviderResponseError("server_error","render stream disconnected",status=503,headers={"retry-after":"0"},request_id="upstream-disconnect")
                output_dir.mkdir(parents=True,exist_ok=True);path=output_dir/"result.png"
                Image.new("RGB",(8,8),"blue").save(path)
                return {"ok":True,"path":str(path),"uri":path.as_uri(),"format":"png","width":8,"height":8}
            controller.image_generator=SimpleNamespace(generate=generate)
            with patch.object(image_reasoning,"app_data_dir",return_value=root/"data"),patch.object(image_files,"generated_pictures_dir",return_value=root/"Pictures"):
                result=controller.generate_image("key","two oranges",[],{"requestId":"retry-two-images","imageCount":2},events.append)
            self.assertTrue(result["ok"]);self.assertEqual(counts,{"item-001":1,"item-002":2})
            retry=next(event for event in events if event["type"]=="request_retry")
            self.assertEqual((retry["stage"],retry["attempt"],retry["maxAttempts"],retry["itemIndex"]),("image",2,3,1))
            self.assertEqual(len([event for event in events if event["type"]=="item_completed"]),2)

    def test_image_retries_exhaust_with_exact_provider_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);controller=self.controller();events=[]
            controller.image_generator=SimpleNamespace(generate=Mock(side_effect=ProviderResponseError("server_error","GPU admission timeout",status=503,headers={"retry-after":"0"},request_id="gpu-queue-503")))
            with patch.object(image_reasoning,"app_data_dir",return_value=root/"data"),patch.object(image_files,"generated_pictures_dir",return_value=root/"Pictures"):
                result=controller.generate_image("key","orange",[],{"requestId":"retry-exhausted"},events.append)
            self.assertFalse(result["ok"]);self.assertEqual(controller.image_generator.generate.call_count,3)
            self.assertIn("GPU admission timeout",result["error"])
            self.assertEqual(result["errorDetails"]["diagnosticId"],"gpu-queue-503")
            self.assertEqual(result["errorDetails"]["attempts"],3)
            failure=next(event for event in events if event["type"]=="item_failed")
            self.assertEqual(failure["errorDetails"],result["errorDetails"])

    def test_real_partial_only_parser_eof_retries_until_final_image_arrives(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);controller=self.controller();events=[];calls=[]
            png=io.BytesIO();Image.new("RGB",(8,8),"orange").save(png,format="PNG")
            encoded=base64.b64encode(png.getvalue()).decode("ascii")
            def generate(base,secret,fields,on_partial):
                calls.append(fields)
                kind="image_generation.partial_image" if len(calls)==1 else "image_generation.completed"
                lines=[("data: "+json.dumps({"type":kind,"b64_json":encoded})+"\n\n").encode()]
                return ImageGenerationClient._read_response(lines,True,on_partial)
            controller.image_generator=ImageGenerationService(SimpleNamespace(generate_image=generate))
            with patch.object(image_reasoning,"app_data_dir",return_value=root/"data"),patch.object(image_files,"generated_pictures_dir",return_value=root/"Pictures"),patch("mirra_agents.retry.retry_delay",return_value=0):
                result=controller.generate_image("key","orange",[],{"requestId":"partial-eof-retry"},events.append)
            self.assertTrue(result["ok"]);self.assertEqual(len(calls),2)
            self.assertEqual(len([event for event in events if event["type"]=="item_partial"]),1)
            self.assertEqual(len([event for event in events if event["type"]=="item_completed"]),1)
            retry=next(event for event in events if event["type"]=="request_retry")
            self.assertIn("未返回最终图片",retry["cause"])

    def test_partial_only_eof_and_empty_completion_keep_code_and_exhaust_three_attempts(self):
        class EventResponse(io.BytesIO):
            status=200
            headers={"Content-Type":"text/event-stream","Retry-After":"0"}
        for empty_completion in (False,True):
            with self.subTest(empty_completion=empty_completion),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);controller=self.controller();controller.image_generator=ImageGenerationService();events=[]
                png=io.BytesIO();Image.new("RGB",(8,8),"orange").save(png,format="PNG")
                encoded=base64.b64encode(png.getvalue()).decode("ascii")
                payload="data: "+json.dumps({"type":"image_generation.partial_image","b64_json":encoded})+"\n\n"
                if empty_completion:payload+='data: {"type":"image_generation.completed","usage":{"total_tokens":7}}\n\n'
                open_request=Mock(side_effect=lambda *args,**kwargs:EventResponse(payload.encode()))
                with patch.object(image_reasoning,"app_data_dir",return_value=root/"data"),patch.object(image_files,"generated_pictures_dir",return_value=root/"Pictures"),patch("backend.image_editor.open_request",open_request),patch("backend.image_editor.log_network_error"):
                    result=controller.generate_image("key","orange",[],{"requestId":"partial-only-exhausted"},events.append)
                self.assertFalse(result["ok"]);self.assertEqual(open_request.call_count,3)
                self.assertEqual(result["errorDetails"]["code"],"incomplete_stream")
                self.assertEqual(result["errorDetails"]["attempts"],3)
                self.assertEqual(result["errorDetails"]["status"],200)
                self.assertTrue(result["errorDetails"]["diagnosticId"])
                self.assertEqual(len([event for event in events if event["type"]=="item_partial"]),3)
                self.assertFalse(any(event["type"]=="item_completed" for event in events))


if __name__=="__main__":unittest.main()
