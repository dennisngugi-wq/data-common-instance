"""Paths and environment settings. Everything about the data itself lives in config/ (see registry.py)."""

import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", PROJECT_ROOT / "config"))
DATA_DIR = PROJECT_ROOT / "data"           # local files a feed may read (fetch.path must be inside it)
SRC_DIR = Path(__file__).resolve().parent

HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "180"))
HTTP_RETRIES = int(os.environ.get("HTTP_RETRIES", "4"))
USER_AGENT = "datacommon-project-pipeline/1.0"

# Data Commons API key: only used to resolve place names that are not in the built-in gazetteer.
DC_API_KEY = os.environ.get("DC_API_KEY", "")
DC_API_ROOT = os.environ.get("DC_API_ROOT", "https://api.datacommons.org")

# Layout of the store (local folder now, GCS bucket later).
DCP_INPUT_ROOT = "ingestion/input"         # <store>/ingestion/input/<import>/...
STATE_KEY = "state/pipeline_state.json"
PLACE_CACHE_KEY = "state/place_cache.json"
BACKUP_PREFIX = "backups"
REPORT_PREFIX = "reports"
