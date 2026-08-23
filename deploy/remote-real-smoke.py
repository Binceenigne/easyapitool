from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

from PIL import Image


service_url = os.environ.get(
    "EASYAPITOOL_REAL_SMOKE_URL",
    "http://127.0.0.1:8765",
).rstrip("/")
service_token = os.environ.get("API_TOOLS_SERVICE_TOKEN", "")
provider_url = (
    os.environ.get("OPENAI_BASE_URL")
    or os.environ.get("BASE_URL", "")
).rstrip("/")
provider_key = os.environ.get("API_KEY", "").strip() or sys.stdin.readline().strip()

def request(path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {service_token}",
        "Accept": "application/json",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request_object = urllib.request.Request(
        service_url + path,
        data=body,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request_object, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def download_asset(resource_id: str) -> tuple[int, tuple[int, int], str]:
    request_object = urllib.request.Request(
        f"{service_url}/api/v1/assets/{resource_id}",
        headers={"Authorization": f"Bearer {service_token}"},
    )
    with urllib.request.urlopen(request_object, timeout=60) as response:
        body = response.read()
        content_type = response.headers.get_content_type()
    with tempfile.NamedTemporaryFile(suffix=".image") as temporary:
        temporary.write(body)
        temporary.flush()
        with Image.open(temporary.name) as image:
            image.verify()
        with Image.open(temporary.name) as image:
            dimensions = image.size
    return len(body), dimensions, content_type


if not provider_key:
    print("real_generation=blocked provider_key_not_configured")
    raise SystemExit(0)
if "work.easyclin.cn" not in provider_url:
    print("real_generation=blocked provider_url_not_easyclin")
    raise SystemExit(0)

key_id = "real-smoke-key"
try:
    request(
        "/api/v1/keys",
        method="POST",
        payload={
            "keyId": key_id,
            "name": "temporary-real-smoke",
            "value": provider_key,
        },
    )
    creation = request(
        "/api/v1/image-generations",
        method="POST",
        payload={
            "keyId": key_id,
            "prompt": "A simple studio photograph of one red apple on a white table",
            "referenceIds": [],
            "options": {
                "requestId": "real-smoke-request",
                "sessionId": "real-smoke-request",
                "imageCount": 1,
                "reasoningMode": "instant",
                "webSearchEnabled": False,
                "quality": "low",
                "outputPreset": "small",
            },
        },
    )
    request_id = str(creation.get("requestId") or "")
    if not request_id:
        print("real_generation=failed missing_request_id")
        raise SystemExit(1)
    final_state = None
    for _ in range(180):
        time.sleep(2)
        try:
            state = request(f"/api/v1/image-generations/{request_id}")
        except urllib.error.HTTPError:
            continue
        event = state.get("event") or {}
        if event.get("type") in {"task_result", "task_failed", "set_completed", "set_cancelled"}:
            final_state = event
            break
    if final_state is None:
        print("real_generation=failed timeout")
        raise SystemExit(1)
    result = final_state.get("result") if final_state.get("type") == "task_result" else final_state
    items = result.get("items") if isinstance(result, dict) else []
    successful = [item for item in items if item.get("ok")]
    download = None
    if successful:
        resource_id = str(successful[0].get("resourceId") or "")
        if resource_id:
            size_bytes, dimensions, content_type = download_asset(resource_id)
            download = {
                "resourceDownloaded": True,
                "sizeBytes": size_bytes,
                "dimensions": list(dimensions),
                "contentType": content_type,
            }
    print(json.dumps({
        "real_generation": "passed" if result.get("ok") and successful and download else "failed",
        "eventType": final_state.get("type"),
        "requestIdPresent": bool(request_id),
        "itemCount": len(items) if isinstance(items, list) else 0,
        "successfulItems": len(successful),
        "download": download,
        "error": str(result.get("error") or "")[:240],
    }, ensure_ascii=False))
finally:
    try:
        request(f"/api/v1/keys/{key_id}", method="DELETE")
    except Exception:
        pass