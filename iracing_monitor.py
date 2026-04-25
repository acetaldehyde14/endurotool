import threading
import time
from datetime import datetime, timezone

import api_client
import irsdk
from config import POLL_INTERVAL_SECONDS, TELEMETRY_BATCH_SIZE, TELEMETRY_HZ

NEARBY_WINDOW = 2  # Positions ahead/behind to include in the nearby cars list.


class IRacingMonitor:
    """
    Two-thread iRacing poller.

    Slow loop (every POLL_INTERVAL_SECONDS):
        - driver_change when the driver in the car changes
        - fuel_update with current fuel level and estimated minutes remaining
        - position_update with overall/class position, lap times, nearby cars, and gaps
        - live telemetry session start/end

    Fast loop (TELEMETRY_HZ times per second):
        - collect telemetry frames
        - detect lap changes
        - upload batches directly through api_client
        - fire telemetry_batch events for the GUI
    """

    def __init__(self, on_event, on_status_change=None, coach_manager=None):
        self.ir = irsdk.IRSDK()
        self.on_event = on_event
        self.on_status_change = on_status_change
        self._coach = coach_manager

        self._running = False
        self._slow_thread = None
        self._fast_thread = None
        self._connected = False
        self._last_driver = None
        self._current_lap = None

        self._telem_buf = []
        self._telem_lock = threading.Lock()

        self._session_id = None
        self._session_active = False
        self._session_starting = False
        self._sub_session_id = None
        self._session_retry_after = 0.0

        self._laps_completed = 0
        self._best_lap_s = None
        self._fuel_per_lap = []
        self._lap_fuel_start = None

    # Lifecycle
    def start(self):
        self._running = True
        self._slow_thread = threading.Thread(target=self._slow_loop, daemon=True)
        self._fast_thread = threading.Thread(target=self._fast_loop, daemon=True)
        self._slow_thread.start()
        self._fast_thread.start()

    def stop(self):
        self._running = False
        self._end_session()

    def is_connected(self) -> bool:
        return self._connected

    def _set_status(self, msg: str):
        print(f"[Monitor] {msg}")
        if self.on_status_change:
            self.on_status_change(msg)

    # Slow loop
    def _slow_loop(self):
        self._set_status("Starting - waiting for iRacing...")
        while self._running:
            try:
                if not self.ir.is_initialized:
                    ok = self.ir.startup()
                    if not ok:
                        if self._connected:
                            self._connected = False
                            self._end_session()
                            self._set_status("iRacing not detected. Waiting...")
                        time.sleep(5)
                        continue

                if not self.ir.is_connected:
                    if self._connected:
                        self._connected = False
                        self._end_session()
                        self._set_status("iRacing closed. Waiting...")
                    time.sleep(5)
                    continue

                if not self._connected:
                    self._connected = True
                    self._set_status("Connected to iRacing")

                self.ir.freeze_var_buffer_latest()
                self._try_start_session()
                self._check_driver()
                self._check_fuel()
                self._check_position()
            except Exception as e:
                self._set_status(f"Error: {e}")
                time.sleep(5)
                try:
                    self.ir.shutdown()
                except Exception:
                    pass

            time.sleep(POLL_INTERVAL_SECONDS)

    # Fast loop
    def _fast_loop(self):
        interval = 1.0 / TELEMETRY_HZ
        while self._running:
            try:
                if self._connected and self.ir.is_connected:
                    self.ir.freeze_var_buffer_latest()
                    self._collect_sample()
            except Exception as e:
                print(f"[Monitor] Fast loop error: {e}")
            time.sleep(interval)

    def _collect_sample(self):
        try:
            speed_raw = self.ir["Speed"] or 0.0
            steering_rad = self.ir["SteeringWheelAngle"] or 0.0
            lap_number = int(self.ir["Lap"] or 0)

            if self._current_lap is None:
                self._current_lap = lap_number
                self._lap_fuel_start = self._f("FuelLevel")

            sample = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "session_time": round(float(self.ir["SessionTime"] or 0.0), 4),
                "lap_number": lap_number,
                "lap_dist_pct": round(float(self.ir["LapDistPct"] or 0.0), 4),
                "speed_kph": round(float(speed_raw) * 3.6, 2),
                "throttle": round(float(self.ir["Throttle"] or 0.0), 4),
                "brake": round(float(self.ir["Brake"] or 0.0), 4),
                "clutch": round(float(self.ir["Clutch"] or 0.0), 4),
                "steering_deg": round(float(steering_rad) * 57.2958, 3),
                "gear": int(self.ir["Gear"] or 0),
                "rpm": int(self.ir["RPM"] or 0),
                "lat_accel": round(float(self.ir["LatAccel"] or 0.0), 4),
                "long_accel": round(float(self.ir["LongAccel"] or 0.0), 4),
                "yaw_rate": round(float(self.ir["YawRate"] or 0.0), 4),
                "yaw": round(float(self.ir["Yaw"] or 0.0), 4),
                "vel_x": round(float(self.ir["VelocityX"] or 0.0), 3),
                "vel_y": round(float(self.ir["VelocityY"] or 0.0), 3),
                "track_temp_c": round(float(self.ir["TrackTempCrew"] or 0.0), 2),
                "air_temp_c": round(float(self.ir["AirTemp"] or 0.0), 2),
                "on_pit_road": self._b("OnPitRoad"),
            }

            if lap_number != self._current_lap and lap_number > 0:
                self._on_lap_change(self._current_lap)
                self._current_lap = lap_number

            if self._coach:
                try:
                    self._coach.on_live_sample(sample)
                except Exception as e:
                    print(f"[Monitor] CoachManager.on_live_sample error: {e}")

            with self._telem_lock:
                self._telem_buf.append(sample)
                if len(self._telem_buf) >= TELEMETRY_BATCH_SIZE:
                    batch = self._telem_buf[:]
                    self._telem_buf.clear()
                    self._upload_batch(batch, self._current_lap)
        except Exception as e:
            print(f"[Monitor] Sample error: {e}")

    def _on_lap_change(self, completed_lap: int):
        """Read lap markers, flush the buffer, and notify the server."""
        try:
            lap_time_s = float(self.ir["LapLastLapTime"] or -1)
            valid = lap_time_s > 0
            incidents = self.ir["PlayerCarMyIncidentCount"]
            fuel_now = self._f("FuelLevel")
        except Exception:
            lap_time_s = None
            valid = False
            incidents = None
            fuel_now = None

        if valid and lap_time_s is not None:
            self._laps_completed += 1
            if self._best_lap_s is None or lap_time_s < self._best_lap_s:
                self._best_lap_s = lap_time_s

        if self._lap_fuel_start is not None and fuel_now is not None:
            used = self._lap_fuel_start - fuel_now
            if used > 0:
                self._fuel_per_lap.append(round(used, 3))
        self._lap_fuel_start = fuel_now

        with self._telem_lock:
            batch = self._telem_buf[:]
            self._telem_buf.clear()

        if batch:
            self._upload_batch(batch, completed_lap)

        if self._coach:
            try:
                self._coach.on_lap_completed(
                    completed_lap,
                    lap_time_s=round(lap_time_s, 3) if valid and lap_time_s else None,
                    valid=valid,
                )
            except Exception as e:
                print(f"[Monitor] CoachManager.on_lap_completed error: {e}")

        if self._session_active and self._session_id:
            session_id = self._session_id
            threading.Thread(
                target=api_client.telemetry_lap_complete,
                args=(session_id, completed_lap),
                kwargs={
                    "lap_time_s": round(lap_time_s, 3) if valid and lap_time_s else None,
                    "valid": valid,
                    "incidents": incidents,
                },
                daemon=True,
            ).start()
            label = "%.3f s" % lap_time_s if valid and lap_time_s else "invalid"
            print(f"[Monitor] Lap {completed_lap} complete - {label}")

    def _upload_batch(self, batch: list, lap: int):
        """Fire-and-forget upload of a telemetry batch."""
        if not self._session_active or not self._session_id or not batch:
            return

        session_id = self._session_id
        count = len(batch)

        def _send():
            api_client.telemetry_batch(session_id, lap or 0, batch, TELEMETRY_HZ)

        threading.Thread(target=_send, daemon=True).start()
        self.on_event("telemetry_batch", {"count": count})

    # Session lifecycle
    def _try_start_session(self):
        """Called from the slow loop. Starts a live telemetry session."""
        if self._session_active or self._session_starting:
            return
        if time.time() < self._session_retry_after:
            return

        try:
            weekend = self.ir["WeekendInfo"] or {}
            driver_info = self.ir["DriverInfo"] or {}
            sub_sid = weekend.get("SubSessionID")

            drivers = driver_info.get("Drivers", [])
            player_idx = self.ir["PlayerCarIdx"]
            driver = next((d for d in drivers if d.get("CarIdx") == player_idx), {})

            from config import load_config

            cfg = load_config()
            driver_name = driver.get("UserName") or cfg.get("username", "")

            session_type_map = {
                "practice": "practice",
                "open practice": "practice",
                "offline testing": "test",
                "lone qualify": "qualify",
                "open qualify": "qualify",
                "race": "race",
                "heat race": "race",
            }
            raw_type = str(self.ir["SessionType"] or "practice").lower().strip()
            session_type = session_type_map.get(raw_type, "practice")
            print(
                f"[Monitor] SessionType from iRacing: '{self.ir['SessionType']}' "
                f"-> mapped to '{session_type}'"
            )

            payload = {
                "sim_session_uid": str(sub_sid or ""),
                "sub_session_id": sub_sid,
                "track_id": weekend.get("TrackName", "unknown"),
                "track_name": weekend.get("TrackDisplayName", "Unknown Track"),
                "car_id": driver.get("CarPath", "unknown"),
                "car_name": driver.get("CarScreenName", "Unknown Car"),
                "session_type": session_type,
                "driver_name": driver_name,
                "iracing_driver_id": driver.get("UserID"),
                "started_at": datetime.now(timezone.utc).isoformat(),
            }

            print(f"[Monitor] Starting session with payload: {payload}")
            self._session_starting = True
            self.on_event("telemetry_session_status", {"status": "starting"})

            def _start():
                try:
                    sid = api_client.telemetry_session_start(payload)
                    if sid:
                        self._session_id = sid
                        self._sub_session_id = sub_sid
                        self._session_active = True
                        self._laps_completed = 0
                        self._best_lap_s = None
                        self._fuel_per_lap = []
                        self._lap_fuel_start = None
                        print(f"[Monitor] Telemetry session started: {sid}")
                        self.on_event(
                            "telemetry_session_status",
                            {
                                "status": "active",
                                "session_id": sid,
                                "track_id": payload["track_id"],
                                "car_id": payload["car_id"],
                            },
                        )
                        if self._coach:
                            try:
                                self._coach.on_session_started(
                                    {
                                        "session_id": sid,
                                        "track_id": payload["track_id"],
                                        "track_name": payload["track_name"],
                                        "car_id": payload["car_id"],
                                        "car_name": payload["car_name"],
                                    }
                                )
                            except Exception as e:
                                print(f"[Monitor] CoachManager.on_session_started error: {e}")
                    else:
                        self._session_retry_after = time.time() + 30
                        print("[Monitor] Session start failed - retrying in 30 s")
                        self.on_event("telemetry_session_status", {"status": "waiting"})
                finally:
                    self._session_starting = False

            threading.Thread(target=_start, daemon=True).start()
        except Exception as e:
            self._session_starting = False
            print(f"[Monitor] Session start error: {e}")

    def _end_session(self):
        """Flush remaining data, send the session summary, and close the session."""
        if not self._session_active or not self._session_id:
            return

        session_id = self._session_id
        self._session_active = False
        self._session_id = None

        if self._coach:
            try:
                self._coach.on_session_ended()
            except Exception as e:
                print(f"[Monitor] CoachManager.on_session_ended error: {e}")

        with self._telem_lock:
            batch = self._telem_buf[:]
            self._telem_buf.clear()

        if batch and self._current_lap is not None:
            api_client.telemetry_batch(session_id, self._current_lap, batch, TELEMETRY_HZ)

        avg_fuel = (
            round(sum(self._fuel_per_lap) / len(self._fuel_per_lap), 3)
            if self._fuel_per_lap
            else None
        )
        summary = {
            "total_laps": self._laps_completed,
            "best_lap_s": round(self._best_lap_s, 3) if self._best_lap_s else None,
            "avg_fuel_per_lap": avg_fuel,
        }

        api_client.telemetry_session_end(session_id, summary)
        best_label = "%.3f s" % self._best_lap_s if self._best_lap_s else "-"
        print(
            f"[Monitor] Session ended - {self._laps_completed} laps, "
            f"best {best_label}, avg fuel {avg_fuel} L/lap"
        )

        self._laps_completed = 0
        self._best_lap_s = None
        self._fuel_per_lap = []
        self._lap_fuel_start = None
        self._current_lap = None
        self._session_starting = False

    # Driver change
    def _check_driver(self):
        try:
            player_idx = self.ir["PlayerCarIdx"]
            if player_idx is None:
                return

            drivers = self.ir["DriverInfo"]["Drivers"] or []
            current = next((d for d in drivers if d.get("CarIdx") == player_idx), None)
            if not current:
                return

            name = current.get("UserName", "").strip()
            user_id = str(current.get("UserID", ""))
            if name and name != self._last_driver:
                self._last_driver = name
                self.on_event(
                    "driver_change",
                    {
                        "driver_name": name,
                        "driver_id": user_id,
                        "session_time": self._f("SessionTime") or 0,
                    },
                )
        except Exception as e:
            print(f"[Monitor] Driver check error: {e}")

    # Fuel
    def _check_fuel(self):
        try:
            fuel = self._f("FuelLevel")
            fuel_pct = self._f("FuelLevelPct")
            use_rate = self._f("FuelUsePerHour")
            if fuel is None:
                return

            mins = round((fuel / use_rate) * 60, 1) if use_rate and use_rate > 0.01 else None
            self.on_event(
                "fuel_update",
                {
                    "fuel_level": fuel,
                    "fuel_pct": fuel_pct or 0,
                    "mins_remaining": mins,
                    "session_time": self._f("SessionTime") or 0,
                },
            )
        except Exception as e:
            print(f"[Monitor] Fuel check error: {e}")

    # Position / standings
    def _check_position(self):
        try:
            player_idx = self.ir["PlayerCarIdx"]
            if player_idx is None:
                return

            pos_arr = self.ir["CarIdxPosition"]
            cls_arr = self.ir["CarIdxClassPosition"]
            f2t_arr = self.ir["CarIdxF2Time"]
            lap_arr = self.ir["CarIdxLap"]
            last_arr = self.ir["CarIdxLastLapTime"]
            best_arr = self.ir["CarIdxBestLapTime"]
            ldp_arr = self.ir["CarIdxLapDistPct"]

            if not pos_arr:
                return

            drivers = self.ir["DriverInfo"]["Drivers"] or []
            driver_map = {driver["CarIdx"]: driver for driver in drivers}

            my_pos = pos_arr[player_idx]
            my_gap = f2t_arr[player_idx] if f2t_arr else None
            my_ldp = ldp_arr[player_idx] if ldp_arr else None

            standings = []
            for idx, pos in enumerate(pos_arr):
                if pos <= 0:
                    continue
                driver = driver_map.get(idx, {})
                gap_raw = f2t_arr[idx] if f2t_arr else None
                standings.append(
                    {
                        "car_idx": idx,
                        "position": pos,
                        "class_pos": cls_arr[idx] if cls_arr else None,
                        "driver_name": driver.get("UserName", f"Car {idx}"),
                        "car_number": driver.get("CarNumber", "?"),
                        "car_class": driver.get("CarClassShortName", ""),
                        "lap": lap_arr[idx] if lap_arr else None,
                        "last_lap": self._fmt_lap(last_arr[idx] if last_arr else None),
                        "best_lap": self._fmt_lap(best_arr[idx] if best_arr else None),
                        "gap_to_leader": self._fmt_gap(gap_raw),
                        "gap_raw": gap_raw,
                        "is_player": idx == player_idx,
                    }
                )

            standings.sort(key=lambda entry: entry["position"])

            nearby = []
            for car in standings:
                delta = car["position"] - my_pos
                if abs(delta) <= NEARBY_WINDOW:
                    gap_to_us = None
                    if car["gap_raw"] is not None and my_gap is not None:
                        raw = car["gap_raw"] - my_gap
                        gap_to_us = f"+{raw:.3f}s" if raw >= 0 else f"{raw:.3f}s"
                    nearby.append({**car, "delta_position": delta, "gap_to_us": gap_to_us})

            self.on_event(
                "position_update",
                {
                    "position": my_pos,
                    "class_position": cls_arr[player_idx] if cls_arr else None,
                    "lap": lap_arr[player_idx] if lap_arr else None,
                    "last_lap": self._fmt_lap(last_arr[player_idx] if last_arr else None),
                    "best_lap": self._fmt_lap(best_arr[player_idx] if best_arr else None),
                    "gap_to_leader": self._fmt_gap(my_gap),
                    "lap_dist_pct": round(my_ldp, 4) if my_ldp is not None else None,
                    "nearby": nearby,
                    "standings": standings,
                    "session_time": self._f("SessionTime") or 0,
                },
            )
        except Exception as e:
            print(f"[Monitor] Position check error: {e}")

    # Helpers
    def _f(self, key) -> float | None:
        try:
            value = self.ir[key]
            return round(float(value), 4) if value is not None else None
        except Exception:
            return None

    def _b(self, key) -> bool | None:
        try:
            value = self.ir[key]
            return bool(value) if value is not None else None
        except Exception:
            return None

    @staticmethod
    def _fmt_lap(seconds) -> str | None:
        if not seconds or seconds <= 0:
            return None
        minutes = int(seconds // 60)
        remainder = seconds % 60
        return f"{minutes}:{remainder:06.3f}"

    @staticmethod
    def _fmt_gap(f2time) -> str | None:
        if f2time is None or f2time < 0:
            return None
        return "Leader" if f2time == 0 else f"+{f2time:.3f}s"
