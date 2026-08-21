from __future__ import annotations

from .common import *
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
    root = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
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
    return f'"{executable}" "{ENTRY_SCRIPT}"'


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
            or Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
        ).resolve()
        self.releases_root = self.data_root / "static"
        self.lock = threading.RLock()
        self.install_thread: threading.Thread | None = None
        self.source_files = {
            relative: self.bundle_root / Path(relative)
            for relative in FRONTEND_RUNTIME_FILES
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
            "frontend/vendor/lucide/lucide.min.js": LUCIDE_SHA256,
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
            lucide_path = staging / "frontend" / "vendor" / "lucide" / "lucide.min.js"
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
    "benchmark_run",
    "check_for_updates",
    "cancel_image_generation",
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
