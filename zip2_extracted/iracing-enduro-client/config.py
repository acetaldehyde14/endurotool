import json
import os

# Store config in %APPDATA%\iRacingEnduro on Windows
_APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
CONFIG_DIR  = os.path.join(_APPDATA, "iRacingEnduro")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# ── Set your server URL here ───────────────────────────────────
SERVER_URL = "https://your-server.com"
# ──────────────────────────────────────────────────────────────

POLL_INTERVAL_SECONDS  = 2    # slow loop: driver / fuel / position checks
LOW_FUEL_THRESHOLD_MINS = 20  # not used client-side (server handles alerts)

TELEMETRY_HZ         = 10    # samples per second collected from iRacing
TELEMETRY_FLUSH_SECS = 2.0   # how often to upload a batch to the server


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(data: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def clear_config():
    if os.path.exists(CONFIG_PATH):
        os.remove(CONFIG_PATH)
