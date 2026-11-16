import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", PROJECT_ROOT / "config"))
DATA_DIR = PROJECT_ROOT / "data"
SRC_DIR = Path(__file__).resolve().parent

HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "180"))
HTTP_RETRIES = int(os.environ.get("HTTP_RETRIES", "4"))
USER_AGENT = "datacommon-project-pipeline/1.0"

DC_API_KEY = os.environ.get("DC_API_KEY", "")
DC_API_ROOT = os.environ.get("DC_API_ROOT", "https://api.datacommons.org")

DCP_INPUT_ROOT = "ingestion/input"
STATE_KEY = "state/pipeline_state.json"
PLACE_CACHE_KEY = "state/place_cache.json"
BACKUP_PREFIX = "backups"
REPORT_PREFIX = "reports"
