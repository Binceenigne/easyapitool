from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path


config_path = Path(sys.argv[1])
snippet_path = Path(sys.argv[2])
marker = "    #PROXY-CONF-START"
config = config_path.read_text(encoding="utf-8")
snippet = snippet_path.read_text(encoding="utf-8").rstrip() + "\n"

if "location ^~ /easyapitool-api/" not in config:
    if marker not in config:
        raise SystemExit("Nginx proxy marker not found")
    backup = config_path.with_name(
        config_path.name + f".easyapitool.bak.{int(time.time())}"
    )
    shutil.copy2(config_path, backup)
    config = config.replace(marker, snippet + "\n" + marker, 1)
    config_path.write_text(config, encoding="utf-8")
    print(f"installed:{backup}")
else:
    print("already-installed")