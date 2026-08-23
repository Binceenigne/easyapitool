from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path


http_config_path = Path(sys.argv[1])
https_config_path = Path(sys.argv[2])
https_source_path = Path(sys.argv[3])
http_config = http_config_path.read_text(encoding="utf-8")
https_config = https_source_path.read_text(encoding="utf-8")
location_marker = 'location ^~ /easyapitool-api/ {'

if location_marker not in http_config:
    raise SystemExit("API proxy location is missing from HTTP configuration")
if 'return 308 https://$host$request_uri;' not in http_config:
    backup = http_config_path.with_name(
        http_config_path.name + f".easyapitool-https.bak.{int(time.time())}"
    )
    shutil.copy2(http_config_path, backup)
    http_config = http_config.replace(
        location_marker,
        location_marker + "\n        return 308 https://$host$request_uri;",
        1,
    )
    http_config_path.write_text(http_config, encoding="utf-8")
    print(f"http-updated:{backup}")
else:
    print("http-already-updated")

if https_config_path.exists():
    existing = https_config_path.read_text(encoding="utf-8")
    if existing != https_config:
        backup = https_config_path.with_name(
            https_config_path.name + f".bak.{int(time.time())}"
        )
        shutil.copy2(https_config_path, backup)
        print(f"https-backed-up:{backup}")
https_config_path.write_text(https_config, encoding="utf-8")
print(f"https-installed:{https_config_path}")