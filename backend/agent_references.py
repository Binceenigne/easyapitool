from __future__ import annotations

import asyncio
import concurrent.futures
import json
from typing import Any, Callable
from urllib.parse import urlsplit

from mirra_agents.retry import safe_text


# Keep pending DNS/connect work bounded and outside asyncio.run's default
# executor, whose shutdown would otherwise delay an already cancelled Agent.
_REFERENCE_DOWNLOADS = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="reference-inspection")


class VisualReferenceSession:
    """Only actual hosted image results can enter the inspect/select tool loop."""

    def __init__(self, service: Any, *, inspect_limit: int, adopt_limit: Callable[[], int],
                 vision_limit: Callable[[], int], emit: Callable[..., None], task_context: Any = None) -> None:
        self.service, self.inspect_limit = service, min(12, max(0, inspect_limit))
        self.adopt_limit, self.vision_limit = adopt_limit, vision_limit
        self.emit, self.task_context = emit, task_context
        self.candidates: dict[str, dict[str, Any]] = {}
        self.inspected: dict[str, dict[str, Any]] = {}
        self.attempted: set[str] = set()
        self.selected_ids: list[str] = []
        self.selection_made = False
        self.rationale = ""
        self._lock = asyncio.Lock()

    def observe(self, response: dict[str, Any]) -> None:
        for output in response.get("output") or []:
            if output.get("type") != "web_search_call":
                continue
            for item in output.get("results") or []:
                if not isinstance(item, dict) or item.get("type") != "image_result":
                    continue
                image_url = str(item.get("image_url") or "").strip()
                try:
                    parsed = urlsplit(image_url)
                    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
                        continue
                except ValueError:
                    continue
                if len(image_url) > 4096 or any(ord(char) < 32 for char in image_url):
                    continue
                self.candidates.setdefault(image_url, {
                    "id": self.service._candidate_id(image_url),
                    "imageUrl": image_url,
                    "thumbnailUrl": str(item.get("thumbnail_url") or "")[:4096],
                    "sourceUrl": str(item.get("source_website_url") or "")[:4096],
                    "title": str(item.get("caption") or item.get("title") or "网络参考图")[:240],
                    "caption": str(item.get("caption") or "")[:400],
                    "provider": "OpenAI web search",
                })
                if len(self.candidates) >= 48:
                    return

    def _check(self) -> None:
        if self.task_context is not None:
            self.task_context.check_cancelled()

    @staticmethod
    def _metadata(record: dict[str, Any], preview: bool = False) -> dict[str, Any]:
        result = {key: record.get(key) for key in ("id", "title", "caption", "imageUrl", "thumbnailUrl", "sourceUrl", "provider", "width", "height")}
        if preview:
            result["previewUri"] = record.get("previewDataUrl") or ""
        return result

    async def inspect(self, _context: Any, arguments: dict[str, Any]) -> Any:
        from agents.tool import ToolOutputImage, ToolOutputText
        async with self._lock:
            self._check()
            requested = list(dict.fromkeys(arguments["image_urls"])) or list(self.candidates)
            unknown = [url for url in requested if url not in self.candidates]
            if unknown:
                return json.dumps({"error": {"code": "unknown_search_image", "message": "只能检视本轮官方图片搜索实际返回的候选URL"}}, ensure_ascii=False)
            remaining = max(0, min(self.inspect_limit, self.vision_limit()) - len(self.inspected))
            fresh = [self.candidates[url] for url in requested if url not in self.attempted][:remaining]
            self.attempted.update(record["imageUrl"] for record in fresh)
            verified = await asyncio.wrap_future(_REFERENCE_DOWNLOADS.submit(
                self.service.inspect_image_results, fresh, remaining, self.task_context))
            self._check()
            remaining = max(0, min(self.inspect_limit, self.vision_limit()) - len(self.inspected))
            new_records = verified["candidates"][:remaining]
            for record in new_records:
                self.inspected[record["id"]] = record
            self.emit("react_visual_results", webSearchResultCount=len(self.candidates),
                      candidates=[self._metadata(record, True) for record in self.inspected.values()],
                      errors=[{"id": error["id"], "message": safe_text(error["message"])} for error in verified["errors"]])
            metadata = {
                "candidates": [self._metadata(record) for record in self.inspected.values()],
                "errors": [{"id": error["id"], "message": safe_text(error["message"])} for error in verified["errors"]],
                "remainingInspectionSlots": max(0, remaining - len(new_records)),
                "maxAdopt": self.adopt_limit(),
                "message": "仅新检视的候选附带真实图片；已检视图片仍在对话中。请结合图片决定采用哪些ID，不需要时明确选择空数组。",
            }
            return [ToolOutputText(text=json.dumps(metadata, ensure_ascii=False)),
                    *[part for record in new_records for part in (
                        ToolOutputText(text=json.dumps({"candidate_id": record["id"], "caption": record.get("caption") or ""}, ensure_ascii=False)),
                        ToolOutputImage(image_url=record["previewDataUrl"]),
                    )]]

    async def select(self, _context: Any, arguments: dict[str, Any]) -> str:
        async with self._lock:
            self._check()
            selected = list(dict.fromkeys(arguments["candidate_ids"]))
            maximum = max(0, min(8, self.adopt_limit()))
            if any(candidate not in self.inspected for candidate in selected):
                return json.dumps({"error": {"code": "uninspected_reference", "message": "只能采用已成功检视的参考图ID"}}, ensure_ascii=False)
            if len(selected) > maximum:
                return json.dumps({"error": {"code": "reference_limit", "message": f"本次最多可采用 {maximum} 张网络参考图"}}, ensure_ascii=False)
            self.selected_ids = selected
            self.selection_made = True
            self.rationale = str(arguments["rationale"]).strip()
            self.emit("react_visual_selected", selectedCandidateIds=selected, rationale=self.rationale,
                      webReferenceCount=len(selected),
                      webReferences=[self._metadata(self.inspected[item], True) for item in selected])
            return json.dumps({"selected_candidate_ids": selected, "rationale": self.rationale,
                               "message": "已确认采用的真实图片，它们会随最终生图请求提交。请完成提示词。" if selected else "本次不额外采用网络参考图，请依据已核对的信息完成提示词。"}, ensure_ascii=False)

    def feedback(self, _text: str) -> str | None:
        if not self.candidates or self.inspect_limit <= 0 or self.vision_limit() <= 0:
            return None
        if not self.attempted and not self.inspected:
            return "本轮官方搜索已返回真实图片候选。请先调用 inspect_visual_references（image_urls 可为空）查看图片，再调用 select_visual_references 明确采用或不采用，最后按原标记返回修订后的工作摘要与最终提示词。"
        if not self.selection_made:
            return "参考图检视已经返回。请调用 select_visual_references 决定要采用的候选ID；没有适用图片时传空数组并说明原因。之后按原标记返回工作摘要与最终提示词。"
        return None

    @property
    def adopted(self) -> list[dict[str, Any]]:
        return [self.inspected[candidate] for candidate in self.selected_ids]
