from __future__ import annotations

from ..common import *
from ..platform import *
from ..usage import *
from ..web_search import WebSearchService
from ..store import Store
from ..client import EasyClinClient
from ..image_editor import image_resolution_for_size
from ..transport import UnsentConnectionError, abort_response
from mirra_agents import AgentRequest, AgentRunFailure, run_agent
from mirra_agents.retry import AgentCancelled, failure_details, max_attempts, retry_sync
from ..prompt_targets import (format_prompt_instructions, choose_reasoning_pair, stored_model_profile, apply_reasoning_profile)

class ImageGenerationCancelled(Exception):
    pass


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
            cancelled = self.cancel_event.is_set()
            if not cancelled:
                self._responses[id(response)] = response
        if cancelled:
            abort_response(response)
            raise ImageGenerationCancelled("生成已停止")

    def remove_response(self, response: Any) -> None:
        with self._response_lock:
            self._responses.pop(id(response), None)

    def cancel(self) -> None:
        self.cancel_event.set()
        with self._response_lock:
            responses = tuple(self._responses.values())
            self._responses.clear()
        closers = [abort_response(response) for response in responses]
        # Give already-unblocked cleanup one small shared opportunity to finish.
        # A slow/foreign close must never hold up the stop RPC.
        deadline = time.monotonic() + .01
        for closer in closers:
            closer.join(max(0, deadline - time.monotonic()))


class ImageReasoningMixin:
    @staticmethod
    def _run_sdk_agent(request: AgentRequest, **kwargs: Any) -> dict[str, Any]:
        return run_agent(request, **kwargs)

    def _call_reasoning_with_retries(self, *args: Any, task_context: ImageTaskContext | None, on_retry: Any = None, **kwargs: Any) -> str:
        base_url, secret, model, instructions, user_input = args[:5]
        def progress(event: dict[str, Any]) -> None:
            if event["type"] == "text_delta" and kwargs.get("on_delta"):
                kwargs["on_delta"](event["delta"])
            elif event["type"] == "turn_completed" and kwargs.get("on_completed"):
                kwargs["on_completed"](event["response"])
            elif event["type"] == "retry" and on_retry is not None:
                on_retry(event["attempt"] - 1, event["delayMs"] / 1000, event["cause"])
        request = AgentRequest(base_url=base_url, api_key=secret, model=model,
            instructions=instructions, input=user_input,
            effort=str(kwargs.get("reasoning_effort") or ""),
            mode=str(kwargs.get("reasoning_mode") or ""),
            max_attempts=max_attempts("agent"))
        try:
            return str(self._run_sdk_agent(request,
                cancel_event=getattr(task_context, "cancel_event", None),
                on_event=progress)["text"])
        except AgentCancelled:
            raise ImageGenerationCancelled("生成已停止") from None

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
        if "reasoning_mode" in kwargs and "reasoning_mode" not in parameters and not any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        ):
            kwargs.pop("reasoning_mode")
        if task_context is not None and accepts_task_context:
            kwargs["task_context"] = task_context
        return method(*args, **kwargs)

    def _tier_reference_limit(self, record: Any) -> int:
        try:
            key_id = record["id"]
        except (KeyError, IndexError):
            return 16
        profile = stored_model_profile(getattr(self, "store", None), key_id)
        tier = profile.get("tier") if isinstance(profile, dict) else None
        if not isinstance(tier, dict):
            return 16
        if not tier.get("imageEditing"):
            return 0
        try:
            return max(0, min(16, int(tier.get("maxReferences", 16))))
        except (TypeError, ValueError):
            return 0

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
        try:
            record_id = record["id"]
        except (KeyError, IndexError):
            record_id = ""
        profile = stored_model_profile(getattr(self, "store", None), record_id)
        planner_model, planner_effort = choose_reasoning_pair(
            profile, IMAGE_CONTINUATION_PLANNER_MODEL, IMAGE_CONTINUATION_PLANNER_EFFORT,
            purpose="continuation",
        )
        output = self._call_reasoning_with_retries(
            record["base_url"],
            secret,
            planner_model,
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
            reasoning_effort=planner_effort,
            reasoning_mode="continuation",
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
        try:
            record_id = record["id"]
        except (KeyError, IndexError):
            record_id = ""
        profile = stored_model_profile(getattr(self, "store", None), record_id)
        planner_model, planner_effort = choose_reasoning_pair(
            profile, IMAGE_CONTINUATION_PLANNER_MODEL, IMAGE_CONTINUATION_PLANNER_EFFORT,
            purpose="continuation",
        )
        output = self._call_reasoning_with_retries(
            record["base_url"],
            secret,
            planner_model,
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
            reasoning_effort=planner_effort,
            reasoning_mode="continuation",
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
        reference_limit = self._tier_reference_limit(record)
        if undescribed_assets and reference_limit == 0:
            raise ValueError("当前密钥档位不允许参考图片")
        for batch_start in range(0, len(undescribed_assets), max(1, reference_limit)):
            batch = undescribed_assets[batch_start:batch_start + reference_limit]
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
        self, record: Any, secret: str, prompt: str, image_paths: tuple[Path, ...],
        reasoning_mode: str, web_search_enabled: bool, emit: Any,
        continuation_context: dict[str, Any] | None = None,
        visible_assets: list[dict[str, Any]] | None = None,
        task_context: ImageTaskContext | None = None,
        reasoning_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .agent_sdk import run_prompt_agent
        try:
            return run_prompt_agent(self, record, secret, prompt, image_paths,
                reasoning_mode, web_search_enabled, emit, continuation_context,
                visible_assets, task_context, reasoning_config)
        except AgentCancelled:
            raise ImageGenerationCancelled("生成已停止") from None

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
        record = self.store.get_key_record(str(key_id or ""))
        if record is None:
            return {"ok": False, "error": "请选择有效的 API Key"}
        secret = self.store.get_secret(record["id"])
        snapshot = self.store.latest_payload(record["id"]) or {}
        profile = snapshot.get("appModels")
        if not profile and not is_mirra_api_url(record["base_url"]):
            models = [model for model in snapshot.get("_models") or [] if model.startswith("gpt-image-")]
            if models:
                profile = {"defaultImage": "gpt-image-2" if "gpt-image-2" in models else models[0],
                           "imageModels": models, "svg": {"enabled": True}}

        def prepare_request(text, paths, requested_options):
            return prepare_image_generation(text, paths, requested_options, app_models=profile)

        try:
            reasoning_config = apply_reasoning_profile(
                profile, reasoning_mode, reasoning_config, resolve_image_reasoning_config("flash"),
            )
            if continuation_enabled and isinstance(profile, dict):
                tier = profile.get("tier") or {}
                if tier and not tier.get("imageEditing"):
                    raise ValueError("当前密钥档位未开放图片续作")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        # Continue through the same Agent core, including Instant's short,
        # non-expanding edit path. No separate router or pre-description pass.
        if continuation_enabled and reasoning_config is None:
            basic = resolve_image_reasoning_config("flash")
            try:
                main_model, main_effort = choose_reasoning_pair(profile, basic["model"], basic["effort"], purpose="continuation")
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            reasoning_config = {**basic, "mode": "instant", "model": main_model, "effort": main_effort,
                "max_turns": 1, "max_references": 0, "visual_search_results": 0,
                "depth": "即时继续编辑：只处理当前要求和必要素材，保持用户原始提示词，不扩写、不反思。"}
        use_reasoning = reasoning_config is not None
        requested_web_search = clean_options.get("webSearchEnabled")
        web_search_enabled = (
            use_reasoning and reasoning_mode != "instant"
            and (
                reasoning_mode == "advanced"
                or requested_web_search is not False
            )
        )
        tier = profile.get("tier") if isinstance(profile, dict) else None
        if isinstance(tier, dict) and not tier.get("webSearch"):
            web_search_enabled = False
        reference_limit = self._tier_reference_limit(record)
        if reasoning_config is not None:
            reasoning_config["outputOptions"] = dict(clean_options)
            reasoning_config["max_references"] = min(int(reasoning_config["max_references"]), reference_limit)
        clean_options.update(
            {
                "moderation": "auto" if record["base_url"] == OPENAI_API_URL else "low",
                "stream": True,
                "partialImages": 3,
                "continuation": continuation_enabled,
            }
        )
        try:
            validation_options = dict(clean_options)
            if continuation_enabled:
                validation_options["operation"] = "generate"
            request = prepare_request(prompt, image_paths, validation_options)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        clean_options["operation"] = request.operation
        submitted_image_paths = tuple(request.image_paths)
        if not continuation_enabled and submitted_image_paths:
            clean_options["inputReferencePaths"] = [
                str(path) for path in submitted_image_paths
            ]

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
        agent_metadata: dict[str, Any] = {}
        web_search_used = False
        web_search_failed = False
        web_search_result_count = 0
        staged_web_references: list[dict[str, Any]] = []
        persisted_web_references: list[dict[str, Any]] = []
        staged_inspected_references: list[dict[str, Any]] = []
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
                    include_workspace=True,
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
                        include_workspace=True,
                    )
                if len(registered_input_asset_ids) > reference_limit:
                    raise ValueError(f"当前档位最多允许 {reference_limit} 张参考图")
                visible_asset_ids = list(
                    dict.fromkeys(
                        [
                            *list(continuation_context.get("parentOutputAssetIds") or [])[:max(0, reference_limit - len(registered_input_asset_ids))],
                            *registered_input_asset_ids,
                        ]
                    )
                )
                if len(visible_asset_ids) > reference_limit:
                    raise ValueError(f"父轮输出与本轮新参考图合计不能超过 {reference_limit} 张")
                continuation_context["requiredInputAssetIds"] = registered_input_asset_ids
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
            except (OSError, RuntimeError, ValueError) as exc:
                discard_pending_assets()
                release_session_activity()
                return {"ok": False, "error": f"续作规划失败：{exc}",
                        "errorDetails": exc.details if isinstance(exc, AgentRunFailure) else None}
        if use_reasoning:
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
                # Inspection bytes/previews are staged separately, never copied
                # into options or persisted as a base64-bearing public event.
                agent_metadata = {name: agent_result[name] for name in IMAGE_AGENT_METADATA_FIELDS
                                  if name in agent_result and name != "webInspectedReferences"}
                if continuation_enabled:
                    continuation_plan = agent_result.get("continuationPlan")
            except (RuntimeError, OSError, ValueError) as exc:
                discard_pending_assets()
                release_session_activity()
                shutil.rmtree(web_reference_dir, ignore_errors=True)
                details = exc.details if isinstance(exc, AgentRunFailure) else None
                emit("react_failed", mode=reasoning_mode, error=str(exc), errorDetails=details)
                return {"ok": False, "error": f"思维处理失败：{exc}", "errorDetails": details}
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
            final_reference_asset_ids = list(
                dict.fromkeys(selected_asset_ids)
            )[:reference_limit]
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
                request = prepare_request(
                    final_prompt,
                    selected_asset_paths,
                    clean_options,
                )
            except ValueError as exc:
                discard_pending_assets()
                release_session_activity()
                return {"ok": False, "error": f"续作规划失败：{exc}"}
        else:
            request = prepare_request(
                final_prompt,
                [str(path) for path in request.image_paths],
                clean_options,
            )
        if use_reasoning:
            try:
                available_slots = 0 if request.output_format == "svg" else max(0, reference_limit - len(request.image_paths))
                web_candidates = list(agent_result.get("webCandidates") or [])
                inspected_candidates = list(agent_result.get("inspectedCandidates") or [])
                if inspected_candidates:
                    staged_inspected_references = self._call_with_task_context(
                        self.web_search.stage_reference_records, inspected_candidates,
                        web_reference_dir / "inspected", min(12, len(inspected_candidates)), task_context=task_context)
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
                request = prepare_request(final_prompt, combined_paths, clean_options)
            except (OSError, RuntimeError, ValueError) as exc:
                discard_pending_assets()
                release_session_activity()
                shutil.rmtree(web_reference_dir, ignore_errors=True)
                emit("react_failed", mode=reasoning_mode, error=str(exc))
                return {"ok": False, "error": f"思维处理失败：{exc}"}
        clean_options.pop("webInspectedReferences", None)
        clean_options.update(
            {
                "reasoningMode": reasoning_mode,
                "size": request.requested_size,
                "resolution": image_resolution_for_size(request.requested_size, clean_options.get("resolution")),
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
                "imageModel": request.fields["model"],
                "aspectRatio": str(clean_options.get("aspectRatio") or "auto"),
                "transparency": str(clean_options.get("transparency") or "auto"),
                "webSearchEnabled": web_search_enabled,
                "webSearchUsed": web_search_used,
                "webSearchFailed": web_search_failed,
                "webSearchResultCount": web_search_result_count,
                "webReferenceCount": len(web_reference_paths),
                **agent_metadata,
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

        if staged_inspected_references:
            try:
                agent_metadata["webInspectedReferences"] = session_store.persist_web_references(
                    session_id, request_id, staged_inspected_references, inspected_only=True)
            except (OSError, RuntimeError, ValueError) as exc:
                active_sets.discard(request_id)
                release_session_activity()
                shutil.rmtree(web_reference_dir, ignore_errors=True)
                try:
                    session_store.delete_set(session_id, request_id)
                except (OSError, RuntimeError):
                    pass
                return {"ok": False, "error": f"无法保存已检视的参考图：{exc}"}

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
            imageModel=request.fields["model"],
            size=request.requested_size,
            resolution=clean_options["resolution"],
            quality=request.requested_quality,
            complexity=str(clean_options.get("complexity") or "auto"),
            outputFormat=request.output_format,
            frameCount=int(clean_options.get("frame_count") or 9),
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
            **agent_metadata,
        )

        def generate_one(item_index: int) -> dict[str, Any]:
            if task_context is not None:
                task_context.check_cancelled()
            emit("item_started", item_index)

            def on_partial(partial: dict[str, Any]) -> None:
                emit("item_partial", item_index, **partial)

            try:
                def generate_attempt(attempt: int) -> dict[str, Any]:
                    if task_context is not None:
                        task_context.check_cancelled()
                    return self._call_with_task_context(
                        self.image_generator.generate,
                        record["base_url"], secret, request,
                        session_store.folder_for_set(session_id, request_id)
                        / "process-images" / f"item-{item_index + 1:03d}",
                        on_partial=on_partial, task_context=task_context,
                    )

                try:
                    result = retry_sync(
                        generate_attempt, stage="image",
                        cancel_event=getattr(task_context, "cancel_event", None),
                        on_retry=lambda details: emit("request_retry", item_index,
                            **{key: value for key, value in details.items() if key != "type"}),
                        secrets=(secret,),
                    )
                except AgentCancelled:
                    raise ImageGenerationCancelled("生成已停止") from None
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
                details = exc.details if isinstance(exc, AgentRunFailure) else failure_details(
                    exc, "image", 1, max_attempts("image"), (secret,))
                if details["code"] == "cancelled":
                    raise ImageGenerationCancelled("生成已停止") from None
                try:
                    session_store.record_failure(session_id, request_id, item_index, error)
                except (RuntimeError, OSError):
                    pass
                emit("item_failed", item_index, error=error, errorDetails=details)
                return {"ok": False, "itemIndex": item_index, "error": error, "errorDetails": details}

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
            "imageModel": request.fields["model"],
            "size": request.requested_size,
            "resolution": clean_options["resolution"],
            "quality": request.requested_quality,
            "complexity": str(clean_options.get("complexity") or "auto"),
            "outputFormat": request.output_format,
            "frameCount": int(clean_options.get("frame_count") or 9),
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
            **agent_metadata,
        }
        if not successful:
            result["error"] = "所有图片生成请求均失败：" + str(items[0].get("error") or "未返回有效图片")
            result["errorDetails"] = items[0].get("errorDetails")
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
            **agent_metadata,
        )
        return result
