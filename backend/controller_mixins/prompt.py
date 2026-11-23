from __future__ import annotations

from ..common import *
from ..platform import *
from ..usage import *
from ..web_search import WebSearchService
from ..store import Store
from ..client import EasyClinClient
from ..prompt_targets import format_prompt_instructions, choose_reasoning_pair, stored_model_profile
from ..image_editor import reference_preview_data_url
from mirra_agents import AgentRunFailure

class PromptMixin:
    @staticmethod
    def _agent_input(prompt: str, image_paths: tuple[Path, ...] = ()) -> Any:
        if not image_paths:
            return prompt
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for image_path in image_paths:
            content.append(
                {
                    "type": "input_image",
                    "image_url": reference_preview_data_url(image_path.read_bytes(), max_side=1024),
                }
            )
        return [{"role": "user", "content": content}]

    def polish_prompt(
        self,
        key_id: str,
        prompt: str,
        options: dict[str, Any] | None = None,
        event_callback: Any = None,
    ) -> dict[str, Any]:
        if callable(options) and event_callback is None:
            event_callback, options = options, None
        clean_options = dict(options) if isinstance(options, dict) else {}
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            return {"ok": False, "error": "请输入需要润色的提示词"}
        record = self.store.get_key_record(str(key_id or ""))
        if record is None:
            return {"ok": False, "error": "请选择有效的 API Key"}
        secret = self.store.get_secret(record["id"])
        try:
            model, effort = choose_reasoning_pair(
                stored_model_profile(self.store, record["id"]),
                PROMPT_POLISH_MODEL, PROMPT_POLISH_REASONING_EFFORT, purpose="polish",
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        def emit(event_type: str, **details: Any) -> None:
            if event_callback is not None:
                event_callback({"type": event_type, **details})

        emit(
            "prompt_polish_started",
            model=model,
            reasoningEffort=effort,
        )
        try:
            polished = self._call_reasoning_with_retries(
                record["base_url"],
                secret,
                model,
                (
                    "你是专业视觉提示词编辑器。先根据用户输入自动判断属于艺术创作、工作创作、"
                    "图像编辑、文字润色或其他场景，再采用匹配该场景的表达方式补全主体、构图、"
                    "光线、材质、风格、约束和交付目标。保留用户意图，不虚构冲突要求。"
                    "只输出可直接用于图片生成的最终提示词，不要解释判断过程。"
                ) + "\n" + format_prompt_instructions(clean_options),
                clean_prompt,
                on_delta=lambda delta: emit("prompt_polish_delta", delta=delta),
                reasoning_effort=effort,
                reasoning_mode="polish",
                task_context=None,
                on_retry=lambda retry, delay, error: emit("request_retry", stage="agent", attempt=retry + 1, maxAttempts=3, delayMs=round(delay * 1000), cause=error),
            ).strip()
        except RuntimeError as exc:
            details = exc.details if isinstance(exc, AgentRunFailure) else None
            emit("prompt_polish_failed", error=str(exc), errorDetails=details)
            return {"ok": False, "error": str(exc), "errorDetails": details}
        if not polished:
            return {"ok": False, "error": "润色模型未返回提示词"}
        emit(
            "prompt_polish_completed",
            prompt=polished,
            model=model,
            reasoningEffort=effort,
        )
        return {
            "ok": True,
            "prompt": polished,
            "model": model,
            "reasoningEffort": effort,
        }
