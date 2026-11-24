"""Workspace tools run inside the main SDK Agent through its scoped Go lease."""
from __future__ import annotations
import json
from visual import WorkerVisualSession


class WorkerWorkspaceSession:
    def __init__(self, parameters, cancellation):
        self.transport = WorkerVisualSession(dict(parameters), cancellation)
        self.transport.route = "workspace"
        self.state = {}

    async def initialize(self):
        self.state = await self.transport.request({"operation": "list", "kind": "assets", "offset": 0, "limit": 1})

    async def list(self, _context, arguments):
        return json.dumps(await self.transport.request({"operation": "list", **arguments}), ensure_ascii=False)

    async def view(self, _context, arguments):
        from agents.tool import ToolOutputImage, ToolOutputText
        result = await self.transport.request({"operation": "view", **arguments})
        images = []
        for asset in result.get("assets") or []:
            preview = asset.pop("previewDataUrl", "")
            if preview.startswith("data:image/"):
                images.append(ToolOutputImage(image_url=preview))
        return [ToolOutputText(text=json.dumps(result, ensure_ascii=False)), *images]

    async def select(self, _context, arguments):
        result = await self.transport.request({"operation": "select", "image_operation": arguments["operation"],
            "asset_ids": arguments["asset_ids"], "rationale": arguments["rationale"]})
        if not result.get("error"):
            self.state.update(operation=result.get("operation"), selectedAssetIds=result.get("selectedAssetIds"), rationale=result.get("rationale"))
        return json.dumps(result, ensure_ascii=False)

    def tools(self):
        from mirra_agents.workspace_tools import make_workspace_tools
        return make_workspace_tools(self.list, self.view, self.select)

    def metadata(self):
        return {"workspaceId": self.state.get("workspaceId"), "continuationPlan": {
            "operation": self.state.get("operation"), "selectedAssetIds": self.state.get("selectedAssetIds") or [],
            "descriptions": {}, "rationale": self.state.get("rationale", "")}}
