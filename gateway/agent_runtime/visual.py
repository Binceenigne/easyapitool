"""SDK visual tools, with all download/adoption authority retained in Go."""
from __future__ import annotations

import asyncio
import json


class WorkerVisualSession:
    def __init__(self, parameters, cancellation):
        self.parameters = parameters
        self.cancel = cancellation
        self.limit = parameters.pop("visual_reference_limit", 0)
        self.adopt_limit = parameters.pop("visual_reference_adopt_limit", 0)
        self.images = {}
        self.attempted = set()
        self.inspected = {}
        self.selected = []
        self.selection_made = False
        self.inspect_lock = asyncio.Lock()

    def observe(self, response):
        from mirra_agents.runtime import _web_images
        for item in _web_images(response):
            if len(self.images) < 48:
                self.images[item["image_url"]] = item

    async def request(self, body):
        import httpx2
        if self.cancel.is_set():
            raise asyncio.CancelledError()
        async with httpx2.AsyncClient(trust_env=False, timeout=35) as client:
            async with client.stream("POST", self.parameters["base_url"].rstrip("/") + "/reference-images",
                    json=body, headers={"Authorization": "Bearer " + self.parameters["api_key"]}) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 2 * 1024 * 1024:
                        raise RuntimeError("参考图工具返回的内容过大")
                value = json.loads(data)
                if response.status_code >= 400:
                    return {"error": value.get("error") or {"message": "参考图操作被网关拒绝"}}
                return value

    async def inspect(self, _context, arguments):
        async with self.inspect_lock:
            return await self._inspect(arguments)

    async def _inspect(self, arguments):
        from agents.tool import ToolOutputImage, ToolOutputText
        urls = arguments["image_urls"] or list(self.images)[:12]
        if any(url not in self.images for url in urls):
            return json.dumps({"error": {"code": "unknown_reference", "message": "只能检视本轮 OpenAI 图片搜索返回的图片URL"}}, ensure_ascii=False)
        self.attempted.update(urls)
        result = await self.request({"operation": "inspect", "image_urls": urls})
        output = []
        metadata = {**result, "candidates": []}
        for item in result.get("candidates") or []:
            item = dict(item)
            preview = item.pop("previewDataUrl", "")
            metadata["candidates"].append(item)
            if item["id"] not in self.inspected:
                self.inspected[item["id"]] = item
                self.selection_made = False
                if preview.startswith("data:image/"):
                    output.append(ToolOutputImage(image_url=preview))
        return [ToolOutputText(text=json.dumps(metadata, ensure_ascii=False)), *output]

    async def select(self, _context, arguments):
        ids = arguments["candidate_ids"]
        if len(ids) > self.adopt_limit or any(identifier not in self.inspected for identifier in ids):
            return json.dumps({"error": {"code": "invalid_reference_selection", "message": f"只能采用已检视的参考图，当前最多可采用 {self.adopt_limit} 张；无需采用时传空数组"}}, ensure_ascii=False)
        result = await self.request({"operation": "select", **arguments})
        if not result.get("error"):
            self.selection_made = True
            self.selected = result.get("webReferences") or []
        return json.dumps(result, ensure_ascii=False)

    def feedback(self, _text):
        if self.limit and self.images and not self.attempted.intersection(self.images):
            return "本轮官方图片搜索已返回候选，但尚未检视真实图片。请先调用 inspect_visual_references 读取与任务相关的候选，再根据实际画面选择是否采用，不要把网址当作已看过的图片。"
        if self.inspected and not self.selection_made:
            return f"已看到真实参考图片。请调用 select_visual_references 明确选择实际采用的候选ID（最多 {self.adopt_limit} 张），或传空数组说明不采用的原因，再完成最终生图提示词。"
        return None

    def tools(self):
        if not self.limit:
            return []
        from mirra_agents.visual_tools import make_visual_reference_tools
        return make_visual_reference_tools(self.inspect, self.select)
