from dataclasses import dataclass, field
from typing import Optional


@dataclass(slots=True)
class CoachingCue:
    text: str
    display_text: str = ""
    subtitle: str = ""
    zone_label: str = ""
    voice_key: str = ""
    sequence: list[str] = field(default_factory=list)
    state: str = "neutral"
    gear: Optional[int] = None
    brake: str = ""
    throttle: str = ""
    timing: str = ""
    upcoming: str = ""


@dataclass(slots=True)
class CoachingZone:
    zone_id: str
    name: str
    sequence_index: int
    segment_type: str
    lap_dist_start: float
    lap_dist_callout: float
    lap_dist_end: float
    target_speed_entry_kph: Optional[float] = None
    target_speed_min_kph: Optional[float] = None
    target_speed_exit_kph: Optional[float] = None
    target_brake_initial_pct: Optional[float] = None
    target_brake_peak_pct: Optional[float] = None
    target_brake_release_pct: Optional[float] = None
    target_throttle_min_pct: Optional[float] = None
    target_throttle_reapply_pct: Optional[float] = None
    target_gear: Optional[int] = None
    priority: int = 5
    generic_display_text: str = ""
    generic_voice_key: str = ""
    correction_templates: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


@dataclass(slots=True)
class CoachingProfile:
    profile_id: str
    track_id: str
    car_id: str
    track_name: str = ""
    car_name: str = ""
    track_length_m: Optional[float] = None
    zones: list[CoachingZone] = field(default_factory=list)
    startup_sequence: list[str] = field(default_factory=list)
    version: int = 1


@dataclass(slots=True)
class LiveZoneObservation:
    zone_id: str
    lap_number: int
    entry_speed_kph: Optional[float] = None
    min_speed_kph: Optional[float] = None
    exit_speed_kph: Optional[float] = None
    brake_start_dist: Optional[float] = None
    brake_peak_pct: Optional[float] = None
    brake_release_dist: Optional[float] = None
    throttle_reapply_dist: Optional[float] = None
    entry_gear: Optional[int] = None
    samples: int = 0
    last_brake_pct: float = 0.0


@dataclass(slots=True)
class VoiceAsset:
    key: str
    url: str
    local_path: str = ""
    cached: bool = False
