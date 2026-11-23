"""Text-only model fixtures at the desktop's official SDK execution boundary.

The SDK's actual FunctionTool loop, HTTP retries and hosted tools are exercised
in test_agents_runtime. These fixtures keep desktop persistence/UI tests local.
"""
from typing import Any


def sdk_text_fixture(response: Any):
    def run(request, *, on_event=None, **runtime_options):
        text_parts=[]
        completed=False
        if on_event:
            on_event({"type":"turn_started","turn":1,"attempt":1,"maxAttempts":request.max_attempts,"reset":False})
        def delta(text):
            text_parts.append(text)
            if on_event:on_event({"type":"text_delta","delta":text,"turn":1,"attempt":1})
        def complete(payload):
            nonlocal completed
            completed=True
            if on_event:on_event({"type":"turn_completed","turn":1,"attempt":1,"response":payload,"usage":payload.get("usage") or {}})
        text=response(request.base_url,request.api_key,request.model,request.instructions,request.input,
            on_delta=delta,on_completed=complete,reasoning_effort=request.effort,
            reasoning_mode=request.mode,tools=[{"type":"web_search"}] if request.web_search_enabled else [])
        if not text_parts and text:delta(text)
        if not completed:complete({"output":[],"usage":{}})
        return {"text":text,"usage":{},"webSearchUsed":False,"attempts":1,"responses":[],"sources":[]}
    return run
