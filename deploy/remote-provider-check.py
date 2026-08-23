from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


base_url = os.environ.get("OPENAI_BASE_URL", "https://work.easyclin.cn/v1").rstrip("/")
secret = os.environ.get("API_KEY", "")
request = urllib.request.Request(
    f"{base_url}/models",
    headers={"Authorization": f"Bearer {secret}", "Accept": "application/json"},
)

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(1024).decode("utf-8", "replace")
        print(json.dumps({"status": response.status, "bodyPrefix": body[:180]}, ensure_ascii=False))
except urllib.error.HTTPError as error:
    body = error.read(1024).decode("utf-8", "replace")
    body = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", body)
    print(json.dumps({"status": error.code, "bodyPrefix": body[:240]}, ensure_ascii=False))
except Exception as error:
    print(json.dumps({"status": None, "errorType": type(error).__name__, "error": str(error)[:240]}, ensure_ascii=False))