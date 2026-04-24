"""
AudioPlayer — local WAV playback for coaching voice cues.

Uses stdlib winsound for non-blocking Windows WAV playback.
Volume control is not available via winsound; install pygame if needed.

Voice files are downloaded from the server manifest on first use and
cached in COACHING_AUDIO_CACHE_DIR.  If voice is disabled or files are
missing, all play() calls are silent no-ops.
"""

import os
import threading
import time
from typing import Dict, Optional

import api_client
from config import (
    COACHING_AUDIO_CACHE_DIR,
    COACHING_MIN_SECONDS_BETWEEN_VOICE,
    COACHING_VOICE_ENABLED,
)
from coaching_models import VoiceAsset


class AudioPlayer:
    """
    Download and play pre-generated coaching voice lines.

    Thread-safe.  play() returns immediately (fire-and-forget).
    """

    def __init__(self):
        self._manifest:  Dict[str, VoiceAsset] = {}   # key -> VoiceAsset
        self._lock       = threading.Lock()
        self._last_play  = 0.0
        self._enabled    = COACHING_VOICE_ENABLED
        self._cache_dir  = COACHING_AUDIO_CACHE_DIR
        self._manifest_loaded = False

    # ── Public API ─────────────────────────────────────────────────

    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def load_manifest(self):
        """Fetch the server voice manifest in the background."""
        threading.Thread(target=self._fetch_manifest, daemon=True).start()

    def play(self, voice_key: str):
        """Play the WAV for voice_key if available and not in cooldown."""
        if not self._enabled or not voice_key:
            return
        now = time.time()
        if now - self._last_play < COACHING_MIN_SECONDS_BETWEEN_VOICE:
            return

        with self._lock:
            asset = self._manifest.get(voice_key)

        if asset is None:
            # Unknown key — try to download if manifest exists
            return

        if not asset.cached:
            # Download in background; the cue will play on the next lap
            threading.Thread(
                target=self._download_asset, args=(asset,), daemon=True
            ).start()
            return

        if not os.path.isfile(asset.local_path):
            asset.cached = False
            return

        self._last_play = now
        threading.Thread(
            target=self._play_wav, args=(asset.local_path,), daemon=True
        ).start()

    # ── Internal ───────────────────────────────────────────────────

    def _play_wav(self, path: str):
        try:
            import winsound
            # SND_ASYNC: non-blocking (new call will preempt previous)
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except ImportError:
            print("[Audio] winsound not available (non-Windows?)")
        except Exception as e:
            print(f"[Audio] Playback error: {e}")

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
                    key=key, url=url, local_path=local_path, cached=cached
                )
            with self._lock:
                self._manifest = new_manifest
            self._manifest_loaded = True
            cached_count = sum(1 for a in new_manifest.values() if a.cached)
            print(f"[Audio] Manifest loaded — {len(new_manifest)} keys, "
                  f"{cached_count} cached locally")
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


def _safe_filename(key: str) -> str:
    """Convert a voice key to a safe WAV filename."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return safe + ".wav"
