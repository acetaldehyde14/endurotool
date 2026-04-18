import os
import json as _json
import uuid
import requests
import threading
import time
from datetime import datetime, timezone
from config import load_config, SERVER_URL, SPOOL_DIR

_lock = threading.Lock()
_last_position_post = 0.0
POSITION_POST_INTERVAL = 3.0   # seconds between position_update POSTs


def _headers() -> dict:
    cfg = load_config()
    return {
        "Authorization": f"Bearer {cfg.get('token', '')}",
        "Content-Type": "application/json",
    }


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


def register(username: str, password: str) -> dict:
    """Returns { token, user } or raises on failure."""
    r = requests.post(
        f"{SERVER_URL}/api/auth/register",
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


# ── General event dispatcher ───────────────────────────────────

def post_event(event_type: str, data: dict) -> bool:
    """
    Post a race event to /api/iracing/event.
    position_update is debounced to once every 3 s.
    telemetry_batch is excluded — the monitor posts those directly.
    """
    if event_type == "telemetry_batch":
        return True  # handled internally by IRacingMonitor

    if event_type == "position_update":
        global _last_position_post
        now = time.time()
        with _lock:
            if now - _last_position_post < POSITION_POST_INTERVAL:
                return True
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


# ── Offline spool ──────────────────────────────────────────────

def _spool_write(payload: dict):
    """Persist a failed batch to disk so it can be replayed later."""
    try:
        os.makedirs(SPOOL_DIR, exist_ok=True)
        path = os.path.join(SPOOL_DIR, f"{uuid.uuid4().hex}.json")
        with open(path, "w") as f:
            _json.dump(payload, f)
        print(f"[API] Spooled batch to {os.path.basename(path)}")
    except Exception as e:
        print(f"[API] Spool write failed: {e}")


def _spool_replay():
    """
    Try to re-upload any spooled batches in order.
    Called automatically after a successful batch upload.
    Stops on first failure to avoid hammering a flaky server.
    """
    if not os.path.isdir(SPOOL_DIR):
        return
    for fname in sorted(os.listdir(SPOOL_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(SPOOL_DIR, fname)
        try:
            with open(path) as f:
                payload = _json.load(f)
            r = requests.post(
                f"{SERVER_URL}/api/telemetry/live/batch",
                json=payload,
                headers=_headers(),
                timeout=15,
            )
            if r.status_code == 200:
                os.remove(path)
                print(f"[API] Replayed spooled batch {fname}")
            else:
                break   # server error — try again next time
        except Exception:
            break       # network error — try again next time


# ── Live telemetry session ─────────────────────────────────────

def telemetry_session_start(payload: dict) -> str | None:
    """
    Start a new live telemetry session.
    payload keys: sim_session_uid, sub_session_id, track_id, track_name,
                  car_id, car_name, session_type, driver_name,
                  iracing_driver_id, started_at
    Returns server-assigned session_id, or None on failure.
    """
    try:
        r = requests.post(
            f"{SERVER_URL}/api/telemetry/live/session/start",
            json=payload,
            headers=_headers(),
            timeout=15,
        )
        if not r.ok:
            print(f"[API] telemetry_session_start HTTP {r.status_code}: {r.text[:500]}")
            return None
        data = r.json()
        sid = data.get("session_id")
        if not sid:
            print(f"[API] telemetry_session_start succeeded but no session_id in response: {data}")
        return sid
    except Exception as e:
        print(f"[API] telemetry_session_start failed: {e}")
        return None


def telemetry_batch(session_id: str, lap_number: int,
                    frames: list, sample_rate_hz: int) -> bool:
    """
    Upload a batch of telemetry frames for a given lap.
    On failure the batch is written to the spool directory for later replay.
    On success any spooled batches are replayed.
    """
    payload = {
        "session_id":     session_id,
        "lap_number":     lap_number,
        "sample_rate_hz": sample_rate_hz,
        "frames":         frames,
    }
    try:
        r = requests.post(
            f"{SERVER_URL}/api/telemetry/live/batch",
            json=payload,
            headers=_headers(),
            timeout=15,
        )
        if r.status_code == 200:
            _spool_replay()   # clear backlog while we have connectivity
            return True
        _spool_write(payload)
        return False
    except Exception as e:
        print(f"[API] telemetry_batch failed: {e}")
        _spool_write(payload)
        return False


def telemetry_lap_complete(session_id: str, lap_number: int,
                           lap_time_s: float | None = None,
                           valid: bool = True,
                           incidents: int | None = None) -> bool:
    """
    Notify the server that a lap has been completed.

    lap_time_s  — raw seconds from iRacing (None if unavailable)
    valid       — False for laps invalidated by iRacing
    incidents   — cumulative incident count at lap end (server computes delta)
    """
    payload = {
        "session_id": session_id,
        "lap_number": lap_number,
        "lap_time":   round(lap_time_s, 3) if lap_time_s is not None else None,
        "is_valid":   valid,
    }
    if incidents is not None:
        payload["incidents"] = incidents
    try:
        r = requests.post(
            f"{SERVER_URL}/api/telemetry/live/lap-complete",
            json=payload,
            headers=_headers(),
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[API] telemetry_lap_complete failed: {e}")
        return False


def telemetry_session_end(session_id: str, summary: dict | None = None) -> bool:
    """
    Close a live telemetry session.

    summary (optional) may contain:
        total_laps, best_lap_s, avg_fuel_per_lap
    These are merged into the request body so the server can store them
    without re-reading all raw frames.
    """
    payload = {
        "session_id": session_id,
        "ended_at":   datetime.now(timezone.utc).isoformat(),
    }
    if summary:
        payload.update(summary)
    try:
        r = requests.post(
            f"{SERVER_URL}/api/telemetry/live/session/end",
            json=payload,
            headers=_headers(),
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[API] telemetry_session_end failed: {e}")
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
