import json as _json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests

from config import SERVER_URL, SPOOL_DIR, load_config

_lock = threading.Lock()
_last_position_post = 0.0
POSITION_POST_INTERVAL = 3.0


def _headers() -> dict:
    cfg = load_config()
    return {
        "Authorization": f"Bearer {cfg.get('token', '')}",
        "Content-Type": "application/json",
    }


# Auth
def login(username: str, password: str) -> dict:
    """Returns {token, user} or raises on failure."""
    response = requests.post(
        f"{SERVER_URL}/api/auth/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def register(username: str, password: str) -> dict:
    """Returns {token, user} or raises on failure."""
    response = requests.post(
        f"{SERVER_URL}/api/auth/register",
        json={"username": username, "password": password},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def validate_token() -> bool:
    """Returns True if the stored JWT is still valid."""
    try:
        response = requests.post(
            f"{SERVER_URL}/api/auth/validate",
            headers=_headers(),
            timeout=5,
        )
        return response.status_code == 200
    except Exception:
        return False


# General event dispatcher
def post_event(event_type: str, data: dict) -> bool:
    """
    Post a race event to /api/iracing/event.

    position_update is debounced to once every 3 seconds.
    telemetry_batch is excluded because the monitor posts those directly.
    """
    if event_type == "telemetry_batch":
        return True

    if event_type == "position_update":
        global _last_position_post
        now = time.time()
        with _lock:
            if now - _last_position_post < POSITION_POST_INTERVAL:
                return True
            _last_position_post = now

    try:
        response = requests.post(
            f"{SERVER_URL}/api/iracing/event",
            json={"event": event_type, "data": data},
            headers=_headers(),
            timeout=5,
        )
        return response.status_code == 200
    except Exception as e:
        print(f"[API] post_event({event_type}) failed: {e}")
        return False


# Offline spool
def _spool_write(payload: dict):
    """Persist a failed batch to disk so it can be replayed later."""
    try:
        os.makedirs(SPOOL_DIR, exist_ok=True)
        path = os.path.join(SPOOL_DIR, f"{uuid.uuid4().hex}.json")
        with open(path, "w", encoding="utf-8") as file_obj:
            _json.dump(payload, file_obj)
        print(f"[API] Spooled batch to {os.path.basename(path)}")
    except Exception as e:
        print(f"[API] Spool write failed: {e}")


def _spool_replay():
    """
    Try to re-upload any spooled batches in order.

    Called automatically after a successful batch upload. Stops on the first
    failure to avoid hammering a flaky server.
    """
    if not os.path.isdir(SPOOL_DIR):
        return

    for filename in sorted(os.listdir(SPOOL_DIR)):
        if not filename.endswith(".json"):
            continue

        path = os.path.join(SPOOL_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as file_obj:
                payload = _json.load(file_obj)
            response = requests.post(
                f"{SERVER_URL}/api/telemetry/live/batch",
                json=payload,
                headers=_headers(),
                timeout=15,
            )
            if response.status_code == 200:
                os.remove(path)
                print(f"[API] Replayed spooled batch {filename}")
            else:
                break
        except Exception:
            break


# Live telemetry session
def telemetry_session_start(payload: dict) -> str | None:
    """Start a new live telemetry session."""
    try:
        response = requests.post(
            f"{SERVER_URL}/api/telemetry/live/session/start",
            json=payload,
            headers=_headers(),
            timeout=15,
        )
        if not response.ok:
            print(
                f"[API] telemetry_session_start HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
            return None
        data = response.json()
        session_id = data.get("session_id")
        if not session_id:
            print(
                "[API] telemetry_session_start succeeded but no session_id in "
                f"response: {data}"
            )
        return session_id
    except Exception as e:
        print(f"[API] telemetry_session_start failed: {e}")
        return None


def telemetry_batch(
    session_id: str,
    lap_number: int,
    frames: list,
    sample_rate_hz: int,
) -> bool:
    """
    Upload a batch of telemetry frames for a given lap.

    On failure the batch is written to the spool directory for later replay.
    On success any spooled batches are replayed.
    """
    payload = {
        "session_id": session_id,
        "lap_number": lap_number,
        "sample_rate_hz": sample_rate_hz,
        "frames": frames,
    }
    try:
        response = requests.post(
            f"{SERVER_URL}/api/telemetry/live/batch",
            json=payload,
            headers=_headers(),
            timeout=15,
        )
        if response.status_code == 200:
            _spool_replay()
            return True
        _spool_write(payload)
        return False
    except Exception as e:
        print(f"[API] telemetry_batch failed: {e}")
        _spool_write(payload)
        return False


def telemetry_lap_complete(
    session_id: str,
    lap_number: int,
    lap_time_s: float | None = None,
    valid: bool = True,
    incidents: int | None = None,
) -> bool:
    """Notify the server that a lap has been completed."""
    payload = {
        "session_id": session_id,
        "lap_number": lap_number,
        "lap_time": round(lap_time_s, 3) if lap_time_s is not None else None,
        "is_valid": valid,
    }
    if incidents is not None:
        payload["incidents"] = incidents

    try:
        response = requests.post(
            f"{SERVER_URL}/api/telemetry/live/lap-complete",
            json=payload,
            headers=_headers(),
            timeout=15,
        )
        return response.status_code == 200
    except Exception as e:
        print(f"[API] telemetry_lap_complete failed: {e}")
        return False


def telemetry_session_end(session_id: str, summary: dict | None = None) -> bool:
    """Close a live telemetry session."""
    payload = {
        "session_id": session_id,
        "ended_at": datetime.now(timezone.utc).isoformat(),
    }
    if summary:
        payload.update(summary)

    try:
        response = requests.post(
            f"{SERVER_URL}/api/telemetry/live/session/end",
            json=payload,
            headers=_headers(),
            timeout=15,
        )
        return response.status_code == 200
    except Exception as e:
        print(f"[API] telemetry_session_end failed: {e}")
        return False


# Status / version
def get_status() -> dict | None:
    """Fetch current race status."""
    try:
        response = requests.get(
            f"{SERVER_URL}/api/iracing/status",
            headers=_headers(),
            timeout=5,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def get_client_version() -> dict | None:
    """Fetch latest version info for the auto-updater."""
    try:
        response = requests.get(f"{SERVER_URL}/api/client/version", timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


# Coaching
def get_active_coaching_profile(
    track_id: str,
    car_id: str,
    track_name: str | None = None,
    car_name: str | None = None,
) -> dict | None:
    """Fetch the active coaching profile for the given track/car combination."""
    from config import COACHING_API_TIMEOUT_SECONDS

    try:
        params = {"track_id": track_id, "car_id": car_id}
        if track_name:
            params["track_name"] = track_name
        if car_name:
            params["car_name"] = car_name

        response = requests.get(
            f"{SERVER_URL}/api/coaching/profile/active",
            params=params,
            headers=_headers(),
            timeout=COACHING_API_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            return response.json()
        if response.status_code == 404:
            return None
        print(f"[API] get_active_coaching_profile HTTP {response.status_code}")
        return None
    except Exception as e:
        print(f"[API] get_active_coaching_profile failed: {e}")
        return None


def get_voice_manifest() -> dict | None:
    """Fetch the voice asset manifest."""
    from config import COACHING_API_TIMEOUT_SECONDS

    try:
        response = requests.get(
            f"{SERVER_URL}/api/coaching/voice/manifest",
            headers=_headers(),
            timeout=COACHING_API_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"[API] get_voice_manifest failed: {e}")
        return None


def download_voice_asset(asset_url: str, local_path: str) -> bool:
    """Download a voice asset and save it locally."""
    try:
        if not asset_url.startswith(("http://", "https://")):
            asset_url = urljoin(SERVER_URL.rstrip("/") + "/", asset_url)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        response = requests.get(
            asset_url,
            headers=_headers(),
            timeout=30,
            stream=True,
        )
        if not response.ok:
            return False
        with open(local_path, "wb") as file_obj:
            for chunk in response.iter_content(chunk_size=16384):
                file_obj.write(chunk)
        return True
    except Exception as e:
        print(f"[API] download_voice_asset failed: {e}")
        return False


def post_zone_feedback(
    session_id: str,
    lap_number: int,
    zone_feedback_payload: list,
) -> bool:
    """Post per-zone live performance observations for a completed lap."""
    from config import COACHING_API_TIMEOUT_SECONDS

    try:
        response = requests.post(
            f"{SERVER_URL}/api/coaching/observations",
            json={
                "session_id": session_id,
                "lap_number": lap_number,
                "observations": zone_feedback_payload,
            },
            headers=_headers(),
            timeout=COACHING_API_TIMEOUT_SECONDS,
        )
        return response.status_code == 200
    except Exception as e:
        print(f"[API] post_zone_feedback failed: {e}")
        return False


def get_reference_lap_candidates(
    track_id: str,
    car_id: str,
    limit: int = 20,
) -> dict | None:
    """Return candidate reference laps for the given track/car."""
    from config import COACHING_API_TIMEOUT_SECONDS

    try:
        response = requests.get(
            f"{SERVER_URL}/api/coaching/reference/candidates",
            params={"track_id": track_id, "car_id": car_id, "limit": limit},
            headers=_headers(),
            timeout=COACHING_API_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            return response.json()
        if response.status_code == 404:
            return None
        print(f"[API] get_reference_lap_candidates HTTP {response.status_code}")
        return None
    except Exception as e:
        print(f"[API] get_reference_lap_candidates failed: {e}")
        return None


def get_all_laps() -> dict | None:
    """Return all uploaded/live laps for the current user."""
    from config import COACHING_API_TIMEOUT_SECONDS

    try:
        response = requests.get(
            f"{SERVER_URL}/api/telemetry/all-laps",
            headers=_headers(),
            timeout=COACHING_API_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            return response.json()
        print(f"[API] get_all_laps HTTP {response.status_code}")
        return None
    except Exception as e:
        print(f"[API] get_all_laps failed: {e}")
        return None


def activate_reference_lap(lap_id: int) -> bool:
    """Set a lap as the active coaching reference for its track/car."""
    from config import COACHING_API_TIMEOUT_SECONDS

    try:
        response = requests.post(
            f"{SERVER_URL}/api/coaching/reference/{lap_id}/activate",
            json={},
            headers=_headers(),
            timeout=COACHING_API_TIMEOUT_SECONDS,
        )
        return response.status_code == 200
    except Exception as e:
        print(f"[API] activate_reference_lap({lap_id}) failed: {e}")
        return False


def upload_telemetry_file(file_path: str, session_type: str = "practice") -> dict | None:
    """Upload an iRacing telemetry file so its laps can be used for coaching."""
    from config import COACHING_API_TIMEOUT_SECONDS

    headers = _headers()
    headers.pop("Content-Type", None)

    try:
        with open(file_path, "rb") as file_obj:
            response = requests.post(
                f"{SERVER_URL}/api/telemetry/upload",
                headers=headers,
                data={"session_type": session_type},
                files={"telemetry": (os.path.basename(file_path), file_obj)},
                timeout=max(60, COACHING_API_TIMEOUT_SECONDS),
            )
        if response.ok:
            return response.json()
        print(
            f"[API] upload_telemetry_file HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )
        return None
    except Exception as e:
        print(f"[API] upload_telemetry_file failed: {e}")
        return None
