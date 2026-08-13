from __future__ import annotations

import json
import os
import re
import sys
import threading
import traceback
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


NETWORK_ERROR_LOG_MAX_BYTES = 5 * 1024 * 1024
NETWORK_ERROR_LOG_PATH = (
    Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    / "API_TOOLS"
    / "network-errors.jsonl"
)
_NETWORK_ERROR_LOG_LOCK = threading.Lock()
_RESPONSE_HEADER_NAMES = {
    "content-type",
    "cf-ray",
    "date",
    "retry-after",
    "server",
    "via",
    "x-envoy-upstream-service-time",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "x-request-id",
}
_SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "b64json",
    "dataurl",
    "image",
    "images",
    "input",
    "instructions",
    "prompt",
    "secret",
    "token",
}


def new_network_request_id() -> str:
    return uuid.uuid4().hex[:12]


def _redact_text(value: Any, secrets: tuple[str, ...] = ()) -> str:
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)(api[_-]?key|authorization|token|secret)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "[REDACTED_IMAGE]", text)
    text = re.sub(r"[A-Za-z0-9+/]{512,}={0,2}", "[REDACTED_LARGE_DATA]", text)
    return text


def _safe_endpoint(url: Any) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return "[invalid-url]"
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    query_names = sorted(
        {
            key
            for key, _value in urllib.parse.parse_qsl(
                parsed.query, keep_blank_values=True
            )
        }
    )
    query = f"?fields={','.join(query_names)}" if query_names else ""
    return f"{parsed.scheme}://{hostname}{port}{parsed.path}{query}"


def _safe_proxy(value: Any) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(value or ""))
    except ValueError:
        return "[invalid-proxy]"
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{hostname}{port}"


def _safe_value(value: Any, secrets: tuple[str, ...], depth: int = 0) -> Any:
    if depth > 6:
        return "[max-depth]"
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:80]:
            key = str(raw_key)
            normalized = key.casefold().replace("_", "").replace("-", "")
            clean[key] = (
                "[REDACTED]"
                if normalized in _SENSITIVE_KEYS
                else _safe_value(raw_value, secrets, depth + 1)
            )
        return clean
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, secrets, depth + 1) for item in value[:80]]
    if isinstance(value, str):
        return _redact_text(value, secrets)[:8192]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(value, secrets)[:8192]


def _safe_response_headers(headers: Any, secrets: tuple[str, ...]) -> dict[str, str]:
    if headers is None:
        return {}
    try:
        items = headers.items()
    except AttributeError:
        return {}
    return {
        str(name).lower(): _redact_text(value, secrets)[:1024]
        for name, value in items
        if str(name).lower() in _RESPONSE_HEADER_NAMES
    }


def _safe_response_body(body: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(body, (dict, list, tuple)):
        return _safe_value(body, secrets)
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return _redact_text(text, secrets)[:4096]
    return _safe_value(parsed, secrets)


def _exception_chain(error: BaseException, secrets: tuple[str, ...]) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        item: dict[str, Any] = {
            "type": type(current).__name__,
            "message": _redact_text(current, secrets)[:4096],
        }
        for name in ("errno", "winerror", "verify_code", "verify_message"):
            value = getattr(current, name, None)
            if value is not None:
                item[name] = _safe_value(value, secrets)
        chain.append(item)
        reason = getattr(current, "reason", None)
        current = (
            reason
            if isinstance(reason, BaseException)
            else current.__cause__ or current.__context__
        )
    return chain


def log_network_error(
    stage: str,
    *,
    request_id: str,
    method: str,
    url: str,
    error: BaseException,
    status: int | None = None,
    elapsed_ms: float | None = None,
    attempt: int | None = None,
    response_headers: Any = None,
    response_body: Any = "",
    details: dict[str, Any] | None = None,
    secrets: tuple[str, ...] = (),
) -> None:
    try:
        proxies = {
            scheme: _safe_proxy(proxy)
            for scheme, proxy in urllib.request.getproxies().items()
            if scheme in {"http", "https"} and proxy
        }
        trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        record = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "requestId": str(request_id),
            "pid": os.getpid(),
            "thread": threading.current_thread().name,
            "frozen": bool(getattr(sys, "frozen", False)),
            "stage": str(stage),
            "method": str(method).upper(),
            "endpoint": _safe_endpoint(url),
            "status": status,
            "attempt": attempt,
            "elapsedMs": round(float(elapsed_ms), 1) if elapsed_ms is not None else None,
            "proxy": proxies,
            "responseHeaders": _safe_response_headers(response_headers, secrets),
            "responseBody": _safe_response_body(response_body, secrets),
            "exceptions": _exception_chain(error, secrets),
            "traceback": _redact_text(trace, secrets)[-12_000:],
            "details": _safe_value(details or {}, secrets),
        }
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        path = NETWORK_ERROR_LOG_PATH
        with _NETWORK_ERROR_LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            encoded_size = len(encoded.encode("utf-8"))
            if path.is_file() and path.stat().st_size + encoded_size > NETWORK_ERROR_LOG_MAX_BYTES:
                previous = path.with_name("network-errors.previous.jsonl")
                previous.unlink(missing_ok=True)
                path.replace(previous)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
    except Exception:
        pass