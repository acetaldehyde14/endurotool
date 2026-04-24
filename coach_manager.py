"""
CoachManager — real-time driver coaching engine.

Loads a reference profile from the server, compares live telemetry against
zone targets, and fires text/voice coaching cues locally without round-tripping
to the server on every frame.

All public methods are thread-safe (called from monitor fast-loop threads).
Callbacks (on_cue, on_status_change) must be set before start().
"""

import threading
import time
from typing import Optional, Dict, List

import api_client
from config import (
    COACHING_ENABLED,
    COACHING_REFRESH_SECONDS,
    COACHING_LOOKAHEAD_MIN_LAP_DIST,
    COACHING_LOOKAHEAD_MAX_LAP_DIST,
    COACHING_MIN_SECONDS_BETWEEN_TEXT,
    COACHING_CORRECTION_START_LAP,
    COACHING_ZONE_MATCH_TOLERANCE_LAP_DIST,
    COACHING_MAX_ACTIVE_MESSAGES,
    COACHING_VOICE_ENABLED,
    COACHING_OVERLAY_ENABLED,
)
from coaching_models import (
    CoachingCue,
    CoachingProfile,
    CoachingZone,
    LiveZoneObservation,
)

# Default generic cues for each segment type when no custom text is provided.
_SEGMENT_DEFAULTS: Dict[str, tuple] = {
    "brake_zone":      ("Brake here",         "urgent_brake",  "reference_brake_now_marker"),
    "lift_zone":       ("Small lift here",     "caution_lift",  "reference_small_lift_before_turn_in"),
    "light_brake":     ("Light brake",         "caution_lift",  "reference_light_brake"),
    "throttle_pickup": ("Throttle on exit",    "throttle_go",   "reference_throttle_on_exit"),
    "wait_rotate":     ("Wait on throttle",    "caution_lift",  "reference_wait_on_throttle"),
    "apex":            ("Apex",                "neutral",       "reference_apex_marker"),
    "exit":            ("Power now",           "throttle_go",   "reference_power_now"),
}

# How many recent laps to average for corrections.
_CORRECTION_HISTORY_LAPS = 3

# Minimum samples in a zone before observations count.
_MIN_ZONE_SAMPLES = 3


class CoachManager:
    """
    Receives live telemetry samples and fires coaching cues.

    Integration contract (called from IRacingMonitor threads):
        on_session_started(session_info)   — when telemetry session opens
        on_session_ended()                 — when telemetry session closes
        on_live_sample(sample)             — every telemetry frame (~30 Hz)
        on_lap_completed(lap, time, valid) — when a lap crosses the line
    """

    def __init__(self, on_cue=None, on_status_change=None):
        self.on_cue           = on_cue           or (lambda c: None)
        self.on_status_change = on_status_change or (lambda s: None)

        # Feature toggles (can be changed at runtime)
        self._enabled         = COACHING_ENABLED
        self._voice_enabled   = COACHING_VOICE_ENABLED
        self._overlay_enabled = COACHING_OVERLAY_ENABLED

        # Profile (guarded by _profile_lock)
        self._profile: Optional[CoachingProfile] = None
        self._profile_lock = threading.Lock()

        # Session identity
        self._session_id:  Optional[str] = None
        self._track_id:    Optional[str] = None
        self._car_id:      Optional[str] = None
        self._track_name:  str = ""
        self._car_name:    str = ""

        # Lap-level state (reset each lap)
        self._current_lap:       int  = 0
        self._valid_laps_done:   int  = 0
        self._lap_cues_fired:    set  = set()   # zone_ids that fired this lap
        self._pending_corrections: Dict[str, str] = {}  # zone_id -> correction text

        # Zone observation accumulation (current lap)
        self._active_obs: Dict[str, LiveZoneObservation] = {}
        self._exited_zones: set = set()  # zones we've passed through this lap

        # Multi-lap history for correction analysis: zone_id -> list[obs]
        self._zone_history: Dict[str, List[LiveZoneObservation]] = {}

        # Cue spam control
        self._last_text_time = 0.0

        # Status
        self._status = "disabled"

        # Background refresh bookkeeping
        self._running       = False
        self._refresh_timer: Optional[threading.Timer] = None

    # ── Lifecycle ──────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._set_status("Waiting for session")

    def stop(self):
        self._running = False
        if self._refresh_timer:
            self._refresh_timer.cancel()

    def set_callbacks(self, on_cue=None, on_status_change=None):
        """Allow wiring callbacks after construction (e.g. once GUI is ready)."""
        if on_cue is not None:
            self.on_cue = on_cue
        if on_status_change is not None:
            self.on_status_change = on_status_change

    # ── Session events ─────────────────────────────────────────────

    def on_session_started(self, session_info: dict):
        self._session_id = session_info.get("session_id")
        self._track_id   = session_info.get("track_id")
        self._car_id     = session_info.get("car_id")
        self._track_name = session_info.get("track_name", "")
        self._car_name   = session_info.get("car_name", "")

        self._reset_lap_state()
        self._valid_laps_done  = 0
        self._zone_history     = {}
        self._pending_corrections = {}

        threading.Thread(target=self._fetch_profile, daemon=True).start()
        self._schedule_refresh()

    def on_session_ended(self):
        self._session_id = None
        if self._refresh_timer:
            self._refresh_timer.cancel()
        with self._profile_lock:
            self._profile = None
        self._set_status("Session ended")

    # ── Live data ─────────────────────────────────────────────────

    def on_live_sample(self, sample: dict):
        if not self._enabled:
            return
        with self._profile_lock:
            profile = self._profile
        if not profile:
            return

        # Keep _current_lap in sync with the actual iRacing lap counter so
        # LiveZoneObservations carry the correct lap number for post-lap feedback.
        self._current_lap = sample.get("lap_number", self._current_lap)

        lap_dist  = sample.get("lap_dist_pct", 0.0)
        speed_kph = sample.get("speed_kph", 0.0)

        lookahead = _calc_lookahead(speed_kph, profile.track_length_m)
        self._check_for_cues(lap_dist, lookahead, profile)
        self._update_observations(lap_dist, sample, profile)

    def on_lap_completed(self, lap_number: int,
                         lap_time_s: float | None = None,
                         valid: bool = True):
        if valid:
            self._valid_laps_done += 1

        with self._profile_lock:
            profile = self._profile

        self._finalize_lap_observations(lap_number, valid)

        if (self._valid_laps_done >= COACHING_CORRECTION_START_LAP
                and profile is not None):
            self._generate_corrections(profile)

        if self._session_id and valid:
            self._post_feedback(lap_number)

        self._reset_lap_state()
        self._current_lap = lap_number + 1

    # ── Public control ─────────────────────────────────────────────

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        if not enabled:
            self._set_status("Disabled")

    def set_voice_enabled(self, enabled: bool):
        self._voice_enabled = enabled

    def set_overlay_enabled(self, enabled: bool):
        self._overlay_enabled = enabled

    def get_current_state(self) -> dict:
        with self._profile_lock:
            has_profile = self._profile is not None
            zones = len(self._profile.zones) if self._profile else 0
        return {
            "enabled":             self._enabled,
            "has_profile":         has_profile,
            "zones":               zones,
            "status":              self._status,
            "valid_laps":          self._valid_laps_done,
            "pending_corrections": len(self._pending_corrections),
        }

    # ── Cue logic ─────────────────────────────────────────────────

    def _check_for_cues(self, lap_dist: float, lookahead: float,
                        profile: CoachingProfile):
        now = time.time()
        if now - self._last_text_time < COACHING_MIN_SECONDS_BETWEEN_TEXT:
            return

        ahead_end = lap_dist + lookahead

        # Sort by priority descending so we always fire the most important first.
        for zone in sorted(profile.zones, key=lambda z: z.priority, reverse=True):
            if not zone.enabled:
                continue
            if zone.zone_id in self._lap_cues_fired:
                continue
            if lap_dist <= zone.lap_dist_callout <= ahead_end:
                cue = self._build_cue(zone)
                if cue and cue.text:
                    self._fire_cue(cue)
                    self._lap_cues_fired.add(zone.zone_id)
                    # COACHING_MAX_ACTIVE_MESSAGES = 1: stop after first cue
                    break

    def _build_cue(self, zone: CoachingZone) -> Optional[CoachingCue]:
        # Use correction text if we have enough history and a correction exists.
        if (self._valid_laps_done >= COACHING_CORRECTION_START_LAP
                and zone.zone_id in self._pending_corrections):
            correction = self._pending_corrections[zone.zone_id]
            return _make_correction_cue(zone, correction)
        return _make_generic_cue(zone)

    def _fire_cue(self, cue: CoachingCue):
        self._last_text_time = time.time()
        print(f"[Coach] Cue fired: \"{cue.text}\" @ {cue.zone_label}")
        try:
            self.on_cue(cue)
        except Exception as e:
            print(f"[Coach] on_cue callback error: {e}")

    # ── Zone observation tracking ──────────────────────────────────

    def _update_observations(self, lap_dist: float, sample: dict,
                             profile: CoachingProfile):
        for zone in profile.zones:
            if not zone.enabled:
                continue

            in_zone = zone.lap_dist_start <= lap_dist <= zone.lap_dist_end

            if in_zone:
                obs = self._active_obs.get(zone.zone_id)
                if obs is None:
                    # Zone entry: initialise observation
                    obs = LiveZoneObservation(
                        zone_id=zone.zone_id,
                        lap_number=self._current_lap,
                        entry_speed_kph=sample.get("speed_kph"),
                        entry_gear=sample.get("gear"),
                    )
                    self._active_obs[zone.zone_id] = obs

                obs.samples += 1
                speed    = sample.get("speed_kph", 0.0)
                brake    = sample.get("brake", 0.0)
                throttle = sample.get("throttle", 0.0)

                if obs.min_speed_kph is None or speed < obs.min_speed_kph:
                    obs.min_speed_kph = speed

                if obs.brake_peak_pct is None or brake > obs.brake_peak_pct:
                    obs.brake_peak_pct = brake

                if obs.brake_start_dist is None and brake > 0.1:
                    obs.brake_start_dist = lap_dist

                # Throttle reapply: first significant throttle after braking started
                if (obs.throttle_reapply_dist is None
                        and throttle > 0.1
                        and obs.brake_start_dist is not None):
                    obs.throttle_reapply_dist = lap_dist

                # Continuously update exit speed so final value = last sample
                obs.exit_speed_kph = speed

    def _finalize_lap_observations(self, lap_number: int, valid: bool):
        if not valid:
            self._active_obs.clear()
            return
        for zone_id, obs in self._active_obs.items():
            if obs.samples < _MIN_ZONE_SAMPLES:
                continue
            if zone_id not in self._zone_history:
                self._zone_history[zone_id] = []
            self._zone_history[zone_id].append(obs)
            # Keep only the most recent laps to avoid stale data.
            if len(self._zone_history[zone_id]) > 5:
                self._zone_history[zone_id] = self._zone_history[zone_id][-5:]

    # ── Correction generation ──────────────────────────────────────

    def _generate_corrections(self, profile: CoachingProfile):
        new_corrections: Dict[str, str] = {}
        for zone in profile.zones:
            if not zone.enabled:
                continue
            history = self._zone_history.get(zone.zone_id, [])
            if len(history) < _CORRECTION_HISTORY_LAPS:
                continue
            recent = history[-_CORRECTION_HISTORY_LAPS:]
            correction = _analyze_zone(zone, recent, profile.track_length_m)
            if correction:
                new_corrections[zone.zone_id] = correction
                print(f"[Coach] Correction — {zone.name}: {correction}")
        self._pending_corrections = new_corrections

    # ── Zone feedback post ─────────────────────────────────────────

    def _post_feedback(self, lap_number: int):
        if not self._session_id:
            return
        feedback = []
        for zone_id, history in self._zone_history.items():
            for obs in history:
                if obs.lap_number == lap_number:
                    feedback.append({
                        "zone_id":              zone_id,
                        "lap_number":           obs.lap_number,
                        "entry_speed_kph":      obs.entry_speed_kph,
                        "min_speed_kph":        obs.min_speed_kph,
                        "exit_speed_kph":       obs.exit_speed_kph,
                        "brake_start_dist":     obs.brake_start_dist,
                        "brake_peak_pct":       obs.brake_peak_pct,
                        "throttle_reapply_dist": obs.throttle_reapply_dist,
                    })
        if not feedback:
            return
        sid = self._session_id
        threading.Thread(
            target=api_client.post_zone_feedback,
            args=(sid, lap_number, feedback),
            daemon=True,
        ).start()

    # ── Profile loading ────────────────────────────────────────────

    def _fetch_profile(self):
        if not self._track_id or not self._car_id:
            return
        print(f"[Coach] Fetching profile for track={self._track_id} car={self._car_id}")
        try:
            data = api_client.get_active_coaching_profile(
                self._track_id, self._car_id,
                track_name=self._track_name,
                car_name=self._car_name,
            )
            if data:
                profile = _parse_profile(data, self._track_id, self._car_id,
                                         self._track_name, self._car_name)
                with self._profile_lock:
                    self._profile = profile
                self._set_status(f"Active — {len(profile.zones)} zones")
                print(f"[Coach] Profile loaded: {profile.profile_id}, "
                      f"{len(profile.zones)} zones")
            else:
                self._set_status("No reference lap available")
                print("[Coach] No coaching profile available for this car/track")
        except Exception as e:
            self._set_status("Backend unavailable")
            print(f"[Coach] Profile fetch error: {e}")

    def _schedule_refresh(self):
        if not self._running:
            return
        if self._refresh_timer:
            self._refresh_timer.cancel()
        self._refresh_timer = threading.Timer(
            COACHING_REFRESH_SECONDS, self._on_refresh_tick
        )
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def _on_refresh_tick(self):
        if self._session_id and self._running:
            self._fetch_profile()
            self._schedule_refresh()

    # ── Helpers ───────────────────────────────────────────────────

    def _reset_lap_state(self):
        self._lap_cues_fired = set()
        self._active_obs     = {}
        self._exited_zones   = set()

    def _set_status(self, status: str):
        self._status = status
        try:
            self.on_status_change(status)
        except Exception:
            pass


# ── Module-level helpers (pure functions) ─────────────────────────

def _calc_lookahead(speed_kph: float, track_length_m: Optional[float]) -> float:
    """Return lookahead as a lap_dist_pct fraction."""
    if track_length_m and track_length_m > 0:
        speed_ms = speed_kph / 3.6
        dist_m   = speed_ms * 1.5   # look 1.5 seconds ahead
        dist_pct = dist_m / track_length_m
    else:
        # Fallback: scale linearly with speed (0–300 kph)
        frac     = min(speed_kph / 300.0, 1.0)
        dist_pct = (COACHING_LOOKAHEAD_MIN_LAP_DIST
                    + frac * (COACHING_LOOKAHEAD_MAX_LAP_DIST
                               - COACHING_LOOKAHEAD_MIN_LAP_DIST))
    return max(COACHING_LOOKAHEAD_MIN_LAP_DIST,
               min(COACHING_LOOKAHEAD_MAX_LAP_DIST, dist_pct))


def _make_generic_cue(zone: CoachingZone) -> Optional[CoachingCue]:
    display_text = zone.generic_display_text
    voice_key    = zone.generic_voice_key
    state        = "neutral"

    if not display_text:
        defaults = _SEGMENT_DEFAULTS.get(zone.segment_type)
        if not defaults:
            return None
        display_text, state, default_voice = defaults
        if not voice_key:
            voice_key = default_voice

    return CoachingCue(
        text=display_text,
        zone_label=zone.name,
        voice_key=voice_key,
        state=state,
    )


def _make_correction_cue(zone: CoachingZone, correction: str) -> CoachingCue:
    lower = correction.lower()
    if "brake" in lower and ("earlier" in lower or "more" in lower):
        state = "urgent_brake"
    elif "throttle" in lower or "power" in lower:
        state = "throttle_go"
    else:
        state = "caution_lift"
    return CoachingCue(text=correction, zone_label=zone.name, state=state)


def _analyze_zone(zone: CoachingZone,
                  observations: List[LiveZoneObservation],
                  track_length_m: Optional[float]) -> Optional[str]:
    """Return the single most important correction text, or None."""
    tol = COACHING_ZONE_MATCH_TOLERANCE_LAP_DIST

    # --- Brake-start timing ---
    if zone.lap_dist_callout is not None:
        brake_starts = [o.brake_start_dist for o in observations
                        if o.brake_start_dist is not None]
        if brake_starts:
            avg = sum(brake_starts) / len(brake_starts)
            delta = avg - zone.lap_dist_callout
            if delta > tol:
                if track_length_m:
                    m = round(abs(delta) * track_length_m)
                    return f"Brake around {m}m earlier"
                return "Brake a little earlier here"
            if delta < -tol:
                if track_length_m:
                    m = round(abs(delta) * track_length_m)
                    return f"Brake about {m}m later"
                return "You can brake a little later here"

    # --- Peak brake pressure ---
    if zone.target_brake_peak_pct is not None:
        peaks = [o.brake_peak_pct for o in observations
                 if o.brake_peak_pct is not None]
        if peaks:
            avg = sum(peaks) / len(peaks)
            delta = zone.target_brake_peak_pct - avg
            if delta > 0.08:
                return f"Use about {round(delta * 100)}% more brake here"
            if delta < -0.08:
                return f"Ease off about {round(abs(delta) * 100)}% brake here"

    # --- Minimum (apex) speed ---
    if zone.target_speed_min_kph is not None:
        mins = [o.min_speed_kph for o in observations
                if o.min_speed_kph is not None]
        if mins:
            avg = sum(mins) / len(mins)
            delta = zone.target_speed_min_kph - avg
            if delta > 3:
                return f"Carry {round(delta)} kph more minimum speed"
            if delta < -3:
                return f"Slow down {round(abs(delta))} kph more before apex"

    # --- Throttle reapply timing ---
    if zone.target_throttle_reapply_pct is not None:
        throttles = [o.throttle_reapply_dist for o in observations
                     if o.throttle_reapply_dist is not None]
        if throttles:
            avg = sum(throttles) / len(throttles)
            delta = avg - zone.target_throttle_reapply_pct
            if delta < -tol:
                if track_length_m:
                    m = round(abs(delta) * track_length_m)
                    return f"Get back to throttle about {m}m later"
                return "Wait a little longer before getting on throttle"
            if delta > tol:
                if track_length_m:
                    m = round(abs(delta) * track_length_m)
                    return f"Get on throttle {m}m earlier"
                return "Get on throttle a bit earlier"

    return None


def _parse_profile(data: dict, fallback_track_id: str, fallback_car_id: str,
                   fallback_track_name: str, fallback_car_name: str
                   ) -> CoachingProfile:
    zones = []
    for z in data.get("zones", []):
        zone = CoachingZone(
            zone_id=z.get("zone_id", ""),
            name=z.get("name", ""),
            sequence_index=int(z.get("sequence_index", 0)),
            segment_type=z.get("segment_type", ""),
            lap_dist_start=float(z.get("lap_dist_start", 0)),
            lap_dist_callout=float(z.get("lap_dist_callout", 0)),
            lap_dist_end=float(z.get("lap_dist_end", 0)),
            target_speed_entry_kph=z.get("target_speed_entry_kph"),
            target_speed_min_kph=z.get("target_speed_min_kph"),
            target_speed_exit_kph=z.get("target_speed_exit_kph"),
            target_brake_initial_pct=z.get("target_brake_initial_pct"),
            target_brake_peak_pct=z.get("target_brake_peak_pct"),
            target_brake_release_pct=z.get("target_brake_release_pct"),
            target_throttle_min_pct=z.get("target_throttle_min_pct"),
            target_throttle_reapply_pct=z.get("target_throttle_reapply_pct"),
            target_gear=z.get("target_gear"),
            priority=int(z.get("priority", 5)),
            generic_display_text=z.get("generic_display_text", ""),
            generic_voice_key=z.get("generic_voice_key", ""),
            correction_templates=z.get("correction_templates", {}),
            enabled=bool(z.get("enabled", True)),
        )
        zones.append(zone)

    return CoachingProfile(
        profile_id=data.get("profile_id", ""),
        track_id=data.get("track_id", fallback_track_id),
        car_id=data.get("car_id", fallback_car_id),
        track_name=data.get("track_name", fallback_track_name),
        car_name=data.get("car_name", fallback_car_name),
        track_length_m=data.get("track_length_m"),
        zones=zones,
        version=int(data.get("version", 1)),
    )
