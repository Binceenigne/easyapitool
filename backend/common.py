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
from .image_editor import (
    ImageGenerationService,
    ImageSessionStore,
    image_preview_data_url,
    prepare_image_generation,
)
from PIL import Image
from winotify import Notification, audio

APP_NAME = "DJYX_APITOOL"
WINDOW_TITLE = "DJYX_APITOOL"
APP_VERSION = "1.1.0"
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
STATIC_UI_VERSION = "50"
IMAGE_STREAM_DEBUG_LOG_MAX_BYTES = 20 * 1024 * 1024
WEB_SEARCH_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
WEB_REFERENCE_IMAGE_MAX_BYTES = 16 * 1024 * 1024
WEB_REFERENCE_MAX_COUNT = 6
MAIN_PAGE_NAME = "frontend/index.html"
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



PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRY_SCRIPT = PROJECT_ROOT / "app.py"
FRONTEND_RUNTIME_FILES = (
    MAIN_PAGE_NAME,
    "frontend/styles/app.css",
    "frontend/styles/legacy.css",
    "frontend/assets/title_logo.png",
    "frontend/scripts/modules/00-core-ui.js",
    "frontend/scripts/modules/01-app-state.js",
    "frontend/scripts/modules/02-image-controls.js",
    "frontend/scripts/modules/03-image-reasoning.js",
    "frontend/scripts/modules/04-image-stream-rendering.js",
    "frontend/scripts/modules/05-image-events-sessions.js",
    "frontend/scripts/modules/06-backend-bridge.js",
    "frontend/scripts/modules/07-page-initializer.js",
    "frontend/scripts/modules/08-window-settings-updates.js",
    "frontend/scripts/modules/09-modals-input-refresh.js",
    "frontend/scripts/modules/10-dashboard-rendering.js",
    "frontend/scripts/modules/11-theme-bootstrap.js",
    "frontend/scripts/modules/12-bootstrap.js",
)
