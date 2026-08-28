from __future__ import annotations

from ..common import *
from ..platform import *
from ..usage import *
from ..web_search import WebSearchService
from ..store import Store
from ..client import EasyClinClient

class ImageGenerationCancelled(Exception):
    pass


def _append_image_prompt_constraints(prompt: str, options: dict[str, Any]) -> str:
    aspect_ratio = str(options.get("aspectRatio") or "").strip()
    transparency = str(options.get("transparency") or "").strip().lower()
    suffixes = []
    if aspect_ratio and aspect_ratio != "auto":
        suffixes.append(f"以以下比例要求为准：强制生成比例为{aspect_ratio}的图片")
    if transparency == "transparent":
        suffixes.append("以以下透明度要求为准：强制生成透明背景的图片")
    elif transparency == "opaque":
        suffixes.append("以以下透明度要求为准：强制生成不透明背景的图片")
    if not suffixes:
        return prompt.strip()
    constraint_lines = set(suffixes)
    base_lines = [line for line in prompt.splitlines() if line.strip() not in constraint_lines]
    base_prompt = "\n".join(base_lines).strip()
    return "\n".join([base_prompt, *suffixes]) if base_prompt else "\n".join(suffixes)


class ImageTaskContext:
    def __init__(self) -> None:
        self.cancel_event = threading.Event()
        self._response_lock = threading.Lock()
        self._responses: dict[int, Any] = {}

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise ImageGenerationCancelled("生成已停止")

    def add_response(self, response: Any) -> None:
        with self._response_lock:
            if self.cancel_event.is_set():
                try:
                    response.close()
                finally:
                    raise ImageGenerationCancelled("生成已停止")
            self._responses[id(response)] = response

    def remove_response(self, response: Any) -> None:
        with self._response_lock:
            self._responses.pop(id(response), None)

    def cancel(self) -> None:
        self.cancel_event.set()
        with self._response_lock:
            responses = tuple(self._responses.values())
        for response in responses:
            try:
                response.close()
            except Exception:
                pass


class ImageReasoningMixin:
    @staticmethod
    def _call_with_task_context(
        method: Any,
        *args: Any,
        task_context: ImageTaskContext | None,
        **kwargs: Any,
    ) -> Any:
        side_effect = getattr(method, "side_effect", None)
        signature_target = side_effect if callable(side_effect) else method
        parameters = inspect.signature(signature_target).parameters
        accepts_task_context = "task_context" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if task_context is not None and accepts_task_context:
            kwargs["task_context"] = task_context
        return method(*args, **kwargs)

    def _register_image_task(self, request_id: str) -> ImageTaskContext:
        task_lock = getattr(self, "image_task_lock", None)
        if task_lock is None:
            task_lock = threading.Lock()
            self.image_task_lock = task_lock
        with task_lock:
            tasks = getattr(self, "image_tasks", None)
            if tasks is None:
                tasks = {}
                self.image_tasks = tasks
            if request_id in tasks:
                raise ValueError("图片任务编号已存在")
            context = ImageTaskContext()
            tasks[request_id] = context
            return context

    def _release_image_task(self, request_id: str) -> None:
        task_lock = getattr(self, "image_task_lock", None)
        if task_lock is None:
            return
        with task_lock:
            getattr(self, "image_tasks", {}).pop(request_id, None)

    def cancel_image_generation(self, request_id: str) -> dict[str, Any]:
        clean_request_id = str(request_id or "")[:80]
        task_lock = getattr(self, "image_task_lock", None)
        if not clean_request_id or task_lock is None:
            return {"ok": False, "error": "任务不存在或已经结束"}
        with task_lock:
            context = getattr(self, "image_tasks", {}).get(clean_request_id)
        if context is None:
            return {"ok": False, "error": "任务不存在或已经结束"}
        context.cancel()
        return {"ok": True, "requestId": clean_request_id}

    def _run_instant_image_continuation_planner(
        self,
        record: Any,
        secret: str,
        current_request: str,
        context: dict[str, Any],
        visible_assets: list[dict[str, Any]],
        task_context: ImageTaskContext | None = None,
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
        output = self._call_with_task_context(
            self.client.stream_response,
            record["base_url"],
            secret,
            IMAGE_CONTINUATION_PLANNER_MODEL,
            (
                "你是图片续作的隐藏轻量路由器。只判断本轮应做局部 edit 还是 generate 延续创作，"
                "并从素材目录中选择真正需要送给图片模型的 assetId。edit 必须选图；generate 可选图"
                "以保持角色、物体或世界观一致性，也可不选。完整保留用户本轮原始请求，不润色、"
                "不反思、不联网。你实际看到的图片按 visibleAssetIds 顺序附在文字后；必须为"
                "descriptionRequiredAssetIds 中每张图写一条客观、可复用的视觉描述。只输出 JSON："
                "上一轮及更早轮次用户上传的图片会保留在素材池中，但本轮不会自动附带，也不要默认"
                "让它们占用最终 16 个参考图槽位；如果某张图确实有助于保持主体、材质、风格或世界观"
                "一致性，应主动从素材目录中选入并加载。"
                '{"operation":"edit|generate","selected_asset_ids":[],"descriptions":'
                '[{"asset_id":"...","description":"..."}],"rationale":"..."}'
            ),
            self._agent_input(planning_input, visible_paths),
            reasoning_effort=IMAGE_CONTINUATION_PLANNER_EFFORT,
            task_context=task_context,
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
        task_context: ImageTaskContext | None = None,
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
        output = self._call_with_task_context(
            self.client.stream_response,
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
            task_context=task_context,
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
        task_context: ImageTaskContext | None = None,
    ) -> dict[str, Any]:
        undescribed_assets = [
            asset
            for asset in context.get("assets") or []
            if asset.get("assetId")
            and asset.get("assetId") not in visible_asset_ids
            and not str(asset.get("description") or "").strip()
        ]
        for batch_start in range(0, len(undescribed_assets), 16):
            batch = undescribed_assets[batch_start:batch_start + 16]
            descriptions = (
                self._describe_image_asset_batch(record, secret, batch, task_context)
                if task_context is not None
                else self._describe_image_asset_batch(record, secret, batch)
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
        task_context: ImageTaskContext | None = None,
        reasoning_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reasoning_started_at = time.perf_counter()
        config = dict(reasoning_config or resolve_image_reasoning_config(reasoning_mode))
        advanced_mode = bool(config.get("advanced"))
        model = str(config["model"])
        reasoning_effort = str(config["effort"])
        max_agent_turns = int(config["max_turns"])
        max_reference_count = int(config["max_references"])
        search_workers = int(config["search_workers"])
        search_parallel_queries = int(config["search_parallel_queries"])
        web_search_results = int(config["web_search_results"])
        visual_search_results = int(config["visual_search_results"])
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
        if advanced_mode:
            instructions += (
                "当前为高级模式，必须完整执行需求理解、信息缺口判断、方案设计、工具化检索、"
                "候选比较、视觉与事实风险复核、最终提示词整理这些阶段；不得因深度较低而关闭"
                "任何阶段或工具。深度仅用于限制最大思考轮数、并行搜索规模和候选结果池大小。"
            )
        if web_search_enabled:
            instructions += (
                "你可以按需调用网页与视觉参考搜索工具。涉及真实产品、地点、事件、人物、时效信息、"
                "历史考据或难以仅靠文字准确描述的视觉对象时，鼓励先搜索；纯想象创作或已有参考足够时"
                "可以完全不调用。视觉搜索后请检查缩略图，只有确实能提高构图、形态、材质或事实准确性"
                "的候选才通过 select_visual_references 选择；不合适时选择空列表。不要仅凭标题纳入图片。"
                "你可以在同一轮并行发起多个不同检索，也可以根据首轮结果在后续轮次继续搜索；在完成"
                "必要检索并比较全部候选后，再统一调用 select_visual_references 提交最终采用列表。"
                f"当前模式每轮最多建议并行发起 {search_parallel_queries} 个互补查询；每个网页查询将返回"
                f"最多 {web_search_results} 条结果，每个视觉查询将返回最多 {visual_search_results} 个"
                "已验证缩略图。高级模式应充分利用并行查询覆盖不同关键词、语言和信息源。"
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
                "其画面。上一轮及更早轮次用户上传的图片会保留在图片池中，不会默认附带或优先占用"
                "最终 16 个参考图槽位；若它们有助于保持角色、物体、材质、风格或世界观一致性，鼓励"
                "通过 plan_image_continuation 主动选入，再把操作和素材选择纳入后续搜索、反思及最终提示词。"
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
            if task_context is not None:
                task_context.check_cancelled()
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
            output = self._call_with_task_context(
                self.client.stream_response,
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
                task_context=task_context,
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
                    max_workers=min(search_workers, len(search_indexes)),
                    thread_name_prefix="image-web-search",
                ) as executor:
                    for index in search_indexes:
                        call = parsed_calls[index]
                        arguments = call["arguments"]
                        web_search_calls += 1
                        if call["name"] == "search_web":
                            search_futures[index] = executor.submit(
                                self._call_with_task_context,
                                self.web_search.search_web,
                                arguments.get("query"),
                                web_search_results,
                                task_context=task_context,
                            )
                        else:
                            search_futures[index] = executor.submit(
                                self._call_with_task_context,
                                self.web_search.search_visual_references,
                                arguments.get("query"),
                                visual_search_results,
                                task_context=task_context,
                            )

            for index in search_indexes:
                if task_context is not None:
                    task_context.check_cancelled()
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
        request_id = str(clean_options.get("requestId") or uuid.uuid4().hex)[:80]
        session_id = str(clean_options.get("sessionId") or request_id)[:80]
        parent_set_id = str(clean_options.get("parentSetId") or "")[:80]
        clean_options["requestId"] = request_id
        try:
            task_context = self._register_image_task(request_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            return self._generate_image(
                key_id,
                prompt,
                image_paths,
                clean_options,
                event_callback,
                task_context,
            )
        except ImageGenerationCancelled:
            getattr(self, "active_image_sets", set()).discard(request_id)
            self._release_image_session_activity(session_id)
            shutil.rmtree(
                app_data_dir() / "image-search-references" / request_id,
                ignore_errors=True,
            )
            try:
                cancel_round = getattr(self._image_session_store(), "cancel_round", None)
                if callable(cancel_round):
                    cancel_round(session_id, request_id)
            except (OSError, RuntimeError, ValueError):
                pass
            event = {
                "type": "set_cancelled",
                "requestId": request_id,
                "setId": request_id,
                "sessionId": session_id,
                "parentSetId": parent_set_id,
                "error": "生成已停止",
            }
            if event_callback is not None:
                try:
                    event_callback(event)
                except Exception:
                    pass
            return {
                "ok": False,
                "cancelled": True,
                "error": "生成已停止",
                "requestId": request_id,
                "setId": request_id,
                "sessionId": session_id,
                "parentSetId": parent_set_id,
            }
        finally:
            self._release_image_task(request_id)

    def _generate_image(
        self,
        key_id: str,
        prompt: str,
        image_paths: list[str],
        options: dict[str, Any] | None = None,
        event_callback: Any = None,
        task_context: ImageTaskContext | None = None,
    ) -> dict[str, Any]:
        clean_options = dict(options) if isinstance(options, dict) else {}
        if task_context is not None:
            task_context.check_cancelled()
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
        reasoning_config: dict[str, Any] | None = None
        if reasoning_mode != "instant":
            try:
                reasoning_config = resolve_image_reasoning_config(
                    reasoning_mode,
                    clean_options.get("reasoningModel"),
                    clean_options.get("reasoningEffort"),
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        requested_web_search = clean_options.get("webSearchEnabled")
        web_search_enabled = (
            reasoning_mode != "instant"
            and (
                reasoning_mode == "advanced"
                or requested_web_search is not False
            )
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
        required_input_asset_ids: list[str] = []
        required_input_paths: list[str] = []
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
                required_input_asset_ids = list(dict.fromkeys(registered_input_asset_ids))
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
                    task_context,
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
                        task_context,
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                discard_pending_assets()
                release_session_activity()
                return {"ok": False, "error": f"续作规划失败：{exc}"}
        if reasoning_mode != "instant":
            reasoning_effort = str((reasoning_config or {})["effort"])
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
                    task_context,
                    reasoning_config,
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
        final_prompt = _append_image_prompt_constraints(final_prompt, clean_options)
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
            required_input_paths = [
                str(asset_by_id[asset_id]["path"])
                for asset_id in required_input_asset_ids
                if asset_id in asset_by_id and Path(str(asset_by_id[asset_id].get("path") or "")).is_file()
            ]
            final_reference_asset_ids = list(
                dict.fromkeys([*required_input_asset_ids, *selected_asset_ids])
            )[:16]
            selected_asset_paths = [
                str(asset_by_id[asset_id]["path"])
                for asset_id in final_reference_asset_ids
                if asset_id in asset_by_id and Path(str(asset_by_id[asset_id].get("path") or "")).is_file()
            ]
            clean_options.update(
                {
                    "operation": str(continuation_plan["operation"]),
                    "continuationRationale": str(continuation_plan.get("rationale") or ""),
                    "selectedAssetIds": selected_asset_ids,
                    "inputAssetIds": final_reference_asset_ids,
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
        else:
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
                    staged_web_references = self._call_with_task_context(
                        self.web_search.stage_reference_records,
                        web_candidates,
                        web_reference_dir,
                        min(
                            int((reasoning_config or {})["max_references"]),
                            available_slots,
                        ),
                        task_context=task_context,
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
                "reasoningDepth": (
                    str((reasoning_config or {}).get("effort") or "")
                    if reasoning_mode == "advanced"
                    else ""
                ),
                "reasoningSummary": reasoning_summary,
                "reasoningDurationMs": reasoning_duration_ms,
                "reasoningUsage": reasoning_usage,
                "originalPrompt": original_prompt,
                "aspectRatio": str(clean_options.get("aspectRatio") or "auto"),
                "transparency": str(clean_options.get("transparency") or "auto"),
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
            aspectRatio=str(clean_options.get("aspectRatio") or "auto"),
            transparency=str(clean_options.get("transparency") or "auto"),
            reasoningMode=reasoning_mode,
            reasoningModel=reasoning_model,
            reasoningEffort=reasoning_effort,
            reasoningDepth=(
                str((reasoning_config or {}).get("effort") or "")
                if reasoning_mode == "advanced"
                else ""
            ),
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
            if task_context is not None:
                task_context.check_cancelled()
            emit("item_started", item_index)

            def on_partial(partial: dict[str, Any]) -> None:
                emit("item_partial", item_index, **partial)

            try:
                result = self._call_with_task_context(
                    self.image_generator.generate,
                    record["base_url"],
                    secret,
                    request,
                    session_store.root
                    / session_store._safe_id(session_id)
                    / str(round_data["directory"])
                    / "process-images"
                    / f"item-{item_index + 1:03d}",
                    on_partial=on_partial,
                    task_context=task_context,
                )
                if task_context is not None:
                    task_context.check_cancelled()
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
            if task_context is not None:
                task_context.check_cancelled()
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
                "aspectRatio": str(clean_options.get("aspectRatio") or "auto"),
                "transparency": str(clean_options.get("transparency") or "auto"),
            "reasoningMode": reasoning_mode,
            "reasoningModel": reasoning_model,
            "reasoningEffort": reasoning_effort,
            "reasoningDepth": (
                str((reasoning_config or {}).get("effort") or "")
                if reasoning_mode == "advanced"
                else ""
            ),
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
            webSearchEnabled=web_search_enabled,
            webSearchUsed=web_search_used,
            webSearchFailed=web_search_failed,
            webSearchResultCount=web_search_result_count,
            webReferenceCount=len(persisted_web_references),
            webReferences=persisted_web_references,
        )
        return result
