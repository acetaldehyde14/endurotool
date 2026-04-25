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
_CORRECTION_CONTEXT = "there"


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
        self._active_zones: dict[str, CoachingZone] = {}
        self._zone_history: dict[str, list[LiveZoneObservation]] = {}
        self._startup_fired = False

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
            self._startup_fired = False
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
            with self._profile_lock:
                profile = self._profile
            if profile is None:
                return

            self._current_lap = sample.get("lap_number", self._current_lap)
            lap_dist = float(sample.get("lap_dist_pct", 0.0) or 0.0)
            speed_kph = float(sample.get("speed_kph", 0.0) or 0.0)

            self._maybe_fire_startup(sample, profile)
            if not self._enabled:
                return

            lookahead = _calc_lookahead(speed_kph, profile.track_length_m)
            self._check_for_cues(lap_dist, lookahead, profile, sample)
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
        sample: dict,
    ) -> None:
        if COACHING_MAX_ACTIVE_MESSAGES <= 0:
            return

        now = time.time()
        cooldown_active = now - self._last_text_time < COACHING_MIN_SECONDS_BETWEEN_TEXT

        ahead_end = lap_dist + lookahead
        sorted_zones = sorted(profile.zones, key=lambda zone: zone.priority, reverse=True)

        for zone in sorted_zones:
            if not zone.enabled or zone.zone_id in self._lap_cues_fired:
                continue
            if lap_dist <= zone.lap_dist_callout <= ahead_end:
                cue = self._build_cue(zone, profile, sample, lap_dist)
                if cue and cue.text:
                    if cooldown_active and cue.state not in {"urgent_brake", "startup", "info"}:
                        continue
                    self._fire_cue(cue)
                    self._lap_cues_fired.add(zone.zone_id)
                    break

    def _build_cue(
        self,
        zone: CoachingZone,
        profile: CoachingProfile,
        sample: dict,
        lap_dist: float,
    ) -> Optional[CoachingCue]:
        upcoming = _next_zone_label(profile.zones, zone, lap_dist)
        if (
            self._valid_laps_done >= COACHING_CORRECTION_START_LAP
            and zone.zone_id in self._pending_corrections
        ):
            return _make_correction_cue(
                zone,
                self._pending_corrections[zone.zone_id],
                sample,
                upcoming,
            )
        return _make_generic_cue(zone, sample, upcoming)

    def _fire_cue(self, cue: CoachingCue) -> None:
        if cue.state not in {"urgent_brake", "startup", "info"}:
            self._last_text_time = time.time()
        try:
            self.on_cue(cue)
        except Exception as exc:
            print(f"[Coach] on_cue callback error: {exc}")

    def _maybe_fire_startup(self, sample: dict, profile: CoachingProfile) -> None:
        if self._startup_fired or profile is None:
            return
        if _is_in_pit_lane(sample):
            return

        self._enabled = True
        sequence = profile.startup_sequence or ["coaching_active"]
        self._startup_fired = True
        self._set_status("Coaching active for this track")
        self._fire_cue(
            CoachingCue(
                text="Coaching active for this track",
                display_text="Coaching active for this track",
                subtitle=profile.track_name,
                zone_label=profile.car_name,
                sequence=sequence,
                voice_key=sequence[0] if sequence else "coaching_active",
                state="info",
            )
        )

    def _update_observations(
        self,
        lap_dist: float,
        sample: dict,
        profile: CoachingProfile,
    ) -> None:
        inside_zone_ids: set[str] = set()

        for zone in profile.zones:
            if not zone.enabled:
                continue

            in_zone = zone.lap_dist_start <= lap_dist <= zone.lap_dist_end
            if not in_zone:
                continue
            inside_zone_ids.add(zone.zone_id)

            observation = self._active_obs.get(zone.zone_id)
            if observation is None:
                observation = LiveZoneObservation(
                    zone_id=zone.zone_id,
                    lap_number=self._current_lap,
                    entry_speed_kph=sample.get("speed_kph"),
                    entry_gear=sample.get("gear"),
                )
                self._active_obs[zone.zone_id] = observation
                self._active_zones[zone.zone_id] = zone

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
                observation.brake_release_dist is None
                and observation.brake_start_dist is not None
                and observation.last_brake_pct > 0.1
                and brake <= 0.05
            ):
                observation.brake_release_dist = lap_dist

            if (
                observation.throttle_reapply_dist is None
                and throttle > 0.1
                and observation.brake_start_dist is not None
            ):
                observation.throttle_reapply_dist = lap_dist

            observation.exit_speed_kph = speed
            observation.last_brake_pct = brake

        for zone_id in list(self._active_obs):
            if zone_id in inside_zone_ids:
                continue
            observation = self._active_obs.pop(zone_id)
            zone = self._active_zones.pop(zone_id, None)
            if zone is not None:
                self._finalize_zone_exit(zone, observation, profile)

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
        self._active_zones = {}

    def _finalize_zone_exit(
        self,
        zone: CoachingZone,
        observation: LiveZoneObservation,
        profile: CoachingProfile,
    ) -> None:
        if observation.samples >= _MIN_ZONE_SAMPLES:
            history = self._zone_history.setdefault(zone.zone_id, [])
            history.append(observation)
            if len(history) > 5:
                del history[:-5]

        cue = _make_immediate_correction_cue(zone, observation, profile.track_length_m)
        if cue:
            self._fire_cue(cue)

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


def _make_generic_cue(
    zone: CoachingZone,
    sample: dict | None = None,
    upcoming: str = "",
) -> Optional[CoachingCue]:
    display_text = zone.generic_display_text
    voice_key = zone.generic_voice_key
    state = "neutral"

    if not display_text:
        defaults = _SEGMENT_DEFAULTS.get(zone.segment_type)
        if defaults is not None:
            display_text, state, default_voice = defaults
            if not voice_key:
                voice_key = default_voice
        else:
            display_text, state = _infer_first_lap_cue(zone)

    return CoachingCue(
        text=display_text,
        display_text=display_text,
        subtitle=_zone_subtitle(zone),
        zone_label=zone.name,
        voice_key=voice_key,
        sequence=_generic_sequence(zone),
        state=state,
        gear=zone.target_gear,
        brake=_brake_instruction(zone, sample),
        throttle=_throttle_instruction(zone, sample),
        timing=_timing_instruction(zone),
        upcoming=upcoming,
    )


def _infer_first_lap_cue(zone: CoachingZone) -> tuple[str, str]:
    if zone.target_brake_peak_pct is not None or zone.target_brake_initial_pct is not None:
        return "Brake here", "urgent_brake"
    if zone.target_throttle_min_pct is not None and zone.target_throttle_min_pct < 0.75:
        return "Lift here", "caution_lift"
    if zone.target_throttle_reapply_pct is not None:
        return "Throttle on exit", "throttle_go"
    if zone.target_gear is not None:
        return f"Use gear {zone.target_gear}", "neutral"
    if zone.target_speed_min_kph is not None:
        return "Hit the apex speed", "neutral"
    return "Reference marker", "neutral"


def _generic_sequence(zone: CoachingZone) -> list[str]:
    if zone.segment_type == "brake_zone":
        return ["reference_brake_now_at_the_marker", "here"]
    if zone.segment_type == "light_brake":
        return ["reference_light_brake_here", "here"]
    if zone.segment_type == "lift_zone":
        return ["reference_small_lift_before_turn_in", "nextcorner"]
    if zone.segment_type == "throttle_pickup":
        return ["reference_back_to_throttle_on_exit", "here"]
    if zone.segment_type == "wait_rotate":
        return ["reference_wait_before_throttle_pickup", "thiscorner"]
    if zone.segment_type == "exit":
        return ["reference_begin_to_feed_in_throttle", "here"]
    if zone.target_brake_peak_pct is not None or zone.target_brake_initial_pct is not None:
        return ["reference_brake_now_at_the_marker", "here"]
    if zone.target_throttle_reapply_pct is not None:
        return ["reference_back_to_throttle_on_exit", "here"]
    if zone.target_throttle_min_pct is not None and zone.target_throttle_min_pct < 0.75:
        return ["reference_small_lift_before_turn_in", "nextcorner"]
    return []


def _make_correction_cue(
    zone: CoachingZone,
    correction: str,
    sample: dict | None = None,
    upcoming: str = "",
) -> CoachingCue:
    lowered = correction.lower()
    if "brake" in lowered and ("earlier" in lowered or "more" in lowered):
        state = "urgent_brake"
    elif "throttle" in lowered or "power" in lowered:
        state = "throttle_go"
    else:
        state = "caution_lift"
    return CoachingCue(
        text=correction,
        display_text=correction,
        subtitle=_zone_subtitle(zone),
        zone_label=zone.name,
        state=state,
        gear=zone.target_gear,
        brake=_brake_instruction(zone, sample),
        throttle=_throttle_instruction(zone, sample),
        timing=_timing_instruction(zone),
        upcoming=upcoming,
    )


def _zone_subtitle(zone: CoachingZone) -> str:
    parts: list[str] = []
    if zone.target_speed_min_kph is not None:
        parts.append(f"min {round(zone.target_speed_min_kph)} kph")
    if zone.target_speed_exit_kph is not None:
        parts.append(f"exit {round(zone.target_speed_exit_kph)} kph")
    return " | ".join(parts)


def _brake_instruction(zone: CoachingZone, sample: dict | None) -> str:
    target = zone.target_brake_peak_pct
    if target is None:
        if zone.segment_type in {"brake_zone", "light_brake"}:
            return "Brake reference"
        if zone.segment_type in {"lift_zone", "wait_rotate"}:
            return "No heavy brake"
        return ""

    target_pct = round(target * 100)
    if not sample:
        return f"Brake {target_pct}%"

    current = float(sample.get("brake", 0.0) or 0.0)
    delta = target - current
    if abs(delta) < 0.08:
        return f"Brake {target_pct}%"
    if delta > 0:
        return f"Brake +{round(delta * 100)}%"
    return f"Brake -{round(abs(delta) * 100)}%"


def _throttle_instruction(zone: CoachingZone, sample: dict | None) -> str:
    target_min = zone.target_throttle_min_pct
    target_reapply = zone.target_throttle_reapply_pct

    if target_reapply is not None:
        return "Throttle earlier" if zone.segment_type == "throttle_pickup" else "Throttle reference"

    if target_min is not None:
        target_pct = round(target_min * 100)
        if not sample:
            return f"Throttle {target_pct}%"
        current = float(sample.get("throttle", 0.0) or 0.0)
        delta = target_min - current
        if abs(delta) < 0.08:
            return f"Throttle {target_pct}%"
        if delta > 0:
            return f"Throttle +{round(delta * 100)}%"
        return f"Lift {round(abs(delta) * 100)}%"

    if zone.segment_type == "lift_zone":
        return "Lift less"
    if zone.segment_type == "wait_rotate":
        return "Wait, then power"
    if zone.segment_type in {"throttle_pickup", "exit"}:
        return "Throttle earlier"
    return ""


def _timing_instruction(zone: CoachingZone) -> str:
    if zone.segment_type in {"brake_zone", "light_brake"}:
        return "Brake here"
    if zone.segment_type == "lift_zone":
        return "Lift here"
    if zone.segment_type == "apex":
        return "Apex"
    if zone.segment_type in {"throttle_pickup", "exit"}:
        return "Power now"
    return ""


def _next_zone_label(
    zones: list[CoachingZone],
    current: CoachingZone,
    lap_dist: float,
) -> str:
    enabled = [zone for zone in zones if zone.enabled]
    if not enabled:
        return ""

    upcoming = [
        zone
        for zone in enabled
        if zone.zone_id != current.zone_id and zone.lap_dist_callout > lap_dist
    ]
    if not upcoming:
        upcoming = [zone for zone in enabled if zone.zone_id != current.zone_id]
    if not upcoming:
        return ""

    next_zone = min(upcoming, key=lambda zone: zone.lap_dist_callout)
    label = next_zone.name or _segment_label(next_zone.segment_type)
    action = _segment_label(next_zone.segment_type)
    if label == action:
        return f"Next: {label}"
    return f"Next: {label} - {action}"


def _segment_label(segment_type: str) -> str:
    labels = {
        "brake_zone": "brake",
        "light_brake": "light brake",
        "lift_zone": "lift",
        "throttle_pickup": "throttle",
        "wait_rotate": "wait",
        "apex": "apex",
        "exit": "exit",
    }
    return labels.get(segment_type, segment_type.replace("_", " "))


def _make_immediate_correction_cue(
    zone: CoachingZone,
    observation: LiveZoneObservation,
    track_length_m: Optional[float],
) -> Optional[CoachingCue]:
    tolerance = COACHING_ZONE_MATCH_TOLERANCE_LAP_DIST

    if zone.lap_dist_callout is not None and observation.brake_start_dist is not None:
        delta = observation.brake_start_dist - zone.lap_dist_callout
        if abs(delta) > tolerance:
            metres = _delta_metres(delta, track_length_m)
            if delta > 0:
                display = f"Brake {metres}m earlier {_CORRECTION_CONTEXT}"
                sequence = [f"correction_brake_{metres}m_earlier", _CORRECTION_CONTEXT]
            else:
                display = f"Brake {metres}m later {_CORRECTION_CONTEXT}"
                sequence = [f"correction_brake_{metres}m_later", _CORRECTION_CONTEXT]
            return _correction_cue(zone, display, sequence)

    if zone.target_brake_peak_pct is not None and observation.brake_peak_pct is not None:
        delta_pct = round((zone.target_brake_peak_pct - observation.brake_peak_pct) * 100)
        if abs(delta_pct) >= 10:
            if delta_pct > 0:
                display = f"Use {abs(delta_pct)}% more peak brake {_CORRECTION_CONTEXT}"
                sequence = [
                    f"correction_add_about_{abs(delta_pct)}_percent_more_brake_here",
                    _CORRECTION_CONTEXT,
                ]
            else:
                display = f"Use {abs(delta_pct)}% less peak brake {_CORRECTION_CONTEXT}"
                sequence = [
                    f"correction_reduce_about_{abs(delta_pct)}_percent_brake_here",
                    _CORRECTION_CONTEXT,
                ]
            return _correction_cue(zone, display, sequence)

    if (
        zone.target_throttle_reapply_pct is not None
        and observation.throttle_reapply_dist is not None
    ):
        delta = observation.throttle_reapply_dist - zone.target_throttle_reapply_pct
        if abs(delta) > tolerance:
            if delta < 0:
                display = f"Wait longer before throttle {_CORRECTION_CONTEXT}"
                sequence = [
                    "correction_wait_longer_before_throttle_pickup",
                    _CORRECTION_CONTEXT,
                ]
            else:
                display = f"Throttle earlier {_CORRECTION_CONTEXT}"
                sequence = [
                    "correction_back_to_throttle_earlier",
                    _CORRECTION_CONTEXT,
                ]
            return _correction_cue(zone, display, sequence)

    return None


def _correction_cue(
    zone: CoachingZone,
    display: str,
    sequence: list[str],
) -> CoachingCue:
    return CoachingCue(
        text=display,
        display_text=display,
        subtitle=_zone_subtitle(zone),
        zone_label=zone.name,
        sequence=sequence,
        voice_key=sequence[0] if sequence else "",
        state="correction",
        gear=zone.target_gear,
        brake=_brake_instruction(zone, None),
        throttle=_throttle_instruction(zone, None),
        timing="Correction",
    )


def _delta_metres(delta: float, track_length_m: Optional[float]) -> int:
    if track_length_m and track_length_m > 0:
        metres = abs(delta) * track_length_m
    else:
        metres = abs(delta) * 1000
    return max(5, int(round(metres / 5.0) * 5))


def _is_in_pit_lane(sample: dict) -> bool:
    for key in ("on_pit_road", "is_on_pit_road", "pit_road", "in_pits"):
        if key in sample and sample.get(key) is not None:
            return bool(sample.get(key))
    return float(sample.get("speed_kph", 0.0) or 0.0) <= 30.0


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
                return f"Last laps: brake {round(abs(delta) * track_length_m)}m earlier"
            return "Last laps: brake earlier here"
        if delta < -tolerance:
            if track_length_m:
                return f"Last laps: brake {round(abs(delta) * track_length_m)}m later"
            return "Last laps: brake later here"

    peaks = [
        observation.brake_peak_pct
        for observation in observations
        if observation.brake_peak_pct is not None
    ]
    if zone.target_brake_peak_pct is not None and peaks:
        avg_peak = sum(peaks) / len(peaks)
        delta = zone.target_brake_peak_pct - avg_peak
        if delta > 0.08:
            return f"Last laps: brake {round(delta * 100)}% more here"
        if delta < -0.08:
            return f"Last laps: brake {round(abs(delta) * 100)}% less here"

    mins = [
        observation.min_speed_kph
        for observation in observations
        if observation.min_speed_kph is not None
    ]
    if zone.target_speed_min_kph is not None and mins:
        avg_min = sum(mins) / len(mins)
        delta = zone.target_speed_min_kph - avg_min
        if delta > 3:
            return f"Last laps: carry {round(delta)} kph more min speed"
        if delta < -3:
            return f"Last laps: slow {round(abs(delta))} kph more before apex"

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
                return f"Last laps: throttle {round(abs(delta) * track_length_m)}m later"
            return "Last laps: wait longer before throttle"
        if delta > tolerance:
            if track_length_m:
                return f"Last laps: throttle {round(abs(delta) * track_length_m)}m earlier"
            return "Last laps: throttle earlier"

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
    startup_cue = data.get("startup_cue")
    startup_sequence: list[str] = []
    if isinstance(startup_cue, dict):
        raw_sequence = startup_cue.get("sequence", [])
        if isinstance(raw_sequence, list):
            startup_sequence = [str(item) for item in raw_sequence if item]
    if not startup_sequence and isinstance(reference.get("startup_cue"), dict):
        raw_sequence = reference["startup_cue"].get("sequence", [])
        if isinstance(raw_sequence, list):
            startup_sequence = [str(item) for item in raw_sequence if item]

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
        startup_sequence=startup_sequence,
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
