import json
import os

# Store config in %APPDATA%\iRacingEnduro on Windows
_APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
CONFIG_DIR = os.path.join(_APPDATA, "iRacingEnduro")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# ── Change this to your server URL ────────────────────────────
SERVER_URL = "https://smcorse.com"
# ──────────────────────────────────────────────────────────────

POLL_INTERVAL_SECONDS  = 2    # slow loop: driver / fuel / position checks
LOW_FUEL_THRESHOLD_MINS = 20  # not used client-side (server handles alerts)

TELEMETRY_HZ         = 30    # samples per second collected from iRacing
TELEMETRY_BATCH_SIZE = 30    # frames per batch upload (~1 s at 30 Hz)

SPOOL_DIR = os.path.join(CONFIG_DIR, "spool")  # offline batch queue

# ── Coaching ──────────────────────────────────────────────────────
COACHING_ENABLED                       = True
COACHING_API_TIMEOUT_SECONDS           = 3
COACHING_REFRESH_SECONDS               = 15
COACHING_LOOKAHEAD_MIN_LAP_DIST        = 0.004
COACHING_LOOKAHEAD_MAX_LAP_DIST        = 0.012
COACHING_VOICE_ENABLED                 = False
COACHING_VOICE_VOLUME                  = 0.85
COACHING_MIN_SECONDS_BETWEEN_VOICE     = 2.5
COACHING_MIN_SECONDS_BETWEEN_TEXT      = 0.8
COACHING_OVERLAY_ENABLED               = True
COACHING_CORRECTION_START_LAP          = 3
COACHING_ZONE_MATCH_TOLERANCE_LAP_DIST = 0.01
COACHING_AUDIO_CACHE_DIR               = os.path.join(CONFIG_DIR, "coach_audio")
COACHING_MAX_ACTIVE_MESSAGES           = 1


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
