from __future__ import annotations

from ..common import *
from ..platform import *
from ..usage import *
from ..web_search import WebSearchService
from ..store import Store
from ..client import EasyClinClient

class UpdateMixin:
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
