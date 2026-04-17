import requests
import threading
import time
import json
import gzip
from config import load_config, SERVER_URL

_lock = threading.Lock()
_last_position_post = 0.0
POSITION_POST_INTERVAL = 3.0   # seconds between position_update POSTs


def _headers(compressed: bool = False) -> dict:
    cfg = load_config()
    h = {
        "Authorization": f"Bearer {cfg.get('token', '')}",
        "Content-Type": "application/json",
    }
    if compressed:
        h["Content-Encoding"] = "gzip"
    return h


# ── Auth ───────────────────────────────────────────────────────

def login(username: str, password: str) -> dict:
    """Returns { token, user } or raises on failure."""
    r = requests.post(
        f"{SERVER_URL}/api/auth/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def validate_token() -> bool:
    """Returns True if the stored JWT is still valid."""
    try:
        r = requests.post(
            f"{SERVER_URL}/api/auth/validate",
            headers=_headers(),
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


# ── Event dispatcher ───────────────────────────────────────────

def post_event(event_type: str, data: dict) -> bool:
    """
    Route an event to the correct server endpoint.
      - telemetry_batch  → POST /api/iracing/telemetry  (gzip-compressed)
      - position_update  → POST /api/iracing/event      (debounced to 3s)
      - everything else  → POST /api/iracing/event
    """
    if event_type == "telemetry_batch":
        return _post_telemetry(data)

    if event_type == "position_update":
        global _last_position_post
        now = time.time()
        with _lock:
            if now - _last_position_post < POSITION_POST_INTERVAL:
                return True   # silently skip — too soon
            _last_position_post = now

    try:
        r = requests.post(
            f"{SERVER_URL}/api/iracing/event",
            json={"event": event_type, "data": data},
            headers=_headers(),
            timeout=5,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[API] post_event({event_type}) failed: {e}")
        return False


def _post_telemetry(data: dict) -> bool:
    """
    POST a telemetry batch gzip-compressed to /api/iracing/telemetry.
    A 2-second batch at 10 Hz ≈ 20 samples.
    Compression typically reduces payload by ~70%.
    """
    try:
        raw     = json.dumps(data).encode("utf-8")
        payload = gzip.compress(raw, compresslevel=6)
        r = requests.post(
            f"{SERVER_URL}/api/iracing/telemetry",
            data=payload,
            headers=_headers(compressed=True),
            timeout=8,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[API] telemetry upload failed: {e}")
        return False


# ── Status / version ───────────────────────────────────────────

def get_status() -> dict | None:
    """Fetch current race status (active race, current driver, last fuel)."""
    try:
        r = requests.get(
            f"{SERVER_URL}/api/iracing/status",
            headers=_headers(),
            timeout=5,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def get_client_version() -> dict | None:
    """Fetch latest version info for the auto-updater."""
    try:
        r = requests.get(f"{SERVER_URL}/api/client/version", timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None
