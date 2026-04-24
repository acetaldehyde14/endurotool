"""
Real-time coaching engine.

The monitor calls the public methods on background threads. Any exception in
coaching must be swallowed so telemetry collection keeps running.
"""

import threading
import time
from typing import Optional

import api_client
from coaching_models import (
    CoachingCue,
    CoachingProfile,
    CoachingZone,
    LiveZoneObservation,
)
from config import (
    COACHING_CORRECTION_START_LAP,
    COACHING_ENABLED,
    COACHING_LOOKAHEAD_MAX_LAP_DIST,
    COACHING_LOOKAHEAD_MIN_LAP_DIST,
    COACHING_MAX_ACTIVE_MESSAGES,
    COACHING_MIN_SECONDS_BETWEEN_TEXT,
    COACHING_OVERLAY_ENABLED,
    COACHING_REFRESH_SECONDS,
    COACHING_VOICE_ENABLED,
    COACHING_ZONE_MATCH_TOLERANCE_LAP_DIST,
)

_SEGMENT_DEFAULTS: dict[str, tuple[str, str, str]] = {
    "brake_zone": ("Brake here", "urgent_brake", "reference_brake_now_at_the_marker"),
    "lift_zone": ("Small lift here", "caution_lift", "reference_small_lift_before_turn_in"),
    "light_brake": ("Light brake", "caution_lift", "reference_light_brake_here"),
    "throttle_pickup": ("Throttle on exit", "throttle_go", "reference_back_to_throttle_on_exit"),
    "wait_rotate": ("Wait on throttle", "caution_lift", "reference_wait_before_throttle_pickup"),
    "apex": ("Apex", "neutral", "reference_apex_marker"),
    "exit": ("Power now", "throttle_go", "reference_begin_to_feed_in_throttle"),
}
_CORRECTION_HISTORY_LAPS = 3
_MIN_ZONE_SAMPLES = 3


class CoachManager:
    def __init__(self, on_cue=None, on_status_change=None):
        self.on_cue = on_cue or (lambda cue: None)
        self.on_status_change = on_status_change or (lambda status: None)

        self._enabled = COACHING_ENABLED
        self._voice_enabled = COACHING_VOICE_ENABLED
        self._overlay_enabled = COACHING_OVERLAY_ENABLED

        self._profile: Optional[CoachingProfile] = None
        self._profile_lock = threading.Lock()

        self._session_id: Optional[str] = None
        self._track_id: Optional[str] = None
        self._car_id: Optional[str] = None
        self._track_name = ""
        self._car_name = ""

        self._current_lap = 0
        self._valid_laps_done = 0
        self._lap_cues_fired: set[str] = set()
        self._pending_corrections: dict[str, str] = {}
        self._active_obs: dict[str, LiveZoneObservation] = {}
        self._zone_history: dict[str, list[LiveZoneObservation]] = {}

        self._last_text_time = 0.0
        self._status = "disabled"
        self._running = False
        self._refresh_timer: Optional[threading.Timer] = None

    def start(self) -> None:
        self._running = True
        self._set_status("Waiting for session")

    def stop(self) -> None:
        self._running = False
        if self._refresh_timer:
            self._refresh_timer.cancel()
            self._refresh_timer = None

    def set_callbacks(self, on_cue=None, on_status_change=None) -> None:
        if on_cue is not None:
            self.on_cue = on_cue
        if on_status_change is not None:
            self.on_status_change = on_status_change

    def on_session_started(self, session_info: dict) -> None:
        try:
            self._session_id = session_info.get("session_id")
            self._track_id = session_info.get("track_id")
            self._car_id = session_info.get("car_id")
            self._track_name = session_info.get("track_name", "")
            self._car_name = session_info.get("car_name", "")
            self._reset_lap_state()
            self._valid_laps_done = 0
            self._zone_history = {}
            self._pending_corrections = {}
            self.reload_profile()
            self._schedule_refresh()
        except Exception as exc:
            self._set_status("Coaching unavailable")
            print(f"[Coach] on_session_started error: {exc}")

    def on_session_ended(self) -> None:
        try:
            self._session_id = None
            if self._refresh_timer:
                self._refresh_timer.cancel()
                self._refresh_timer = None
            with self._profile_lock:
                self._profile = None
            self._set_status("Session ended")
        except Exception as exc:
            print(f"[Coach] on_session_ended error: {exc}")

    def on_live_sample(self, sample: dict) -> None:
        try:
            if not self._enabled:
                return
            with self._profile_lock:
                profile = self._profile
            if profile is None:
                return

            self._current_lap = sample.get("lap_number", self._current_lap)
            lap_dist = float(sample.get("lap_dist_pct", 0.0) or 0.0)
            speed_kph = float(sample.get("speed_kph", 0.0) or 0.0)

            lookahead = _calc_lookahead(speed_kph, profile.track_length_m)
            self._check_for_cues(lap_dist, lookahead, profile)
            self._update_observations(lap_dist, sample, profile)
        except Exception as exc:
            print(f"[Coach] on_live_sample error: {exc}")

    def on_lap_completed(
        self,
        lap_number: int,
        lap_time_s: float | None = None,
        valid: bool = True,
    ) -> None:
        del lap_time_s
        try:
            if valid:
                self._valid_laps_done += 1

            with self._profile_lock:
                profile = self._profile

            self._finalize_lap_observations(valid)

            if (
                valid
                and profile is not None
                and self._valid_laps_done >= COACHING_CORRECTION_START_LAP
            ):
                self._generate_corrections(profile)

            if self._session_id and valid:
                self._post_feedback(lap_number)

            self._reset_lap_state()
            self._current_lap = lap_number + 1
        except Exception as exc:
            print(f"[Coach] on_lap_completed error: {exc}")

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self._set_status("Disabled")

    def set_voice_enabled(self, enabled: bool) -> None:
        self._voice_enabled = enabled

    def set_overlay_enabled(self, enabled: bool) -> None:
        self._overlay_enabled = enabled

    def reload_profile(self) -> None:
        threading.Thread(target=self._fetch_profile, daemon=True).start()

    def get_current_state(self) -> dict:
        with self._profile_lock:
            profile = self._profile
        return {
            "enabled": self._enabled,
            "has_profile": profile is not None,
            "zones": len(profile.zones) if profile else 0,
            "status": self._status,
            "valid_laps": self._valid_laps_done,
            "pending_corrections": len(self._pending_corrections),
        }

    def _check_for_cues(
        self,
        lap_dist: float,
        lookahead: float,
        profile: CoachingProfile,
    ) -> None:
        if COACHING_MAX_ACTIVE_MESSAGES <= 0:
            return

        now = time.time()
        if now - self._last_text_time < COACHING_MIN_SECONDS_BETWEEN_TEXT:
            return

        ahead_end = lap_dist + lookahead
        sorted_zones = sorted(profile.zones, key=lambda zone: zone.priority, reverse=True)

        for zone in sorted_zones:
            if not zone.enabled or zone.zone_id in self._lap_cues_fired:
                continue
            if lap_dist <= zone.lap_dist_callout <= ahead_end:
                cue = self._build_cue(zone)
                if cue and cue.text:
                    self._fire_cue(cue)
                    self._lap_cues_fired.add(zone.zone_id)
                    break

    def _build_cue(self, zone: CoachingZone) -> Optional[CoachingCue]:
        if (
            self._valid_laps_done >= COACHING_CORRECTION_START_LAP
            and zone.zone_id in self._pending_corrections
        ):
            return _make_correction_cue(zone, self._pending_corrections[zone.zone_id])
        return _make_generic_cue(zone)

    def _fire_cue(self, cue: CoachingCue) -> None:
        self._last_text_time = time.time()
        try:
            self.on_cue(cue)
        except Exception as exc:
            print(f"[Coach] on_cue callback error: {exc}")

    def _update_observations(
        self,
        lap_dist: float,
        sample: dict,
        profile: CoachingProfile,
    ) -> None:
        for zone in profile.zones:
            if not zone.enabled:
                continue

            in_zone = zone.lap_dist_start <= lap_dist <= zone.lap_dist_end
            if not in_zone:
                continue

            observation = self._active_obs.get(zone.zone_id)
            if observation is None:
                observation = LiveZoneObservation(
                    zone_id=zone.zone_id,
                    lap_number=self._current_lap,
                    entry_speed_kph=sample.get("speed_kph"),
                    entry_gear=sample.get("gear"),
                )
                self._active_obs[zone.zone_id] = observation

            observation.samples += 1
            speed = sample.get("speed_kph")
            brake = float(sample.get("brake", 0.0) or 0.0)
            throttle = float(sample.get("throttle", 0.0) or 0.0)

            if observation.min_speed_kph is None or (
                speed is not None and speed < observation.min_speed_kph
            ):
                observation.min_speed_kph = speed

            if observation.brake_peak_pct is None or brake > observation.brake_peak_pct:
                observation.brake_peak_pct = brake

            if observation.brake_start_dist is None and brake > 0.1:
                observation.brake_start_dist = lap_dist

            if (
                observation.throttle_reapply_dist is None
                and throttle > 0.1
                and observation.brake_start_dist is not None
            ):
                observation.throttle_reapply_dist = lap_dist

            observation.exit_speed_kph = speed

    def _finalize_lap_observations(self, valid: bool) -> None:
        if not valid:
            self._active_obs.clear()
            return

        for zone_id, observation in self._active_obs.items():
            if observation.samples < _MIN_ZONE_SAMPLES:
                continue
            history = self._zone_history.setdefault(zone_id, [])
            history.append(observation)
            if len(history) > 5:
                del history[:-5]

    def _generate_corrections(self, profile: CoachingProfile) -> None:
        new_corrections: dict[str, str] = {}
        for zone in profile.zones:
            if not zone.enabled:
                continue
            history = self._zone_history.get(zone.zone_id, [])
            if len(history) < _CORRECTION_HISTORY_LAPS:
                continue
            correction = _analyze_zone(
                zone,
                history[-_CORRECTION_HISTORY_LAPS:],
                profile.track_length_m,
            )
            if correction:
                new_corrections[zone.zone_id] = correction
        self._pending_corrections = new_corrections

    def _post_feedback(self, lap_number: int) -> None:
        if not self._session_id:
            return

        observations: list[dict] = []
        for zone_id, history in self._zone_history.items():
            for observation in history:
                if observation.lap_number != lap_number:
                    continue
                observations.append(
                    {
                        "zone_id": zone_id,
                        "lap_number": observation.lap_number,
                        "entry_speed_kph": observation.entry_speed_kph,
                        "min_speed_kph": observation.min_speed_kph,
                        "exit_speed_kph": observation.exit_speed_kph,
                        "brake_start_dist": observation.brake_start_dist,
                        "brake_peak_pct": observation.brake_peak_pct,
                        "throttle_reapply_dist": observation.throttle_reapply_dist,
                    }
                )

        if not observations:
            return

        threading.Thread(
            target=api_client.post_zone_feedback,
            args=(self._session_id, lap_number, observations),
            daemon=True,
        ).start()

    def _fetch_profile(self) -> None:
        if not self._track_id or not self._car_id:
            return

        try:
            data = api_client.get_active_coaching_profile(
                self._track_id,
                self._car_id,
                track_name=self._track_name,
                car_name=self._car_name,
            )
            if not data:
                with self._profile_lock:
                    self._profile = None
                self._set_status("No reference lap available")
                return

            profile = _parse_profile(
                data,
                self._track_id,
                self._car_id,
                self._track_name,
                self._car_name,
            )
            with self._profile_lock:
                self._profile = profile
            self._set_status(f"Active - {len(profile.zones)} zones")
        except Exception as exc:
            self._set_status("Backend unavailable")
            print(f"[Coach] Profile fetch error: {exc}")

    def _schedule_refresh(self) -> None:
        if not self._running:
            return

        if self._refresh_timer:
            self._refresh_timer.cancel()

        self._refresh_timer = threading.Timer(
            COACHING_REFRESH_SECONDS,
            self._on_refresh_tick,
        )
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def _on_refresh_tick(self) -> None:
        if self._session_id and self._running:
            self._fetch_profile()
            self._schedule_refresh()

    def _reset_lap_state(self) -> None:
        self._lap_cues_fired.clear()
        self._active_obs = {}

    def _set_status(self, status: str) -> None:
        self._status = status
        try:
            self.on_status_change(status)
        except Exception:
            pass


def _calc_lookahead(speed_kph: float, track_length_m: Optional[float]) -> float:
    if track_length_m and track_length_m > 0:
        speed_ms = speed_kph / 3.6
        dist_pct = (speed_ms * 1.5) / track_length_m
    else:
        frac = min(speed_kph / 300.0, 1.0)
        dist_pct = COACHING_LOOKAHEAD_MIN_LAP_DIST + (
            frac * (COACHING_LOOKAHEAD_MAX_LAP_DIST - COACHING_LOOKAHEAD_MIN_LAP_DIST)
        )
    return max(COACHING_LOOKAHEAD_MIN_LAP_DIST, min(COACHING_LOOKAHEAD_MAX_LAP_DIST, dist_pct))


def _make_generic_cue(zone: CoachingZone) -> Optional[CoachingCue]:
    display_text = zone.generic_display_text
    voice_key = zone.generic_voice_key
    state = "neutral"

    if not display_text:
        defaults = _SEGMENT_DEFAULTS.get(zone.segment_type)
        if defaults is None:
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
    lowered = correction.lower()
    if "brake" in lowered and ("earlier" in lowered or "more" in lowered):
        state = "urgent_brake"
    elif "throttle" in lowered or "power" in lowered:
        state = "throttle_go"
    else:
        state = "caution_lift"
    return CoachingCue(text=correction, zone_label=zone.name, state=state)


def _analyze_zone(
    zone: CoachingZone,
    observations: list[LiveZoneObservation],
    track_length_m: Optional[float],
) -> Optional[str]:
    tolerance = COACHING_ZONE_MATCH_TOLERANCE_LAP_DIST

    brake_starts = [
        observation.brake_start_dist
        for observation in observations
        if observation.brake_start_dist is not None
    ]
    if zone.lap_dist_callout is not None and brake_starts:
        avg_start = sum(brake_starts) / len(brake_starts)
        delta = avg_start - zone.lap_dist_callout
        if delta > tolerance:
            if track_length_m:
                return f"Brake around {round(abs(delta) * track_length_m)}m earlier"
            return "Brake a little earlier here"
        if delta < -tolerance:
            if track_length_m:
                return f"Brake about {round(abs(delta) * track_length_m)}m later"
            return "You can brake a little later here"

    peaks = [
        observation.brake_peak_pct
        for observation in observations
        if observation.brake_peak_pct is not None
    ]
    if zone.target_brake_peak_pct is not None and peaks:
        avg_peak = sum(peaks) / len(peaks)
        delta = zone.target_brake_peak_pct - avg_peak
        if delta > 0.08:
            return f"Use about {round(delta * 100)}% more brake here"
        if delta < -0.08:
            return f"Ease off about {round(abs(delta) * 100)}% brake here"

    mins = [
        observation.min_speed_kph
        for observation in observations
        if observation.min_speed_kph is not None
    ]
    if zone.target_speed_min_kph is not None and mins:
        avg_min = sum(mins) / len(mins)
        delta = zone.target_speed_min_kph - avg_min
        if delta > 3:
            return f"Carry {round(delta)} kph more minimum speed"
        if delta < -3:
            return f"Slow down {round(abs(delta))} kph more before apex"

    throttles = [
        observation.throttle_reapply_dist
        for observation in observations
        if observation.throttle_reapply_dist is not None
    ]
    if zone.target_throttle_reapply_pct is not None and throttles:
        avg_reapply = sum(throttles) / len(throttles)
        delta = avg_reapply - zone.target_throttle_reapply_pct
        if delta < -tolerance:
            if track_length_m:
                return f"Get back to throttle about {round(abs(delta) * track_length_m)}m later"
            return "Wait a little longer before getting on throttle"
        if delta > tolerance:
            if track_length_m:
                return f"Get on throttle {round(abs(delta) * track_length_m)}m earlier"
            return "Get on throttle a bit earlier"

    return None


def _parse_profile(
    data: dict,
    fallback_track_id: str,
    fallback_car_id: str,
    fallback_track_name: str,
    fallback_car_name: str,
) -> CoachingProfile:
    reference = data.get("reference")
    if not isinstance(reference, dict):
        reference = {}

    zones_raw = data.get("zones", [])
    zones: list[CoachingZone] = []
    for zone_data in zones_raw:
        if not isinstance(zone_data, dict):
            continue
        zones.append(
            CoachingZone(
                zone_id=str(zone_data.get("zone_id", "")),
                name=str(zone_data.get("name", "")),
                sequence_index=int(zone_data.get("sequence_index", 0) or 0),
                segment_type=str(zone_data.get("segment_type", "")),
                lap_dist_start=float(zone_data.get("lap_dist_start", 0.0) or 0.0),
                lap_dist_callout=float(zone_data.get("lap_dist_callout", 0.0) or 0.0),
                lap_dist_end=float(zone_data.get("lap_dist_end", 0.0) or 0.0),
                target_speed_entry_kph=_as_float(zone_data.get("target_speed_entry_kph")),
                target_speed_min_kph=_as_float(zone_data.get("target_speed_min_kph")),
                target_speed_exit_kph=_as_float(zone_data.get("target_speed_exit_kph")),
                target_brake_initial_pct=_as_float(zone_data.get("target_brake_initial_pct")),
                target_brake_peak_pct=_as_float(zone_data.get("target_brake_peak_pct")),
                target_brake_release_pct=_as_float(zone_data.get("target_brake_release_pct")),
                target_throttle_min_pct=_as_float(zone_data.get("target_throttle_min_pct")),
                target_throttle_reapply_pct=_as_float(zone_data.get("target_throttle_reapply_pct")),
                target_gear=_as_int(zone_data.get("target_gear")),
                priority=int(zone_data.get("priority", 5) or 5),
                generic_display_text=str(zone_data.get("generic_display_text", "")),
                generic_voice_key=str(zone_data.get("generic_voice_key", "")),
                correction_templates=dict(zone_data.get("correction_templates") or {}),
                enabled=bool(zone_data.get("enabled", True)),
            )
        )

    profile_id = (
        reference.get("profile_id")
        or reference.get("reference_id")
        or f"{fallback_track_id}:{fallback_car_id}"
    )
    return CoachingProfile(
        profile_id=str(profile_id),
        track_id=str(reference.get("track_id") or fallback_track_id),
        car_id=str(reference.get("car_id") or fallback_car_id),
        track_name=str(reference.get("track_name") or fallback_track_name),
        car_name=str(reference.get("car_name") or fallback_car_name),
        track_length_m=_as_float(reference.get("track_length_m")),
        zones=zones,
        version=int(reference.get("version", data.get("version", 1)) or 1),
    )


def _as_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
