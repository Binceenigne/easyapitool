"""Shared rules for ordinary visual references and public image endpoints."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


VISUAL_REFERENCE_POLICY = (
    "按任务需要自行检索、看图和选择有帮助的素材。"
    "用 select_visual_references 确认选中的图片，它们会作为生图输入；不需要时也可以不选。"
)


def public_reference_url(value: object, *, allow_http: bool = False) -> str:
    """Validate metadata syntax without doing DNS or allowing an HTTP download.

    The downloader independently validates public DNS, redirects and HTTPS.
    HTTP is allowed here only for a canonical source returned by hosted search;
    it is never made into a download endpoint.
    """
    url = str(value or "").strip()
    if not url or len(url) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in url):
        return ""
    try:
        parsed = urlsplit(url)
        schemes = {"http", "https"} if allow_http else {"https"}
        allowed_port = 80 if parsed.scheme == "http" else 443
        if parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, allowed_port}:
            return ""
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith((".localhost", ".local")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
    except ValueError:
        return ""
    return url


def reference_download_urls(image_url: object, thumbnail_url: object = "") -> tuple[str, ...]:
    """Use only original HTTPS or the HTTPS thumbnail supplied by hosted search."""
    original = public_reference_url(image_url)
    thumbnail = public_reference_url(thumbnail_url)
    return tuple(dict.fromkeys(url for url in (original, thumbnail) if url))
