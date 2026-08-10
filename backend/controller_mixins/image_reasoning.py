from __future__ import annotations

from ..common import *
from ..platform import *
from ..usage import *
from ..web_search import WebSearchService
from ..store import Store
from ..client import EasyClinClient

class ImageReasoningMixin:
    def _run_instant_image_continuation_planner(
        self,
        record: Any,
        secret: str,
        current_request: str,
        context: dict[str, Any],
        visible_assets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        visible_asset_ids = [
            str(asset.get("assetId") or "")
            for asset in visible_assets
            if asset.get("assetId")
        ]
        required_description_ids = [
            str(asset.get("assetId") or "")
            for asset in visible_assets
            if asset.get("assetId") and not str(asset.get("description") or "").strip()
        ]
        available_asset_ids = [
            str(asset.get("assetId") or "")
            for asset in context.get("assets") or []
            if asset.get("assetId")
        ]
        planning_input = image_continuation_prompt(
            current_request,
            context,
            visible_assets,
        )
        visible_paths = tuple(
            Path(str(asset.get("path") or ""))
            for asset in visible_assets
            if Path(str(asset.get("path") or "")).is_file()
        )
        output = self.client.stream_response(
            record["base_url"],
            secret,
            IMAGE_CONTINUATION_PLANNER_MODEL,
            (
                "你是图片续作的隐藏轻量路由器。只判断本轮应做局部 edit 还是 generate 延续创作，"
                "并从素材目录中选择真正需要送给图片模型的 assetId。edit 必须选图；generate 可选图"
                "以保持角色、物体或世界观一致性，也可不选。完整保留用户本轮原始请求，不润色、"
                "不反思、不联网。你实际看到的图片按 visibleAssetIds 顺序附在文字后；必须为"
                "descriptionRequiredAssetIds 中每张图写一条客观、可复用的视觉描述。只输出 JSON："
                '{"operation":"edit|generate","selected_asset_ids":[],"descriptions":'
                '[{"asset_id":"...","description":"..."}],"rationale":"..."}'
            ),
            self._agent_input(planning_input, visible_paths),
            reasoning_effort=IMAGE_CONTINUATION_PLANNER_EFFORT,
        )
        return parse_image_continuation_plan(
            output,
            available_asset_ids,
            visible_asset_ids,
            required_description_ids,
        )

    def _describe_image_asset_batch(
        self,
        record: Any,
        secret: str,
        assets: list[dict[str, Any]],
    ) -> dict[str, str]:
        if not assets or len(assets) > 16:
            raise ValueError("单批素材描述必须包含 1 到 16 张图片")
        asset_ids = [str(asset.get("assetId") or "") for asset in assets]
        image_paths = tuple(
            Path(str(asset.get("path") or ""))
            for asset in assets
            if Path(str(asset.get("path") or "")).is_file()
        )
        if len(image_paths) != len(assets) or any(not asset_id for asset_id in asset_ids):
            raise ValueError("待描述素材不可用")
        output = self.client.stream_response(
            record["base_url"],
            secret,
            IMAGE_CONTINUATION_PLANNER_MODEL,
            (
                "你是图片素材描述器。按输入 assetIds 与随后图片的相同顺序，为每张图写一条客观、"
                "紧凑、可跨轮复用的视觉描述，覆盖主体身份特征、构图、环境和关键风格；不要推测"
                "看不见的信息，不润色用户请求，不反思，不联网。只输出 JSON："
                '{"descriptions":[{"asset_id":"...","description":"..."}]}'
            ),
            self._agent_input(
                json.dumps({"assetIds": asset_ids}, ensure_ascii=False),
                image_paths,
            ),
            reasoning_effort=IMAGE_CONTINUATION_PLANNER_EFFORT,
        )
        return parse_image_asset_descriptions(output, asset_ids)

    def _cache_undescribed_image_assets(
        self,
        record: Any,
        secret: str,
        session_store: ImageSessionStore,
        session_id: str,
        parent_set_id: str,
        context: dict[str, Any],
        visible_asset_ids: list[str],
        additional_asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        undescribed_assets = [
            asset
            for asset in context.get("assets") or []
            if asset.get("assetId")
            and asset.get("assetId") not in visible_asset_ids
            and not str(asset.get("description") or "").strip()
        ]
        for batch_start in range(0, len(undescribed_assets), 16):
            descriptions = self._describe_image_asset_batch(
                record,
                secret,
                undescribed_assets[batch_start:batch_start + 16],
            )
            session_store.update_asset_descriptions(session_id, descriptions)
        if not undescribed_assets:
            return context
        return session_store.continuation_context(
            session_id,
            parent_set_id,
            additional_asset_ids,
        )

    def _run_image_prompt_agent(
        self,
        record: Any,
        secret: str,
        prompt: str,
        image_paths: tuple[Path, ...],
        reasoning_mode: str,
        web_search_enabled: bool,
        emit: Any,
        continuation_context: dict[str, Any] | None = None,
        visible_assets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        reasoning_started_at = time.perf_counter()
        config = IMAGE_REASONING_MODES[reasoning_mode]
        model = str(config["model"])
        reasoning_effort = str(config["effort"])
        max_agent_turns = int(config["max_turns"])
        max_reference_count = int(config["max_references"])
        complete_text = ""
        published_length = 0
        current_turn = 0
        current_turn_start = 0
        candidate_by_id: dict[str, dict[str, Any]] = {}
        selected_ids: list[str] = []
        selected_rationale = ""
        selection_submitted = False
        web_search_calls = 0
        web_search_succeeded = False
        reasoning_usage: dict[str, Any] = {}
        continuation_enabled = bool(continuation_context)
        clean_visible_assets = list(visible_assets or [])
        available_assets = list((continuation_context or {}).get("assets") or [])
        visible_asset_ids = [
            str(asset.get("assetId") or "")
            for asset in clean_visible_assets
            if asset.get("assetId")
        ]
        required_description_ids = [
            str(asset.get("assetId") or "")
            for asset in clean_visible_assets
            if asset.get("assetId") and not str(asset.get("description") or "").strip()
        ]
        asset_by_id = {
            str(asset.get("assetId") or ""): asset
            for asset in available_assets
            if asset.get("assetId")
        }
        continuation_plan: dict[str, Any] | None = None
        plan_submitted = False

        def on_delta(delta: str) -> None:
            nonlocal complete_text, published_length
            complete_text += delta
            marker_index = complete_text.find(PROMPT_RESULT_MARKER)
            safe_length = marker_index if marker_index >= 0 else max(
                0, len(complete_text) - len(PROMPT_RESULT_MARKER) + 1
            )
            if safe_length > published_length:
                visible_delta = complete_text[published_length:safe_length]
                emit("react_turn_delta", turn=current_turn, delta=visible_delta)
                emit("react_summary_delta", delta=visible_delta)
                published_length = safe_length

        def flush_turn_tail() -> None:
            nonlocal published_length
            marker_index = complete_text.find(PROMPT_RESULT_MARKER)
            safe_length = marker_index if marker_index >= 0 else len(complete_text)
            if safe_length <= published_length:
                return
            visible_delta = complete_text[published_length:safe_length]
            emit("react_turn_delta", turn=current_turn, delta=visible_delta)
            emit("react_summary_delta", delta=visible_delta)
            published_length = safe_length

        emit(
            "react_started",
            mode=reasoning_mode,
            model=model,
            reasoningEffort=reasoning_effort,
            webSearchEnabled=web_search_enabled,
        )
        instructions = (
            "你是一个用于图片创作的轻量 ReAct 智能体。根据用户文字和可选参考图，从需求理解"
            "出发，自主选择必要的分析、方案设计、工具化检查与反思步骤；不要机械套用固定步骤数。"
            f"当前深度建议：{config['depth']}。"
            "无论当前深度如何，第一步都必须先判断：用户真正要你完成什么；哪些约束和视觉目标已经"
            "明确；哪些对象、概念、事实、外观或时效信息是你不确定、不理解或仅凭现有输入无法可靠"
            "判断的；这些信息缺口是否会实质影响生成结果。不得因为处于 Flash 模式就跳过这项"
            "判断。若缺口不影响结果，可以直接采用合理假设；若缺口会影响事实、主体外观、构图或用户"
            "意图，则在工具可用时先做针对性检索，再依据结果定稿。"
            "如果用户明确指定现有 IP、作品、品牌、世界观、人物或角色，必须保留其名称、身份、"
            "所属设定和标志性视觉特征，按用户要求直接构思；不要仅因对象属于知名 IP 就改写为"
            "原创角色、致敬款、同类替代或主动规避相似性。只有用户明确要求原创、重新设计或避开"
            "现有 IP 时，才进行原创化处理。"
            "请流畅输出面向用户、可审计的简短工作摘要，描述正在判断的创作环境、关键需求、"
            "候选方案与可用性评估；不要披露隐藏内部推理、逐 token 思维链或敏感信息。"
        )
        if web_search_enabled:
            instructions += (
                "你可以按需调用网页与视觉参考搜索工具。涉及真实产品、地点、事件、人物、时效信息、"
                "历史考据或难以仅靠文字准确描述的视觉对象时，鼓励先搜索；纯想象创作或已有参考足够时"
                "可以完全不调用。视觉搜索后请检查缩略图，只有确实能提高构图、形态、材质或事实准确性"
                "的候选才通过 select_visual_references 选择；不合适时选择空列表。不要仅凭标题纳入图片。"
                "你可以在同一轮并行发起多个不同检索，也可以根据首轮结果在后续轮次继续搜索；在完成"
                "必要检索并比较全部候选后，再统一调用 select_visual_references 提交最终采用列表。"
                f"当前模式最多采用 {max_reference_count} 张真正有帮助的视觉参考；宁缺毋滥。"
            )
        if continuation_enabled:
            instructions += (
                "这是跨轮图片续作。输入包含本轮原始请求、截至父轮的完整用户输入历史、可审计摘要、"
                "素材文字目录，以及按 visibleAssetIds 顺序附带的父轮产出图。第一轮必须调用一次"
                " plan_image_continuation：独立判断本轮是局部 edit 还是 generate 延续创作，并按"
                " assetId 选择最终图片模型需要的会话素材。edit 必须选图；generate 也可以选图保持"
                "角色、物体和世界观一致性。必须为 descriptionRequiredAssetIds 中每张首次读取图片"
                "提供客观、可复用的描述。更早素材先依据缓存描述筛选；工具返回被选素材原图后再检查"
                "其画面，并把操作和素材选择纳入后续搜索、反思及最终提示词。"
            )
        instructions += (
            f"摘要结束后单独输出标记 {PROMPT_RESULT_MARKER}，标记后只写可直接提交给图片模型的最终提示词。"
        )
        initial_prompt = (
            image_continuation_prompt(prompt, continuation_context or {}, clean_visible_assets)
            if continuation_enabled
            else prompt
        )
        initial_input = self._agent_input(initial_prompt, image_paths)
        if isinstance(initial_input, list):
            current_input: list[dict[str, Any]] = list(initial_input)
        else:
            current_input = [{"role": "user", "content": initial_input}]
        output = ""
        total_agent_turns = max_agent_turns + (1 if continuation_enabled else 0)
        for turn_index in range(total_agent_turns):
            current_turn = turn_index + 1
            current_turn_start = published_length
            emit(
                "react_turn_started",
                turn=current_turn,
                title="正在分析问题" if current_turn == 1 else "正在完善方案",
            )
            completed_payloads: list[dict[str, Any]] = []
            if continuation_enabled and not plan_submitted:
                tools = [IMAGE_CONTINUATION_PLAN_TOOL]
                tool_choice = "required"
            else:
                tools = (
                    list(IMAGE_AGENT_WEB_TOOLS)
                    if web_search_enabled
                    and turn_index < total_agent_turns - 1
                    and not selection_submitted
                    else None
                )
                tool_choice = "auto" if tools else None
            output = self.client.stream_response(
                record["base_url"],
                secret,
                model,
                instructions,
                current_input,
                on_delta=on_delta,
                reasoning_effort=reasoning_effort,
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=True if tools else None,
                on_completed=completed_payloads.append,
                allow_empty_text=bool(tools),
            )
            response = completed_payloads[-1] if completed_payloads else {}
            turn_usage = response_usage_metrics(response, model)
            reasoning_usage = merge_reasoning_usage(reasoning_usage, turn_usage)
            response_output = [
                item for item in response.get("output") or [] if isinstance(item, dict)
            ]
            function_calls = [
                item for item in response_output if item.get("type") == "function_call"
            ]
            flush_turn_tail()
            turn_text = complete_text[current_turn_start:published_length].strip()
            emit(
                "react_turn_completed",
                turn=current_turn,
                title=(
                    "正在拆解方案"
                    if function_calls and current_turn == 1
                    else "正在核对参考信息"
                    if function_calls
                    else "方案已确定"
                ),
                text=turn_text,
                usage=turn_usage,
                reasoningUsage=reasoning_usage,
            )
            if not function_calls:
                break
            current_input.extend(response_output)
            parsed_calls: list[dict[str, Any]] = []
            for tool_call in function_calls:
                parsed_call = {
                    "callId": str(tool_call.get("call_id") or ""),
                    "name": str(tool_call.get("name") or ""),
                    "arguments": {},
                    "error": None,
                    "output": None,
                }
                try:
                    arguments = json.loads(str(tool_call.get("arguments") or "{}"))
                    if not isinstance(arguments, dict):
                        raise ValueError("工具参数必须是对象")
                    parsed_call["arguments"] = arguments
                    emit(
                        "react_tool_started",
                        turn=current_turn,
                        callId=parsed_call["callId"],
                        tool=parsed_call["name"],
                        arguments=arguments,
                    )
                except (ValueError, json.JSONDecodeError) as exc:
                    parsed_call["error"] = exc
                parsed_calls.append(parsed_call)

            search_futures: dict[int, concurrent.futures.Future[Any]] = {}
            search_indexes = [
                index
                for index, call in enumerate(parsed_calls)
                if call["error"] is None
                and call["name"] in {"search_web", "search_visual_references"}
            ]
            if search_indexes:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(4, len(search_indexes)),
                    thread_name_prefix="image-web-search",
                ) as executor:
                    for index in search_indexes:
                        call = parsed_calls[index]
                        arguments = call["arguments"]
                        web_search_calls += 1
                        if call["name"] == "search_web":
                            search_futures[index] = executor.submit(
                                self.web_search.search_web,
                                arguments.get("query"),
                                arguments.get("max_results", 5),
                            )
                        else:
                            search_futures[index] = executor.submit(
                                self.web_search.search_visual_references,
                                arguments.get("query"),
                                arguments.get("max_results", 6),
                            )

            for index in search_indexes:
                call = parsed_calls[index]
                try:
                    tool_result = search_futures[index].result()
                    web_search_succeeded = True
                    if call["name"] == "search_web":
                        call["output"] = json.dumps(tool_result, ensure_ascii=False)
                        call["resultCount"] = len(tool_result.get("results") or [])
                        continue
                    public_results: list[dict[str, Any]] = []
                    visual_output: list[dict[str, str]] = []
                    for candidate in tool_result.get("results") or []:
                        candidate_id = str(candidate.get("id") or "")
                        if not candidate_id:
                            continue
                        candidate_by_id[candidate_id] = candidate
                        public_candidate = {
                            key: value
                            for key, value in candidate.items()
                            if not key.startswith("_") and key != "previewDataUrl"
                        }
                        public_results.append(public_candidate)
                        visual_output.extend(
                            [
                                {
                                    "type": "input_text",
                                    "text": json.dumps(public_candidate, ensure_ascii=False),
                                },
                                {
                                    "type": "input_image",
                                    "image_url": str(candidate["previewDataUrl"]),
                                },
                            ]
                        )
                    if not visual_output:
                        visual_output.append(
                            {
                                "type": "input_text",
                                "text": json.dumps(
                                    {
                                        "query": tool_result.get("query"),
                                        "results": [],
                                        "message": "未找到可验证的视觉候选",
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        )
                    call["output"] = visual_output
                    call["visualEvent"] = {
                        "query": tool_result.get("query"),
                        "resultCount": len(public_results),
                        "webSearchResultCount": len(candidate_by_id),
                    }
                except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                    call["error"] = exc

            for call in parsed_calls:
                call_id = call["callId"]
                name = call["name"]
                error = call["error"]
                if error is not None:
                    tool_output: Any = json.dumps(
                        {"ok": False, "error": str(error)[:500]}, ensure_ascii=False
                    )
                    emit(
                        "react_tool_failed",
                        turn=current_turn,
                        callId=call_id,
                        tool=name,
                        error=str(error),
                    )
                elif name in {"search_web", "search_visual_references"}:
                    tool_output = call["output"]
                    if call.get("visualEvent"):
                        emit("react_visual_results", **call["visualEvent"])
                    emit(
                        "react_tool_completed",
                        turn=current_turn,
                        callId=call_id,
                        tool=name,
                        resultCount=(
                            call.get("visualEvent", {}).get("resultCount")
                            if call.get("visualEvent")
                            else call.get("resultCount", 0)
                        ),
                    )
                elif name == "plan_image_continuation":
                    try:
                        continuation_plan = parse_image_continuation_plan(
                            call["arguments"],
                            list(asset_by_id),
                            visible_asset_ids,
                            required_description_ids,
                        )
                        plan_submitted = True
                        plan_output: list[dict[str, str]] = [
                            {
                                "type": "input_text",
                                "text": json.dumps(
                                    {
                                        "operation": continuation_plan["operation"],
                                        "selectedAssetIds": continuation_plan["selectedAssetIds"],
                                        "rationale": continuation_plan["rationale"],
                                        "message": "已加载选中的更早素材；父轮可见素材沿用初始输入。",
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ]
                        for asset_id in continuation_plan["selectedAssetIds"]:
                            if asset_id in visible_asset_ids:
                                continue
                            asset = asset_by_id[asset_id]
                            asset_path = Path(str(asset.get("path") or ""))
                            if not asset_path.is_file():
                                continue
                            plan_output.extend(
                                [
                                    {
                                        "type": "input_text",
                                        "text": json.dumps(
                                            {
                                                "assetId": asset_id,
                                                "description": str(asset.get("description") or ""),
                                            },
                                            ensure_ascii=False,
                                        ),
                                    },
                                    {
                                        "type": "input_image",
                                        "image_url": image_preview_data_url(
                                            asset_path.read_bytes(), max_side=1024
                                        ),
                                    },
                                ]
                            )
                        tool_output = plan_output
                        emit(
                            "react_continuation_planned",
                            operation=continuation_plan["operation"],
                            selectedAssetIds=continuation_plan["selectedAssetIds"],
                            rationale=continuation_plan["rationale"],
                        )
                        emit(
                            "react_tool_completed",
                            turn=current_turn,
                            callId=call_id,
                            tool=name,
                            resultCount=len(continuation_plan["selectedAssetIds"]),
                        )
                    except (OSError, ValueError) as exc:
                        tool_output = json.dumps(
                            {"ok": False, "error": str(exc)[:500]}, ensure_ascii=False
                        )
                        emit(
                            "react_tool_failed",
                            turn=current_turn,
                            callId=call_id,
                            tool=name,
                            error=str(exc),
                        )
                elif name == "select_visual_references":
                    arguments = call["arguments"]
                    try:
                        selection_submitted = True
                        requested_ids = arguments.get("reference_ids") or []
                        if not isinstance(requested_ids, list):
                            raise ValueError("reference_ids 必须是数组")
                        selected_ids = []
                        for candidate_id in requested_ids[:max_reference_count]:
                            clean_id = str(candidate_id)
                            if clean_id in candidate_by_id and clean_id not in selected_ids:
                                selected_ids.append(clean_id)
                        selected_rationale = str(arguments.get("rationale") or "")[:600]
                        tool_result = {
                            "selectedIds": selected_ids,
                            "acceptedCount": len(selected_ids),
                        }
                        tool_output = json.dumps(tool_result, ensure_ascii=False)
                        emit(
                            "react_visual_selected",
                            selectedCount=len(selected_ids),
                            rationale=selected_rationale,
                        )
                        emit(
                            "react_tool_completed",
                            turn=current_turn,
                            callId=call_id,
                            tool=name,
                            resultCount=len(selected_ids),
                        )
                    except (ValueError, json.JSONDecodeError) as exc:
                        tool_output = json.dumps(
                            {"ok": False, "error": str(exc)[:500]}, ensure_ascii=False
                        )
                        emit(
                            "react_tool_failed",
                            turn=current_turn,
                            callId=call_id,
                            tool=name,
                            error=str(exc),
                        )
                else:
                    tool_output = json.dumps(
                        {"ok": False, "error": "未知工具"}, ensure_ascii=False
                    )
                    emit(
                        "react_tool_failed",
                        turn=current_turn,
                        callId=call_id,
                        tool=name,
                        error="未知工具",
                    )
                current_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": tool_output,
                    }
                )
        else:
            raise RuntimeError("ReAct 工具调用次数超过限制")
        if continuation_enabled and continuation_plan is None:
            raise RuntimeError("ReAct 未返回续作操作与素材规划")
        output = complete_text or output
        marker_index = output.find(PROMPT_RESULT_MARKER)
        if marker_index < 0:
            raise RuntimeError("ReAct 未返回最终提示词标记")
        summary = output[:marker_index].strip()
        final_prompt = output[marker_index + len(PROMPT_RESULT_MARKER):].strip()
        if not summary:
            raise RuntimeError("ReAct 未返回可审计工作摘要")
        if not final_prompt:
            raise RuntimeError("ReAct 未返回最终提示词")
        reasoning_duration_ms = max(
            1,
            round((time.perf_counter() - reasoning_started_at) * 1000),
        )
        emit(
            "react_completed",
            mode=reasoning_mode,
            model=model,
            reasoningEffort=reasoning_effort,
            summary=summary,
            prompt=final_prompt,
            webSearchEnabled=web_search_enabled,
            webSearchUsed=web_search_calls > 0,
            webSearchFailed=web_search_calls > 0 and not web_search_succeeded,
            webSearchResultCount=len(candidate_by_id),
            webReferenceCount=len(selected_ids),
            reasoningDurationMs=reasoning_duration_ms,
            reasoningUsage=reasoning_usage,
        )
        return {
            "prompt": final_prompt,
            "summary": summary,
            "model": model,
            "webSearchEnabled": web_search_enabled,
            "webSearchUsed": web_search_calls > 0,
            "webSearchFailed": web_search_calls > 0 and not web_search_succeeded,
            "webSearchResultCount": len(candidate_by_id),
            "webCandidates": [candidate_by_id[candidate_id] for candidate_id in selected_ids],
            "webSelectionRationale": selected_rationale,
            "continuationPlan": continuation_plan,
            "reasoningDurationMs": reasoning_duration_ms,
            "reasoningUsage": reasoning_usage,
        }

    def generate_image(
        self,
        key_id: str,
        prompt: str,
        image_paths: list[str],
        options: dict[str, Any] | None = None,
        event_callback: Any = None,
    ) -> dict[str, Any]:
        clean_options = dict(options) if isinstance(options, dict) else {}
        try:
            image_count = int(clean_options.get("imageCount") or 1)
        except (TypeError, ValueError):
            image_count = 1
        if not 1 <= image_count <= 9:
            return {"ok": False, "error": "图片数量必须在 1 到 9 之间"}
        request_id = str(clean_options.get("requestId") or uuid.uuid4().hex)[:80]
        session_id = str(clean_options.get("sessionId") or request_id)[:80]
        parent_set_id = str(clean_options.get("parentSetId") or "")[:80]
        continuation_enabled = bool(clean_options.get("continuation") or parent_set_id)
        if continuation_enabled and not parent_set_id:
            return {"ok": False, "error": "续作请求缺少来源轮次"}
        reasoning_mode = str(clean_options.get("reasoningMode") or "instant").lower()
        if reasoning_mode not in {"instant", *IMAGE_REASONING_MODES}:
            return {"ok": False, "error": "无效的思维模式"}
        requested_web_search = clean_options.get("webSearchEnabled")
        web_search_enabled = (
            reasoning_mode != "instant"
            and requested_web_search is not False
        )
        clean_options.update(
            {
                "background": "auto",
                "moderation": "low",
                "stream": True,
                "partialImages": 3,
                "continuation": continuation_enabled,
            }
        )
        try:
            validation_options = dict(clean_options)
            if continuation_enabled:
                validation_options["operation"] = "generate"
            request = prepare_image_generation(prompt, image_paths, validation_options)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        clean_options["operation"] = request.operation
        submitted_image_paths = tuple(request.image_paths)
        if not continuation_enabled and submitted_image_paths:
            clean_options["inputReferencePaths"] = [
                str(path) for path in submitted_image_paths
            ]

        record = self.store.get_key_record(str(key_id or ""))
        if record is None:
            return {"ok": False, "error": "请选择有效的 API Key"}
        secret = self.store.get_secret(record["id"])
        session_store = self._image_session_store()
        session_activity_reserved = False
        pending_asset_source = False

        if continuation_enabled:
            if not self._reserve_image_session_activity(session_id):
                return {"ok": False, "error": "该图片会话正在生成中，请稍后再试"}
            session_activity_reserved = True

        def discard_pending_assets() -> None:
            if not pending_asset_source:
                return
            try:
                session_store.discard_asset_source(session_id, request_id)
            except OSError:
                pass

        def release_session_activity() -> None:
            nonlocal session_activity_reserved
            if not session_activity_reserved:
                return
            self._release_image_session_activity(session_id)
            session_activity_reserved = False

        def emit(event_type: str, item_index: int | None = None, **details: Any) -> None:
            if event_callback is None:
                return
            event = {
                "type": event_type,
                "requestId": request_id,
                "setId": request_id,
                "sessionId": session_id,
                "parentSetId": parent_set_id,
                **details,
            }
            if item_index is not None:
                event["itemIndex"] = item_index
            try:
                event_callback(event)
            except Exception:
                pass

        original_prompt = request.prompt
        final_prompt = original_prompt
        reasoning_summary = ""
        reasoning_model = ""
        reasoning_effort = ""
        reasoning_duration_ms = 0
        reasoning_usage: dict[str, Any] = {}
        web_search_used = False
        web_search_failed = False
        web_search_result_count = 0
        staged_web_references: list[dict[str, Any]] = []
        persisted_web_references: list[dict[str, Any]] = []
        web_reference_paths: tuple[Path, ...] = ()
        web_reference_dir = app_data_dir() / "image-search-references" / request_id
        continuation_context: dict[str, Any] = {}
        visible_assets: list[dict[str, Any]] = []
        continuation_plan: dict[str, Any] | None = None
        selected_asset_ids: list[str] = []
        selected_asset_paths: list[str] = []
        if continuation_enabled:
            try:
                continuation_context = session_store.continuation_context(
                    session_id,
                    parent_set_id,
                )
                if not continuation_context:
                    release_session_activity()
                    return {"ok": False, "error": "找不到续作来源轮次"}
                registered_inputs = session_store.register_assets(
                    session_id,
                    request.image_paths,
                    request_id,
                    "input",
                )
                pending_asset_source = bool(request.image_paths)
                registered_input_asset_ids = [
                    str(asset.get("assetId") or "")
                    for asset in registered_inputs
                    if asset.get("assetId")
                ]
                if registered_inputs:
                    continuation_context = session_store.continuation_context(
                        session_id,
                        parent_set_id,
                        registered_input_asset_ids,
                    )
                visible_asset_ids = list(
                    dict.fromkeys(
                        [
                            *list(continuation_context.get("parentOutputAssetIds") or []),
                            *registered_input_asset_ids,
                        ]
                    )
                )
                if len(visible_asset_ids) > 16:
                    raise ValueError("父轮输出与本轮新参考图合计不能超过 16 张")
                continuation_context = self._cache_undescribed_image_assets(
                    record,
                    secret,
                    session_store,
                    session_id,
                    parent_set_id,
                    continuation_context,
                    visible_asset_ids,
                    registered_input_asset_ids,
                )
                asset_by_id = {
                    str(asset.get("assetId") or ""): asset
                    for asset in continuation_context.get("assets") or []
                    if asset.get("assetId")
                }
                visible_assets = [
                    asset_by_id[asset_id]
                    for asset_id in visible_asset_ids
                    if asset_id in asset_by_id
                ]
                if reasoning_mode == "instant":
                    continuation_plan = self._run_instant_image_continuation_planner(
                        record,
                        secret,
                        original_prompt,
                        continuation_context,
                        visible_assets,
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                discard_pending_assets()
                release_session_activity()
                return {"ok": False, "error": f"续作规划失败：{exc}"}
        if reasoning_mode != "instant":
            reasoning_effort = str(IMAGE_REASONING_MODES[reasoning_mode]["effort"])
            try:
                agent_result = self._run_image_prompt_agent(
                    record,
                    secret,
                    original_prompt,
                    tuple(
                        Path(str(asset.get("path") or ""))
                        for asset in visible_assets
                        if Path(str(asset.get("path") or "")).is_file()
                    )
                    if continuation_enabled
                    else request.image_paths,
                    reasoning_mode,
                    web_search_enabled,
                    emit,
                    continuation_context if continuation_enabled else None,
                    visible_assets if continuation_enabled else None,
                )
                final_prompt = str(agent_result["prompt"])
                reasoning_summary = str(agent_result["summary"])
                reasoning_model = str(agent_result["model"])
                reasoning_duration_ms = int(agent_result.get("reasoningDurationMs") or 0)
                reasoning_usage = dict(agent_result.get("reasoningUsage") or {})
                web_search_enabled = bool(agent_result.get("webSearchEnabled"))
                web_search_used = bool(agent_result.get("webSearchUsed"))
                web_search_failed = bool(agent_result.get("webSearchFailed"))
                web_search_result_count = int(agent_result.get("webSearchResultCount") or 0)
                if continuation_enabled:
                    continuation_plan = agent_result.get("continuationPlan")
            except (RuntimeError, OSError, ValueError) as exc:
                discard_pending_assets()
                release_session_activity()
                shutil.rmtree(web_reference_dir, ignore_errors=True)
                emit("react_failed", mode=reasoning_mode, error=str(exc))
                return {"ok": False, "error": f"思维处理失败：{exc}"}
        if continuation_enabled:
            if not isinstance(continuation_plan, dict):
                discard_pending_assets()
                release_session_activity()
                return {"ok": False, "error": "续作规划未返回有效结果"}
            selected_asset_ids = list(continuation_plan.get("selectedAssetIds") or [])
            descriptions = dict(continuation_plan.get("descriptions") or {})
            try:
                session_store.update_asset_descriptions(session_id, descriptions)
            except (OSError, RuntimeError) as exc:
                discard_pending_assets()
                release_session_activity()
                return {"ok": False, "error": f"无法保存素材描述：{exc}"}
            asset_by_id = {
                str(asset.get("assetId") or ""): asset
                for asset in continuation_context.get("assets") or []
                if asset.get("assetId")
            }
            selected_asset_paths = [
                str(asset_by_id[asset_id]["path"])
                for asset_id in selected_asset_ids
                if asset_id in asset_by_id and Path(str(asset_by_id[asset_id].get("path") or "")).is_file()
            ]
            clean_options.update(
                {
                    "operation": str(continuation_plan["operation"]),
                    "continuationRationale": str(continuation_plan.get("rationale") or ""),
                    "selectedAssetIds": selected_asset_ids,
                    "inputAssetIds": selected_asset_ids,
                    "inputReferencePaths": selected_asset_paths,
                }
            )
            try:
                request = prepare_image_generation(
                    final_prompt,
                    selected_asset_paths,
                    clean_options,
                )
            except ValueError as exc:
                discard_pending_assets()
                release_session_activity()
                return {"ok": False, "error": f"续作规划失败：{exc}"}
        elif reasoning_mode != "instant":
            request = prepare_image_generation(
                final_prompt,
                [str(path) for path in request.image_paths],
                clean_options,
            )
        if reasoning_mode != "instant":
            try:
                available_slots = max(0, 16 - len(request.image_paths))
                web_candidates = list(agent_result.get("webCandidates") or [])
                if web_candidates and available_slots:
                    staged_web_references = self.web_search.stage_reference_records(
                        web_candidates,
                        web_reference_dir,
                        min(
                            int(IMAGE_REASONING_MODES[reasoning_mode]["max_references"]),
                            available_slots,
                        ),
                    )
                    web_reference_paths = tuple(
                        Path(reference["path"]) for reference in staged_web_references
                    )
                combined_paths = [str(path) for path in request.image_paths]
                combined_paths.extend(str(path) for path in web_reference_paths)
                request = prepare_image_generation(final_prompt, combined_paths, clean_options)
            except (OSError, RuntimeError, ValueError) as exc:
                discard_pending_assets()
                release_session_activity()
                shutil.rmtree(web_reference_dir, ignore_errors=True)
                emit("react_failed", mode=reasoning_mode, error=str(exc))
                return {"ok": False, "error": f"思维处理失败：{exc}"}
        clean_options.update(
            {
                "reasoningMode": reasoning_mode,
                "reasoningModel": reasoning_model,
                "reasoningEffort": reasoning_effort,
                "reasoningSummary": reasoning_summary,
                "reasoningDurationMs": reasoning_duration_ms,
                "reasoningUsage": reasoning_usage,
                "originalPrompt": original_prompt,
                "webSearchEnabled": web_search_enabled,
                "webSearchUsed": web_search_used,
                "webSearchFailed": web_search_failed,
                "webSearchResultCount": web_search_result_count,
                "webReferenceCount": len(web_reference_paths),
                "operation": request.operation,
            }
        )
        try:
            round_data = session_store.begin_round(
                session_id,
                request_id,
                request.prompt,
                image_count,
                len(request.image_paths),
                clean_options,
                parent_set_id=parent_set_id,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            discard_pending_assets()
            release_session_activity()
            shutil.rmtree(web_reference_dir, ignore_errors=True)
            return {"ok": False, "error": f"无法创建图片集：{exc}"}
        active_sets = getattr(self, "active_image_sets", None)
        if active_sets is None:
            active_sets = set()
            self.active_image_sets = active_sets
        active_sets.add(request_id)

        if not continuation_enabled and submitted_image_paths:
            try:
                registered_inputs = session_store.register_assets(
                    session_id,
                    submitted_image_paths,
                    request_id,
                    "input",
                )
                input_asset_ids = [
                    str(asset.get("assetId") or "")
                    for asset in registered_inputs
                    if asset.get("assetId")
                ]
                session_store.update_round_options(
                    session_id,
                    request_id,
                    {
                        "inputAssetIds": input_asset_ids,
                        "selectedAssetIds": input_asset_ids,
                    },
                )
            except (OSError, RuntimeError, ValueError) as exc:
                active_sets.discard(request_id)
                release_session_activity()
                try:
                    session_store.delete_set(session_id, request_id)
                except (OSError, RuntimeError):
                    pass
                return {"ok": False, "error": f"无法保存参考素材：{exc}"}

        if staged_web_references:
            try:
                persisted_web_references = session_store.persist_web_references(
                    session_id,
                    request_id,
                    staged_web_references,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                active_sets.discard(request_id)
                release_session_activity()
                shutil.rmtree(web_reference_dir, ignore_errors=True)
                try:
                    session_store.delete_set(session_id, request_id)
                except (OSError, RuntimeError):
                    pass
                return {"ok": False, "error": f"无法保存网络参考图：{exc}"}

        emit(
            "set_started",
            requestedCount=image_count,
            prompt=request.prompt,
            referenceCount=len(request.image_paths),
            operation=request.operation,
            continuation=continuation_enabled,
            continuationRationale=str((continuation_plan or {}).get("rationale") or ""),
            selectedAssetIds=selected_asset_ids,
            roundNumber=int(round_data["roundNumber"]),
            originalPrompt=original_prompt,
            reasoningMode=reasoning_mode,
            reasoningModel=reasoning_model,
            reasoningEffort=reasoning_effort,
            reasoningSummary=reasoning_summary,
            reasoningDurationMs=reasoning_duration_ms,
            reasoningUsage=reasoning_usage,
            webSearchEnabled=web_search_enabled,
            webSearchUsed=web_search_used,
            webSearchFailed=web_search_failed,
            webSearchResultCount=web_search_result_count,
            webReferenceCount=len(persisted_web_references),
            webReferences=persisted_web_references,
        )

        def generate_one(item_index: int) -> dict[str, Any]:
            emit("item_started", item_index)

            def on_partial(partial: dict[str, Any]) -> None:
                emit("item_partial", item_index, **partial)

            try:
                result = self.image_generator.generate(
                    record["base_url"],
                    secret,
                    request,
                    session_store.root
                    / session_store._safe_id(session_id)
                    / str(round_data["directory"])
                    / "process-images"
                    / f"item-{item_index + 1:03d}",
                    on_partial=on_partial,
                )
                source_path = Path(result["path"])
                try:
                    result["managedPath"] = str(source_path)
                    result = session_store.persist_result(
                        session_id,
                        request_id,
                        item_index,
                        source_path,
                        result,
                    )
                except OSError as exc:
                    raise RuntimeError(f"持久化图片集失败：{exc}") from exc
                result["itemIndex"] = item_index
                emit("item_completed", item_index, result=result)
                return result
            except (RuntimeError, OSError, ValueError) as exc:
                error = str(exc)
                try:
                    session_store.record_failure(session_id, request_id, item_index, error)
                except (RuntimeError, OSError):
                    pass
                emit("item_failed", item_index, error=error)
                return {"ok": False, "itemIndex": item_index, "error": error}

        items: list[dict[str, Any]] = []
        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(3, image_count),
                thread_name_prefix="image-generation",
            ) as executor:
                futures = [executor.submit(generate_one, index) for index in range(image_count)]
                for future in concurrent.futures.as_completed(futures):
                    items.append(future.result())
        except BaseException:
            active_sets.discard(request_id)
            release_session_activity()
            shutil.rmtree(web_reference_dir, ignore_errors=True)
            raise
        items.sort(key=lambda item: int(item.get("itemIndex") or 0))
        successful = [item for item in items if item.get("ok")]
        result = {
            "ok": bool(successful),
            "requestId": request_id,
            "setId": request_id,
            "requestedCount": image_count,
            "items": items,
            "prompt": request.prompt,
            "originalPrompt": original_prompt,
            "reasoningMode": reasoning_mode,
            "reasoningModel": reasoning_model,
            "reasoningEffort": reasoning_effort,
            "reasoningSummary": reasoning_summary,
            "reasoningDurationMs": reasoning_duration_ms,
            "reasoningUsage": reasoning_usage,
            "referenceCount": len(request.image_paths),
            "operation": request.operation,
            "transportOperation": "edit" if request.image_paths else "generate",
            "continuation": continuation_enabled,
            "continuationRationale": str((continuation_plan or {}).get("rationale") or ""),
            "selectedAssetIds": selected_asset_ids,
            "webSearchEnabled": web_search_enabled,
            "webSearchUsed": web_search_used,
            "webSearchFailed": web_search_failed,
            "webSearchResultCount": web_search_result_count,
            "webReferenceCount": len(persisted_web_references),
            "webReferences": persisted_web_references,
        }
        if not successful:
            result["error"] = "所有图片生成请求均失败"
        try:
            session_store.complete_round(session_id, request_id)
        finally:
            active_sets.discard(request_id)
            release_session_activity()
            shutil.rmtree(web_reference_dir, ignore_errors=True)
        result.update(
            {
                "sessionId": session_id,
                "parentSetId": parent_set_id,
                "roundNumber": int(round_data["roundNumber"]),
            }
        )
        emit(
            "set_completed",
            items=items,
            ok=bool(successful),
            operation=request.operation,
            continuation=continuation_enabled,
            continuationRationale=str((continuation_plan or {}).get("rationale") or ""),
            selectedAssetIds=selected_asset_ids,
        )
        return result
