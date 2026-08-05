from __future__ import annotations

import base64
import binascii
import concurrent.futures
import ctypes
import hashlib
from html import unescape
from html.parser import HTMLParser
import http.client
import ipaddress
import io
import json
import math
import multiprocessing
import os
import socket
import shutil
import ssl
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ElementTree
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
import winreg
from multiprocessing.connection import Client, Listener

PROCESS_STARTED_AT = time.perf_counter()

import webview
from image_editor import (
    ImageGenerationService,
    ImageSessionStore,
    image_preview_data_url,
    prepare_image_generation,
)
from PIL import Image
from winotify import Notification, audio

APP_NAME = "DJYX_APITOOL"
WINDOW_TITLE = "DJYX_APITOOL"
APP_VERSION = "1.0.26"
TITLE_BAR_MODES = {"default", "minimal", "original"}
BACKGROUND_UI_MODES = {"delayed", "active", "low_power"}
GITHUB_REPOSITORY = os.environ.get(
    "API_TOOLS_GITHUB_REPOSITORY", "Binceenigne/easyapitool"
)
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
RELEASE_ASSET_NAME = "API_TOOLS.exe"
RELEASE_BRANCH = "imagen"
RELEASE_TAG_PREFIX = "imagen-v"
MUTEX_NAME = "Local\\API_TOOLS_EasyClin_Quota_Monitor"
SHOW_EVENT_NAME = "Local\\API_TOOLS_EasyClin_Show_Window"
DEFAULT_BASE_URL = "https://work.easyclin.cn/v1"
FOREGROUND_INTERVAL = 60
BACKGROUND_INTERVAL = 300
BACKGROUND_UI_RELEASE_DELAY = 300
RETENTION_DAYS = 30
LIMIT_CHANGE_DISPLAY_SECONDS = 600
BUSINESS_TIMEZONE = timezone(timedelta(hours=8), name="UTC+8")
STATIC_CACHE_SCHEMA = 1
STATIC_UI_VERSION = "44"
IMAGE_STREAM_DEBUG_LOG_MAX_BYTES = 20 * 1024 * 1024
WEB_SEARCH_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
WEB_REFERENCE_IMAGE_MAX_BYTES = 16 * 1024 * 1024
WEB_REFERENCE_MAX_COUNT = 6
MAIN_PAGE_NAME = "API_TOOLS_响应式悬浮窗完整版_v3.html"
LUCIDE_VERSION = "0.468.0"
LUCIDE_SHA256 = "3411692820cb8d47543f69496aa25fd603a358f4498046f41c508a5a3342210e"
LUCIDE_MIRRORS = (
    (
        "npmmirror 文件镜像",
        "https://registry.npmmirror.com/lucide/0.468.0/files/dist/umd/lucide.min.js",
        "script",
    ),
    (
        "npmmirror 包镜像",
        "https://cdn.npmmirror.com/packages/lucide/0.468.0/lucide-0.468.0.tgz",
        "archive",
    ),
)

ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
SW_SHOW = 5
SW_RESTORE = 9
WM_NCLBUTTONDOWN = 0x00A1
WM_SYSCOMMAND = 0x0112
SC_MINIMIZE = 0xF020
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
UPDATE_CHECK_INTERVAL = 7 * 24 * 60 * 60
RESTART_READY_ENV = "API_TOOLS_RESTART_READY"
MANUAL_REFRESH_COOLDOWN_SECONDS = 5
CF_DIB = 8
GMEM_MOVEABLE = 0x0002
DEFAULT_WINDOW_WIDTH = 920
DEFAULT_WINDOW_HEIGHT = 680
MIN_WINDOW_WIDTH = 220
MIN_WINDOW_HEIGHT = 96
MAX_WINDOW_WIDTH = 8192
MAX_WINDOW_HEIGHT = 8192
WINDOW_SIZE_SAVE_DELAY = 0.25
PROMPT_POLISH_MODEL = "gpt-5.6-terra"
PROMPT_POLISH_REASONING_EFFORT = "medium"
PROMPT_RESULT_MARKER = "<<<FINAL_PROMPT>>>"
IMAGE_CONTINUATION_OPERATIONS = {"edit", "generate"}
IMAGE_REASONING_MODES = {
    "flash": {
        "model": "gpt-5.6-luna",
        "effort": "medium",
        "max_turns": 3,
        "max_references": 3,
        "depth": "Flash 模式：仍须先判断自己要完成什么、哪些要求已明确、是否存在自己不理解或会影响结果的信息缺口；在此基础上迅速选择一个可执行方案。若开启搜索且缺口重要，立即做必要检索并根据结果快速复核，然后形成生图提示词进入迭代。Flash 压缩的是比较和反思轮次，不是省略任务理解与信息缺口判断。",
    },
    "medium": {
        "model": "gpt-5.6-terra",
        "effort": "medium",
        "max_turns": 5,
        "max_references": 4,
        "depth": "Medium 模式：进行均衡的需求拆解、方案设计和适度信息获取，检查关键可用性后形成生图方案。",
    },
    "high": {
        "model": "gpt-5.6-terra",
        "effort": "high",
        "max_turns": 7,
        "max_references": 6,
        "depth": "High 模式：详细拆解需求和视觉方案，主动获取有价值的信息，比较主要候选方案，并对构图、事实准确性和生成风险做一轮明确反思后再定稿。",
    },
    "extra": {
        "model": "gpt-5.6-terra",
        "effort": "xhigh",
        "max_turns": 10,
        "max_references": 6,
        "depth": "Max 模式：先拟定多个足够详细的候选方案，主动且可多轮搜索网页与图片，交叉核对信息并阅读视觉候选；频繁反思遗漏、冲突、构图、材质和事实风险，只有确认信息与方案均充分周全后才形成最终生图提示词。",
    },
    "max": {
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
        "max_turns": 12,
        "max_references": 8,
        "depth": "Max 模式：先拟定多个足够详细的候选方案，主动且可多轮搜索网页与图片，交叉核对信息并阅读视觉候选；频繁反思遗漏、冲突、构图、材质和事实风险，只有确认信息与方案均充分周全后才形成最终生图提示词。",
    },
}
IMAGE_WEB_SEARCH_MODES = set(IMAGE_REASONING_MODES)
IMAGE_CONTINUATION_PLANNER_MODEL = "gpt-5.6-luna"
IMAGE_CONTINUATION_PLANNER_EFFORT = "minimal"


def image_asset_record(image_path: Path) -> dict[str, Any]:
    resolved_path = image_path.expanduser().resolve()
    digest = hashlib.sha256(resolved_path.read_bytes()).hexdigest()
    return {
        "assetId": f"asset-{digest[:24]}",
        "description": "",
        "sourceSetIds": [],
        "sourceRoles": ["input"],
        "path": str(resolved_path),
    }


def image_continuation_prompt(
    current_request: str,
    context: dict[str, Any],
    visible_assets: list[dict[str, Any]],
) -> str:
    history = [
        {
            "setId": str(item.get("setId") or ""),
            "roundNumber": int(item.get("roundNumber") or 0),
            "userPrompt": str(item.get("userPrompt") or ""),
            "reasoningSummary": str(item.get("reasoningSummary") or ""),
            "operation": str(item.get("operation") or "generate"),
            "inputAssetIds": list(item.get("inputAssetIds") or []),
            "outputAssetIds": list(item.get("outputAssetIds") or []),
        }
        for item in context.get("history") or []
    ]
    catalog = [
        {
            "assetId": str(asset.get("assetId") or ""),
            "description": str(asset.get("description") or "")
            or "尚无缓存描述；除非该素材列在 visibleAssetIds 中，否则不要假定其画面内容。",
            "sourceSetIds": list(asset.get("sourceSetIds") or []),
            "sourceRoles": list(asset.get("sourceRoles") or []),
        }
        for asset in context.get("assets") or []
        if asset.get("assetId")
    ]
    payload = {
        "currentRequest": str(current_request),
        "history": history,
        "assetCatalog": catalog,
        "visibleAssetIds": [
            str(asset.get("assetId") or "")
            for asset in visible_assets
            if asset.get("assetId")
        ],
        "descriptionRequiredAssetIds": [
            str(asset.get("assetId") or "")
            for asset in visible_assets
            if asset.get("assetId") and not str(asset.get("description") or "").strip()
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_image_asset_descriptions(
    value: Any,
    expected_asset_ids: list[str] | tuple[str, ...],
) -> dict[str, str]:
    payload = value
    if isinstance(payload, str):
        clean_value = payload.strip()
        if clean_value.startswith("```"):
            first_line_end = clean_value.find("\n")
            clean_value = clean_value[first_line_end + 1:] if first_line_end >= 0 else ""
            if clean_value.endswith("```"):
                clean_value = clean_value[:-3]
        try:
            payload = json.loads(clean_value.strip())
        except json.JSONDecodeError as exc:
            raise ValueError("素材描述未返回有效 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("descriptions"), list):
        raise ValueError("素材描述必须返回 descriptions 数组")
    expected_ids = list(dict.fromkeys(str(asset_id) for asset_id in expected_asset_ids))
    expected_id_set = set(expected_ids)
    descriptions: dict[str, str] = {}
    for item in payload["descriptions"]:
        if not isinstance(item, dict):
            raise ValueError("素材描述项必须是对象")
        asset_id = str(item.get("asset_id") or "").strip()
        description = str(item.get("description") or "").strip()
        if asset_id not in expected_id_set:
            raise ValueError("素材描述包含未读取的图片")
        if not description:
            raise ValueError("素材描述不能为空")
        descriptions[asset_id] = description[:1200]
    if set(descriptions) != expected_id_set:
        raise ValueError("素材描述必须覆盖本批全部图片")
    return {asset_id: descriptions[asset_id] for asset_id in expected_ids}


def parse_image_continuation_plan(
    value: Any,
    available_assets: int | list[str] | tuple[str, ...],
    visible_asset_ids: list[str] | tuple[str, ...] | set[str] = (),
    required_description_ids: list[str] | tuple[str, ...] | set[str] = (),
) -> dict[str, Any]:
    payload = value
    if isinstance(payload, str):
        clean_value = payload.strip()
        if clean_value.startswith("```"):
            first_line_end = clean_value.find("\n")
            clean_value = clean_value[first_line_end + 1:] if first_line_end >= 0 else ""
            if clean_value.endswith("```"):
                clean_value = clean_value[:-3]
        try:
            payload = json.loads(clean_value.strip())
        except json.JSONDecodeError as exc:
            raise ValueError("续作规划未返回有效 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("续作规划必须是 JSON 对象")

    operation = str(payload.get("operation") or "").strip().lower()
    if operation not in IMAGE_CONTINUATION_OPERATIONS:
        raise ValueError("续作规划必须选择 edit 或 generate")

    if not isinstance(available_assets, int):
        available_ids = list(dict.fromkeys(str(asset_id) for asset_id in available_assets))
        available_id_set = set(available_ids)
        raw_asset_ids = payload.get("selected_asset_ids")
        if not isinstance(raw_asset_ids, list):
            raise ValueError("续作规划必须返回素材 ID 数组")
        selected_asset_ids: list[str] = []
        for raw_asset_id in raw_asset_ids:
            if not isinstance(raw_asset_id, str):
                raise ValueError("续作素材 ID 必须是字符串")
            asset_id = raw_asset_id.strip()
            if asset_id not in available_id_set:
                raise ValueError("续作素材 ID 不存在")
            if asset_id not in selected_asset_ids:
                selected_asset_ids.append(asset_id)
        if len(selected_asset_ids) > 16:
            raise ValueError("续作参考图不能超过 16 张")
        if operation == "edit" and not selected_asset_ids:
            raise ValueError("edit 续作至少需要一张参考图")

        raw_descriptions = payload.get("descriptions") or []
        if not isinstance(raw_descriptions, list):
            raise ValueError("续作图片描述必须是数组")
        visible_id_set = set(visible_asset_ids)
        descriptions: dict[str, str] = {}
        for item in raw_descriptions:
            if not isinstance(item, dict):
                raise ValueError("续作图片描述项必须是对象")
            asset_id = str(item.get("asset_id") or "").strip()
            description = str(item.get("description") or "").strip()
            if asset_id not in visible_id_set:
                raise ValueError("只能描述本轮实际读取的图片")
            if not description:
                raise ValueError("续作图片描述不能为空")
            descriptions[asset_id] = description[:1200]
        missing_descriptions = set(required_description_ids) - descriptions.keys()
        if missing_descriptions:
            raise ValueError("续作规划必须描述本轮首次读取的全部图片")
        rationale = str(payload.get("rationale") or "").strip()[:600]
        if not rationale:
            raise ValueError("续作规划必须提供可审计理由")
        return {
            "operation": operation,
            "selectedAssetIds": selected_asset_ids,
            "descriptions": descriptions,
            "rationale": rationale,
        }

    reference_count = available_assets
    raw_indexes = payload.get("selected_reference_indexes")
    if not isinstance(raw_indexes, list):
        raise ValueError("续作规划必须返回参考图索引数组")
    clean_indexes: list[int] = []
    for raw_index in raw_indexes:
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            raise ValueError("续作参考图索引必须是整数")
        if not 0 <= raw_index < reference_count:
            raise ValueError("续作参考图索引超出范围")
        if raw_index not in clean_indexes:
            clean_indexes.append(raw_index)
    if operation == "edit" and not clean_indexes:
        raise ValueError("edit 续作至少需要一张参考图")
    return {
        "operation": operation,
        "selectedReferenceIndexes": clean_indexes,
        "rationale": str(payload.get("rationale") or "").strip()[:600],
    }


IMAGE_AGENT_WEB_TOOLS = (
    {
        "type": "function",
        "name": "search_web",
        "description": (
            "Search current public web pages for factual or visual-design context. Use when current, "
            "real-world, product, place, event, or historically accurate information can improve the image."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Focused web search query."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            "required": ["query", "max_results"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "search_visual_references",
        "description": (
            "Search public image sources and return visual candidates with thumbnails. Use when seeing real "
            "objects, products, landmarks, people, events, or styles would materially improve accuracy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Focused image-search query."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            "required": ["query", "max_results"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "select_visual_references",
        "description": (
            "Select zero to three candidate IDs after inspecting visual-search thumbnails. Only selected IDs "
            "are downloaded and supplied to the image model. Use an empty list when none are suitable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reference_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": WEB_REFERENCE_MAX_COUNT,
                },
                "rationale": {
                    "type": "string",
                    "description": "Brief auditable reason for selecting or rejecting the candidates.",
                },
            },
            "required": ["reference_ids", "rationale"],
            "additionalProperties": False,
        },
        "strict": True,
    },
)

IMAGE_CONTINUATION_PLAN_TOOL = {
    "type": "function",
    "name": "plan_image_continuation",
    "description": (
        "Decide whether this continuation is a local edit or a new generation, select session assets by "
        "stable asset ID, and describe every newly visible image. Call exactly once before finalizing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["edit", "generate"]},
            "selected_asset_ids": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 16,
            },
            "descriptions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "asset_id": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["asset_id", "description"],
                    "additionalProperties": False,
                },
                "maxItems": 16,
            },
            "rationale": {
                "type": "string",
                "description": "Brief auditable explanation of the operation and asset selection.",
            },
        },
        "required": ["operation", "selected_asset_ids", "descriptions", "rationale"],
        "additionalProperties": False,
    },
    "strict": True,
}


class NetworkTransportError(RuntimeError):
    pass


def open_url_with_direct_fallback(request: urllib.request.Request, timeout: int) -> Any:
    def connection_was_refused(error: BaseException) -> bool:
        pending: list[Any] = [error]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if current is None or id(current) in seen:
                continue
            seen.add(id(current))
            if isinstance(current, ConnectionRefusedError):
                return True
            if getattr(current, "winerror", None) == 10061:
                return True
            if getattr(current, "errno", None) in {61, 111, 10061}:
                return True
            pending.extend(
                [
                    getattr(current, "reason", None),
                    getattr(current, "__cause__", None),
                    getattr(current, "__context__", None),
                ]
            )
        return False

    def clone_request() -> urllib.request.Request:
        return urllib.request.Request(
            request.full_url,
            data=request.data,
            headers=dict(request.header_items()),
            method=request.get_method(),
        )

    try:
        return urllib.request.urlopen(clone_request(), timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, OSError) as proxy_error:
        if not connection_was_refused(proxy_error):
            raise
        direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            return direct_opener.open(clone_request(), timeout=timeout)
        except Exception as direct_error:
            raise RuntimeError(
                f"系统代理连接失败 ({proxy_error})；直连也失败 ({direct_error})"
            ) from direct_error


def curl_get_bytes(request: urllib.request.Request, timeout: int = 10) -> bytes:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise FileNotFoundError("系统未找到 curl")
    proxy = (
        urllib.request.getproxies().get("https")
        or urllib.request.getproxies().get("http")
    )
    environments: list[tuple[str, dict[str, str], list[str], int]] = []
    if proxy:
        proxy_environment = os.environ.copy()
        proxy_environment["HTTPS_PROXY"] = proxy
        proxy_environment["HTTP_PROXY"] = proxy
        proxy_environment.pop("NO_PROXY", None)
        proxy_environment.pop("no_proxy", None)
        environments.append(("系统代理", proxy_environment, [], 4))
    direct_environment = os.environ.copy()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        direct_environment.pop(name, None)
    direct_environment["NO_PROXY"] = "*"
    environments.append(("直连", direct_environment, ["--noproxy", "*"], max(6, int(timeout))))
    errors: list[str] = []
    for route, environment, route_arguments, route_timeout in environments:
        command = [
            curl,
            "--silent",
            "--show-error",
            "--http1.1",
            "--location",
            "--retry",
            "2",
            "--retry-all-errors",
            "--retry-delay",
            "0",
            "--connect-timeout",
            str(min(4, route_timeout)),
            "--max-time",
            str(route_timeout),
            "--max-filesize",
            str(4 * 1024 * 1024),
            "--write-out",
            "\n%{http_code}",
            *route_arguments,
        ]
        for header, value in request.header_items():
            command.extend(["--header", f"{header}: {value}"])
        command.append(request.full_url)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=route_timeout + 5,
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{route}: {exc}")
            continue
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", "replace").strip()
            errors.append(f"{route}: {message or f'curl {completed.returncode}'}")
            continue
        try:
            payload, status_line = completed.stdout.rsplit(b"\n", 1)
            status = int(status_line.strip())
        except (ValueError, TypeError):
            errors.append(f"{route}: curl 响应格式无效")
            continue
        if status >= 400:
            message = payload.decode("utf-8", "replace")[:200].strip()
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                message or f"HTTP {status}",
                {},
                io.BytesIO(payload),
            )
        return payload
    raise NetworkTransportError("；".join(errors) or "curl 无法连接")


def get_small_url_bytes(request: urllib.request.Request, timeout: int = 10) -> bytes:
    try:
        return curl_get_bytes(request, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except FileNotFoundError:
        pass
    except Exception as exc:
        raise NetworkTransportError(str(exc)) from exc
    try:
        with open_url_with_direct_fallback(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError:
        raise
    except Exception as exc:
        raise NetworkTransportError(str(exc)) from exc


def normalize_title_bar_mode(mode: Any) -> str:
    clean = str(mode or "").strip().lower()
    return clean if clean in TITLE_BAR_MODES else "default"


def normalize_background_ui_mode(mode: Any) -> str:
    clean = str(mode or "").strip().lower()
    return clean if clean in BACKGROUND_UI_MODES else "delayed"


def window_frame_options(title_bar_mode: Any) -> dict[str, bool]:
    original = normalize_title_bar_mode(title_bar_mode) == "original"
    return {"frameless": not original, "easy_drag": original}


def window_min_size(title_bar_mode: Any) -> tuple[int, int]:
    return (220, 96) if normalize_title_bar_mode(title_bar_mode) == "minimal" else (260, 120)


def normalize_window_size(width: Any, height: Any) -> dict[str, int]:
    def clean(value: Any, fallback: int, minimum: int, maximum: int) -> int:
        try:
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError
            return min(max(int(round(numeric)), minimum), maximum)
        except (TypeError, ValueError, OverflowError):
            return fallback

    return {
        "width": clean(width, DEFAULT_WINDOW_WIDTH, MIN_WINDOW_WIDTH, MAX_WINDOW_WIDTH),
        "height": clean(height, DEFAULT_WINDOW_HEIGHT, MIN_WINDOW_HEIGHT, MAX_WINDOW_HEIGHT),
    }


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.ReleaseCapture.argtypes = []
user32.ReleaseCapture.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD
kernel32.SetLastError.argtypes = [wintypes.DWORD]
kernel32.SetLastError.restype = None
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HANDLE
kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
kernel32.GlobalFree.restype = wintypes.HANDLE
dwmapi = ctypes.windll.dwmapi
dwmapi.DwmSetWindowAttribute.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
def activate_ui_window() -> bool:
    hwnd = user32.FindWindowW(None, WINDOW_TITLE)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, SW_SHOW)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True


def image_to_windows_dib(source: Path) -> bytes:
    bitmap = io.BytesIO()
    with Image.open(source) as image:
        image.convert("RGB").save(bitmap, format="BMP")
    return bitmap.getvalue()[14:]


def copy_image_to_windows_clipboard(source: Path) -> None:
    dib = image_to_windows_dib(source)
    memory = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
    if not memory:
        raise ctypes.WinError()
    transferred = False
    try:
        pointer = kernel32.GlobalLock(memory)
        if not pointer:
            raise ctypes.WinError()
        try:
            ctypes.memmove(pointer, dib, len(dib))
        finally:
            kernel32.GlobalUnlock(memory)
        opened = False
        for _ in range(10):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.02)
        if not opened:
            raise RuntimeError("剪贴板正被其他应用占用")
        try:
            if not user32.EmptyClipboard():
                raise ctypes.WinError()
            if not user32.SetClipboardData(CF_DIB, memory):
                raise ctypes.WinError()
            transferred = True
        finally:
            user32.CloseClipboard()
    finally:
        if not transferred:
            kernel32.GlobalFree(memory)


def activate_existing_instance() -> bool:
    if activate_ui_window():
        return True
    event_handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, SHOW_EVENT_NAME)
    if not event_handle:
        return False
    try:
        return bool(kernel32.SetEvent(event_handle))
    finally:
        kernel32.CloseHandle(event_handle)


def acquire_single_instance() -> int | None:
    kernel32.SetLastError(0)
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ctypes.WinError()
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        activate_existing_instance()
        kernel32.CloseHandle(handle)
        return None
    return handle


def resource_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / relative


def app_data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    path = root / "API_TOOLS"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def version_tuple(value: Any) -> tuple[int, ...]:
    cleaned = str(value or "").strip().lower().lstrip("v")
    parts = cleaned.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return ()
    return tuple(int(part) for part in parts)


def is_newer_version(candidate: Any, current: Any = APP_VERSION) -> bool:
    candidate_parts = version_tuple(candidate)
    current_parts = version_tuple(current)
    if not candidate_parts or not current_parts:
        return False
    width = max(len(candidate_parts), len(current_parts))
    return candidate_parts + (0,) * (width - len(candidate_parts)) > (
        current_parts + (0,) * (width - len(current_parts))
    )


def release_version(tag_name: Any) -> str:
    match = __import__("re").fullmatch(
        rf"{__import__('re').escape(RELEASE_TAG_PREFIX)}(\d+(?:\.\d+)+)",
        str(tag_name or "").strip(),
        flags=__import__("re").I,
    )
    return match.group(1) if match else ""


def release_matches_channel(release: dict[str, Any]) -> bool:
    return bool(release_version(release.get("tag_name"))) and str(
        release.get("target_commitish") or ""
    ).strip().lower() == RELEASE_BRANCH.lower()


def ignored_release_key(version: Any) -> str:
    clean = str(version or "").strip()
    if not clean:
        return ""
    if clean.lower().startswith(RELEASE_TAG_PREFIX.lower()):
        return clean
    return f"{RELEASE_TAG_PREFIX}{clean.lstrip('v')}"


def startup_command() -> str:
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return f'"{executable}"'
    return f'"{executable}" "{Path(__file__).resolve()}"'


def set_startup_enabled(enabled: bool) -> bool:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
    return enabled


def startup_is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
        return value == startup_command()
    except FileNotFoundError:
        return False


def bundled_changelog() -> str:
    try:
        return resource_path("CHANGELOG.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return f"# v{APP_VERSION}\n\n- 当前版本暂无本地更新日志。"


def version_tuple(version: Any) -> tuple[int, ...]:
    clean = str(version or "").strip().lower().lstrip("v")
    values: list[int] = []
    for part in clean.split("."):
        digits = "".join(character for character in part if character.isdigit())
        if not digits:
            break
        values.append(int(digits))
    return tuple(values)


def changelog_between(markdown: str, current_version: Any, latest_version: Any) -> str:
    current = version_tuple(current_version)
    latest = version_tuple(latest_version)
    sections: list[tuple[tuple[int, ...], list[str]]] = []
    active_version: tuple[int, ...] | None = None
    active_lines: list[str] = []
    for line in str(markdown or "").splitlines():
        match = __import__("re").match(r"^##\s+v?(\d+(?:\.\d+)+)\b", line.strip())
        if match:
            if active_version is not None:
                sections.append((active_version, active_lines))
            active_version = version_tuple(match.group(1))
            active_lines = [line]
        elif active_version is not None:
            active_lines.append(line)
    if active_version is not None:
        sections.append((active_version, active_lines))
    selected = [
        "\n".join(lines).strip()
        for version, lines in sections
        if current < version <= latest
    ]
    return "\n\n".join(item for item in selected if item)


def changelog_for_update(
    markdown: str,
    current_version: Any,
    latest_version: Any,
    latest_release_notes: str = "",
) -> str:
    current = version_tuple(current_version)
    latest = version_tuple(latest_version)
    if latest > current:
        selected = changelog_between(markdown, current_version, latest_version)
        section_count = len(__import__("re").findall(r"^##\s+v?\d", selected, flags=__import__("re").M))
        if section_count <= 1 and str(latest_release_notes or "").strip():
            return str(latest_release_notes).strip()
        return selected or str(latest_release_notes or "").strip()
    current_notes = changelog_between(markdown, "0", current_version)
    sections = __import__("re").split(r"(?=^##\s+v?\d)", current_notes, flags=__import__("re").M)
    return next((section.strip() for section in sections if section.strip()), "")


def release_notes_since(releases: list[dict[str, Any]], current_version: Any) -> str:
    current = version_tuple(current_version)
    pending = [
        release
        for release in releases
        if not release.get("draft")
        and not release.get("prerelease")
        and version_tuple(release.get("tag_name")) > current
    ]
    pending.sort(key=lambda release: version_tuple(release.get("tag_name")), reverse=True)
    sections: list[str] = []
    for release in pending:
        tag_name = str(release.get("tag_name") or "").strip()
        version = release_version(tag_name) or tag_name.lstrip("v")
        notes = str(release.get("body") or "").strip()
        if not notes:
            notes = "- 本版本暂无更新说明。"
        versioned_notes = changelog_between(notes, current_version, version)
        if versioned_notes:
            sections.append(versioned_notes)
            continue
        notes = __import__("re").sub(
            r"^#{1,3}\s+(更新日志|更新内容|Release Notes)\s*\r?\n+",
            "",
            notes,
            count=1,
            flags=__import__("re").I,
        ).strip()
        version_heading = __import__("re").compile(
            rf"^#{{1,3}}\s+v?{__import__('re').escape(version)}\b",
            __import__("re").I,
        )
        if not version_heading.match(notes):
            notes = f"## {version}\n\n{notes}"
        sections.append(notes)
    return "\n\n".join(sections)


class ReleaseNotesHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.block_tag = ""
        self.block_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3", "p", "li"}:
            self._flush()
            self.block_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self.block_tag:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.block_tag:
            self.block_text.append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self.block_text).split())
        if text:
            prefix = {"h1": "# ", "h2": "## ", "h3": "### ", "li": "- "}.get(
                self.block_tag, ""
            )
            self.lines.append(f"{prefix}{text}")
        self.block_tag = ""
        self.block_text = []

    def markdown(self) -> str:
        self._flush()
        return "\n".join(self.lines).strip()


def parse_github_release_feed(payload: bytes) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(payload)
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    releases: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", namespace):
        release_url = next(
            (
                str(link.get("href") or "")
                for link in entry.findall("atom:link", namespace)
                if link.get("rel") == "alternate"
            ),
            "",
        )
        tag_name = release_url.rsplit("/", 1)[-1].strip()
        if not release_version(tag_name):
            continue
        parser = ReleaseNotesHtmlParser()
        parser.feed(entry.findtext("atom:content", default="", namespaces=namespace))
        download_base = f"https://github.com/{GITHUB_REPOSITORY}/releases/download/{tag_name}"
        releases.append(
            {
                "tag_name": tag_name,
                "body": parser.markdown(),
                "draft": False,
                "prerelease": False,
                "target_commitish": RELEASE_BRANCH,
                "assets": [
                    {
                        "name": RELEASE_ASSET_NAME,
                        "url": "",
                        "browser_download_url": f"{download_base}/{RELEASE_ASSET_NAME}",
                    },
                    {
                        "name": f"{RELEASE_ASSET_NAME}.sha256",
                        "url": "",
                        "browser_download_url": f"{download_base}/{RELEASE_ASSET_NAME}.sha256",
                    },
                ],
            }
        )
    return releases


class StaticAssetCache:
    def __init__(
        self,
        data_root: Path | None = None,
        bundle_root: Path | None = None,
    ) -> None:
        self.data_root = (data_root or app_data_dir()).resolve()
        self.bundle_root = (
            bundle_root
            or Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        ).resolve()
        self.releases_root = self.data_root / "static"
        self.lock = threading.RLock()
        self.install_thread: threading.Thread | None = None
        self.source_files = {
            MAIN_PAGE_NAME: self.bundle_root / MAIN_PAGE_NAME,
            "assets/app.css": self.bundle_root / "assets" / "app.css",
            "assets/title_logo.png": self.bundle_root / "assets" / "title_logo.png",
        }
        self.source_hashes = {
            relative: sha256_file(path) for relative, path in self.source_files.items()
        }
        fingerprint_payload = json.dumps(
            {
                "schema": STATIC_CACHE_SCHEMA,
                "ui": STATIC_UI_VERSION,
                "lucide": LUCIDE_VERSION,
                "files": self.source_hashes,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = sha256_bytes(fingerprint_payload)[:12]
        self.release_id = f"ui-{STATIC_UI_VERSION}-lucide-{LUCIDE_VERSION}-{fingerprint}"
        self.release_dir = self.releases_root / self.release_id
        self.main_page = self.release_dir / MAIN_PAGE_NAME
        self.expected_hashes = {
            **self.source_hashes,
            "vendor/lucide/lucide.min.js": LUCIDE_SHA256,
        }
        ready = self.is_ready()
        self._state: dict[str, Any] = {
            "ok": True,
            "status": "ready" if ready else "idle",
            "percent": 100 if ready else 0,
            "message": "静态资源缓存可用" if ready else "等待初始化",
            "item": f"Lucide {LUCIDE_VERSION}",
            "url": self.main_page.as_uri() if ready else None,
        }

    @property
    def manifest_path(self) -> Path:
        return self.release_dir / "manifest.json"

    def _manifest(self) -> dict[str, Any]:
        return {
            "schema": STATIC_CACHE_SCHEMA,
            "releaseId": self.release_id,
            "uiVersion": STATIC_UI_VERSION,
            "lucideVersion": LUCIDE_VERSION,
            "files": self.expected_hashes,
        }

    def _validate_release(self, root: Path) -> bool:
        manifest_path = root / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if manifest != self._manifest():
            return False
        for relative, expected_hash in self.expected_hashes.items():
            path = root / Path(relative)
            try:
                if not path.is_file() or sha256_file(path) != expected_hash:
                    return False
            except OSError:
                return False
        return True

    def is_ready(self) -> bool:
        with self.lock:
            return self._validate_release(self.release_dir)

    def _set_state(self, **changes: Any) -> None:
        with self.lock:
            self._state.update(changes)

    def status(self) -> dict[str, Any]:
        with self.lock:
            return dict(self._state)

    def start_install(self, retry: bool = False) -> dict[str, Any]:
        with self.lock:
            if self.is_ready():
                self._state.update(
                    ok=True,
                    status="ready",
                    percent=100,
                    message="静态资源缓存可用",
                    item=f"Lucide {LUCIDE_VERSION}",
                    url=self.main_page.as_uri(),
                )
                return dict(self._state)
            if self.install_thread and self.install_thread.is_alive():
                return dict(self._state)
            if self._state.get("status") == "failed" and not retry:
                return dict(self._state)
            self._state = {
                "ok": True,
                "status": "installing",
                "percent": 4,
                "message": "正在准备静态资源缓存",
                "item": f"Lucide {LUCIDE_VERSION}",
                "url": None,
            }
            self.install_thread = threading.Thread(
                target=self._install_worker,
                name="static-assets-installer",
                daemon=True,
            )
            self.install_thread.start()
            return dict(self._state)

    def _install_worker(self) -> None:
        try:
            self.install()
        except Exception as exc:
            trace_startup("static_assets_failed", error=str(exc))
            self._set_state(
                ok=False,
                status="failed",
                message="静态资源初始化失败",
                item=str(exc),
                url=None,
            )

    @staticmethod
    def _read_url(url: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/javascript, application/octet-stream, */*",
                "User-Agent": f"{APP_NAME}/1.0 static-cache",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    @staticmethod
    def _script_from_archive(data: bytes) -> bytes:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            member = archive.getmember("package/dist/umd/lucide.min.js")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise RuntimeError("Lucide 压缩包缺少浏览器构建")
            return extracted.read()

    def _download_lucide(self) -> bytes:
        errors: list[str] = []
        for index, (name, url, resource_type) in enumerate(LUCIDE_MIRRORS):
            self._set_state(
                percent=12 + index * 18,
                message=f"正在连接{name}",
                item=f"Lucide {LUCIDE_VERSION}",
            )
            try:
                downloaded = self._read_url(url)
                script = (
                    self._script_from_archive(downloaded)
                    if resource_type == "archive"
                    else downloaded
                )
                if sha256_bytes(script) != LUCIDE_SHA256:
                    raise RuntimeError("SHA-256 校验不一致")
                return script
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        raise RuntimeError("；".join(errors))

    @staticmethod
    def _replace_release_path(source: Path, destination: Path) -> None:
        for attempt in range(5):
            try:
                os.replace(source, destination)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.08 * (2 ** attempt))

    def install(self) -> None:
        trace_startup("static_assets_started", release=self.release_id)
        self.releases_root.mkdir(parents=True, exist_ok=True)
        staging = self.releases_root / f".{self.release_id}.{uuid.uuid4().hex}.tmp"
        quarantine: Path | None = None
        try:
            staging.mkdir(parents=True)
            script = self._download_lucide()
            self._set_state(
                percent=68,
                message="正在校验并写入 Lucide",
                item=f"SHA-256 {LUCIDE_SHA256[:12]}…",
            )
            lucide_path = staging / "vendor" / "lucide" / "lucide.min.js"
            lucide_path.parent.mkdir(parents=True)
            lucide_path.write_bytes(script)

            self._set_state(
                percent=82,
                message="正在准备本地界面",
                item="HTML、CSS 与项目标识",
            )
            for relative, source in self.source_files.items():
                destination = staging / Path(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            (staging / "manifest.json").write_text(
                json.dumps(self._manifest(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if not self._validate_release(staging):
                raise RuntimeError("安装后的静态资源校验失败")

            self._set_state(
                percent=95,
                message="正在启用本地缓存",
                item=self.release_id,
            )
            old_moved = False
            new_moved = False
            failed_release: Path | None = None
            with self.lock:
                try:
                    if self.release_dir.exists():
                        quarantine = self.releases_root / f".{self.release_id}.{uuid.uuid4().hex}.old"
                        self._replace_release_path(self.release_dir, quarantine)
                        old_moved = True
                    self._replace_release_path(staging, self.release_dir)
                    new_moved = True
                    if not self._validate_release(self.release_dir):
                        raise RuntimeError("静态资源缓存启用失败")
                except Exception:
                    if new_moved and self.release_dir.exists():
                        failed_release = self.releases_root / f".{self.release_id}.{uuid.uuid4().hex}.failed"
                        self._replace_release_path(self.release_dir, failed_release)
                    if old_moved and quarantine and quarantine.exists() and not self.release_dir.exists():
                        self._replace_release_path(quarantine, self.release_dir)
                        quarantine = None
                    raise
                finally:
                    if failed_release and failed_release.exists():
                        shutil.rmtree(failed_release, ignore_errors=True)
            if quarantine and quarantine.exists():
                shutil.rmtree(quarantine, ignore_errors=True)
            self._set_state(
                ok=True,
                status="ready",
                percent=100,
                message="初始化完成，正在进入应用",
                item="Lucide 已从本地缓存加载",
                url=self.main_page.as_uri(),
            )
            trace_startup("static_assets_finished", release=self.release_id)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)


class StartupTrace:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.session_id = uuid.uuid4().hex[:8]
        self.lock = threading.Lock()
        if path.exists() and path.stat().st_size > 1_000_000:
            path.replace(path.with_suffix(".previous.log"))

    def write(self, stage: str, **details: Any) -> None:
        record = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "session": self.session_id,
            "pid": os.getpid(),
            "elapsedMs": round((time.perf_counter() - PROCESS_STARTED_AT) * 1000, 1),
            "stage": stage,
            **details,
        }
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


startup_trace: StartupTrace | None = None


def trace_startup(stage: str, **details: Any) -> None:
    if startup_trace is not None:
        startup_trace.write(stage, **details)


RPC_METHODS = {
    "add_key",
    "append_image_stream_debug",
    "check_for_updates",
    "delete_key",
    "delete_image_set",
    "defer_update_restart",
    "dismiss_update_prompt",
    "download_update",
    "generate_image",
    "exit_app",
    "get_asset_status",
    "get_state",
    "ignore_update_version",
    "initialize_assets",
    "load_generated_image",
    "list_image_sets",
    "open_generated_pictures",
    "polish_prompt",
    "refresh_now",
    "report_startup",
    "restart_app",
    "restart_update",
    "set_always_on_top",
    "set_window_size",
    "claim_ui_release",
    "notify_ui_hidden",
    "set_ui_visible",
    "update_app_preferences",
    "update_refresh_intervals",
    "update_rate_limit_progress_mode",
    "update_thresholds",
}


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> "GUID":
        parsed = uuid.UUID(value)
        return cls(
            parsed.time_low,
            parsed.time_mid,
            parsed.time_hi_version,
            (ctypes.c_ubyte * 8)(*parsed.bytes[8:]),
        )


def windows_pictures_dir() -> Path:
    folder_id = GUID.from_string("33E28130-4E1E-4676-835A-98395C3BC3BB")
    path_pointer = ctypes.c_wchar_p()
    try:
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id),
            0,
            None,
            ctypes.byref(path_pointer),
        )
        if result == 0 and path_pointer.value:
            return Path(path_pointer.value)
    except (AttributeError, OSError):
        pass
    finally:
        if path_pointer.value:
            try:
                ctypes.windll.ole32.CoTaskMemFree(path_pointer)
            except (AttributeError, OSError):
                pass
    return Path.home() / "Pictures"


def generated_pictures_dir() -> Path:
    return windows_pictures_dir() / APP_NAME


class ControllerRpcServer:
    def __init__(self, controller: "AppController", address: str, authkey: bytes) -> None:
        self.controller = controller
        self.address = address
        self.authkey = authkey
        self.listener: Listener | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.listener = Listener(self.address, family="AF_PIPE", authkey=self.authkey)
        self.thread = threading.Thread(target=self._serve, name="controller-rpc", daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self.controller.stopping.is_set():
            try:
                connection = self.listener.accept() if self.listener else None
            except (OSError, EOFError):
                break
            if connection is None:
                continue
            threading.Thread(
                target=self._handle_connection,
                args=(connection,),
                name="controller-rpc-request",
                daemon=True,
            ).start()

    def _handle_connection(self, connection: Any) -> None:
        try:
            request = connection.recv()
            method_name = str(request.get("method") or "") if isinstance(request, dict) else ""
            arguments = request.get("args", []) if isinstance(request, dict) else []
            if method_name not in RPC_METHODS:
                raise ValueError("不允许的后台调用")
            method = getattr(self.controller, method_name)
            if method_name in {"generate_image", "polish_prompt"}:
                send_lock = threading.Lock()

                def send_event(event: dict[str, Any]) -> None:
                    try:
                        with send_lock:
                            connection.send({"ok": True, "event": event})
                    except (OSError, EOFError, BrokenPipeError):
                        pass

                result = method(*arguments, event_callback=send_event)
                with send_lock:
                    connection.send({"ok": True, "result": result})
            else:
                connection.send({"ok": True, "result": method(*arguments)})
        except Exception as exc:
            try:
                connection.send({"ok": False, "error": str(exc)})
            except (OSError, EOFError):
                pass
        finally:
            connection.close()

    def stop(self) -> None:
        listener = self.listener
        self.listener = None
        if listener:
            listener.close()


class ControllerRpcClient:
    def __init__(self, address: str, authkey: bytes) -> None:
        self.address = address
        self.authkey = authkey

    def call(self, method: str, *arguments: Any) -> Any:
        connection = Client(self.address, family="AF_PIPE", authkey=self.authkey)
        try:
            connection.send({"method": method, "args": list(arguments)})
            response = connection.recv()
        finally:
            connection.close()
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "后台服务调用失败")
        return response.get("result")

    def call_with_events(
        self,
        method: str,
        *arguments: Any,
        on_event: Any = None,
    ) -> Any:
        connection = Client(self.address, family="AF_PIPE", authkey=self.authkey)
        try:
            connection.send({"method": method, "args": list(arguments)})
            while True:
                response = connection.recv()
                if "event" in response:
                    if on_event is not None:
                        on_event(response["event"])
                    continue
                break
        finally:
            connection.close()
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "后台服务调用失败")
        return response.get("result")


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect_secret(value: str) -> str:
    source, source_buffer = _blob(value.encode("utf-8"))
    result = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), APP_NAME, None, None, None, 0, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(result.pbData, result.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)
        del source_buffer


def unprotect_secret(value: str) -> str:
    encrypted = base64.b64decode(value)
    source, source_buffer = _blob(encrypted)
    result = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)
        del source_buffer


def parse_timestamp(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


GPT_5_6_PRICING = {
    "gpt-5.6-luna": ((0.20, 0.02, 1.20), (0.40, 0.04, 1.80)),
    "gpt-5.6-terra": ((2.00, 0.20, 12.00), (4.00, 0.40, 18.00)),
    "gpt-5.6-sol": ((5.00, 0.50, 30.00), (10.00, 1.00, 45.00)),
}
GPT_5_6_LONG_CONTEXT_TOKENS = 272_000
GPT_IMAGE_2_PRICING = {
    "textInput": 5.00,
    "imageInput": 8.00,
    "imageOutput": 30.00,
}
GPT_IMAGE_2_OUTPUT_COSTS = {
    "low": {"square": 0.006, "portrait": 0.005, "landscape": 0.005},
    "medium": {"square": 0.053, "portrait": 0.041, "landscape": 0.041},
    "high": {"square": 0.211, "portrait": 0.165, "landscape": 0.165},
}


def _usage_cost_value(value: Any) -> float | None:
    if isinstance(value, dict):
        for name in ("total", "cost", "amount", "value", "usd"):
            if name in value:
                parsed = _usage_cost_value(value[name])
                if parsed is not None:
                    return parsed
        return None
    try:
        parsed = float(str(value).strip().lstrip("$"))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _usage_token_value(usage: dict[str, Any], *names: str) -> int:
    for name in names:
        if name not in usage:
            continue
        try:
            return max(0, int(usage[name]))
        except (TypeError, ValueError):
            continue
    return 0


def response_usage_metrics(payload: Any, model: str = "") -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    usage = source.get("usage")
    if not isinstance(usage, dict):
        response = source.get("response")
        usage = response.get("usage") if isinstance(response, dict) else {}
    if not isinstance(usage, dict):
        usage = {}

    input_tokens = _usage_token_value(
        usage, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens"
    )
    output_tokens = _usage_token_value(
        usage,
        "output_tokens", "outputTokens", "completion_tokens", "completionTokens"
    )
    total_tokens = _usage_token_value(usage, "total_tokens", "totalTokens")
    if not total_tokens and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens
    has_token_usage = any(
        name in usage
        for name in (
            "input_tokens", "inputTokens", "prompt_tokens", "promptTokens",
            "output_tokens", "outputTokens", "completion_tokens", "completionTokens",
            "total_tokens", "totalTokens",
        )
    )
    cost = None
    for container in (usage, source):
        for name in ("cost", "total_cost", "totalCost", "cost_usd", "costUsd"):
            if name not in container:
                continue
            cost = _usage_cost_value(container[name])
            if cost is not None:
                break
        if cost is not None:
            break
    cost_source = "response" if cost is not None else ""
    estimated_call_count = 0
    pricing = GPT_5_6_PRICING.get(str(model or "").lower())
    if cost is None and pricing is not None and has_token_usage:
        details = usage.get("input_tokens_details") or usage.get("inputTokensDetails") or {}
        cached_tokens = _usage_token_value(
            details if isinstance(details, dict) else {}, "cached_tokens", "cachedTokens"
        )
        uncached_tokens = max(0, input_tokens - cached_tokens)
        input_rate, cached_rate, output_rate = pricing[
            1 if input_tokens >= GPT_5_6_LONG_CONTEXT_TOKENS else 0
        ]
        cost = (
            uncached_tokens * input_rate
            + cached_tokens * cached_rate
            + output_tokens * output_rate
        ) / 1_000_000
        cost_source = "estimate"
        estimated_call_count = 1
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "callCount": 1,
        "costUsd": cost or 0.0,
        "costedCallCount": 1 if cost is not None else 0,
        "estimatedCallCount": estimated_call_count,
        "costSource": cost_source,
        "hasTokenUsage": has_token_usage,
        "hasCost": cost is not None,
    }


def image_usage_metrics(
    usage_value: Any,
    model: str,
    quality: str,
    size: str,
    partial_images: int = 0,
) -> dict[str, Any]:
    usage = usage_value if isinstance(usage_value, dict) else {}
    actual = response_usage_metrics({"usage": usage})
    if actual["hasCost"]:
        return actual
    input_tokens = _usage_token_value(usage, "input_tokens", "inputTokens")
    output_tokens = _usage_token_value(usage, "output_tokens", "outputTokens")
    has_reported_output_tokens = output_tokens > 0
    details = usage.get("input_tokens_details") or usage.get("inputTokensDetails") or {}
    if not isinstance(details, dict):
        details = {}
    text_tokens = _usage_token_value(details, "text_tokens", "textTokens")
    image_tokens = _usage_token_value(details, "image_tokens", "imageTokens")
    if not text_tokens and not image_tokens:
        text_tokens = input_tokens
    elif input_tokens > text_tokens + image_tokens:
        text_tokens += input_tokens - text_tokens - image_tokens

    clean_model = str(model or "").lower()
    clean_quality = str(quality or "auto").lower()
    size_match = __import__("re").fullmatch(r"(\d+)x(\d+)", str(size or ""))
    if clean_model == "gpt-image-2" and not output_tokens and clean_quality in GPT_IMAGE_2_OUTPUT_COSTS:
        width, height = (
            (int(value) for value in size_match.groups())
            if size_match
            else (1024, 1024)
        )
        orientation = "square" if width == height else "landscape" if width > height else "portrait"
        output_cost = GPT_IMAGE_2_OUTPUT_COSTS[clean_quality][orientation]
        output_tokens = round(output_cost / GPT_IMAGE_2_PRICING["imageOutput"] * 1_000_000)
    if not has_reported_output_tokens:
        output_tokens += max(0, int(partial_images)) * 100
    has_estimate = clean_model == "gpt-image-2" and bool(
        text_tokens or image_tokens or output_tokens
    )
    cost = (
        text_tokens * GPT_IMAGE_2_PRICING["textInput"]
        + image_tokens * GPT_IMAGE_2_PRICING["imageInput"]
        + output_tokens * GPT_IMAGE_2_PRICING["imageOutput"]
    ) / 1_000_000 if has_estimate else 0.0
    return {
        "inputTokens": input_tokens or text_tokens + image_tokens,
        "outputTokens": output_tokens,
        "totalTokens": (input_tokens or text_tokens + image_tokens) + output_tokens,
        "callCount": 1,
        "costUsd": cost,
        "costedCallCount": 1 if has_estimate else 0,
        "estimatedCallCount": 1 if has_estimate else 0,
        "costSource": "estimate" if has_estimate else "",
        "hasTokenUsage": bool(usage) or bool(output_tokens),
        "hasCost": has_estimate,
    }


def merge_reasoning_usage(current: Any, update: Any) -> dict[str, Any]:
    left = current if isinstance(current, dict) else {}
    right = update if isinstance(update, dict) else {}
    call_count = max(0, int(left.get("callCount") or 0)) + max(
        0, int(right.get("callCount") or 0)
    )
    costed_call_count = max(0, int(left.get("costedCallCount") or 0)) + max(
        0, int(right.get("costedCallCount") or 0)
    )
    estimated_call_count = max(0, int(left.get("estimatedCallCount") or 0)) + max(
        0, int(right.get("estimatedCallCount") or 0)
    )
    has_complete_cost = call_count > 0 and costed_call_count == call_count
    response_cost = max(0.0, safe_float(left.get("costUsd"))) + max(
        0.0, safe_float(right.get("costUsd"))
    )
    return {
        "inputTokens": max(0, int(left.get("inputTokens") or 0))
        + max(0, int(right.get("inputTokens") or 0)),
        "outputTokens": max(0, int(left.get("outputTokens") or 0))
        + max(0, int(right.get("outputTokens") or 0)),
        "totalTokens": max(0, int(left.get("totalTokens") or 0))
        + max(0, int(right.get("totalTokens") or 0)),
        "callCount": call_count,
        "costUsd": response_cost if has_complete_cost else 0.0,
        "costedCallCount": costed_call_count,
        "estimatedCallCount": estimated_call_count,
        "costSource": (
            "estimate" if has_complete_cost and estimated_call_count == call_count
            else "mixed" if has_complete_cost and estimated_call_count
            else "response" if has_complete_cost
            else ""
        ),
        "hasTokenUsage": bool(left.get("hasTokenUsage") or right.get("hasTokenUsage")),
        "hasCost": has_complete_cost,
    }


def load_pressure_from_usage_percent(usage_percent: float) -> float:
    value = max(0.0, safe_float(usage_percent))
    pressure_points = (
        (0, 0), (1, 10), (5, 30), (10, 45),
        (25, 65), (50, 82), (100, 100),
    )
    for (left_value, left_pressure), (right_value, right_pressure) in zip(
        pressure_points, pressure_points[1:]
    ):
        if value <= right_value:
            ratio = (value - left_value) / (right_value - left_value)
            return left_pressure + (right_pressure - left_pressure) * ratio
    return 100.0


def interval_load_components(payload: dict[str, Any], cost: float) -> dict[str, Any]:
    spend = max(0.0, safe_float(cost))
    quota = payload.get("quota") or {}
    quota_limit = safe_float(quota.get("limit"))
    quota_percent = spend / quota_limit * 100 if quota_limit > 0 else 0.0

    rate_percent = 0.0
    for item in payload.get("rate_limits") or []:
        limit = safe_float(item.get("limit"))
        if limit > 0:
            rate_percent = max(rate_percent, spend / limit * 100)

    quota_pressure = load_pressure_from_usage_percent(quota_percent)
    rate_pressure = load_pressure_from_usage_percent(rate_percent)
    if quota_pressure > rate_pressure:
        source = "额度"
    elif rate_pressure > quota_pressure:
        source = "速率"
    elif quota_pressure > 0:
        source = "额度和速率"
    else:
        source = "无"
    return {
        "overall": max(quota_pressure, rate_pressure),
        "quota": quota_pressure,
        "rate": rate_pressure,
        "quotaPercent": quota_percent,
        "ratePercent": rate_percent,
        "source": source,
    }


def interval_load_pressure(payload: dict[str, Any], cost: float, seconds: int) -> float:
    del seconds  # Kept for API compatibility; load is now normalized by quota percentages.
    return interval_load_components(payload, cost)["overall"]


def limit_definitions(payload: dict[str, Any] | None) -> dict[str, float]:
    payload = payload or {}
    quota = payload.get("quota") or {}
    windows = {str(item.get("window")): item for item in payload.get("rate_limits") or []}
    return {
        "quota": safe_float(quota.get("limit")),
        "5h": safe_float((windows.get("5h") or {}).get("limit")),
        "1d": safe_float((windows.get("1d") or {}).get("limit")),
        "7d": safe_float((windows.get("7d") or {}).get("limit")),
    }


def annotate_limit_changes(
    payload: dict[str, Any],
    previous: dict[str, Any] | None,
    changed_at: float | None = None,
) -> set[str]:
    if str(payload.get("mode") or "").lower() == "unrestricted":
        payload.pop("_limit_changes", None)
        return set()
    if previous is None:
        payload.pop("_limit_changes", None)
        return set()

    now = changed_at if changed_at is not None else time.time()
    current_limits = limit_definitions(payload)
    previous_limits = limit_definitions(previous)
    previous_changes = previous.get("_limit_changes") or {}
    changes: dict[str, dict[str, float]] = {}
    changed_names: set[str] = set()

    for name, current in current_limits.items():
        old = previous_limits[name]
        if abs(current - old) > 1e-9:
            changes[name] = {"previous": old, "current": current, "changedAt": now}
            changed_names.add(name)
            continue

        carried = previous_changes.get(name) or {}
        carried_at = safe_float(carried.get("changedAt"))
        if carried_at > 0 and now - carried_at <= LIMIT_CHANGE_DISPLAY_SECONDS:
            changes[name] = {
                "previous": safe_float(carried.get("previous")),
                "current": current,
                "changedAt": carried_at,
            }

    if changes:
        payload["_limit_changes"] = changes
    else:
        payload.pop("_limit_changes", None)
    return changed_names


class BingImageResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = {name.lower(): value or "" for name, value in attributes}
        if "iusc" not in values.get("class", "").split():
            return
        metadata = values.get("m")
        if not metadata:
            return
        try:
            payload = json.loads(unescape(metadata))
        except (json.JSONDecodeError, TypeError):
            return
        if isinstance(payload, dict):
            self.results.append(payload)


class HtmlTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if clean:
            self.parts.append(clean)

    def text(self) -> str:
        return " ".join(self.parts)


def html_text(value: Any) -> str:
    parser = HtmlTextParser()
    parser.feed(str(value or ""))
    parser.close()
    return parser.text()


def require_public_https_url(value: Any) -> str:
    raw_url = str(value or "")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_url):
        raise ValueError("图片地址包含控制字符")
    url = raw_url.strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("仅允许公开 HTTPS 图片地址")
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("图片地址包含不允许的连接信息")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ValueError("图片地址不能指向本机或局域网")
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
            ]
        except (OSError, ValueError) as exc:
            raise ValueError("图片地址无法解析") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("图片地址不能指向非公开网络")
    hostname = hostname.encode("idna").decode("ascii")
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port == 443:
        netloc += ":443"
    path = urllib.parse.quote(
        parsed.path,
        safe="/:@-._~!$&'*+,;=%",
        encoding="utf-8",
        errors="strict",
    )
    query = urllib.parse.quote(
        parsed.query,
        safe="/?:@-._~!$&'*+,;=%",
        encoding="utf-8",
        errors="strict",
    )
    fragment = urllib.parse.quote(
        parsed.fragment,
        safe="/?:@-._~!$&'*+,;=%",
        encoding="utf-8",
        errors="strict",
    )
    return urllib.parse.urlunsplit(("https", netloc, path, query, fragment))


class PublicHttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        clean_url = require_public_https_url(new_url)
        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            clean_url,
        )


def read_limited_response(response: Any, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    try:
        if content_length and int(content_length) > max_bytes:
            raise ValueError("远程图片超过大小限制")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) == "远程图片超过大小限制":
            raise
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("远程图片超过大小限制")
    return b"".join(chunks)


class WebSearchService:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) API_TOOLS/1.0"

    @staticmethod
    def _query(value: Any) -> str:
        query = " ".join(str(value or "").split())[:240]
        if not query:
            raise ValueError("搜索词不能为空")
        return query

    @staticmethod
    def _candidate_id(image_url: str) -> str:
        return "webref-" + hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _fixed_get(url: str, accept: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": WebSearchService.USER_AGENT, "Accept": accept},
        )
        return get_small_url_bytes(request, timeout=20)

    @staticmethod
    def _public_image_bytes(url: str, max_bytes: int) -> bytes:
        clean_url = require_public_https_url(url)
        request = urllib.request.Request(
            clean_url,
            headers={
                "User-Agent": WebSearchService.USER_AGENT,
                "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.5",
            },
        )
        openers = (
            urllib.request.build_opener(PublicHttpsRedirectHandler()),
            urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                PublicHttpsRedirectHandler(),
            ),
        )
        errors: list[str] = []
        for opener in openers:
            try:
                with opener.open(request, timeout=20) as response:
                    final_url = response.geturl()
                    require_public_https_url(final_url)
                    content_type = str(response.headers.get("Content-Type") or "").lower()
                    if content_type and not content_type.startswith("image/"):
                        raise ValueError("远程地址未返回图片")
                    return read_limited_response(response, max_bytes)
            except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as exc:
                errors.append(str(exc))
        raise RuntimeError(errors[-1] if errors else "无法下载远程图片")

    @staticmethod
    def _normalized_image_bytes(image_bytes: bytes, max_side: int | None = None) -> bytes:
        with Image.open(io.BytesIO(image_bytes)) as source:
            width, height = source.size
            if width < 32 or height < 32 or width * height > 40_000_000:
                raise ValueError("远程图片尺寸不符合要求")
            source.load()
            image = source.copy()
        if max_side:
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba_image = image.convert("RGBA")
            flattened = Image.new("RGB", rgba_image.size, "white")
            flattened.paste(rgba_image, mask=rgba_image.getchannel("A"))
            image = flattened
        else:
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=90, optimize=True)
        return output.getvalue()

    def search_web(self, query: Any, max_results: int = 5) -> dict[str, Any]:
        clean_query = self._query(query)
        limit = max(1, min(8, int(max_results)))
        url = "https://www.bing.com/search?" + urllib.parse.urlencode(
            {"q": clean_query, "format": "rss"}
        )
        payload = self._fixed_get(url, "application/rss+xml,application/xml,text/xml")
        root = ElementTree.fromstring(payload)
        results: list[dict[str, str]] = []
        for item in root.findall("./channel/item")[:limit]:
            result_url = str(item.findtext("link") or "").strip()
            if not result_url.startswith(("https://", "http://")):
                continue
            results.append(
                {
                    "title": html_text(item.findtext("title"))[:240],
                    "url": result_url[:2048],
                    "snippet": html_text(item.findtext("description"))[:600],
                }
            )
        return {"query": clean_query, "results": results}

    def _bing_images(self, query: str, limit: int) -> list[dict[str, Any]]:
        url = "https://www.bing.com/images/search?" + urllib.parse.urlencode(
            {"q": query, "form": "HDRSC2"}
        )
        parser = BingImageResultParser()
        parser.feed(self._fixed_get(url, "text/html").decode("utf-8", "replace"))
        parser.close()
        results: list[dict[str, Any]] = []
        for metadata in parser.results:
            image_url = str(metadata.get("murl") or "").strip()
            thumbnail_url = str(metadata.get("turl") or "").strip()
            if not image_url.startswith("https://") or not thumbnail_url.startswith("https://"):
                continue
            results.append(
                {
                    "id": self._candidate_id(image_url),
                    "title": html_text(metadata.get("t") or metadata.get("desc"))[:240],
                    "caption": html_text(metadata.get("desc") or metadata.get("t"))[:400],
                    "imageUrl": image_url[:4096],
                    "thumbnailUrl": thumbnail_url[:4096],
                    "sourceUrl": str(metadata.get("purl") or "")[:4096],
                    "width": int(metadata.get("w") or 0),
                    "height": int(metadata.get("h") or 0),
                    "provider": "Bing Images",
                }
            )
            if len(results) >= limit:
                break
        return results

    def _commons_images(self, query: str, limit: int) -> list[dict[str, Any]]:
        url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(
            {
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,
                "gsrlimit": limit,
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": 720,
                "format": "json",
                "origin": "*",
            }
        )
        payload = json.loads(self._fixed_get(url, "application/json").decode("utf-8"))
        pages = (payload.get("query") or {}).get("pages") or {}
        results: list[dict[str, Any]] = []
        for page in pages.values():
            image_info = ((page.get("imageinfo") or [{}])[0])
            image_url = str(image_info.get("url") or "").strip()
            thumbnail_url = str(image_info.get("thumburl") or image_url).strip()
            if not image_url.startswith("https://") or not thumbnail_url.startswith("https://"):
                continue
            metadata = image_info.get("extmetadata") or {}
            caption = html_text(
                (metadata.get("ImageDescription") or {}).get("value")
                or (metadata.get("ObjectName") or {}).get("value")
            )
            title = str(page.get("title") or "").removeprefix("File:")
            results.append(
                {
                    "id": self._candidate_id(image_url),
                    "title": title[:240],
                    "caption": caption[:400],
                    "imageUrl": image_url[:4096],
                    "thumbnailUrl": thumbnail_url[:4096],
                    "sourceUrl": f"https://commons.wikimedia.org/?curid={page.get('pageid')}",
                    "width": int(image_info.get("width") or 0),
                    "height": int(image_info.get("height") or 0),
                    "provider": "Wikimedia Commons",
                }
            )
        return results[:limit]

    def search_visual_references(self, query: Any, max_results: int = 6) -> dict[str, Any]:
        clean_query = self._query(query)
        limit = max(1, min(8, int(max_results)))
        candidates: list[dict[str, Any]] = []
        errors: list[str] = []
        for provider in (self._bing_images, self._commons_images):
            try:
                candidates.extend(provider(clean_query, limit))
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError, ElementTree.ParseError) as exc:
                errors.append(str(exc))
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            image_url = str(candidate.get("imageUrl") or "")
            if not image_url or image_url in seen:
                continue
            seen.add(image_url)
            try:
                preview_source = self._public_image_bytes(
                    str(candidate.get("thumbnailUrl") or image_url),
                    WEB_SEARCH_RESPONSE_MAX_BYTES,
                )
                preview_bytes = self._normalized_image_bytes(preview_source, max_side=720)
            except (OSError, RuntimeError, ValueError, Image.UnidentifiedImageError):
                continue
            clean_candidate = dict(candidate)
            clean_candidate["_previewBytes"] = preview_bytes
            clean_candidate["previewDataUrl"] = (
                "data:image/jpeg;base64," + base64.b64encode(preview_bytes).decode("ascii")
            )
            unique.append(clean_candidate)
            if len(unique) >= limit:
                break
        return {"query": clean_query, "results": unique, "errors": errors}

    def stage_reference_records(
        self,
        candidates: list[dict[str, Any]],
        target_dir: Path,
        max_count: int = WEB_REFERENCE_MAX_COUNT,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        target_dir.mkdir(parents=True, exist_ok=True)
        for candidate in candidates[:max_count]:
            image_bytes = b""
            for url in (candidate.get("imageUrl"), candidate.get("thumbnailUrl")):
                if not url:
                    continue
                try:
                    image_bytes = self._public_image_bytes(
                        str(url), WEB_REFERENCE_IMAGE_MAX_BYTES
                    )
                    image_bytes = self._normalized_image_bytes(image_bytes)
                    break
                except (OSError, RuntimeError, ValueError, Image.UnidentifiedImageError):
                    image_bytes = b""
            if not image_bytes:
                preview_bytes = candidate.get("_previewBytes")
                if isinstance(preview_bytes, bytes):
                    image_bytes = preview_bytes
            if not image_bytes:
                continue
            output_path = target_dir / f"reference-{len(records) + 1}.jpg"
            output_path.write_bytes(image_bytes)
            records.append(
                {
                    "id": str(candidate.get("id") or "")[:80],
                    "title": str(candidate.get("title") or "")[:240],
                    "caption": str(candidate.get("caption") or "")[:400],
                    "provider": str(candidate.get("provider") or "")[:120],
                    "sourceUrl": str(candidate.get("sourceUrl") or "")[:4096],
                    "imageUrl": str(candidate.get("imageUrl") or "")[:4096],
                    "path": str(output_path),
                }
            )
        return records

    def stage_references(
        self,
        candidates: list[dict[str, Any]],
        target_dir: Path,
        max_count: int = WEB_REFERENCE_MAX_COUNT,
    ) -> tuple[Path, ...]:
        return tuple(
            Path(record["path"])
            for record in self.stage_reference_records(candidates, target_dir, max_count)
        )


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.lock, self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    secret_dpapi TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS usage_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_id TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
                    sampled_at REAL NOT NULL,
                    total_cost REAL NOT NULL DEFAULT 0,
                    today_cost REAL NOT NULL DEFAULT 0,
                    quota_limit REAL NOT NULL DEFAULT 0,
                    quota_used REAL NOT NULL DEFAULT 0,
                    remaining REAL NOT NULL DEFAULT 0,
                    used_5h REAL NOT NULL DEFAULT 0,
                    used_1d REAL NOT NULL DEFAULT 0,
                    used_7d REAL NOT NULL DEFAULT 0,
                    today_requests INTEGER NOT NULL DEFAULT 0,
                    total_requests INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_snapshots_key_time
                    ON usage_snapshots(key_id, sampled_at);
                CREATE TABLE IF NOT EXISTS daily_usage (
                    key_id TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
                    usage_date TEXT NOT NULL,
                    cost REAL NOT NULL DEFAULT 0,
                    requests INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (key_id, usage_date)
                );
                CREATE TABLE IF NOT EXISTS alert_state (
                    key_id TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
                    metric TEXT NOT NULL,
                    severity INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (key_id, metric)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            invalid_snapshot_ids: list[int] = []
            for row in db.execute("SELECT id,payload_json FROM usage_snapshots").fetchall():
                try:
                    payload = json.loads(row["payload_json"])
                    value = ((payload.get("usage") or {}).get("total") or {}).get("cost")
                    total_cost = float(value)
                    valid = math.isfinite(total_cost) and total_cost >= 0
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    valid = False
                if not valid:
                    invalid_snapshot_ids.append(int(row["id"]))
            if invalid_snapshot_ids:
                db.executemany(
                    "DELETE FROM usage_snapshots WHERE id=?",
                    ((snapshot_id,) for snapshot_id in invalid_snapshot_ids),
                )

    def add_key(self, name: str, secret: str, base_url: str) -> str:
        key_id = uuid.uuid4().hex
        now = time.time()
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO api_keys(id,name,secret_dpapi,base_url,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (key_id, name, protect_secret(secret), base_url.rstrip("/"), now, now),
            )
        return key_id

    def import_environment_key(self) -> None:
        secret = os.environ.get("OPENAI_API_KEY", "").strip()
        if not secret:
            return
        with self.lock, self.connect() as db:
            existing = db.execute("SELECT secret_dpapi FROM api_keys").fetchall()
            for row in existing:
                try:
                    if unprotect_secret(row["secret_dpapi"]) == secret:
                        return
                except OSError:
                    continue
        self.add_key("环境变量 Key", secret, os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL))

    def list_key_records(self) -> list[sqlite3.Row]:
        with self.lock, self.connect() as db:
            return db.execute("SELECT * FROM api_keys ORDER BY created_at").fetchall()

    def get_key_record(self, key_id: str) -> sqlite3.Row | None:
        with self.lock, self.connect() as db:
            return db.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()

    def get_secret(self, key_id: str) -> str:
        row = self.get_key_record(key_id)
        if row is None:
            raise KeyError("密钥不存在")
        return unprotect_secret(row["secret_dpapi"])

    def delete_key(self, key_id: str) -> None:
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM api_keys WHERE id=?", (key_id,))

    def set_error(self, key_id: str, error: str | None) -> None:
        with self.lock, self.connect() as db:
            db.execute(
                "UPDATE api_keys SET last_error=?,updated_at=? WHERE id=?",
                (error, time.time(), key_id),
            )

    def save_snapshot(self, key_id: str, payload: dict[str, Any]) -> None:
        now = time.time()
        quota = payload.get("quota") or {}
        usage = payload.get("usage") or {}
        today = usage.get("today") or {}
        total = usage.get("total") or {}
        total_cost_raw = total.get("cost")
        try:
            total_cost = float(total_cost_raw)
        except (TypeError, ValueError):
            raise ValueError("上游未返回有效的累计用量") from None
        if not math.isfinite(total_cost) or total_cost < 0:
            raise ValueError("上游返回的累计用量无效")
        windows = {str(item.get("window")): item for item in payload.get("rate_limits") or []}
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO usage_snapshots(
                    key_id,sampled_at,total_cost,today_cost,quota_limit,quota_used,remaining,
                    used_5h,used_1d,used_7d,today_requests,total_requests,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    key_id,
                    now,
                    total_cost,
                    safe_float(today.get("cost")),
                    safe_float(quota.get("limit")),
                    safe_float(quota.get("used")),
                    safe_float(payload.get("remaining")),
                    safe_float((windows.get("5h") or {}).get("used")),
                    safe_float((windows.get("1d") or {}).get("used")),
                    safe_float((windows.get("7d") or {}).get("used")),
                    int(today.get("requests") or 0),
                    int(total.get("requests") or 0),
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            for item in payload.get("daily_usage") or []:
                usage_date = str(item.get("date") or "")[:10]
                if not usage_date:
                    continue
                db.execute(
                    """INSERT INTO daily_usage(
                        key_id,usage_date,cost,requests,input_tokens,output_tokens,total_tokens
                    ) VALUES(?,?,?,?,?,?,?) ON CONFLICT(key_id,usage_date) DO UPDATE SET
                        cost=excluded.cost,requests=excluded.requests,input_tokens=excluded.input_tokens,
                        output_tokens=excluded.output_tokens,total_tokens=excluded.total_tokens""",
                    (
                        key_id,
                        usage_date,
                        safe_float(item.get("cost")),
                        int(item.get("requests") or 0),
                        int(item.get("input_tokens") or 0),
                        int(item.get("output_tokens") or 0),
                        int(item.get("total_tokens") or 0),
                    ),
                )
            cutoff = now - RETENTION_DAYS * 86400
            cutoff_date = datetime.fromtimestamp(cutoff).date().isoformat()
            db.execute("DELETE FROM usage_snapshots WHERE sampled_at < ?", (cutoff,))
            db.execute("DELETE FROM daily_usage WHERE usage_date < ?", (cutoff_date,))
            db.execute("UPDATE api_keys SET last_error=NULL,updated_at=? WHERE id=?", (now, key_id))

    def latest_payload(self, key_id: str) -> dict[str, Any] | None:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT payload_json FROM usage_snapshots WHERE key_id=? ORDER BY sampled_at DESC,id DESC LIMIT 1",
                (key_id,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    @staticmethod
    def _snapshot_interval_hint(db: sqlite3.Connection, key_id: str) -> float:
        rows = db.execute(
            """SELECT sampled_at FROM usage_snapshots WHERE key_id=?
            ORDER BY sampled_at DESC,id DESC LIMIT 6""",
            (key_id,),
        ).fetchall()
        intervals = sorted(
            later["sampled_at"] - earlier["sampled_at"]
            for later, earlier in zip(rows, rows[1:])
            if later["sampled_at"] > earlier["sampled_at"]
        )
        return intervals[len(intervals) // 2] if intervals else FOREGROUND_INTERVAL

    def rates(self, key_id: str) -> dict[str, Any]:
        now = time.time()
        with self.lock, self.connect() as db:
            latest = db.execute(
                "SELECT id,sampled_at,total_cost,today_cost FROM usage_snapshots WHERE key_id=? ORDER BY sampled_at DESC,id DESC LIMIT 1",
                (key_id,),
            ).fetchone()
            if latest is None:
                return {
                    "speed10m": None,
                    "speed1h": None,
                    "intervals": {
                        "10m": {"value": None, "status": "unrecorded", "observedSeconds": 0},
                        "1h": {"value": None, "status": "unrecorded", "observedSeconds": 0},
                    },
                    "avgMin": 0,
                    "avgHour": 0,
                    "avgDay": 0,
                    "averages": {
                        "today": {"cost": 0, "avgMin": 0, "avgHour": 0, "label": ""},
                        "week": {"cost": 0, "avgMin": 0, "avgHour": 0, "label": ""},
                    },
                    "hourly12h": [],
                    "tenMinute2h": [],
                    "timezone": "UTC+8",
                }

            def period_usage(seconds: int) -> dict[str, Any]:
                if now - latest["sampled_at"] > max(600, seconds):
                    return {"value": None, "status": "unrecorded", "observedSeconds": 0}
                target = latest["sampled_at"] - seconds
                before = db.execute(
                    """SELECT id,sampled_at,total_cost FROM usage_snapshots
                    WHERE key_id=? AND sampled_at<=? ORDER BY sampled_at DESC,id DESC LIMIT 1""",
                    (key_id, target),
                ).fetchone()
                after = db.execute(
                    """SELECT id,sampled_at,total_cost FROM usage_snapshots
                    WHERE key_id=? AND sampled_at>=? AND
                    (sampled_at<? OR (sampled_at=? AND id<?))
                    ORDER BY sampled_at ASC,id ASC LIMIT 1""",
                    (key_id, target, latest["sampled_at"], latest["sampled_at"], latest["id"]),
                ).fetchone()
                if before is None:
                    first = db.execute(
                        """SELECT id,sampled_at,total_cost FROM usage_snapshots
                        WHERE key_id=? AND (sampled_at<? OR (sampled_at=? AND id<?))
                        ORDER BY sampled_at ASC,id ASC LIMIT 1""",
                        (key_id, latest["sampled_at"], latest["sampled_at"], latest["id"]),
                    ).fetchone()
                    if first is None:
                        return {"value": None, "status": "unrecorded", "observedSeconds": 0}
                    elapsed = latest["sampled_at"] - first["sampled_at"]
                    delta = latest["total_cost"] - first["total_cost"]
                    if elapsed <= 0 or delta < 0:
                        return {"value": None, "status": "unrecorded", "observedSeconds": 0}
                    return {
                        "value": delta,
                        "status": "estimated",
                        "observedSeconds": round(elapsed),
                    }
                if after is None:
                    after = before
                span = after["sampled_at"] - before["sampled_at"]
                if span > 0:
                    ratio = (target - before["sampled_at"]) / span
                    target_cost = before["total_cost"] + (after["total_cost"] - before["total_cost"]) * ratio
                else:
                    target_cost = before["total_cost"]
                delta = latest["total_cost"] - target_cost
                if delta < 0:
                    return {"value": None, "status": "unrecorded", "observedSeconds": 0}
                boundary_tolerance = min(
                    600,
                    max(90, self._snapshot_interval_hint(db, key_id) * 2),
                )
                covers_start = (
                    target - before["sampled_at"] <= boundary_tolerance
                    and after["sampled_at"] - target <= boundary_tolerance
                )
                return {
                    "value": delta,
                    "status": "recorded" if covers_start else "estimated",
                    "observedSeconds": seconds,
                }

            interval_10m = period_usage(600)
            interval_1h = period_usage(3600)
            now_local = datetime.fromtimestamp(now, BUSINESS_TIMEZONE)
            today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            week_start = today_start - timedelta(days=today_start.weekday())
            rows = db.execute(
                """SELECT usage_date,cost FROM daily_usage
                WHERE key_id=? AND usage_date>=? AND usage_date<=? ORDER BY usage_date""",
                (key_id, week_start.date().isoformat(), today_start.date().isoformat()),
            ).fetchall()
            daily_costs = {row["usage_date"]: safe_float(row["cost"]) for row in rows}
            today_key = today_start.date().isoformat()
            today_cost = daily_costs.get(today_key, safe_float(latest["today_cost"]))
            daily_costs[today_key] = today_cost
            week_cost = sum(daily_costs.values())

            def usage_buckets(
                bucket_seconds: int, count: int, boundary: datetime
            ) -> list[dict[str, Any]]:
                buckets: list[dict[str, Any]] = []
                boundary_tolerance = min(
                    600,
                    max(90, self._snapshot_interval_hint(db, key_id)),
                )
                for offset in range(count, 0, -1):
                    start = boundary - timedelta(seconds=bucket_seconds * offset)
                    end = start + timedelta(seconds=bucket_seconds)
                    start_timestamp = start.timestamp()
                    end_timestamp = end.timestamp()
                    before = db.execute(
                        """SELECT sampled_at,total_cost FROM usage_snapshots
                        WHERE key_id=? AND sampled_at<=? ORDER BY sampled_at DESC,id DESC LIMIT 1""",
                        (key_id, start_timestamp),
                    ).fetchone()
                    after = db.execute(
                        """SELECT sampled_at,total_cost FROM usage_snapshots
                        WHERE key_id=? AND sampled_at<=? ORDER BY sampled_at DESC,id DESC LIMIT 1""",
                        (key_id, end_timestamp),
                    ).fetchone()
                    if before and start_timestamp - before["sampled_at"] > boundary_tolerance:
                        before = db.execute(
                            """SELECT sampled_at,total_cost FROM usage_snapshots
                            WHERE key_id=? AND sampled_at>? AND sampled_at<=?
                            ORDER BY sampled_at ASC,id ASC LIMIT 1""",
                            (key_id, start_timestamp, end_timestamp),
                        ).fetchone()
                    elif before is None:
                        before = db.execute(
                            """SELECT sampled_at,total_cost FROM usage_snapshots
                            WHERE key_id=? AND sampled_at>? AND sampled_at<=?
                            ORDER BY sampled_at ASC,id ASC LIMIT 1""",
                            (key_id, start_timestamp, end_timestamp),
                        ).fetchone()
                    if after and after["sampled_at"] <= start_timestamp:
                        after = None
                    elapsed = (
                        after["sampled_at"] - before["sampled_at"]
                        if before and after
                        else 0
                    )
                    delta = (
                        after["total_cost"] - before["total_cost"]
                        if before and after
                        else -1
                    )
                    if elapsed > 0 and delta >= 0:
                        covers_start = (
                            abs(start_timestamp - before["sampled_at"])
                            <= boundary_tolerance
                        )
                        covers_end = (
                            end_timestamp - after["sampled_at"] <= boundary_tolerance
                        )
                        status = (
                            "recorded" if covers_start and covers_end else "estimated"
                        )
                        cost = delta * bucket_seconds / elapsed
                    else:
                        status = "unrecorded"
                        cost = None
                    buckets.append(
                        {
                            "startTimestamp": int(start_timestamp * 1000),
                            "endTimestamp": int(end_timestamp * 1000),
                            "cost": cost,
                            "status": status,
                            "observedSeconds": round(max(0, elapsed)),
                        }
                    )
                return buckets

            current_hour = now_local.replace(minute=0, second=0, microsecond=0)
            current_ten_minutes = now_local.replace(
                minute=(now_local.minute // 10) * 10,
                second=0,
                microsecond=0,
            )
            hourly_usage = usage_buckets(3600, 12, current_hour)
            ten_minute_usage = usage_buckets(600, 12, current_ten_minutes)

        today_elapsed_minutes = max(1.0, (now_local - today_start).total_seconds() / 60)
        week_elapsed_minutes = max(1.0, (now_local - week_start).total_seconds() / 60)

        def period_average(cost: float, elapsed_minutes: float, label: str) -> dict[str, Any]:
            return {
                "cost": cost,
                "avgMin": cost / elapsed_minutes,
                "avgHour": cost / (elapsed_minutes / 60),
                "label": label,
            }

        today_average = period_average(
            today_cost,
            today_elapsed_minutes,
            today_start.strftime("%Y-%m-%d"),
        )
        week_average = period_average(
            week_cost,
            week_elapsed_minutes,
            f"{week_start:%m-%d} 至 {now_local:%m-%d}",
        )
        return {
            "speed10m": interval_10m["value"],
            "speed1h": interval_1h["value"],
            "intervals": {"10m": interval_10m, "1h": interval_1h},
            "avgMin": today_average["avgMin"],
            "avgHour": today_average["avgHour"],
            "avgDay": today_cost,
            "averages": {"today": today_average, "week": week_average},
            "hourly12h": hourly_usage,
            "tenMinute2h": ten_minute_usage,
            "timezone": "UTC+8",
        }

    def get_thresholds(self) -> dict[str, float]:
        defaults = {"warn": 50.0, "danger": 25.0, "critical": 10.0}
        with self.lock, self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE name='thresholds'").fetchone()
        if not row:
            return defaults
        try:
            loaded = json.loads(row["value"])
            return {key: safe_float(loaded.get(key), value) for key, value in defaults.items()}
        except (TypeError, json.JSONDecodeError):
            return defaults

    def set_thresholds(self, thresholds: dict[str, Any]) -> dict[str, float]:
        clean = {
            "warn": min(100.0, max(0.0, safe_float(thresholds.get("warn"), 50))),
            "danger": min(100.0, max(0.0, safe_float(thresholds.get("danger"), 25))),
            "critical": min(100.0, max(0.0, safe_float(thresholds.get("critical"), 10))),
        }
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO settings(name,value) VALUES('thresholds',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (json.dumps(clean),),
            )
        return clean

    def get_refresh_intervals(self) -> dict[str, int]:
        defaults = {
            "foreground": FOREGROUND_INTERVAL,
            "background": BACKGROUND_INTERVAL,
        }
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='refresh_intervals'"
            ).fetchone()
        if not row:
            return defaults
        try:
            loaded = json.loads(row["value"])
            return {
                "foreground": max(
                    FOREGROUND_INTERVAL,
                    int(safe_float(loaded.get("foreground"), FOREGROUND_INTERVAL)),
                ),
                "background": max(
                    BACKGROUND_INTERVAL,
                    int(safe_float(loaded.get("background"), BACKGROUND_INTERVAL)),
                ),
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            return defaults

    def set_refresh_intervals(
        self, foreground: Any, background: Any
    ) -> dict[str, int]:
        clean = {
            "foreground": max(
                FOREGROUND_INTERVAL,
                int(safe_float(foreground, FOREGROUND_INTERVAL)),
            ),
            "background": max(
                BACKGROUND_INTERVAL,
                int(safe_float(background, BACKGROUND_INTERVAL)),
            ),
        }
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('refresh_intervals',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (json.dumps(clean),),
            )
        return clean

    def get_rate_limit_progress_mode(self) -> str:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='rate_limit_progress_mode'"
            ).fetchone()
        return row["value"] if row and row["value"] in {"remaining", "used"} else "remaining"

    def set_rate_limit_progress_mode(self, mode: Any) -> str:
        clean = "used" if str(mode).lower() == "used" else "remaining"
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('rate_limit_progress_mode',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (clean,),
            )
        return clean

    def get_update_frequency(self) -> str:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='update_frequency'"
            ).fetchone()
        return row["value"] if row and row["value"] in {"startup", "weekly", "manual"} else "startup"

    def set_update_frequency(self, frequency: Any) -> str:
        clean = str(frequency).lower()
        if clean not in {"startup", "weekly", "manual"}:
            clean = "startup"
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('update_frequency',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (clean,),
            )
        return clean

    def get_ignored_update_version(self) -> str:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='ignored_update_version'"
            ).fetchone()
        return str(row["value"]).strip() if row else ""

    def set_ignored_update_version(self, version: Any) -> str:
        clean = str(version or "").strip().lstrip("v")
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('ignored_update_version',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (clean,),
            )
        return clean

    def get_close_action(self) -> str:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='close_action'"
            ).fetchone()
        return row["value"] if row and row["value"] in {"exit", "tray", "ask"} else "ask"

    def set_close_action(self, action: Any) -> str:
        clean = str(action).lower()
        if clean not in {"exit", "tray", "ask"}:
            clean = "ask"
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('close_action',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (clean,),
            )
        return clean

    def get_always_on_top(self) -> bool:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='always_on_top'"
            ).fetchone()
        return bool(row and str(row["value"]).strip() == "1")

    def set_always_on_top(self, enabled: Any) -> bool:
        clean = bool(enabled)
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('always_on_top',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                ("1" if clean else "0",),
            )
        return clean

    def get_window_size(self) -> dict[str, int]:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='window_size'"
            ).fetchone()
        if not row:
            return normalize_window_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        try:
            loaded = json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return normalize_window_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        if not isinstance(loaded, dict):
            return normalize_window_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        return normalize_window_size(loaded.get("width"), loaded.get("height"))

    def set_window_size(self, width: Any, height: Any) -> dict[str, int]:
        clean = normalize_window_size(width, height)
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('window_size',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (json.dumps(clean, separators=(",", ":")),),
            )
        return clean

    def get_background_ui_mode(self) -> str:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='background_ui_mode'"
            ).fetchone()
        return normalize_background_ui_mode(row["value"] if row else None)

    def set_background_ui_mode(self, mode: Any) -> str:
        clean = normalize_background_ui_mode(mode)
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('background_ui_mode',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (clean,),
            )
        return clean

    def get_title_bar_mode(self) -> str:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='title_bar_mode'"
            ).fetchone()
        return normalize_title_bar_mode(row["value"] if row else None)

    def set_title_bar_mode(self, mode: Any) -> str:
        clean = normalize_title_bar_mode(mode)
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('title_bar_mode',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (clean,),
            )
        return clean

    def get_last_update_check(self) -> float:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE name='last_update_check'"
            ).fetchone()
        return max(0.0, safe_float(row["value"])) if row else 0.0

    def set_last_update_check(self, checked_at: float) -> float:
        clean = max(0.0, safe_float(checked_at))
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO settings(name,value) VALUES('last_update_check',?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (str(clean),),
            )
        return clean

    def alert_severity(self, key_id: str, metric: str) -> int:
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT severity FROM alert_state WHERE key_id=? AND metric=?", (key_id, metric)
            ).fetchone()
        return int(row["severity"]) if row else 0

    def set_alert_severity(self, key_id: str, metric: str, severity: int) -> None:
        with self.lock, self.connect() as db:
            db.execute(
                """INSERT INTO alert_state(key_id,metric,severity,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(key_id,metric) DO UPDATE SET severity=excluded.severity,updated_at=excluded.updated_at""",
                (key_id, metric, severity, time.time()),
            )

    def reset_alert_metrics(self, key_id: str, metrics: set[str]) -> None:
        if not metrics:
            return
        with self.lock, self.connect() as db:
            db.executemany(
                "DELETE FROM alert_state WHERE key_id=? AND metric=?",
                ((key_id, metric) for metric in metrics),
            )

    def reset_limit_alerts(self) -> None:
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM alert_state WHERE metric NOT LIKE '%负载'")


class EasyClinClient:
    @staticmethod
    def get_json(base_url: str, secret: str, path: str, timeout: int = 25) -> dict[str, Any]:
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {secret}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(body)
                message = (parsed.get("error") or {}).get("message") or parsed.get("message")
            except json.JSONDecodeError:
                message = body[:200]
            raise RuntimeError(f"HTTP {exc.code}: {message or exc.reason}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"网络请求失败: {exc.reason if hasattr(exc, 'reason') else exc}") from None

    def fetch(self, base_url: str, secret: str) -> tuple[dict[str, Any], list[str] | None]:
        usage = self.get_json(base_url, secret, "/usage?days=30")
        try:
            models = self.get_json(base_url, secret, "/models")
        except RuntimeError:
            return usage, None
        model_ids: list[str] = []
        seen: set[str] = set()
        for item in models.get("data") or []:
            model_id = item.get("id") if isinstance(item, dict) else item
            model_id = str(model_id or "").strip()
            if model_id and model_id not in seen:
                seen.add(model_id)
                model_ids.append(model_id)
        return usage, model_ids

    @staticmethod
    def _response_output_text(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        direct = payload.get("output_text")
        if isinstance(direct, str):
            return direct
        response = payload.get("response")
        if isinstance(response, dict):
            nested = EasyClinClient._response_output_text(response)
            if nested:
                return nested
        parts: list[str] = []
        for output in payload.get("output") or []:
            if not isinstance(output, dict):
                continue
            for content in output.get("content") or []:
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if content.get("type") in {"output_text", "text"} and isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    @staticmethod
    def stream_response(
        base_url: str,
        secret: str,
        model: str,
        instructions: str,
        input_text: Any,
        on_delta: Any = None,
        timeout: int = 240,
        reasoning_effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        include: list[str] | None = None,
        max_tool_calls: int | None = None,
        parallel_tool_calls: bool | None = None,
        on_completed: Any = None,
        allow_empty_text: bool = False,
    ) -> str:
        url = f"{base_url.rstrip('/')}/responses"
        payload = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "stream": True,
        }
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if include:
            payload["include"] = include
        if max_tool_calls is not None:
            payload["max_tool_calls"] = max(1, int(max_tool_calls))
        if parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = bool(parallel_tool_calls)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {secret}",
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if "text/event-stream" not in content_type:
                    payload = json.loads(response.read().decode("utf-8"))
                    if on_completed is not None:
                        on_completed(payload)
                    text = EasyClinClient._response_output_text(payload)
                    if not text and not allow_empty_text:
                        raise RuntimeError("Responses 接口未返回文本")
                    if on_delta is not None:
                        on_delta(text)
                    return text

                chunks: list[str] = []
                event_type = ""
                data_lines: list[str] = []

                def flush_event() -> None:
                    nonlocal event_type, data_lines
                    if not data_lines:
                        event_type = ""
                        return
                    raw_data = "\n".join(data_lines)
                    current_event = event_type
                    event_type = ""
                    data_lines = []
                    if raw_data == "[DONE]":
                        return
                    try:
                        payload = json.loads(raw_data)
                    except json.JSONDecodeError:
                        return
                    payload_type = str(payload.get("type") or current_event or "")
                    if payload_type == "response.output_text.delta":
                        delta = payload.get("delta")
                        if isinstance(delta, str) and delta:
                            chunks.append(delta)
                            if on_delta is not None:
                                on_delta(delta)
                        return
                    if payload_type in {"error", "response.failed", "response.incomplete"}:
                        error = payload.get("error") or payload.get("response", {}).get("error") or {}
                        message = error.get("message") if isinstance(error, dict) else str(error)
                        raise RuntimeError(message or "Responses 接口执行失败")
                    if payload_type == "response.completed" and not chunks:
                        completed_text = EasyClinClient._response_output_text(payload)
                        if completed_text:
                            chunks.append(completed_text)
                            if on_delta is not None:
                                on_delta(completed_text)
                    if payload_type == "response.completed" and on_completed is not None:
                        completed = payload.get("response")
                        on_completed(completed if isinstance(completed, dict) else payload)

                for raw_line in response:
                    line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                    if not line:
                        flush_event()
                    elif line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                flush_event()
                text = "".join(chunks)
                if not text and not allow_empty_text:
                    raise RuntimeError("Responses 接口未返回文本")
                return text
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(response_body)
                error = payload.get("error") or {}
                message = error.get("message") if isinstance(error, dict) else str(error)
            except json.JSONDecodeError:
                message = response_body[:300]
            raise RuntimeError(f"HTTP {exc.code}: {message or exc.reason}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = exc.reason if hasattr(exc, "reason") else exc
            raise RuntimeError(f"Responses 网络请求失败: {reason}") from None

class AppController:
    def __init__(
        self,
        asset_cache: StaticAssetCache | None = None,
        ui_show_callback: Any = None,
        ui_hide_callback: Any = None,
    ) -> None:
        trace_startup("controller_init_started")
        self.asset_cache = asset_cache or StaticAssetCache()
        self.store = Store(app_data_dir() / "api_tools.db")
        self.restart_ready_path = os.environ.pop(RESTART_READY_ENV, "").strip()
        self.active_title_bar_mode = self.store.get_title_bar_mode()
        self.client = EasyClinClient()
        self.image_generator = ImageGenerationService()
        self.web_search = WebSearchService()
        self.store.import_environment_key()
        self.window: webview.Window | None = None
        self.tray: Any = None
        self.ui_show_callback = ui_show_callback
        self.ui_hide_callback = ui_hide_callback
        self.visible = True
        self.ui_visibility_token = 0
        self.always_on_top = self.store.get_always_on_top()
        self.maximized = False
        saved_window_size = self.store.get_window_size()
        self._last_saved_window_size = (
            saved_window_size["width"],
            saved_window_size["height"],
        )
        self._window_size_lock = threading.Lock()
        self._pending_window_size: tuple[int, int] | None = None
        self._window_size_timer: threading.Timer | None = None
        self.drag_restore_suppressed_until = 0.0
        self.stopping = threading.Event()
        self.frontend_ready = threading.Event()
        self.refresh_wakeup = threading.Event()
        self.refresh_lock = threading.Lock()
        self.manual_refresh_lock = threading.Lock()
        self.manual_refresh_available_at = 0.0
        self.update_lock = threading.Lock()
        self.image_stream_debug_lock = threading.Lock()
        self.active_image_sets: set[str] = set()
        self.image_session_activity_lock = threading.Lock()
        self.active_image_sessions: set[str] = set()
        full_release_notes = bundled_changelog()
        self.update_state: dict[str, Any] = {
            "status": "idle",
            "percent": 0,
            "message": "尚未检查更新",
            "currentVersion": APP_VERSION,
            "latestVersion": APP_VERSION,
            "releaseNotes": changelog_for_update(
                full_release_notes, APP_VERSION, APP_VERSION
            ),
            "fullReleaseNotes": full_release_notes,
            "available": False,
            "showPrompt": False,
        }
        intervals = self.store.get_refresh_intervals()
        self.foreground_interval = intervals["foreground"]
        self.background_interval = intervals["background"]
        self.next_refresh_at = time.time() + self.foreground_interval
        self.icon_png = resource_path("assets/api_tools_icon.png")
        trace_startup("controller_init_finished")

    def bind_window(self, window: webview.Window) -> None:
        self.window = window
        window.events.loaded += self._on_page_loaded
        window.events.minimized += self._on_minimized
        window.events.maximized += self._on_maximized
        window.events.restored += self._on_restored
        window.events.resized += self._on_resized
        window.events.closing += self._on_closing

    def set_window_size(self, width: Any, height: Any) -> dict[str, Any]:
        clean = normalize_window_size(width, height)
        size = (clean["width"], clean["height"])
        lock = getattr(self, "_window_size_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._window_size_lock = lock
        with lock:
            if size == getattr(
                self,
                "_last_saved_window_size",
                (DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT),
            ):
                return {"ok": True, "windowSize": clean, "changed": False}
            saved = self.store.set_window_size(clean["width"], clean["height"])
            self._last_saved_window_size = (saved["width"], saved["height"])
        return {"ok": True, "windowSize": saved, "changed": True}

    def _window_is_normal(self) -> bool:
        if getattr(self, "maximized", False):
            return False
        try:
            hwnd = self._window_handle()
        except (AttributeError, OSError, RuntimeError):
            return True
        return not hwnd or (not user32.IsZoomed(hwnd) and not user32.IsIconic(hwnd))

    def _current_window_size(
        self, width: Any = None, height: Any = None
    ) -> tuple[int, int]:
        window = getattr(self, "window", None)
        if width is None:
            try:
                width = window.width if window else None
            except (AttributeError, OSError, RuntimeError):
                width = None
        if height is None:
            try:
                height = window.height if window else None
            except (AttributeError, OSError, RuntimeError):
                height = None
        if width is None or height is None:
            return getattr(
                self,
                "_last_saved_window_size",
                (DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT),
            )
        clean = normalize_window_size(width, height)
        return clean["width"], clean["height"]

    def _schedule_window_size_save(
        self, width: Any = None, height: Any = None
    ) -> None:
        if not self._window_is_normal():
            return
        size = self._current_window_size(width, height)
        if size == getattr(
            self,
            "_last_saved_window_size",
            (DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT),
        ):
            return
        lock = getattr(self, "_window_size_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._window_size_lock = lock
        with lock:
            self._pending_window_size = size
            timer = getattr(self, "_window_size_timer", None)
            if timer and timer.is_alive():
                return
            timer = threading.Timer(
                WINDOW_SIZE_SAVE_DELAY, self._persist_pending_window_size
            )
            timer.daemon = True
            self._window_size_timer = timer
            timer.start()

    def _persist_pending_window_size(self) -> None:
        lock = getattr(self, "_window_size_lock", None)
        if lock is None:
            return
        with lock:
            size = getattr(self, "_pending_window_size", None)
            self._pending_window_size = None
            self._window_size_timer = None
        if size is None or not self._window_is_normal():
            return
        try:
            self.set_window_size(*size)
        except Exception:
            pass

    def _flush_window_size(self) -> None:
        lock = getattr(self, "_window_size_lock", None)
        if lock is None:
            return
        with lock:
            size = getattr(self, "_pending_window_size", None)
            self._pending_window_size = None
            timer = getattr(self, "_window_size_timer", None)
            self._window_size_timer = None
        if timer:
            timer.cancel()
        if size is None:
            size = self._current_window_size()
        if not self._window_is_normal():
            return
        try:
            self.set_window_size(*size)
        except Exception:
            pass

    def choose_edit_images(self) -> dict[str, Any]:
        if not self.window:
            return {"ok": False, "error": "应用窗口尚未就绪"}
        selected = self.window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=str(windows_pictures_dir()),
            allow_multiple=True,
            file_types=("图片文件 (*.png;*.jpg;*.jpeg;*.webp)",),
        )
        selected_paths = [str(Path(path).resolve()) for path in selected or []]
        eligible_paths = [
            path
            for path in selected_paths
            if Path(path).is_file() and Path(path).stat().st_size <= 50 * 1024 * 1024
        ]
        oversized_count = len(selected_paths) - len(eligible_paths)
        paths = eligible_paths[:16]
        warnings = []
        if len(eligible_paths) > 16:
            warnings.append("参考图片最多 16 张，已仅导入前 16 张")
        if oversized_count:
            warnings.append(f"{oversized_count} 张图片超过 50 MB，未导入")
        return {
            "ok": True,
            "paths": paths,
            "warning": "；".join(warnings),
            "files": [
                {
                    "name": Path(path).name,
                    "sizeBytes": Path(path).stat().st_size,
                    "uri": Path(path).as_uri(),
                    "previewUri": image_preview_data_url(Path(path).read_bytes()),
                }
                for path in paths
                if Path(path).is_file()
            ],
        }

    def import_reference_image(self, data_url: str, name: str = "") -> dict[str, Any]:
        raw_data = str(data_url or "")
        if not raw_data.startswith("data:image/") or "," not in raw_data:
            return {"ok": False, "error": "只支持粘贴或拖入图片文件"}
        header, encoded = raw_data.split(",", 1)
        mime_type = header[5:].split(";", 1)[0].lower()
        suffixes = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }
        suffix = suffixes.get(mime_type)
        if suffix is None or ";base64" not in header.lower():
            return {"ok": False, "error": "仅支持 PNG、JPEG 或 WebP 图片"}
        if len(encoded) > 68 * 1024 * 1024:
            return {"ok": False, "error": "图片超过 50 MB，未导入"}
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return {"ok": False, "error": "图片数据无效"}
        if len(image_bytes) > 50 * 1024 * 1024:
            return {"ok": False, "error": "图片超过 50 MB，未导入"}
        try:
            with Image.open(io.BytesIO(image_bytes)) as source_image:
                source_image.verify()
        except (OSError, ValueError):
            return {"ok": False, "error": "图片内容无效"}

        reference_dir = app_data_dir() / "image-references"
        reference_dir.mkdir(parents=True, exist_ok=True)
        clean_stem = Path(str(name or "reference")).stem.strip()[:48] or "reference"
        safe_stem = "".join(character for character in clean_stem if character.isalnum() or character in "-_ ").strip() or "reference"
        output_path = reference_dir / f"{safe_stem}-{uuid.uuid4().hex[:8]}{suffix}"
        output_path.write_bytes(image_bytes)
        return {
            "ok": True,
            "path": str(output_path),
            "name": output_path.name,
            "sizeBytes": len(image_bytes),
            "uri": output_path.as_uri(),
            "previewUri": image_preview_data_url(image_bytes),
        }

    def load_generated_image(self, source_path: str) -> dict[str, Any]:
        source = Path(str(source_path or "")).resolve()
        allowed_roots = (
            (app_data_dir() / "image-generations").resolve(),
            generated_pictures_dir().resolve(),
        )
        if not source.is_file() or not any(source.is_relative_to(root) for root in allowed_roots):
            return {"ok": False, "error": "只能读取本应用生成的图片"}
        content_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        content_type = content_types.get(source.suffix.lower())
        if content_type is None or source.stat().st_size > 50 * 1024 * 1024:
            return {"ok": False, "error": "生成图片格式或大小无效"}
        try:
            image_bytes = source.read_bytes()
            with Image.open(io.BytesIO(image_bytes)) as generated_image:
                generated_image.verify()
        except (OSError, ValueError):
            return {"ok": False, "error": "生成图片内容无效"}
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return {
            "ok": True,
            "path": str(source),
            "dataUrl": f"data:{content_type};base64,{encoded}",
        }

    def copy_generated_image(self, source_path: str) -> dict[str, Any]:
        source = Path(str(source_path or "")).resolve()
        pictures_root = generated_pictures_dir().resolve()
        if not source.is_file() or not source.is_relative_to(pictures_root):
            return {"ok": False, "error": "只能复制本应用保存的最终图片"}
        if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            return {"ok": False, "error": "最终图片格式无效"}
        try:
            copy_image_to_windows_clipboard(source)
        except (OSError, RuntimeError, ValueError) as exc:
            return {"ok": False, "error": f"复制图片失败：{exc}"}
        return {"ok": True, "path": str(source)}

    @staticmethod
    def _image_session_store() -> ImageSessionStore:
        return ImageSessionStore(generated_pictures_dir())

    def _image_session_activity_state(self) -> tuple[threading.Lock, set[str]]:
        lock = getattr(self, "image_session_activity_lock", None)
        if lock is None:
            lock = threading.Lock()
            self.image_session_activity_lock = lock
        active_sessions = getattr(self, "active_image_sessions", None)
        if active_sessions is None:
            active_sessions = set()
            self.active_image_sessions = active_sessions
        return lock, active_sessions

    def _reserve_image_session_activity(self, session_id: str) -> bool:
        lock, active_sessions = self._image_session_activity_state()
        clean_session_id = str(session_id or "")
        with lock:
            if clean_session_id in active_sessions:
                return False
            active_sessions.add(clean_session_id)
            return True

    def _release_image_session_activity(self, session_id: str) -> None:
        lock, active_sessions = self._image_session_activity_state()
        with lock:
            active_sessions.discard(str(session_id or ""))

    def _image_session_is_active(self, session_id: str) -> bool:
        lock, active_sessions = self._image_session_activity_state()
        with lock:
            return str(session_id or "") in active_sessions

    def list_image_sets(self) -> dict[str, Any]:
        try:
            sets = self._image_session_store().list_sets()
            active_sets = getattr(self, "active_image_sets", set())
            for image_set in sets:
                if image_set.get("status") == "running" and image_set.get("setId") not in active_sets:
                    image_set["status"] = "interrupted"
                    for item in image_set.get("items") or []:
                        if item.get("status") not in {"completed", "failed"}:
                            item["status"] = "failed"
                            item["error"] = "生成已中断"
            return {"ok": True, "sets": sets}
        except OSError as exc:
            return {"ok": False, "error": f"无法读取图片集历史：{exc}", "sets": []}

    def append_image_stream_debug(self, records: Any) -> dict[str, Any]:
        if not isinstance(records, list):
            return {"ok": False, "error": "流式图片日志格式无效"}

        sensitive_keys = {"src", "dataurl", "base64"}

        def sanitized(value: Any, depth: int = 0) -> Any:
            if depth > 8:
                return "[max-depth]"
            if isinstance(value, dict):
                clean: dict[str, Any] = {}
                for raw_key, raw_value in list(value.items())[:100]:
                    key = str(raw_key)
                    normalized_key = key.casefold().replace("_", "").replace("-", "")
                    if normalized_key.endswith("uri") or normalized_key in sensitive_keys:
                        continue
                    clean[key] = sanitized(raw_value, depth + 1)
                return clean
            if isinstance(value, (list, tuple)):
                return [sanitized(item, depth + 1) for item in value[:100]]
            if isinstance(value, str):
                if value.casefold().startswith("data:image/"):
                    return "[redacted-image-data]"
                return value[:8192]
            if value is None or isinstance(value, (bool, int)):
                return value
            if isinstance(value, float):
                return value if math.isfinite(value) else None
            return str(value)[:8192]

        lines: list[str] = []
        for record in records[:500]:
            if not isinstance(record, dict):
                continue
            encoded = json.dumps(
                sanitized(record), ensure_ascii=False, separators=(",", ":")
            )
            if len(encoded.encode("utf-8")) <= 128 * 1024:
                lines.append(encoded + "\n")

        log_path = app_data_dir() / "image-stream-blur.jsonl"
        if not lines:
            return {"ok": True, "written": 0, "path": str(log_path)}

        payload = "".join(lines)
        lock = getattr(self, "image_stream_debug_lock", None)
        if lock is None:
            lock = threading.Lock()
            self.image_stream_debug_lock = lock
        try:
            with lock:
                if (
                    log_path.is_file()
                    and log_path.stat().st_size + len(payload.encode("utf-8"))
                    > IMAGE_STREAM_DEBUG_LOG_MAX_BYTES
                ):
                    previous_path = log_path.with_name("image-stream-blur.previous.jsonl")
                    previous_path.unlink(missing_ok=True)
                    log_path.replace(previous_path)
                with log_path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(payload)
        except OSError as exc:
            return {"ok": False, "error": f"写入流式图片日志失败：{exc}"}
        return {"ok": True, "written": len(lines), "path": str(log_path)}

    def delete_image_set(self, session_id: str, set_id: str) -> dict[str, Any]:
        active_sets = getattr(self, "active_image_sets", set())
        if str(set_id) in active_sets or self._image_session_is_active(session_id):
            return {"ok": False, "error": "图片会话仍在生成中，暂时不能删除"}
        try:
            deleted = self._image_session_store().delete_set(session_id, set_id)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": f"删除图片集失败：{exc}"}
        if not deleted:
            return {"ok": False, "error": "图片集不存在或已被删除"}
        return {"ok": True, "setId": str(set_id), "sessionId": str(session_id)}

    def save_edited_image(self, source_path: str) -> dict[str, Any]:
        if not self.window:
            return {"ok": False, "error": "应用窗口尚未就绪"}
        source = Path(str(source_path or "")).resolve()
        output_root = (app_data_dir() / "image-generations").resolve()
        pictures_root = generated_pictures_dir().resolve()
        if not source.is_file() or not any(
            source.is_relative_to(root) for root in (output_root, pictures_root)
        ):
            return {"ok": False, "error": "只能保存本应用生成的图片"}
        selected = self.window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(windows_pictures_dir()),
            save_filename=source.name,
            file_types=("PNG 图片 (*.png)", "JPEG 图片 (*.jpg;*.jpeg)", "WebP 图片 (*.webp)"),
        )
        if not selected:
            return {"ok": True, "cancelled": True}
        destination = Path(selected[0]).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return {"ok": True, "path": str(destination)}

    def open_generated_pictures(self) -> dict[str, Any]:
        output_dir = generated_pictures_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(output_dir))
        except OSError as exc:
            return {"ok": False, "error": f"无法打开图片文件夹：{exc}"}
        return {"ok": True, "path": str(output_dir)}

    def start_workers(self) -> None:
        trace_startup("webview_start_callback")
        if self.restart_ready_path:
            try:
                Path(self.restart_ready_path).write_text(str(os.getpid()), encoding="ascii")
            except OSError:
                pass
        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is not None:
            native_form.VisibleChanged += self._on_native_visibility_changed
        self._set_window_corner(False)
        threading.Thread(target=self._refresh_loop, name="usage-refresh", daemon=True).start()
        threading.Thread(target=self._start_tray, name="tray-initializer", daemon=True).start()
        threading.Thread(
            target=self._initial_refresh,
            name="initial-refresh",
            daemon=True,
        ).start()
        self.check_for_updates(manual=False)
        trace_startup("startup_workers_scheduled")

    def _should_check_for_updates(self) -> bool:
        return True

    def _initial_refresh(self) -> None:
        trace_startup("initial_refresh_started")
        self.refresh_all(push_ui=False)
        if self.frontend_ready.wait(timeout=5):
            self.push_state_to_ui()
        trace_startup("initial_refresh_finished")

    def report_startup(self, stage: str, navigation_ms: Any = 0) -> dict[str, Any]:
        allowed_stages = {"frontend_interactive", "frontend_load"}
        if stage in allowed_stages:
            trace_startup(stage, navigationMs=safe_float(navigation_ms))
            if stage == "frontend_interactive":
                self.frontend_ready.set()
        return {"ok": True}

    def _on_page_loaded(self) -> None:
        trace_startup("webview_page_loaded")
        self._disable_native_zoom_control()
        if self.asset_cache.is_ready():
            self.frontend_ready.set()

    def _disable_native_zoom_control(self) -> bool:
        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is None:
            return False

        def disable_zoom() -> None:
            native_webview = getattr(native_form, "webview", None)
            core_webview = getattr(native_webview, "CoreWebView2", None) if native_webview else None
            if core_webview is not None:
                core_webview.Settings.IsZoomControlEnabled = False

        try:
            if native_form.InvokeRequired:
                from System import Action

                native_form.BeginInvoke(Action(disable_zoom))
            else:
                disable_zoom()
            return True
        except Exception:
            return False

    def set_ui_visible(self, visible: Any) -> dict[str, Any]:
        self.visible = bool(visible)
        self.ui_visibility_token += 1
        self.refresh_wakeup.set()
        return {
            "ok": True,
            "visible": self.visible,
            "visibilityToken": self.ui_visibility_token,
        }

    def set_always_on_top(self, enabled: Any) -> dict[str, Any]:
        self.always_on_top = self.store.set_always_on_top(enabled)
        return {"ok": True, "alwaysOnTop": self.always_on_top}

    def notify_ui_hidden(self) -> dict[str, Any]:
        state = self.set_ui_visible(False)
        state["backgroundUiMode"] = self.store.get_background_ui_mode()
        return state

    def claim_ui_release(self, visibility_token: Any) -> dict[str, Any]:
        try:
            token = int(visibility_token)
        except (TypeError, ValueError):
            token = -1
        release = (
            not self.visible
            and token == self.ui_visibility_token
            and self.store.get_background_ui_mode() == "delayed"
        )
        return {"ok": True, "release": release}

    def initialize_assets(self, retry: Any = False) -> dict[str, Any]:
        return self.asset_cache.start_install(retry=bool(retry))

    def get_asset_status(self) -> dict[str, Any]:
        return self.asset_cache.status()

    def complete_initialization(self) -> dict[str, Any]:
        if not self.asset_cache.is_ready():
            return {"ok": False, "error": "静态资源缓存尚未就绪"}
        if not self.window:
            return {"ok": False, "error": "应用窗口尚未就绪"}
        url = self.asset_cache.main_page.as_uri()
        self.window.load_url(url)
        return {"ok": True, "url": url}

    def _window_handle(self) -> int:
        if self.window:
            native_form = getattr(self.window, "native", None)
            if native_form is not None:
                return native_form.Handle.ToInt64()
        return int(user32.FindWindowW(None, WINDOW_TITLE) or 0)

    def _set_window_corner(self, maximized: bool) -> None:
        hwnd = self._window_handle()
        if not hwnd:
            return
        preference = ctypes.c_int(DWMWCP_DONOTROUND if maximized else DWMWCP_ROUND)
        dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference),
            ctypes.sizeof(preference),
        )

    def _push_window_state(self) -> None:
        if not self.window or not self.visible:
            return
        try:
            self.window.evaluate_js(f"window.applyWindowState({str(self.maximized).lower()})")
        except Exception:
            pass

    def native_drag(self, direction: str) -> dict[str, Any]:
        hit_tests = {
            "move": HTCAPTION,
            "left": HTLEFT,
            "right": HTRIGHT,
            "top": HTTOP,
            "top-left": HTTOPLEFT,
            "top-right": HTTOPRIGHT,
            "bottom": HTBOTTOM,
            "bottom-left": HTBOTTOMLEFT,
            "bottom-right": HTBOTTOMRIGHT,
        }
        hit_test = hit_tests.get(direction)
        hwnd = self._window_handle()
        if not hwnd or hit_test is None:
            return {"ok": False}
        restore_before_move = direction == "move" and bool(user32.IsZoomed(hwnd))
        if restore_before_move:
            self.maximized = False

        def perform_drag() -> None:
            if restore_before_move:
                cursor = POINT()
                maximized_rect = wintypes.RECT()
                user32.GetCursorPos(ctypes.byref(cursor))
                user32.GetWindowRect(hwnd, ctypes.byref(maximized_rect))
                width = max(1, maximized_rect.right - maximized_rect.left)
                horizontal_ratio = min(1.0, max(0.0, (cursor.x - maximized_rect.left) / width))
                self.drag_restore_suppressed_until = time.monotonic() + 1.0
                user32.ShowWindow(hwnd, SW_RESTORE)
                restored_rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(restored_rect))
                restored_width = restored_rect.right - restored_rect.left
                user32.SetWindowPos(
                    hwnd,
                    None,
                    round(cursor.x - restored_width * horizontal_ratio),
                    cursor.y - 16,
                    0,
                    0,
                    SWP_NOSIZE | SWP_NOZORDER,
                )
                self.maximized = False
                self._set_window_corner(False)

            if not user32.IsZoomed(hwnd):
                user32.ReleaseCapture()
                user32.PostMessageW(hwnd, WM_NCLBUTTONDOWN, hit_test, 0)

        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is None:
            return {"ok": False}
        if native_form.InvokeRequired:
            from System import Action
                      
            native_form.BeginInvoke(Action(perform_drag))
        else:
            perform_drag()
        return {"ok": True, "maximized": self.maximized}

    def open_devtools(self) -> dict[str, Any]:
        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is None:
            return {"ok": False, "error": "开发者工具尚未就绪"}

        def open_window() -> None:
            native_webview = getattr(native_form, "webview", None)
            core_webview = getattr(native_webview, "CoreWebView2", None) if native_webview else None
            if core_webview is None:
                trace_startup("devtools_open_failed", error="CoreWebView2 is not ready")
                return
            core_webview.Settings.AreDevToolsEnabled = True
            core_webview.OpenDevToolsWindow()

        try:
            if native_form.InvokeRequired:
                from System import Action

                native_form.BeginInvoke(Action(open_window))
            else:
                open_window()
            return {"ok": True}
        except Exception as exc:
            trace_startup("devtools_open_failed", error=str(exc))
            return {"ok": False, "error": "无法打开开发者工具"}

    def _start_tray(self) -> None:
        trace_startup("tray_init_started")
        try:
            import pystray

            image = Image.open(self.icon_png).convert("RGBA")
            menu = pystray.Menu(
                pystray.MenuItem("显示 DJYX_APITOOL", lambda _icon, _item: self.show_window(), default=True),
                pystray.MenuItem("立即刷新", lambda _icon, _item: self.request_refresh()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", lambda _icon, _item: self.exit_app()),
            )
            self.tray = pystray.Icon(APP_NAME, image, "DJYX_APITOOL", menu)
            trace_startup("tray_init_finished")
            self.tray.run()
        except Exception as exc:
            trace_startup("tray_init_failed", error=str(exc))

    def _refresh_loop(self) -> None:
        while not self.stopping.is_set():
            interval = self.foreground_interval if self.visible else self.background_interval
            self.next_refresh_at = time.time() + interval
            triggered = self.refresh_wakeup.wait(interval)
            self.refresh_wakeup.clear()
            if self.stopping.is_set():
                break
            if triggered:
                continue
            self.refresh_all(push_ui=self.visible)

    def request_refresh(self) -> None:
        threading.Thread(
            target=lambda: self.refresh_all(push_ui=self.visible),
            name="manual-refresh",
            daemon=True,
        ).start()

    def _on_minimized(self) -> None:
        self.visible = False
        self.refresh_wakeup.set()

    def _on_native_visibility_changed(self, sender: Any, _args: Any) -> None:
        self.visible = bool(sender.Visible)
        self.refresh_wakeup.set()
        if self.visible:
            threading.Timer(0.25, self.push_state_to_ui).start()

    def _on_maximized(self) -> None:
        self.visible = True
        self.maximized = True
        self._set_window_corner(True)
        self._push_window_state()
        self.refresh_wakeup.set()

    def _on_restored(self) -> None:
        was_maximized = self.maximized
        self.visible = True
        self.maximized = False
        self.refresh_wakeup.set()
        if time.monotonic() < getattr(self, "drag_restore_suppressed_until", 0.0):
            return
        if was_maximized:
            self._set_window_corner(False)
            self._push_window_state()
        self._schedule_window_size_save()

    def _on_resized(self, width: Any = None, height: Any = None) -> None:
        self._schedule_window_size_save(width, height)

    def _on_closing(self) -> bool | None:
        self._flush_window_size()
        if self.stopping.is_set():
            return None
        self._handle_close_request()
        return False

    def _handle_close_request(self, selection: str | None = None) -> str:
        self._flush_window_size()
        action = selection or self.store.get_close_action()
        if action == "exit":
            threading.Timer(0.05, self.exit_app).start()
        elif action == "tray":
            threading.Timer(0.01, self.hide_window).start()
        elif self.window:
            try:
                self.window.evaluate_js("window.openCloseActionModal();")
            except Exception:
                self.hide_window()
        return action

    def _push_update_state(self) -> None:
        if not self.window or not self.visible:
            return
        state = json.dumps(self.update_state, ensure_ascii=False)
        try:
            self.window.evaluate_js(f"window.applyUpdateState({state});")
        except Exception:
            pass

    def _set_update_state(self, **changes: Any) -> None:
        self.update_state.update(changes)
        self._push_update_state()

    @staticmethod
    def _github_json(path: str) -> Any:
        request = urllib.request.Request(
            f"{GITHUB_API_URL}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        return json.loads(get_small_url_bytes(request, timeout=10).decode("utf-8"))

    @staticmethod
    def _github_release_feed() -> list[dict[str, Any]]:
        request = urllib.request.Request(
            f"https://github.com/{GITHUB_REPOSITORY}/releases.atom",
            headers={
                "Accept": "application/atom+xml",
                "User-Agent": f"{APP_NAME}/{APP_VERSION}",
            },
        )
        releases = parse_github_release_feed(get_small_url_bytes(request, timeout=10))
        if not releases:
            raise RuntimeError("GitHub Release feed 没有可用版本")
        return releases

    @staticmethod
    def _github_api_rate_limited(error: BaseException) -> bool:
        return (
            isinstance(error, urllib.error.HTTPError)
            and error.code in {403, 429}
        ) or "rate limit" in str(error).lower()

    @staticmethod
    def _github_transport_failed(error: BaseException) -> bool:
        return isinstance(
            error,
            (NetworkTransportError, urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError),
        ) or any(
            marker in str(error).lower()
            for marker in ("unexpected_eof", "unexpected eof", "timed out", "tls", "ssl")
        )

    def check_for_updates(self, manual: Any = True) -> dict[str, Any]:
        if self.update_lock.locked():
            return {"ok": True, "update": dict(self.update_state)}
        threading.Thread(
            target=self._check_for_updates_worker,
            args=(bool(manual),),
            name="update-check",
            daemon=True,
        ).start()
        return {"ok": True, "update": dict(self.update_state)}

    def _check_for_updates_worker(self, manual: bool) -> None:
        with self.update_lock:
            self._set_update_state(
                status="checking",
                percent=8,
                message="正在检查 imagen 更新通道",
                showPrompt=manual,
            )
            try:
                try:
                    release_payload = self._github_json("/releases?per_page=20")
                except Exception as list_error:
                    self._set_update_state(
                        percent=38,
                        message="主检查通道不可用，正在尝试 imagen 备用通道",
                    )
                    release_payload = self._github_release_feed()
                releases = (
                    [item for item in release_payload if isinstance(item, dict)]
                    if isinstance(release_payload, list)
                    else [release_payload]
                    if isinstance(release_payload, dict)
                    else []
                )
                stable_releases = [
                    item
                    for item in releases
                    if not item.get("draft")
                    and not item.get("prerelease")
                    and release_matches_channel(item)
                ]
                if not stable_releases:
                    raise RuntimeError("GitHub 没有可用的 imagen 正式版本")
                release = max(
                    stable_releases,
                    key=lambda item: version_tuple(item.get("tag_name")),
                )
                self.store.set_last_update_check(time.time())
                release_tag = str(release.get("tag_name") or "")
                latest = release_version(release_tag)
                assets = {
                    str(asset.get("name")): asset
                    for asset in release.get("assets") or []
                }
                available = is_newer_version(latest) and RELEASE_ASSET_NAME in assets
                full_notes = bundled_changelog()
                pending_notes = release_notes_since(stable_releases, APP_VERSION)
                concise_notes = (
                    pending_notes
                    if available and pending_notes
                    else changelog_for_update(
                        full_notes,
                        APP_VERSION,
                        latest or APP_VERSION,
                        str(release.get("body") or ""),
                    )
                )
                complete_notes = (
                    f"{pending_notes}\n\n{full_notes}" if pending_notes else full_notes
                )
                self.update_state["release"] = {
                    "version": latest,
                    "notes": str(release.get("body") or ""),
                    "downloadSize": int(
                        (assets.get(RELEASE_ASSET_NAME) or {}).get("size") or 0
                    ),
                    "downloadApiUrl": str(
                        (assets.get(RELEASE_ASSET_NAME) or {}).get("url") or ""
                    ),
                    "downloadUrl": str(
                        (assets.get(RELEASE_ASSET_NAME) or {}).get("browser_download_url") or ""
                    ),
                    "checksumApiUrl": str(
                        (assets.get(f"{RELEASE_ASSET_NAME}.sha256") or {}).get("url") or ""
                    ),
                    "checksumUrl": str(
                        (assets.get(f"{RELEASE_ASSET_NAME}.sha256") or {}).get(
                            "browser_download_url"
                        )
                        or ""
                    ),
                }
                self._set_update_state(
                    status="available" if available else "current",
                    percent=100,
                    message=(f"发现新版本 v{latest}" if available else "当前已是最新版本"),
                    latestVersion=latest or APP_VERSION,
                    releaseNotes=concise_notes,
                    fullReleaseNotes=complete_notes,
                    available=available,
                    showPrompt=available and (
                        manual or self.store.get_ignored_update_version() != release_tag
                    ),
                )
                if manual and not available:
                    self.notify("API_TOOLS 更新", "当前已是最新版本。")
            except Exception as exc:
                message = (
                    "检查更新失败：无法连接 GitHub，请检查网络或代理后重试"
                    if self._github_transport_failed(exc)
                    else f"检查更新失败: {exc}"
                )
                self._set_update_state(
                    status="failed",
                    percent=0,
                    message=message,
                    showPrompt=manual,
                )

    def ignore_update_version(self, version: Any) -> dict[str, Any]:
        clean = self.store.set_ignored_update_version(ignored_release_key(version))
        if clean and clean == ignored_release_key(self.update_state.get("latestVersion")):
            self._set_update_state(showPrompt=False)
        return {"ok": True, "ignoredVersion": clean, "update": dict(self.update_state)}

    def dismiss_update_prompt(self) -> dict[str, Any]:
        self._set_update_state(showPrompt=False)
        return {"ok": True, "update": dict(self.update_state)}

    def download_update(self) -> dict[str, Any]:
        release = self.update_state.get("release") or {}
        if self.update_lock.locked():
            return {"ok": False, "error": "更新任务正在进行"}
        if not self.update_state.get("available") or not (
            release.get("downloadApiUrl") or release.get("downloadUrl")
        ):
            return {"ok": False, "error": "没有可下载的新版本"}
        if not getattr(sys, "frozen", False):
            return {"ok": False, "error": "开发模式不能覆盖安装，请先构建 EXE"}
        threading.Thread(
            target=self._download_update_worker,
            name="update-download",
            daemon=True,
        ).start()
        return {"ok": True}

    def _download_release_file(
        self,
        urls: list[str],
        destination: Path,
        message: str,
        expected_size: int = 0,
    ) -> Path:
        candidates = list(dict.fromkeys(url for url in urls if url))
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            raise NetworkTransportError("系统未找到 curl")
        proxy = (
            urllib.request.getproxies().get("https")
            or urllib.request.getproxies().get("http")
        )
        routes: list[tuple[str, dict[str, str], list[str]]] = []
        if proxy:
            proxy_environment = os.environ.copy()
            proxy_environment["HTTPS_PROXY"] = proxy
            proxy_environment["HTTP_PROXY"] = proxy
            proxy_environment.pop("NO_PROXY", None)
            proxy_environment.pop("no_proxy", None)
            routes.append(("系统代理", proxy_environment, []))
        direct_environment = os.environ.copy()
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            direct_environment.pop(name, None)
        direct_environment["NO_PROXY"] = "*"
        routes.append(("直连", direct_environment, ["--noproxy", "*"]))
        errors: list[str] = []
        destination.parent.mkdir(parents=True, exist_ok=True)
        for source_index, url in enumerate(candidates, start=1):
            for route, environment, route_arguments in routes:
                self._set_update_state(
                    message=(
                        f"{message} · 下载源 {source_index}/{len(candidates)} · {route}"
                    )
                )
                command = [
                    curl,
                    "--silent",
                    "--show-error",
                    "--fail",
                    "--http1.1",
                    "--location",
                    "--retry",
                    "6",
                    "--retry-all-errors",
                    "--retry-delay",
                    "1",
                    "--connect-timeout",
                    "8",
                    "--max-time",
                    "600",
                    "--speed-limit",
                    "1024",
                    "--speed-time",
                    "30",
                    "--continue-at",
                    "-",
                    "--output",
                    str(destination),
                    "--header",
                    "Accept: application/octet-stream",
                    "--header",
                    f"User-Agent: {APP_NAME}/{APP_VERSION}",
                    "--header",
                    "X-GitHub-Api-Version: 2022-11-28",
                    *route_arguments,
                    url,
                ]
                try:
                    process = subprocess.Popen(
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        env=environment,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    last_downloaded = -1
                    while process.poll() is None:
                        downloaded = destination.stat().st_size if destination.is_file() else 0
                        if downloaded != last_downloaded:
                            percent = (
                                min(99, int(downloaded * 100 / expected_size))
                                if expected_size > 0
                                else 0
                            )
                            self._set_update_state(
                                percent=percent,
                                message=f"正在下载更新 · {downloaded / 1048576:.1f} MB",
                            )
                            last_downloaded = downloaded
                        time.sleep(0.2)
                    _, stderr = process.communicate()
                except OSError as exc:
                    errors.append(f"{route}: {exc}")
                    continue
                if process.returncode == 0 and destination.is_file():
                    downloaded = destination.stat().st_size
                    self._set_update_state(
                        percent=99 if expected_size > 0 else 0,
                        message=f"正在下载更新 · {downloaded / 1048576:.1f} MB"
                    )
                    return destination
                detail = (stderr or b"").decode("utf-8", "replace").strip()
                errors.append(f"{route}: {detail or f'curl {process.returncode}'}")
        raise NetworkTransportError("；".join(errors) or "没有可用下载地址")

    def _download_text(self, urls: list[str]) -> str:
        errors: list[str] = []
        candidates = list(dict.fromkeys(url for url in urls if url))
        for index, url in enumerate(candidates, start=1):
            self._set_update_state(
                message=f"正在获取校验文件 · 下载源 {index}/{len(candidates)}"
            )
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/octet-stream",
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            try:
                return get_small_url_bytes(request, timeout=12).decode("utf-8").strip()
            except Exception as exc:
                errors.append(str(exc))
        raise NetworkTransportError("；".join(errors) or "没有可用校验地址")

    def _download_update_worker(self) -> None:
        with self.update_lock:
            release = self.update_state.get("release") or {}
            target = app_data_dir() / "updates" / f"API_TOOLS-{release.get('version')}.exe"
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".download")
            try:
                self._set_update_state(status="downloading", percent=0, message="正在连接下载源")
                download_urls = [release.get("downloadApiUrl"), release.get("downloadUrl")]
                self._download_release_file(
                    download_urls,
                    temporary,
                    "正在连接下载源",
                    int(release.get("downloadSize") or 0),
                )
                os.replace(temporary, target)
                checksum_urls = [release.get("checksumApiUrl"), release.get("checksumUrl")]
                if not any(checksum_urls):
                    raise RuntimeError("Release 缺少 SHA-256 校验文件")
                expected = self._download_text(checksum_urls).split()[0].lower()
                actual = sha256_file(target)
                if expected != actual:
                    target.unlink(missing_ok=True)
                    raise RuntimeError("下载文件 SHA-256 校验失败")
                self._set_update_state(
                    status="ready",
                    percent=100,
                    message="下载完成，点击重启以应用更新",
                    downloadedPath=str(target),
                    showPrompt=True,
                )
            except Exception as exc:
                message = (
                    "更新失败：无法连接下载服务器，请检查网络或代理后重试"
                    if self._github_transport_failed(exc)
                    else f"更新失败: {exc}"
                )
                self._set_update_state(status="failed", percent=0, message=message)

    def restart_update(self) -> dict[str, Any]:
        if self.update_lock.locked():
            return {"ok": False, "error": "更新任务正在进行"}
        downloaded = Path(str(self.update_state.get("downloadedPath") or ""))
        if self.update_state.get("status") != "ready" or not downloaded.is_file():
            return {"ok": False, "error": "没有已下载的更新"}
        self._set_update_state(message="正在重启并应用更新")
        self._launch_updater(downloaded)
        return {"ok": True}

    def defer_update_restart(self) -> dict[str, Any]:
        downloaded = Path(str(self.update_state.get("downloadedPath") or ""))
        if self.update_state.get("status") != "ready" or not downloaded.is_file():
            return {"ok": False, "error": "没有已下载的更新"}
        self.pending_update_path = downloaded
        self._set_update_state(showPrompt=False)
        return {"ok": True, "update": dict(self.update_state)}

    def _launch_updater(self, downloaded: Path) -> None:
        current = Path(sys.executable).resolve()
        script = app_data_dir() / "apply-update.ps1"
        log = app_data_dir() / "update.log"
        ready = app_data_dir() / "update.ready"
        restarted = app_data_dir() / "update-restarted.ready"
        ready.unlink(missing_ok=True)
        restarted.unlink(missing_ok=True)
        script.write_text(
            "param([int]$ProcessId,[int]$BootloaderProcessId,[string]$Source,[string]$Target,[string]$Log,[string]$Ready,[string]$Restarted)\n"
            "$ErrorActionPreference = 'Stop'\n"
            "function Wait-ForProcessExit([int]$Id) {\n"
            "  if ($Id -le 0) { return }\n"
            "  $process = Get-Process -Id $Id -ErrorAction SilentlyContinue\n"
            "  if ($process) { $process | Wait-Process -ErrorAction SilentlyContinue }\n"
            "}\n"
            "function Start-UpdatedApplication {\n"
            "  for ($launchAttempt = 1; $launchAttempt -le 2; $launchAttempt++) {\n"
            "    Remove-Item -LiteralPath $Restarted -Force -ErrorAction SilentlyContinue\n"
            "    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'\n"
            f"    $env:{RESTART_READY_ENV} = $Restarted\n"
            "    $started = Start-Process -FilePath $Target -PassThru\n"
            "    for ($check = 1; $check -le 300; $check++) {\n"
            "      if (Test-Path -LiteralPath $Restarted) { return }\n"
            "      if ($started.HasExited) { break }\n"
            "      [System.Threading.Thread]::Sleep(100)\n"
            "      $started.Refresh()\n"
            "    }\n"
            "    if (-not $started.HasExited) { throw 'Updated application startup timed out' }\n"
            "    [System.Threading.Thread]::Sleep(500)\n"
            "  }\n"
            "  throw 'Updated application failed to start'\n"
            "}\n"
            "try {\n"
            "  Set-Content -LiteralPath $Ready -Value 'ready' -Encoding ASCII\n"
            "  Wait-ForProcessExit $ProcessId\n"
            "  Wait-ForProcessExit $BootloaderProcessId\n"
            "  $updated = $false\n"
            "  for ($attempt = 1; $attempt -le 60; $attempt++) {\n"
            "    try {\n"
            "      Copy-Item -LiteralPath $Source -Destination $Target -Force\n"
            "      $updated = $true\n"
            "      break\n"
            "    } catch {\n"
            "      if ($attempt -eq 60) { throw }\n"
            "      [System.Threading.Thread]::Sleep(500)\n"
            "    }\n"
            "  }\n"
            "  if (-not $updated) { throw 'Unable to replace application executable' }\n"
            "  Remove-Item -LiteralPath $Source -Force -ErrorAction SilentlyContinue\n"
            "  Start-UpdatedApplication\n"
            "  Remove-Item -LiteralPath $Restarted -Force -ErrorAction SilentlyContinue\n"
            "  Remove-Item -LiteralPath $Log -Force -ErrorAction SilentlyContinue\n"
            "  Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force\n"
            "} catch {\n"
            "  $_ | Out-String | Set-Content -LiteralPath $Log -Encoding UTF8\n"
            "  exit 1\n"
            "}\n",
            encoding="utf-8",
        )
        bootloader_process_id = os.getppid() if getattr(sys, "frozen", False) else 0
        updater = subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", str(script),
                "-ProcessId", str(os.getpid()),
                "-BootloaderProcessId", str(bootloader_process_id),
                "-Source", str(downloaded), "-Target", str(current),
                "-Log", str(log), "-Ready", str(ready), "-Restarted", str(restarted),
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + 5
        while not ready.exists() and updater.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.exists():
            raise RuntimeError("更新程序启动失败")
        ready.unlink(missing_ok=True)
        self.exit_app()

    def refresh_all(self, push_ui: bool = False) -> dict[str, Any]:
        refreshed: list[str] = []
        failed: list[str] = []
        with self.refresh_lock:
            for record in self.store.list_key_records():
                if self._refresh_key(record["id"]):
                    refreshed.append(record["id"])
                else:
                    failed.append(record["id"])
            if push_ui:
                self.push_state_to_ui()
        return {"refreshed": refreshed, "failed": failed}

    def _refresh_key(self, key_id: str) -> bool:
        record = self.store.get_key_record(key_id)
        if record is None:
            return False
        try:
            previous_payload = self.store.latest_payload(key_id)
            payload, models = self.client.fetch(record["base_url"], self.store.get_secret(key_id))
            if models is None:
                models = list((previous_payload or {}).get("_models") or [])
            payload["_models"] = models
            payload["_models_count"] = len(models)
            changed_names = annotate_limit_changes(payload, previous_payload)
            alert_metrics = {
                "quota": "总额度",
                "5h": "5h 限额",
                "1d": "1d 限额",
                "7d": "7d 限额",
            }
            current_limits = limit_definitions(payload)
            self.store.reset_alert_metrics(
                key_id,
                {
                    alert_metrics[name]
                    for name in changed_names
                    if current_limits[name] <= 0
                },
            )
            self.store.save_snapshot(key_id, payload)
            self._notify_limit_changes(record["name"], payload, changed_names)
            self._check_alerts(key_id, record["name"], payload)
            return True
        except Exception as exc:
            self.store.set_error(key_id, str(exc))
            return False

    def _notify_limit_changes(
        self, key_name: str, payload: dict[str, Any], changed_names: set[str]
    ) -> None:
        labels = {
            "quota": "总额度上限",
            "5h": "5h 速率限制",
            "1d": "1d 速率限制",
            "7d": "7d 速率限制",
        }
        changes = payload.get("_limit_changes") or {}
        for limit_name in ("quota", "5h", "1d", "7d"):
            if limit_name not in changed_names:
                continue
            change = changes.get(limit_name) or {}
            previous = safe_float(change.get("previous"))
            current = safe_float(change.get("current"))
            previous_text = f"{previous:g} USD"
            current_text = f"{current:g} USD"
            if previous <= 0 < current:
                message = f"您的{labels[limit_name]}已新增为 {current_text}"
                severity = 1
            elif current <= 0 < previous:
                message = f"您的{labels[limit_name]}已取消，原限制为 {previous_text}"
                severity = 2
            elif current > previous:
                message = (
                    f"您的{labels[limit_name]}已从 {previous_text} 提高到 {current_text}"
                )
                severity = 0
            else:
                message = (
                    f"您的{labels[limit_name]}已从 {previous_text} 降低到 {current_text}"
                )
                severity = 2
            self.notify(f"{key_name} · 限制调整", message, severity=severity)

    def _check_alerts(self, key_id: str, name: str, payload: dict[str, Any]) -> None:
        thresholds = self.store.get_thresholds()
        metrics: dict[str, float] = {}
        quota = payload.get("quota") or {}
        if safe_float(quota.get("limit")) > 0:
            metrics["总额度"] = 100 * safe_float(quota.get("remaining")) / safe_float(quota.get("limit"))
        for item in payload.get("rate_limits") or []:
            limit = safe_float(item.get("limit"))
            if limit > 0:
                metrics[f"{item.get('window')} 限额"] = 100 * safe_float(item.get("remaining")) / limit
        for metric, percentage in metrics.items():
            if percentage <= thresholds["critical"]:
                severity = 3
            elif percentage <= thresholds["danger"]:
                severity = 2
            elif percentage <= thresholds["warn"]:
                severity = 1
            else:
                severity = 0
            previous = self.store.alert_severity(key_id, metric)
            if severity > previous:
                labels = {
                    1: f"{thresholds['warn']:g}% 预警",
                    2: f"{thresholds['danger']:g}% 危险",
                    3: f"{thresholds['critical']:g}% 严重",
                }
                self.notify(
                    f"{name} · {labels[severity]}",
                    f"{metric}仅剩 {percentage:.2f}%，请及时检查额度。",
                    severity=severity,
                )
            if severity != previous:
                self.store.set_alert_severity(key_id, metric, severity)

        rates_method = getattr(self.store, "rates", None)
        if not callable(rates_method):
            return
        rates = rates_method(key_id) or {}
        intervals = rates.get("intervals") or {}
        for interval_name, seconds in (("10m", 600), ("1h", 3600)):
            interval = intervals.get(interval_name) or {}
            metric = f"{interval_name} 负载"
            if interval.get("status") != "recorded" or interval.get("value") is None:
                continue
            load = interval_load_components(payload, safe_float(interval.get("value")))
            pressure = load["overall"]
            severity = 2 if pressure >= 85 else 1 if pressure >= 65 else 0
            previous = self.store.alert_severity(key_id, metric)
            if severity > previous:
                level = "极高负载" if severity == 2 else "高负载"
                self.notify(
                    f"{name} · {load['source']}{level}",
                    f"最近 {interval_name} 用量 ${safe_float(interval.get('value')):.4f}，综合负载 {pressure:.0f}%"
                    f"（额度 {load['quotaPercent']:.2f}% / 速率 {load['ratePercent']:.2f}%）。",
                    severity=severity,
                )
            if severity != previous:
                self.store.set_alert_severity(key_id, metric, severity)

    def notify(self, title: str, message: str, severity: int = 0) -> None:
        try:
            icon_names = {
                1: "api_tools_warn.png",
                2: "api_tools_danger.png",
                3: "api_tools_critical.png",
            }
            icon_name = icon_names.get(int(severity), "api_tools_normal.png")
            notification = Notification(
                app_id="API_TOOLS 密钥监控",
                title=title,
                msg=message,
                icon=str(resource_path(f"assets/icons/{icon_name}")),
                duration="long",
            )
            notification.set_audio(audio.Default, loop=False)
            notification.show()
        except Exception:
            pass

    def _masked_value(self, secret: str) -> str:
        if len(secret) <= 8:
            return "*" * len(secret)
        return f"{secret[:4]}...{secret[-4:]}"

    def _normalize(self, record: sqlite3.Row, payload: dict[str, Any] | None) -> dict[str, Any]:
        payload = payload or {}
        quota = payload.get("quota") or {}
        windows = {str(item.get("window")): item for item in payload.get("rate_limits") or []}
        usage = payload.get("usage") or {}
        today = usage.get("today") or {}
        total = usage.get("total") or {}
        limit = safe_float(quota.get("limit"))
        remaining = safe_float(quota.get("remaining"), safe_float(payload.get("remaining")))
        used = safe_float(quota.get("used"), max(0.0, limit - remaining))
        if limit <= 0 and payload.get("balance") is not None:
            limit = safe_float(payload.get("balance")) + safe_float(total.get("cost"))
            remaining = safe_float(payload.get("balance"))
            used = safe_float(total.get("cost"))

        def window_data(name: str) -> dict[str, Any]:
            item = windows.get(name) or {}
            change = (payload.get("_limit_changes") or {}).get(name)
            window_limit = max(0.0, safe_float(item.get("limit")))
            window_used = max(0.0, safe_float(item.get("used")))
            remaining_value = item.get("remaining")
            if remaining_value is None:
                window_remaining = max(0.0, window_limit - window_used)
            else:
                window_remaining = min(
                    window_limit,
                    max(0.0, safe_float(remaining_value)),
                )
            return {
                "limit": window_limit,
                "used": window_used,
                "remaining": window_remaining,
                "resetTime": item.get("reset_at"),
                "windowStart": item.get("window_start"),
                "limitChange": change,
            }

        expires = payload.get("expires_at") or ((payload.get("subscription") or {}).get("expires_at"))
        return {
            "id": record["id"],
            "name": record["name"],
            "value": self._masked_value(self.store.get_secret(record["id"])),
            "status": payload.get("status") or ("active" if payload.get("isValid") else "error"),
            "mode": payload.get("mode") or "unknown",
            "planName": payload.get("planName") or "",
            "expireDateStr": expires or "",
            "expireTimestamp": (parse_timestamp(expires) or 0) * 1000,
            "totalQuota": limit,
            "usedQuota": used,
            "remainingQuota": remaining,
            "quotaLimitChange": (payload.get("_limit_changes") or {}).get("quota"),
            "win5h": window_data("5h"),
            "win1d": window_data("1d"),
            "win7d": window_data("7d"),
            "todayRequests": int(today.get("requests") or 0),
            "totalRequests": int(total.get("requests") or 0),
            "todayCost": safe_float(today.get("cost")),
            "totalCost": safe_float(total.get("cost")),
            "modelsCount": int(payload.get("_models_count") or 0),
            "models": [str(model) for model in payload.get("_models") or [] if str(model).strip()],
            "rates": self.store.rates(record["id"]),
            "lastError": record["last_error"],
        }

    def get_state(self) -> dict[str, Any]:
        keys = [self._normalize(record, self.store.latest_payload(record["id"])) for record in self.store.list_key_records()]
        get_window_size = getattr(self.store, "get_window_size", None)
        window_size = (
            get_window_size()
            if callable(get_window_size)
            else normalize_window_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        )
        return {
            "keys": keys,
            "thresholds": self.store.get_thresholds(),
            "rateLimitProgressMode": self.store.get_rate_limit_progress_mode(),
            "appVersion": APP_VERSION,
            "githubRepository": GITHUB_REPOSITORY,
            "releaseBranch": RELEASE_BRANCH,
            "releaseTagPrefix": RELEASE_TAG_PREFIX,
            "updateFrequency": self.store.get_update_frequency(),
            "ignoredUpdateVersion": (
                self.store.get_ignored_update_version()
                if hasattr(self.store, "get_ignored_update_version")
                else ""
            ),
            "closeAction": self.store.get_close_action(),
            "alwaysOnTop": bool(getattr(self, "always_on_top", False)),
            "windowSize": window_size,
            "backgroundUiMode": self.store.get_background_ui_mode(),
            "titleBarMode": self.store.get_title_bar_mode(),
            "activeTitleBarMode": self.active_title_bar_mode,
            "startupEnabled": startup_is_enabled(),
            "update": dict(self.update_state),
            "refreshIntervals": {
                "foreground": self.foreground_interval,
                "background": self.background_interval,
            },
            "isForeground": self.visible,
            "nextRefreshSeconds": max(0, int(self.next_refresh_at - time.time())),
            "databasePath": str(self.store.path),
        }

    def add_key(self, name: str, value: str) -> dict[str, Any]:
        name = (name or "").strip()
        value = (value or "").strip()
        if not name or not value:
            return {"ok": False, "error": "昵称和密钥不能为空"}
        base_url = os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        key_id = self.store.add_key(name, value, base_url)
        with self.refresh_lock:
            self._refresh_key(key_id)
        record = self.store.get_key_record(key_id)
        if record and record["last_error"]:
            error = record["last_error"]
            self.store.delete_key(key_id)
            return {"ok": False, "error": error}
        return {"ok": True, "activeKeyId": key_id, "state": self.get_state()}

    @staticmethod
    def _agent_input(prompt: str, image_paths: tuple[Path, ...] = ()) -> Any:
        if not image_paths:
            return prompt
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for image_path in image_paths:
            content.append(
                {
                    "type": "input_image",
                    "image_url": image_preview_data_url(image_path.read_bytes(), max_side=1024),
                }
            )
        return [{"role": "user", "content": content}]

    def polish_prompt(
        self,
        key_id: str,
        prompt: str,
        event_callback: Any = None,
    ) -> dict[str, Any]:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            return {"ok": False, "error": "请输入需要润色的提示词"}
        record = self.store.get_key_record(str(key_id or ""))
        if record is None:
            return {"ok": False, "error": "请选择有效的 API Key"}
        secret = self.store.get_secret(record["id"])

        def emit(event_type: str, **details: Any) -> None:
            if event_callback is not None:
                event_callback({"type": event_type, **details})

        emit(
            "prompt_polish_started",
            model=PROMPT_POLISH_MODEL,
            reasoningEffort=PROMPT_POLISH_REASONING_EFFORT,
        )
        try:
            polished = self.client.stream_response(
                record["base_url"],
                secret,
                PROMPT_POLISH_MODEL,
                (
                    "你是专业视觉提示词编辑器。先根据用户输入自动判断属于艺术创作、工作创作、"
                    "图像编辑、文字润色或其他场景，再采用匹配该场景的表达方式补全主体、构图、"
                    "光线、材质、风格、约束和交付目标。保留用户意图，不虚构冲突要求。"
                    "只输出可直接用于图片生成的最终提示词，不要解释判断过程，不要添加标题。"
                ),
                clean_prompt,
                on_delta=lambda delta: emit("prompt_polish_delta", delta=delta),
                reasoning_effort=PROMPT_POLISH_REASONING_EFFORT,
            ).strip()
        except RuntimeError as exc:
            emit("prompt_polish_failed", error=str(exc))
            return {"ok": False, "error": str(exc)}
        if not polished:
            return {"ok": False, "error": "润色模型未返回提示词"}
        emit(
            "prompt_polish_completed",
            prompt=polished,
            model=PROMPT_POLISH_MODEL,
            reasoningEffort=PROMPT_POLISH_REASONING_EFFORT,
        )
        return {
            "ok": True,
            "prompt": polished,
            "model": PROMPT_POLISH_MODEL,
            "reasoningEffort": PROMPT_POLISH_REASONING_EFFORT,
        }

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

    def delete_key(self, key_id: str) -> dict[str, Any]:
        self.store.delete_key(key_id)
        return {"ok": True, "state": self.get_state()}

    def refresh_now(self, trace_id: Any = None) -> dict[str, Any]:
        trace_id = str(trace_id or "").strip()[:80] or "backend-refresh"
        debug_started_at = time.perf_counter()
        debug_events: list[dict[str, Any]] = []

        def mark_debug(event: str, **details: Any) -> None:
            debug_events.append({
                "event": event,
                "elapsedMs": round((time.perf_counter() - debug_started_at) * 1000, 1),
                **details,
            })

        def with_debug(payload: dict[str, Any], outcome: str) -> dict[str, Any]:
            mark_debug(
                "response",
                outcome=outcome,
                manualLockLocked=self.manual_refresh_lock.locked(),
                refreshLockLocked=self.refresh_lock.locked(),
            )
            payload["debug"] = {
                "traceId": trace_id,
                "outcome": outcome,
                "durationMs": round((time.perf_counter() - debug_started_at) * 1000, 1),
                "events": debug_events,
            }
            return payload

        now = time.monotonic()
        cooldown = max(0.0, self.manual_refresh_available_at - now)
        mark_debug(
            "received",
            cooldownSeconds=round(cooldown, 3),
            manualLockLocked=self.manual_refresh_lock.locked(),
            refreshLockLocked=self.refresh_lock.locked(),
        )
        if cooldown > 0:
            return with_debug({
                "ok": False,
                "busy": False,
                "cooldownSeconds": cooldown,
                "error": "手动刷新冷却中",
            }, "cooldown")
        if not self.manual_refresh_lock.acquire(blocking=False):
            return with_debug({
                "ok": False,
                "busy": True,
                "cooldownSeconds": cooldown,
                "error": "手动刷新正在进行",
            }, "manual-lock-busy")
        mark_debug("manual-lock-acquired")
        try:
            if not self.refresh_lock.acquire(blocking=False):
                return with_debug({
                    "ok": False,
                    "busy": True,
                    "cooldownSeconds": 0,
                    "error": "后台刷新正在进行",
                }, "background-lock-busy")
            mark_debug("refresh-lock-acquired")
            self.manual_refresh_available_at = (
                now + MANUAL_REFRESH_COOLDOWN_SECONDS
            )
            try:
                refreshed: list[str] = []
                failed: list[str] = []
                records = self.store.list_key_records()
                mark_debug("keys-loaded", count=len(records))
                for index, record in enumerate(records, start=1):
                    mark_debug("key-refresh-started", index=index, total=len(records))
                    succeeded = self._refresh_key(record["id"])
                    mark_debug(
                        "key-refresh-finished",
                        index=index,
                        total=len(records),
                        succeeded=succeeded,
                    )
                    if succeeded:
                        refreshed.append(record["id"])
                    else:
                        failed.append(record["id"])
            finally:
                self.refresh_lock.release()
                mark_debug("refresh-lock-released")
            valid = bool(refreshed) and not failed
            return with_debug({
                "ok": valid,
                "valid": valid,
                "refreshed": refreshed,
                "failed": failed,
                "cooldownSeconds": max(
                    0.0, self.manual_refresh_available_at - time.monotonic()
                ),
                "state": self.get_state(),
                "error": "" if valid else "未获取到全部密钥的有效回复",
            }, "success" if valid else "invalid-response")
        finally:
            self.manual_refresh_lock.release()
            mark_debug("manual-lock-released")

    def update_thresholds(self, thresholds: dict[str, Any]) -> dict[str, Any]:
        clean = self.store.set_thresholds(thresholds)
        self.store.reset_limit_alerts()
        return {"ok": True, "thresholds": clean}

    def update_rate_limit_progress_mode(self, mode: Any) -> dict[str, Any]:
        clean = self.store.set_rate_limit_progress_mode(mode)
        return {"ok": True, "rateLimitProgressMode": clean}

    def update_refresh_intervals(
        self, foreground: Any, background: Any
    ) -> dict[str, Any]:
        intervals = self.store.set_refresh_intervals(foreground, background)
        self.foreground_interval = intervals["foreground"]
        self.background_interval = intervals["background"]
        self.next_refresh_at = time.time() + (
            self.foreground_interval if self.visible else self.background_interval
        )
        self.refresh_wakeup.set()
        return {"ok": True, "refreshIntervals": intervals, "state": self.get_state()}

    def update_app_preferences(
        self,
        update_frequency: Any,
        close_action: Any,
        startup_enabled: Any,
        title_bar_mode: Any = None,
        background_ui_mode: Any = None,
    ) -> dict[str, Any]:
        frequency = self.store.set_update_frequency(update_frequency)
        action = self.store.set_close_action(close_action)
        title_bar = (
            self.store.get_title_bar_mode()
            if title_bar_mode is None
            else self.store.set_title_bar_mode(title_bar_mode)
        )
        background_mode = (
            self.store.get_background_ui_mode()
            if background_ui_mode is None
            else self.store.set_background_ui_mode(background_ui_mode)
        )
        startup = set_startup_enabled(bool(startup_enabled))
        return {
            "ok": True,
            "updateFrequency": frequency,
            "closeAction": action,
            "backgroundUiMode": background_mode,
            "titleBarMode": title_bar,
            "startupEnabled": startup,
            "state": self.get_state(),
        }

    def restart_app(self) -> dict[str, Any]:
        self._flush_window_size()
        script = app_data_dir() / "restart-app.ps1"
        ready = app_data_dir() / "restart.ready"
        ready.unlink(missing_ok=True)
        source_script = "" if getattr(sys, "frozen", False) else str(Path(__file__).resolve())
        script.write_text(
            "param([int]$ProcessId,[string]$Executable,[string]$SourceScript,[string]$Ready)\n"
            "$ErrorActionPreference = 'Stop'\n"
            "Set-Content -LiteralPath $Ready -Value 'ready' -Encoding ASCII\n"
            "$process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue\n"
            "if ($process) { $process | Wait-Process -ErrorAction SilentlyContinue }\n"
            "$env:PYINSTALLER_RESET_ENVIRONMENT = '1'\n"
            "if ($SourceScript) { Start-Process -FilePath $Executable -ArgumentList @($SourceScript) }\n"
            "else { Start-Process -FilePath $Executable }\n"
            "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force\n",
            encoding="utf-8",
        )
        restarter = subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", str(script),
                "-ProcessId", str(os.getpid()), "-Executable", str(Path(sys.executable).resolve()),
                "-SourceScript", source_script, "-Ready", str(ready),
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + 5
        while not ready.exists() and restarter.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.exists():
            raise RuntimeError("重启程序启动失败")
        ready.unlink(missing_ok=True)
        self.stopping.set()
        self.refresh_wakeup.set()
        if self.tray:
            self.tray.stop()
        if self.window:
            self.window.destroy()
        return {"ok": True}

    def window_action(self, action: str) -> dict[str, Any]:
        if not self.window:
            return {"ok": False}
        if action == "minimize":
            self.visible = False
            hwnd = self._window_handle()
            if hwnd:
                user32.PostMessageW(hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)
            else:
                threading.Thread(target=self.window.minimize, name="window-minimize", daemon=True).start()
        elif action == "maximize":
            hwnd = self._window_handle()
            if hwnd and user32.IsZoomed(hwnd):
                self.window.restore()
            else:
                self.window.maximize()
            hwnd = self._window_handle()
            self.maximized = bool(hwnd and user32.IsZoomed(hwnd))
            self._set_window_corner(self.maximized)
            self._push_window_state()
            self.visible = True
        elif action == "close":
            selected = self._handle_close_request()
            return {"ok": True, "action": selected}
        self.refresh_wakeup.set()
        return {"ok": True, "visible": self.visible, "maximized": self.maximized}

    def hide_window(self) -> None:
        self.visible = False
        self.refresh_wakeup.set()
        ui_hide_callback = getattr(self, "ui_hide_callback", None)
        if ui_hide_callback:
            ui_hide_callback()
            return
        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is not None:
            try:
                from System import Action

                native_form.BeginInvoke(Action(native_form.Hide))
                return
            except Exception:
                pass
        if self.window:
            threading.Thread(target=self.window.hide, name="window-hide", daemon=True).start()

    def show_window(self) -> None:
        self.visible = True
        self.refresh_wakeup.set()
        ui_show_callback = getattr(self, "ui_show_callback", None)
        if ui_show_callback:
            ui_show_callback()
            return

        def show_native_window() -> None:
            if not self.window:
                return
            native_form = getattr(self.window, "native", None)
            if native_form is not None:
                native_form.Show()
                native_form.Activate()
            else:
                self.window.show()
            hwnd = self._window_handle()
            if hwnd and user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)

        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is not None:
            try:
                from System import Action

                native_form.BeginInvoke(Action(show_native_window))
            except Exception:
                threading.Thread(target=show_native_window, name="window-show", daemon=True).start()
        else:
            threading.Thread(target=show_native_window, name="window-show", daemon=True).start()

    def set_window_background(self, mode: Any) -> dict[str, Any]:
        color_hex = "#020617" if str(mode).lower() == "dark" else "#ffffff"
        native_form = getattr(self.window, "native", None) if self.window else None
        if native_form is None:
            return {"ok": False}

        def apply_background() -> None:
            from System.Drawing import Color, ColorTranslator

            native_form.BackColor = ColorTranslator.FromHtml(color_hex)
            native_webview = getattr(native_form, "webview", None)
            if native_webview is not None:
                native_webview.DefaultBackgroundColor = Color.FromArgb(
                    255,
                    int(color_hex[1:3], 16),
                    int(color_hex[3:5], 16),
                    int(color_hex[5:7], 16),
                )

        try:
            from System import Action

            native_form.BeginInvoke(Action(apply_background))
            return {"ok": True, "color": color_hex}
        except Exception:
            return {"ok": False}

    def push_state_to_ui(self) -> None:
        if not self.window or not self.visible:
            return
        state = json.dumps(self.get_state(), ensure_ascii=False)
        try:
            self.window.evaluate_js(f"window.applyBackendState({state});")
        except Exception:
            pass

    def exit_app(self) -> None:
        self._flush_window_size()
        pending_update = getattr(self, "pending_update_path", None)
        if pending_update and Path(pending_update).is_file():
            self.pending_update_path = None
            self._launch_updater(Path(pending_update))
            return
        self.stopping.set()
        self.refresh_wakeup.set()
        if self.tray:
            self.tray.stop()
        if self.window:
            self.window.destroy()

    def resolve_close_action(self, action: Any) -> dict[str, Any]:
        clean = str(action).lower()
        if clean not in {"exit", "tray"}:
            return {"ok": False, "error": "无效的关闭操作"}
        self._handle_close_request(clean)
        return {"ok": True, "action": clean}


class WebApi:
    def __init__(self, controller: AppController) -> None:
        self._controller = controller

    def get_state(self) -> dict[str, Any]:
        return self._controller.get_state()

    def initialize_assets(self, retry: Any = False) -> dict[str, Any]:
        return self._controller.initialize_assets(retry)

    def get_asset_status(self) -> dict[str, Any]:
        return self._controller.get_asset_status()

    def complete_initialization(self) -> dict[str, Any]:
        return self._controller.complete_initialization()

    def choose_edit_images(self) -> dict[str, Any]:
        return self._controller.choose_edit_images()

    def import_reference_image(self, data_url: str, name: str = "") -> dict[str, Any]:
        return self._controller.import_reference_image(data_url, name)

    def load_generated_image(self, source_path: str) -> dict[str, Any]:
        return self._controller.load_generated_image(source_path)

    def copy_generated_image(self, source_path: str) -> dict[str, Any]:
        return self._controller.copy_generated_image(source_path)

    def open_generated_pictures(self) -> dict[str, Any]:
        return self._controller.open_generated_pictures()

    def add_key(self, name: str, value: str) -> dict[str, Any]:
        return self._controller.add_key(name, value)

    def delete_key(self, key_id: str) -> dict[str, Any]:
        return self._controller.delete_key(key_id)

    def delete_image_set(self, session_id: str, set_id: str) -> dict[str, Any]:
        return self._controller.delete_image_set(session_id, set_id)

    def list_image_sets(self) -> dict[str, Any]:
        return self._controller.list_image_sets()

    def append_image_stream_debug(self, records: Any) -> dict[str, Any]:
        return self._controller.append_image_stream_debug(records)

    def generate_image(
        self,
        key_id: str,
        prompt: str,
        image_paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._controller.generate_image(key_id, prompt, image_paths, options)

    def polish_prompt(self, key_id: str, prompt: str) -> dict[str, Any]:
        return self._controller.polish_prompt(key_id, prompt)

    def refresh_now(self, trace_id: Any = None) -> dict[str, Any]:
        return self._controller.refresh_now(trace_id)

    def update_thresholds(self, thresholds: dict[str, Any]) -> dict[str, Any]:
        return self._controller.update_thresholds(thresholds)

    def update_rate_limit_progress_mode(self, mode: Any) -> dict[str, Any]:
        return self._controller.update_rate_limit_progress_mode(mode)

    def update_refresh_intervals(self, foreground: Any, background: Any) -> dict[str, Any]:
        return self._controller.update_refresh_intervals(foreground, background)

    def update_app_preferences(
        self,
        update_frequency: Any,
        close_action: Any,
        startup_enabled: Any,
        title_bar_mode: Any = None,
        background_ui_mode: Any = None,
    ) -> dict[str, Any]:
        return self._controller.update_app_preferences(
            update_frequency,
            close_action,
            startup_enabled,
            title_bar_mode,
            background_ui_mode,
        )

    def restart_app(self) -> dict[str, Any]:
        return self._controller.restart_app()

    def check_for_updates(self) -> dict[str, Any]:
        return self._controller.check_for_updates(manual=True)

    def download_update(self) -> dict[str, Any]:
        return self._controller.download_update()

    def defer_update_restart(self) -> dict[str, Any]:
        return self._controller.defer_update_restart()

    def restart_update(self) -> dict[str, Any]:
        return self._controller.restart_update()

    def dismiss_update_prompt(self) -> dict[str, Any]:
        return self._controller.dismiss_update_prompt()

    def ignore_update_version(self, version: Any) -> dict[str, Any]:
        return self._controller.ignore_update_version(version)

    def resolve_close_action(self, action: Any) -> dict[str, Any]:
        return self._controller.resolve_close_action(action)

    def window_action(self, action: str) -> dict[str, Any]:
        return self._controller.window_action(action)

    def set_always_on_top(self, enabled: Any) -> dict[str, Any]:
        return self._controller.set_always_on_top(enabled)

    def set_window_size(self, width: Any, height: Any) -> dict[str, Any]:
        return self._controller.set_window_size(width, height)

    def set_window_background(self, mode: Any) -> dict[str, Any]:
        return self._controller.set_window_background(mode)

    def native_drag(self, direction: str) -> dict[str, Any]:
        return self._controller.native_drag(direction)

    def open_devtools(self) -> dict[str, Any]:
        return self._controller.open_devtools()

    def report_startup(self, stage: str, navigation_ms: Any = 0) -> dict[str, Any]:
        return self._controller.report_startup(stage, navigation_ms)

    def save_edited_image(self, source_path: str) -> dict[str, Any]:
        return self._controller.save_edited_image(source_path)


class BackgroundApp:
    def __init__(self, asset_cache: StaticAssetCache | None = None) -> None:
        self.rpc_address = rf"\\.\pipe\API_TOOLS_{os.getpid()}_{uuid.uuid4().hex}"
        self.rpc_authkey = os.urandom(32)
        self.ui_process: multiprocessing.Process | None = None
        self.ui_lock = threading.Lock()
        self.controller = AppController(
            asset_cache,
            ui_show_callback=self.show_ui,
            ui_hide_callback=self.hide_ui,
        )
        self.rpc_server = ControllerRpcServer(
            self.controller, self.rpc_address, self.rpc_authkey
        )
        self.show_event_handle = kernel32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
        if not self.show_event_handle:
            raise ctypes.WinError()

    def start(self) -> None:
        self.rpc_server.start()
        self.controller.start_workers()
        threading.Thread(target=self._show_event_loop, name="show-window-event", daemon=True).start()
        self.show_ui()

    def _show_event_loop(self) -> None:
        while not self.controller.stopping.is_set():
            result = kernel32.WaitForSingleObject(self.show_event_handle, 500)
            if result == WAIT_OBJECT_0:
                self.show_ui()
            elif result != WAIT_TIMEOUT:
                break

    def _watch_ui_process(self, process: multiprocessing.Process) -> None:
        process.join()
        with self.ui_lock:
            if self.ui_process is process:
                self.ui_process = None
                self.hide_ui()
        trace_startup("ui_process_exited", exitCode=process.exitcode)

    def show_ui(self) -> None:
        with self.ui_lock:
            process = self.ui_process
            if process and process.is_alive():
                activate_ui_window()
                self.controller.set_ui_visible(True)
                return
            process = multiprocessing.Process(
                target=run_ui_process,
                args=(self.rpc_address, self.rpc_authkey),
                name="API_TOOLS_UI",
            )
            process.start()
            self.ui_process = process
            self.controller.set_ui_visible(True)
            threading.Thread(
                target=self._watch_ui_process,
                args=(process,),
                name="ui-process-monitor",
                daemon=True,
            ).start()
            trace_startup("ui_process_started", pid=process.pid)

    def hide_ui(self) -> None:
        self.controller.set_ui_visible(False)

    def stop(self) -> None:
        self.controller.stopping.set()
        self.controller.refresh_wakeup.set()
        self.rpc_server.stop()
        if self.controller.tray:
            self.controller.tray.stop()
        with self.ui_lock:
            process = self.ui_process
            if process and process.is_alive():
                process.terminate()
                process.join(timeout=3)
        if self.show_event_handle:
            kernel32.CloseHandle(self.show_event_handle)
            self.show_event_handle = None


class UiController(AppController):
    def __init__(self, rpc_client: ControllerRpcClient, asset_cache: StaticAssetCache) -> None:
        super().__init__(asset_cache)
        self.rpc_client = rpc_client
        self.release_timer: threading.Timer | None = None

    def _destroy_ui(self) -> None:
        self.stopping.set()
        if self.window:
            threading.Timer(0.01, self.window.destroy).start()

    def _release_if_still_hidden(self, visibility_token: int) -> None:
        try:
            result = self.rpc_client.call("claim_ui_release", visibility_token)
        except Exception:
            result = {"release": True}
        if result.get("release"):
            self._destroy_ui()

    def hide_window(self) -> None:
        AppController.hide_window(self)
        try:
            state = self.rpc_client.call("notify_ui_hidden")
        except Exception:
            self._destroy_ui()
            return
        mode = normalize_background_ui_mode(state.get("backgroundUiMode"))
        if mode == "active":
            return
        if mode == "low_power":
            self._destroy_ui()
            return
        visibility_token = int(state.get("visibilityToken") or -1)
        self.release_timer = threading.Timer(
            BACKGROUND_UI_RELEASE_DELAY,
            self._release_if_still_hidden,
            args=(visibility_token,),
        )
        self.release_timer.daemon = True
        self.release_timer.start()

    def exit_app(self) -> None:
        if self.release_timer:
            self.release_timer.cancel()
        self._flush_window_size()
        try:
            self.rpc_client.call("exit_app")
        finally:
            self.stopping.set()
            if self.window:
                self.window.destroy()

    def push_image_generation_event(self, event: dict[str, Any]) -> None:
        if not self.window:
            return
        payload = json.dumps(event, ensure_ascii=False)
        try:
            self.window.evaluate_js(f"window.applyImageGenerationEvent({payload});")
        except Exception:
            pass

    def restart_app(self) -> dict[str, Any]:
        self._flush_window_size()
        return self.rpc_client.call("restart_app")

    def restart_update(self) -> dict[str, Any]:
        return self.rpc_client.call("restart_update")

    def complete_initialization(self) -> dict[str, Any]:
        if not self.asset_cache.is_ready() or not self.window:
            return {"ok": False, "error": "静态资源缓存尚未就绪"}
        url = self.asset_cache.main_page.as_uri()
        self.window.load_url(url)
        return {"ok": True, "url": url}

    def set_always_on_top(self, enabled: Any) -> dict[str, Any]:
        clean = bool(enabled)
        hwnd = self._window_handle()
        if not hwnd:
            return {"ok": False, "error": "应用窗口尚未就绪"}
        insert_after = HWND_TOPMOST if clean else HWND_NOTOPMOST
        applied = user32.SetWindowPos(
            hwnd,
            insert_after,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
        if not applied:
            return {"ok": False, "error": "无法修改窗口置顶状态"}
        result = self.rpc_client.call("set_always_on_top", clean)
        self.always_on_top = bool(result.get("alwaysOnTop"))
        return {"ok": True, "alwaysOnTop": self.always_on_top}

    def set_window_size(self, width: Any, height: Any) -> dict[str, Any]:
        result = self.rpc_client.call("set_window_size", width, height)
        window_size = result.get("windowSize") if isinstance(result, dict) else None
        if isinstance(window_size, dict):
            clean = normalize_window_size(
                window_size.get("width"), window_size.get("height")
            )
            self._last_saved_window_size = (clean["width"], clean["height"])
        return result


class RemoteWebApi(WebApi):
    def __init__(self, controller: UiController, rpc_client: ControllerRpcClient) -> None:
        super().__init__(controller)
        self._rpc_client = rpc_client

    def _remote(self, method: str, *arguments: Any) -> Any:
        return self._rpc_client.call(method, *arguments)

    def get_state(self) -> dict[str, Any]:
        state = self._remote("get_state")
        state["isForeground"] = True
        return state

    def initialize_assets(self, retry: Any = False) -> dict[str, Any]:
        return self._remote("initialize_assets", retry)

    def get_asset_status(self) -> dict[str, Any]:
        return self._remote("get_asset_status")

    def open_generated_pictures(self) -> dict[str, Any]:
        return self._remote("open_generated_pictures")

    def load_generated_image(self, source_path: str) -> dict[str, Any]:
        return self._remote("load_generated_image", source_path)

    def add_key(self, name: str, value: str) -> dict[str, Any]:
        return self._remote("add_key", name, value)

    def delete_key(self, key_id: str) -> dict[str, Any]:
        return self._remote("delete_key", key_id)

    def delete_image_set(self, session_id: str, set_id: str) -> dict[str, Any]:
        return self._remote("delete_image_set", session_id, set_id)

    def list_image_sets(self) -> dict[str, Any]:
        return self._remote("list_image_sets")

    def append_image_stream_debug(self, records: Any) -> dict[str, Any]:
        return self._remote("append_image_stream_debug", records)

    def generate_image(
        self,
        key_id: str,
        prompt: str,
        image_paths: list[str],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._rpc_client.call_with_events(
            "generate_image",
            key_id,
            prompt,
            image_paths,
            options,
            on_event=self._controller.push_image_generation_event,
        )

    def polish_prompt(self, key_id: str, prompt: str) -> dict[str, Any]:
        return self._rpc_client.call_with_events(
            "polish_prompt",
            key_id,
            prompt,
            on_event=self._controller.push_image_generation_event,
        )

    def refresh_now(self, trace_id: Any = None) -> dict[str, Any]:
        return self._remote("refresh_now", trace_id)

    def update_thresholds(self, thresholds: dict[str, Any]) -> dict[str, Any]:
        return self._remote("update_thresholds", thresholds)

    def update_rate_limit_progress_mode(self, mode: Any) -> dict[str, Any]:
        return self._remote("update_rate_limit_progress_mode", mode)

    def update_refresh_intervals(self, foreground: Any, background: Any) -> dict[str, Any]:
        return self._remote("update_refresh_intervals", foreground, background)

    def update_app_preferences(
        self,
        update_frequency: Any,
        close_action: Any,
        startup_enabled: Any,
        title_bar_mode: Any = None,
        background_ui_mode: Any = None,
    ) -> dict[str, Any]:
        return self._remote(
            "update_app_preferences",
            update_frequency,
            close_action,
            startup_enabled,
            title_bar_mode,
            background_ui_mode,
        )

    def check_for_updates(self) -> dict[str, Any]:
        return self._remote("check_for_updates")

    def download_update(self) -> dict[str, Any]:
        return self._remote("download_update")

    def defer_update_restart(self) -> dict[str, Any]:
        return self._remote("defer_update_restart")

    def dismiss_update_prompt(self) -> dict[str, Any]:
        return self._remote("dismiss_update_prompt")

    def ignore_update_version(self, version: Any) -> dict[str, Any]:
        return self._remote("ignore_update_version", version)

    def report_startup(self, stage: str, navigation_ms: Any = 0) -> dict[str, Any]:
        return self._remote("report_startup", stage, navigation_ms)


def run_ui_process(rpc_address: str, rpc_authkey: bytes) -> None:
    rpc_client = ControllerRpcClient(rpc_address, rpc_authkey)
    asset_cache = StaticAssetCache()
    state = rpc_client.call("get_state")
    title_bar_mode = state.get("titleBarMode") or "default"
    frame_options = window_frame_options(title_bar_mode)
    minimum_size = window_min_size(title_bar_mode)
    saved_window_size = state.get("windowSize")
    window_size = normalize_window_size(
        saved_window_size.get("width") if isinstance(saved_window_size, dict) else None,
        saved_window_size.get("height") if isinstance(saved_window_size, dict) else None,
    )
    window_size["width"] = max(minimum_size[0], window_size["width"])
    window_size["height"] = max(minimum_size[1], window_size["height"])
    initial_page = (
        asset_cache.main_page if asset_cache.is_ready() else resource_path("initialize.html")
    )
    controller = UiController(rpc_client, asset_cache)
    controller.active_title_bar_mode = normalize_title_bar_mode(title_bar_mode)
    window = webview.create_window(
        WINDOW_TITLE,
        url=str(initial_page),
        js_api=RemoteWebApi(controller, rpc_client),
        width=window_size["width"],
        height=window_size["height"],
        min_size=minimum_size,
        resizable=True,
        frameless=frame_options["frameless"],
        easy_drag=frame_options["easy_drag"],
        shadow=True,
        on_top=bool(state.get("alwaysOnTop")),
        background_color="#0f172a",
    )
    if window is None:
        return
    controller.bind_window(window)
    webview.start(gui="edgechromium", icon=str(resource_path("assets/api_tools_icon.ico")))


def main() -> None:
    global startup_trace
    startup_trace = StartupTrace(app_data_dir() / "startup.log")
    trace_startup(
        "python_ready",
        frozen=bool(getattr(sys, "frozen", False)),
        bundlePath=str(getattr(sys, "_MEIPASS", "source")),
    )
    mutex_handle = acquire_single_instance()
    if mutex_handle is None:
        trace_startup("existing_instance_activated")
        return
    background_app = BackgroundApp(StaticAssetCache())
    try:
        background_app.start()
        background_app.controller.stopping.wait()
    finally:
        background_app.stop()
        kernel32.CloseHandle(mutex_handle)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
