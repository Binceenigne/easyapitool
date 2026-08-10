from __future__ import annotations

from .common import *
from .platform import get_small_url_bytes

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
