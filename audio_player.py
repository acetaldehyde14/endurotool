"""
AudioPlayer - local WAV playback for coaching voice cues.

Uses the standard-library winsound module for non-blocking Windows WAV
playback. If voice is disabled or files are missing, play() is a silent no-op.
"""

import os
import threading
import time
from typing import Dict

import api_client
from coaching_models import VoiceAsset
from config import (
    COACHING_AUDIO_CACHE_DIR,
    COACHING_MIN_SECONDS_BETWEEN_VOICE,
    COACHING_VOICE_ENABLED,
)

_VOICE_KEY_FALLBACKS = {
    "reference_brake_now_marker": "reference_brake_now_at_the_marker",
    "reference_light_brake": "reference_light_brake_here",
    "reference_throttle_on_exit": "reference_back_to_throttle_on_exit",
    "reference_wait_on_throttle": "reference_wait_before_throttle_pickup",
    "reference_power_now": "reference_begin_to_feed_in_throttle",
}


class AudioPlayer:
    """
    Download and play pre-generated coaching voice lines.

    Thread-safe. play() returns immediately.
    """

    def __init__(self):
        self._manifest: Dict[str, VoiceAsset] = {}
        self._lock = threading.Lock()
        self._last_play = 0.0
        self._enabled = COACHING_VOICE_ENABLED
        self._cache_dir = COACHING_AUDIO_CACHE_DIR
        self._manifest_loaded = False
        self._sequence_lock = threading.Lock()

    # Public API
    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def load_manifest(self):
        """Fetch the server voice manifest in the background."""
        threading.Thread(target=self._fetch_manifest, daemon=True).start()

    def has_voice_key(self, voice_key: str) -> bool:
        with self._lock:
            return self._resolve_asset_locked(voice_key) is not None

    def manifest_summary(self) -> str:
        with self._lock:
            if not self._manifest_loaded:
                return "Voice manifest not loaded yet"
            if not self._manifest:
                return "Voice manifest loaded but no assets are available"
            return f"Voice manifest loaded with {len(self._manifest)} asset(s)"

    def play(self, voice_key: str, force: bool = False):
        """Play the WAV for voice_key if available and not in cooldown."""
        if not self._enabled or not voice_key:
            return

        now = time.time()
        if not force and now - self._last_play < COACHING_MIN_SECONDS_BETWEEN_VOICE:
            return

        with self._lock:
            asset = self._resolve_asset_locked(voice_key)

        if asset is None:
            print(f"[Audio] Missing voice asset: {voice_key}")
            return

        if not asset.cached:
            threading.Thread(
                target=self._download_asset,
                args=(asset,),
                daemon=True,
            ).start()
            return

        if not os.path.isfile(asset.local_path):
            asset.cached = False
            return

        self._last_play = now
        threading.Thread(
            target=self._play_wav,
            args=(asset.local_path,),
            daemon=True,
        ).start()

    def play_sequence(self, sequence: list[str], force: bool = False):
        """Play voice keys in order. Missing clips are logged and skipped."""
        if not self._enabled or not sequence:
            return

        now = time.time()
        if not force and now - self._last_play < COACHING_MIN_SECONDS_BETWEEN_VOICE:
            return

        threading.Thread(
            target=self._play_sequence_worker,
            args=(list(sequence), now),
            daemon=True,
        ).start()

    # Internal
    def _play_wav(self, path: str):
        try:
            import winsound

            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except ImportError:
            print("[Audio] winsound not available (non-Windows?)")
        except Exception as e:
            print(f"[Audio] Playback error: {e}")

    def _play_wav_sync(self, path: str):
        try:
            import winsound

            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_SYNC)
        except ImportError:
            print("[Audio] winsound not available (non-Windows?)")
        except Exception as e:
            print(f"[Audio] Playback error: {e}")

    def _play_sequence_worker(self, sequence: list[str], started_at: float):
        with self._sequence_lock:
            self._last_play = started_at
            for voice_key in sequence:
                with self._lock:
                    asset = self._resolve_asset_locked(voice_key)

                if asset is None:
                    print(f"[Audio] Missing voice asset: {voice_key}")
                    continue

                if not asset.cached:
                    self._download_asset(asset)

                if not asset.cached or not os.path.isfile(asset.local_path):
                    print(f"[Audio] Voice asset unavailable: {voice_key}")
                    continue

                self._play_wav_sync(asset.local_path)

    def _fetch_manifest(self):
        try:
            data = api_client.get_voice_manifest()
            if not data:
                return

            assets = data.get("assets", data) if isinstance(data, dict) else {}
            os.makedirs(self._cache_dir, exist_ok=True)
            new_manifest: Dict[str, VoiceAsset] = {}

            for key, info in assets.items():
                url = info.get("url", "") if isinstance(info, dict) else str(info)
                local_path = os.path.join(self._cache_dir, _safe_filename(key))
                cached = os.path.isfile(local_path)
                new_manifest[key] = VoiceAsset(
                    key=key,
                    url=url,
                    local_path=local_path,
                    cached=cached,
                )

            with self._lock:
                self._manifest = new_manifest

            self._manifest_loaded = True
            cached_count = sum(1 for asset in new_manifest.values() if asset.cached)
            print(
                f"[Audio] Manifest loaded - {len(new_manifest)} keys, "
                f"{cached_count} cached locally"
            )
        except Exception as e:
            print(f"[Audio] Manifest fetch failed: {e}")

    def _download_asset(self, asset: VoiceAsset):
        if not asset.url:
            return

        print(f"[Audio] Downloading voice asset: {asset.key}")
        ok = api_client.download_voice_asset(asset.url, asset.local_path)
        if ok:
            asset.cached = True
            print(f"[Audio] Cached: {asset.key}")
        else:
            print(f"[Audio] Download failed: {asset.key}")

    def _resolve_asset_locked(self, voice_key: str) -> VoiceAsset | None:
        asset = self._manifest.get(voice_key)
        if asset is not None:
            return asset

        fallback_key = _VOICE_KEY_FALLBACKS.get(voice_key)
        if fallback_key:
            return self._manifest.get(fallback_key)

        for legacy_key, modern_key in _VOICE_KEY_FALLBACKS.items():
            if modern_key == voice_key:
                return self._manifest.get(legacy_key)

        return None


def _safe_filename(key: str) -> str:
    """Convert a voice key to a safe WAV filename."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return safe + ".wav"
