from __future__ import annotations

import multiprocessing

from backend.common import *
from backend.platform import *
from backend.rpc import *
from backend.security import *
from backend.security import _blob
from backend.usage import *
from backend.usage import _usage_cost_value, _usage_token_value
from backend.web_search import *
from backend.store import Store
from backend.client import EasyClinClient
from backend.controller import AppController
from backend.web_api import WebApi
from backend.runtime import BackgroundApp, UiController, RemoteWebApi, run_ui_process, main


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
