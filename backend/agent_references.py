from __future__ import annotations

import asyncio
import concurrent.futures
import json
from typing import Any, Callable

from mirra_agents.reference_policy import public_reference_url
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
        self.candidate_revision = 0
        self.inspection_revision = 0
        self.selection_revision: tuple[int, int] | None = None
        self.failures: dict[str, dict[str, Any]] = {}
        self.inspection_records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def observe(self, response: dict[str, Any]) -> None:
        changed = False
        for output in response.get("output") or []:
            if not isinstance(output, dict):
                continue
            if output.get("type") != "web_search_call":
                continue
            for item in output.get("results") or []:
                if not isinstance(item, dict) or item.get("type") != "image_result":
                    continue
                image_url = public_reference_url(item.get("image_url"), allow_http=True)
                thumbnail_url = public_reference_url(item.get("thumbnail_url"))
                if not image_url or (image_url.startswith("http://") and not thumbnail_url):
                    continue
                candidate = {
                    "id": self.service._candidate_id(image_url),
                    "imageUrl": image_url,
                    "thumbnailUrl": thumbnail_url,
                    "sourceUrl": str(item.get("source_website_url") or "")[:4096],
                    "title": str(item.get("caption") or item.get("title") or "网络参考图")[:240],
                    "caption": str(item.get("caption") or "")[:400],
                    "provider": "OpenAI web search",
                }
                previous = self.candidates.get(image_url)
                if previous is None:
                    if len(self.candidates) >= 48:
                        continue
                    self.candidates[image_url] = candidate
                    changed = True
                elif candidate["thumbnailUrl"] and candidate["thumbnailUrl"] != previous.get("thumbnailUrl"):
                    # A newly returned HTTPS thumbnail can make a previously
                    # failed canonical image readable. Do not cache that failure.
                    previous.update(candidate)
                    if candidate["id"] in self.failures:
                        self.attempted.discard(image_url)
                    changed = True
        if changed:
            self.candidate_revision += 1
            self.selection_made = False

    def _check(self) -> None:
        if self.task_context is not None:
            self.task_context.check_cancelled()

    @staticmethod
    def _metadata(record: dict[str, Any], preview: bool = False) -> dict[str, Any]:
        result = {key: record.get(key) for key in ("id", "title", "caption", "imageUrl", "thumbnailUrl", "sourceUrl", "provider", "width", "height", "downloadUrl", "downloadAttempts")}
        if preview:
            result["previewUri"] = record.get("previewDataUrl") or ""
        return result

    def _metadata_values(self, preview: bool = False) -> dict[str, Any]:
        current = self.selection_made and self.selection_revision == (self.candidate_revision, self.inspection_revision)
        status = "selected" if current and self.selected_ids else "declined" if current else "pending" if self.candidates else "no_candidates"
        return {
            "webInspectedReferences": [self._metadata(record, preview) for record in self.inspected.values()],
            "webReferenceFailures": [dict(record) for record in self.failures.values()],
            "webSelectionRationale": self.rationale,
            "webSelectionStatus": status,
            "webImageCandidateCount": len(self.candidates),
            "webCandidateRevision": self.candidate_revision,
            "webInspectionRevision": self.inspection_revision,
            "webInspectionRecords": [dict(record) for record in self.inspection_records.values()],
        }

    @property
    def metadata(self) -> dict[str, Any]:
        """Public review records without cached image bytes or base64 previews."""
        return self._metadata_values()

    @property
    def inspected_candidates(self) -> list[dict[str, Any]]:
        """Verified bytes for independent inspection-history persistence."""
        return list(self.inspected.values())

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
            verified = await asyncio.wrap_future(_REFERENCE_DOWNLOADS.submit(
                self.service.inspect_image_results, fresh, remaining, self.task_context))
            self._check()
            self.attempted.update(record["imageUrl"] for record in fresh)
            remaining = max(0, min(self.inspect_limit, self.vision_limit()) - len(self.inspected))
            new_records = verified["candidates"][:remaining]
            for record in new_records:
                self.inspected[record["id"]] = record
                self.failures.pop(record["id"], None)
                self.inspection_records[record["id"]] = {**self._metadata(record), "status": "inspected"}
            successful = {record["id"] for record in new_records}
            returned = {record["id"] for record in verified["candidates"]}
            errors = {error["id"]: error for error in verified["errors"]}
            for record in fresh:
                identifier = record["id"]
                if identifier in successful:
                    continue
                error = errors.get(identifier) or {
                    "message": "参考图槽位已被其他素材占用，未向模型提供图片" if identifier in returned else "检视未返回可读取图片",
                    "code": "reference_limit" if identifier in returned else "image_inspection_failed",
                }
                failure = {"id": identifier, "imageUrl": record["imageUrl"], "message": safe_text(error["message"])}
                for key in ("code", "status", "downloadUrl", "attempts"):
                    if error.get(key) is not None:
                        failure[key] = error[key]
                self.failures[identifier] = failure
                self.inspection_records[identifier] = {**self._metadata(record), "status": "failed", "error": dict(failure)}
            if fresh:
                self.inspection_revision += 1
                self.selection_made = False
            public_errors = [dict(record) for record in self.failures.values()]
            self.emit("react_visual_results", webSearchResultCount=len(self.candidates),
                      candidates=[self._metadata(record, True) for record in self.inspected.values()],
                      errors=public_errors, **self._metadata_values(preview=True))
            metadata = {
                "candidates": [self._metadata(record) for record in self.inspected.values()],
                "errors": public_errors,
                "remainingInspectionSlots": max(0, remaining - len(new_records)),
                "maxAdopt": self.adopt_limit(),
                "uninspectedCandidateUrls": [url for url in self.candidates if url not in self.attempted],
                "message": "新检视图片附在下方；已看图片仍在对话中。可按任务需要选择参考图，也可以不选。",
                **self.metadata,
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
            self.selection_revision = (self.candidate_revision, self.inspection_revision)
            self.rationale = safe_text(str(arguments["rationale"]).strip())
            self.emit("react_visual_selected", selectedCandidateIds=selected, rationale=self.rationale,
                      selectedCount=len(selected), webReferenceCount=len(selected),
                      webReferences=[self._metadata(self.inspected[item], True) for item in selected],
                      **self._metadata_values(preview=True))
            return json.dumps({"selected_candidate_ids": selected, "rationale": self.rationale,
                               "message": "已确认采用的真实图片，它们会随最终生图请求提交。请完成提示词。" if selected else "本次不额外采用网络参考图，请依据已核对的信息完成提示词。",
                               **self.metadata}, ensure_ascii=False)

    def feedback(self, _text: str) -> str | None:
        # Choosing references belongs to the Agent. Revision metadata describes
        # what actually happened without forcing another inspection/selection
        # turn or interpreting affirmative/negative prose as a tool decision.
        return None

    @property
    def adopted(self) -> list[dict[str, Any]]:
        return [self.inspected[candidate] for candidate in self.selected_ids]
