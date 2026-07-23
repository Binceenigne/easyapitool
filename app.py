from __future__ import annotations

import base64
import ctypes
import hashlib
from html.parser import HTMLParser
import io
import json
import math
import multiprocessing
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
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
from PIL import Image
from winotify import Notification, audio

APP_NAME = "DJYX_APITOOL"
WINDOW_TITLE = "DJYX_APITOOL"
APP_VERSION = "1.0.14"
TITLE_BAR_MODES = {"default", "minimal", "original"}
BACKGROUND_UI_MODES = {"delayed", "active", "low_power"}
GITHUB_REPOSITORY = os.environ.get(
    "API_TOOLS_GITHUB_REPOSITORY", "Binceenigne/easyapitool"
)
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
RELEASE_ASSET_NAME = "API_TOOLS.exe"
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
STATIC_UI_VERSION = "30"
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
        version = str(release.get("tag_name") or "").strip().lstrip("v")
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
        if not __import__("re").fullmatch(r"v?\d+(?:\.\d+)+", tag_name):
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
                        os.replace(self.release_dir, quarantine)
                        old_moved = True
                    os.replace(staging, self.release_dir)
                    new_moved = True
                    if not self._validate_release(self.release_dir):
                        raise RuntimeError("静态资源缓存启用失败")
                except Exception:
                    if new_moved and self.release_dir.exists():
                        failed_release = self.releases_root / f".{self.release_id}.{uuid.uuid4().hex}.failed"
                        os.replace(self.release_dir, failed_release)
                    if old_moved and quarantine and quarantine.exists() and not self.release_dir.exists():
                        os.replace(quarantine, self.release_dir)
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
    "check_for_updates",
    "delete_key",
    "defer_update_restart",
    "dismiss_update_prompt",
    "download_update",
    "exit_app",
    "get_asset_status",
    "get_state",
    "ignore_update_version",
    "initialize_assets",
    "refresh_now",
    "report_startup",
    "restart_app",
    "restart_update",
    "set_always_on_top",
    "claim_ui_release",
    "notify_ui_hidden",
    "set_ui_visible",
    "update_app_preferences",
    "update_refresh_intervals",
    "update_rate_limit_progress_mode",
    "update_thresholds",
}


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
        self.store.import_environment_key()
        self.window: webview.Window | None = None
        self.tray: Any = None
        self.ui_show_callback = ui_show_callback
        self.ui_hide_callback = ui_hide_callback
        self.visible = True
        self.ui_visibility_token = 0
        self.always_on_top = self.store.get_always_on_top()
        self.maximized = False
        self.drag_restore_suppressed_until = 0.0
        self.stopping = threading.Event()
        self.frontend_ready = threading.Event()
        self.refresh_wakeup = threading.Event()
        self.refresh_lock = threading.Lock()
        self.manual_refresh_lock = threading.Lock()
        self.manual_refresh_available_at = 0.0
        self.update_lock = threading.Lock()
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
        window.events.closing += self._on_closing

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
        if self.asset_cache.is_ready():
            self.frontend_ready.set()

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

    def _on_closing(self) -> bool | None:
        if self.stopping.is_set():
            return None
        self._handle_close_request()
        return False

    def _handle_close_request(self, selection: str | None = None) -> str:
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
        with open_url_with_direct_fallback(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _github_release_feed() -> list[dict[str, Any]]:
        request = urllib.request.Request(
            f"https://github.com/{GITHUB_REPOSITORY}/releases.atom",
            headers={
                "Accept": "application/atom+xml",
                "User-Agent": f"{APP_NAME}/{APP_VERSION}",
            },
        )
        with open_url_with_direct_fallback(request, timeout=25) as response:
            releases = parse_github_release_feed(response.read())
        if not releases:
            raise RuntimeError("GitHub Release feed 没有可用版本")
        return releases

    @staticmethod
    def _github_api_rate_limited(error: BaseException) -> bool:
        return (
            isinstance(error, urllib.error.HTTPError)
            and error.code in {403, 429}
        ) or "rate limit" in str(error).lower()

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
                message="正在检查 GitHub Release",
                showPrompt=manual,
            )
            try:
                try:
                    release_payload = self._github_json("/releases?per_page=20")
                except Exception as list_error:
                    if self._github_api_rate_limited(list_error):
                        release_payload = self._github_release_feed()
                    else:
                        try:
                            release_payload = self._github_json("/releases/latest")
                        except Exception:
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
                    if not item.get("draft") and not item.get("prerelease")
                ]
                if not stable_releases:
                    raise RuntimeError("GitHub 没有可用的正式版本")
                release = max(
                    stable_releases,
                    key=lambda item: version_tuple(item.get("tag_name")),
                )
                self.store.set_last_update_check(time.time())
                latest = str(release.get("tag_name") or "").lstrip("v")
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
                        manual or self.store.get_ignored_update_version() != latest
                    ),
                )
                if manual and not available:
                    self.notify("API_TOOLS 更新", "当前已是最新版本。")
            except Exception as exc:
                self._set_update_state(
                    status="failed",
                    percent=0,
                    message=f"检查更新失败: {exc}",
                    showPrompt=manual,
                )

    def ignore_update_version(self, version: Any) -> dict[str, Any]:
        clean = self.store.set_ignored_update_version(version)
        if clean and clean == str(self.update_state.get("latestVersion") or ""):
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

    def _open_release_asset(
        self, urls: list[str], message: str, timeout: int = 20
    ) -> Any:
        candidates = list(dict.fromkeys(url for url in urls if url))
        errors: list[str] = []
        for index, url in enumerate(candidates, start=1):
            self._set_update_state(message=f"{message} · 下载源 {index}/{len(candidates)}")
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/octet-stream",
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            try:
                return open_url_with_direct_fallback(request, timeout=timeout)
            except Exception as exc:
                errors.append(str(exc))
        detail = errors[-1] if errors else "没有可用下载地址"
        raise RuntimeError(f"{message}失败: {detail}")

    def _download_text(self, urls: list[str]) -> str:
        with self._open_release_asset(urls, "正在获取校验文件") as response:
            return response.read().decode("utf-8").strip()

    def _download_update_worker(self) -> None:
        with self.update_lock:
            release = self.update_state.get("release") or {}
            target = app_data_dir() / "updates" / f"API_TOOLS-{release.get('version')}.exe"
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".download")
            try:
                self._set_update_state(status="downloading", percent=0, message="正在连接下载源")
                download_urls = [release.get("downloadApiUrl"), release.get("downloadUrl")]
                with self._open_release_asset(download_urls, "正在连接下载源") as response, temporary.open("wb") as output:
                    total = int(response.headers.get("Content-Length") or 0)
                    downloaded = 0
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        percent = int(downloaded * 100 / total) if total else 0
                        self._set_update_state(
                            percent=percent,
                            message=f"正在下载更新 · {downloaded / 1048576:.1f} MB",
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
                temporary.unlink(missing_ok=True)
                self._set_update_state(status="failed", percent=0, message=f"更新失败: {exc}")

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
        return {
            "keys": keys,
            "thresholds": self.store.get_thresholds(),
            "rateLimitProgressMode": self.store.get_rate_limit_progress_mode(),
            "appVersion": APP_VERSION,
            "githubRepository": GITHUB_REPOSITORY,
            "updateFrequency": self.store.get_update_frequency(),
            "ignoredUpdateVersion": (
                self.store.get_ignored_update_version()
                if hasattr(self.store, "get_ignored_update_version")
                else ""
            ),
            "closeAction": self.store.get_close_action(),
            "alwaysOnTop": bool(getattr(self, "always_on_top", False)),
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

    def add_key(self, name: str, value: str) -> dict[str, Any]:
        return self._controller.add_key(name, value)

    def delete_key(self, key_id: str) -> dict[str, Any]:
        return self._controller.delete_key(key_id)

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

    def set_window_background(self, mode: Any) -> dict[str, Any]:
        return self._controller.set_window_background(mode)

    def native_drag(self, direction: str) -> dict[str, Any]:
        return self._controller.native_drag(direction)

    def open_devtools(self) -> dict[str, Any]:
        return self._controller.open_devtools()

    def report_startup(self, stage: str, navigation_ms: Any = 0) -> dict[str, Any]:
        return self._controller.report_startup(stage, navigation_ms)


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
        try:
            self.rpc_client.call("exit_app")
        finally:
            self.stopping.set()
            if self.window:
                self.window.destroy()

    def restart_app(self) -> dict[str, Any]:
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

    def add_key(self, name: str, value: str) -> dict[str, Any]:
        return self._remote("add_key", name, value)

    def delete_key(self, key_id: str) -> dict[str, Any]:
        return self._remote("delete_key", key_id)

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
    initial_page = (
        asset_cache.main_page if asset_cache.is_ready() else resource_path("initialize.html")
    )
    controller = UiController(rpc_client, asset_cache)
    controller.active_title_bar_mode = normalize_title_bar_mode(title_bar_mode)
    window = webview.create_window(
        WINDOW_TITLE,
        url=str(initial_page),
        js_api=RemoteWebApi(controller, rpc_client),
        width=920,
        height=680,
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
