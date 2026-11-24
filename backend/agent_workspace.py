"""Read-only, registered image workspace access for the main creative Agent."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from mirra_agents.retry import safe_text
from mirra_agents.workspace_tools import make_workspace_tools


class ImageWorkspaceSession:
    def __init__(self, context: dict[str, Any], visible_assets: list[dict[str, Any]], *,
                 reference_limit: int, emit: Callable[..., None],
                 web_inspected_count: Callable[[], int], web_adopted_count: Callable[[], int],
                 task_context: Any = None) -> None:
        self.context, self.emit, self.task_context = context, emit, task_context
        self.reference_limit = max(0, min(16, reference_limit))
        self.web_inspected_count, self.web_adopted_count = web_inspected_count, web_adopted_count
        root = str(context.get("workspaceRoot") or "")
        self.root = Path(root).resolve() if root else None
        self.assets = {str(item["assetId"]): dict(item) for item in context.get("assets") or []
                       if isinstance(item, dict) and item.get("assetId") and item.get("path")}
        self.required = list(dict.fromkeys(str(identifier) for identifier in context.get("requiredInputAssetIds") or []))
        self.viewed = {str(item["assetId"]) for item in visible_assets
                       if item.get("assetId") in self.assets and self._path(str(item["assetId"])) is not None}
        self.viewed_order = list(dict.fromkeys(str(item["assetId"]) for item in visible_assets
                                              if str(item.get("assetId") or "") in self.viewed))
        defaults = list(context.get("parentOutputAssetIds") or []) + self.required
        if not defaults:
            defaults = [str(item["assetId"]) for item in visible_assets if item.get("assetId")]
        self.selected = [identifier for identifier in dict.fromkeys(defaults) if identifier in self.viewed][:self.reference_limit]
        self.operation = "edit" if self.selected else "generate"
        self.rationale = "沿用所选父集成果与本轮上传参考图，按当前要求继续编辑。"
        self._lock = asyncio.Lock()

    def _check(self) -> None:
        if self.task_context is not None:
            self.task_context.check_cancelled()

    def _path(self, asset_id: str) -> Path | None:
        record = self.assets.get(asset_id)
        if not record:
            return None
        try:
            path = Path(str(record["path"])).resolve(strict=True)
            if (not path.is_file() or path.stat().st_size > 50 * 1024 * 1024
                    or self.root is not None and not path.is_relative_to(self.root)):
                return None
            return path
        except (OSError, ValueError, RuntimeError):
            return None

    def _metadata(self, asset_id: str) -> dict[str, Any]:
        record = self.assets[asset_id]
        path = self._path(asset_id)
        preview = str(record.get("previewPath") or "")
        if preview:
            try:
                candidate = Path(preview).resolve(strict=True)
                preview = str(candidate) if candidate.is_file() and (self.root is None or candidate.is_relative_to(self.root)) else ""
            except (OSError, ValueError, RuntimeError):
                preview = ""
        return {"assetId": asset_id, "path": str(path) if path is not None else "", "previewPath": preview,
                "sourceRoles": list(record.get("sourceRoles") or []), "sourceSetIds": list(record.get("sourceSetIds") or []),
                "description": str(record.get("description") or "")[:1200],
                "format": path.suffix.lstrip(".").lower() if path else "", "exists": path is not None,
                "viewed": asset_id in self.viewed}

    @property
    def plan(self) -> dict[str, Any]:
        return {"operation": self.operation, "selectedAssetIds": list(self.selected),
                "descriptions": {}, "rationale": self.rationale}

    def summary(self) -> dict[str, Any]:
        return {"workspaceId": str(self.context.get("workspaceId") or ""),
                "parentSetId": str(self.context.get("parentSetId") or ""),
                "historyCount": len(self.context.get("history") or []), "assetCount": len(self.assets),
                "selectedAssetIds": list(self.selected), "visibleAssetIds": list(self.viewed_order),
                "uploadedAssetIds": self.required, "operation": self.operation,
                "maxReferences": self.reference_limit,
                "remainingReferenceSlots": max(0, self.reference_limit - len(self.selected) - self.web_adopted_count())}

    async def list(self, _context: Any, arguments: dict[str, Any]) -> str:
        self._check()
        kind, offset, limit = arguments["kind"], arguments["offset"], arguments["limit"]
        if kind == "assets":
            entries = [self._metadata(identifier) for identifier in self.assets]
        else:
            entries = [{key: item.get(key) for key in ("setId", "parentSetId", "roundNumber", "userPrompt", "reasoningSummary",
                       "operation", "inputAssetIds", "outputAssetIds", "activeBranch", "status")}
                       for item in self.context.get("history") or [] if isinstance(item, dict)]
        return json.dumps({**self.summary(), "kind": kind, "offset": offset, "total": len(entries),
                           "hasMore": offset + limit < len(entries), "resultCount": len(entries[offset:offset + limit]),
                           "items": entries[offset:offset + limit]}, ensure_ascii=False)

    async def view(self, _context: Any, arguments: dict[str, Any]) -> Any:
        from agents.tool import ToolOutputImage, ToolOutputText
        from .image_editor import image_preview_data_url

        async with self._lock:
            self._check()
            identifiers = list(dict.fromkeys(arguments["asset_ids"]))
            if any(identifier not in self.assets for identifier in identifiers):
                return json.dumps({"error": {"code": "unknown_workspace_asset", "message": "只能查看当前工作区已注册的素材 ID"}}, ensure_ascii=False)
            available = max(0, self.reference_limit - len(self.viewed) - self.web_inspected_count())
            fresh = [identifier for identifier in identifiers if identifier not in self.viewed]
            if len(fresh) > available:
                return json.dumps({"error": {"code": "workspace_vision_limit", "message": f"当前档位本轮还可读取 {available} 张新图片"}}, ensure_ascii=False)
            parts, errors = [], []
            for identifier in fresh:
                self._check()
                path = self._path(identifier)
                try:
                    if path is None:
                        raise ValueError("图片已不存在或超出当前工作区")
                    data = path.read_bytes()
                    if identifier.startswith("asset-") and len(identifier) == 30 and hashlib.sha256(data).hexdigest()[:24] != identifier[6:]:
                        raise ValueError("图片内容与已注册素材不一致，请重新加载该图片集")
                    preview = image_preview_data_url(data, max_side=1024)
                    image = ToolOutputImage(image_url=preview)
                    self.viewed.add(identifier)
                    self.viewed_order.append(identifier)
                    parts.extend([ToolOutputText(text=json.dumps(self._metadata(identifier), ensure_ascii=False)), image])
                except (OSError, ValueError, RuntimeError) as error:
                    errors.append({"assetId": identifier, "message": safe_text(error)})
            return [ToolOutputText(text=json.dumps({"assets": [self._metadata(identifier) for identifier in identifiers],
                    "errors": errors, "resultCount": sum(identifier in self.viewed for identifier in identifiers), "remainingVisionSlots": max(0, self.reference_limit - len(self.viewed) - self.web_inspected_count()),
                    "message": "只有本次新读取的素材附带真实图片；已查看的图片仍在会话上下文中。"}, ensure_ascii=False)), *parts]

    async def select(self, _context: Any, arguments: dict[str, Any]) -> str:
        async with self._lock:
            self._check()
            identifiers = list(dict.fromkeys(arguments["asset_ids"]))
            if any(identifier not in self.assets or self._path(identifier) is None for identifier in identifiers):
                return json.dumps({"error": {"code": "unknown_workspace_asset", "message": "所选素材不在当前工作区中或文件已不可读取"}}, ensure_ascii=False)
            if len(identifiers) + self.web_adopted_count() > self.reference_limit:
                return json.dumps({"error": {"code": "workspace_reference_limit", "message": "工作区素材与联网参考图合计超过当前档位的图片槽位"}}, ensure_ascii=False)
            if arguments["operation"] == "edit" and not identifiers:
                return json.dumps({"error": {"code": "empty_edit_inputs", "message": "编辑操作至少需要一张实际参考图片"}}, ensure_ascii=False)
            self.selected, self.operation = identifiers, arguments["operation"]
            self.rationale = str(arguments["rationale"]).strip()[:600]
            self.emit("react_continuation_planned", **self.plan)
            return json.dumps({**self.plan, "resultCount": len(self.selected), "message": "已更新当前生图输入；如需更换素材可继续查看并再次选择。"}, ensure_ascii=False)

    def tools(self) -> list[Any]:
        return make_workspace_tools(self.list, self.view, self.select)
