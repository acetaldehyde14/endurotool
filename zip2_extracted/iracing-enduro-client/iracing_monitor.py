import threading
import time
import irsdk
from config import POLL_INTERVAL_SECONDS, TELEMETRY_HZ, TELEMETRY_FLUSH_SECS

NEARBY_WINDOW = 2   # positions ahead/behind to include in the nearby cars list


class IRacingMonitor:
    """
    Two-thread iRacing poller.

    Slow loop  (every POLL_INTERVAL_SECONDS):
        - driver_change   fired when the driver in the car changes
        - fuel_update     current fuel level + estimated minutes remaining
        - position_update overall/class position, lap times, nearby cars + gaps

    Fast loop  (TELEMETRY_HZ times per second, default 10 Hz):
        - telemetry_batch  buffered samples flushed every TELEMETRY_FLUSH_SECS
          Each sample contains: speed, throttle, brake, steering, gear, RPM,
          lap dist %, tyre temps × 4 corners, tyre wear × 4 corners, G-forces.
    """

    def __init__(self, on_event, on_status_change=None):
        self.ir = irsdk.IRSDK()
        self.on_event = on_event
        self.on_status_change = on_status_change

        self._running       = False
        self._slow_thread   = None
        self._fast_thread   = None
        self._connected     = False
        self._last_driver   = None
        self._current_lap   = None

        # Telemetry buffer
        self._telem_buf  = []
        self._telem_lock = threading.Lock()
        self._last_flush = time.time()

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self):
        self._running     = True
        self._slow_thread = threading.Thread(target=self._slow_loop, daemon=True)
        self._fast_thread = threading.Thread(target=self._fast_loop, daemon=True)
        self._slow_thread.start()
        self._fast_thread.start()

    def stop(self):
        self._running = False

    def is_connected(self) -> bool:
        return self._connected

    def _set_status(self, msg: str):
        print(f"[Monitor] {msg}")
        if self.on_status_change:
            self.on_status_change(msg)

    # ── Slow loop ──────────────────────────────────────────────

    def _slow_loop(self):
        self._set_status("Starting — waiting for iRacing...")
        while self._running:
            try:
                if not self.ir.is_initialized:
                    ok = self.ir.startup()
                    if not ok:
                        if self._connected:
                            self._connected = False
                            self._set_status("iRacing not detected. Waiting...")
                        time.sleep(5)
                        continue

                if not self.ir.is_connected:
                    if self._connected:
                        self._connected = False
                        self._set_status("iRacing closed. Waiting...")
                    time.sleep(5)
                    continue

                if not self._connected:
                    self._connected = True
                    self._set_status("Connected to iRacing ✓")

                self.ir.freeze_var_buffer_latest()
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

    # ── Fast loop ──────────────────────────────────────────────

    def _fast_loop(self):
        interval = 1.0 / TELEMETRY_HZ
        while self._running:
            try:
                if self._connected and self.ir.is_connected:
                    self.ir.freeze_var_buffer_latest()
                    self._collect_sample()
                    self._maybe_flush()
            except Exception as e:
                print(f"[Monitor] Fast loop error: {e}")
            time.sleep(interval)

    def _collect_sample(self):
        try:
            sample = {
                # Timestamp (iRacing session time in seconds)
                "t":     self._f("SessionTime"),

                # Motion
                "spd":   self._f("Speed"),               # m/s → display as kph
                "thr":   self._f("Throttle"),            # 0.0–1.0
                "brk":   self._f("Brake"),               # 0.0–1.0
                "steer": self._f("SteeringWheelAngle"),  # radians
                "gear":  self._raw("Gear"),              # -1 R, 0 N, 1-n
                "rpm":   self._f("RPM"),

                # Track position
                "ldp":   self._f("LapDistPct"),          # 0.0–1.0
                "lap":   self._raw("Lap"),

                # Tyre temperatures — [left, centre, right of tyre face]
                "tfl":   self._arr("LFtempCL", "LFtempCM", "LFtempCR"),
                "tfr":   self._arr("RFtempCL", "RFtempCM", "RFtempCR"),
                "trl":   self._arr("LRtempCL", "LRtempCM", "LRtempCR"),
                "trr":   self._arr("RRtempCL", "RRtempCM", "RRtempCR"),

                # Tyre wear (0.0 = new, 1.0 = fully worn)
                "wfl":   self._f("LFwearM"),
                "wfr":   self._f("RFwearM"),
                "wrl":   self._f("LRwearM"),
                "wrr":   self._f("RRwearM"),

                # G-forces (m/s²)
                "glat":  self._f("LatAccel"),
                "glon":  self._f("LongAccel"),
                "gver":  self._f("VertAccel"),
            }

            if sample["lap"] is not None:
                self._current_lap = sample["lap"]

            with self._telem_lock:
                self._telem_buf.append(sample)

        except Exception as e:
            print(f"[Monitor] Sample error: {e}")

    def _maybe_flush(self):
        now = time.time()
        if now - self._last_flush < TELEMETRY_FLUSH_SECS:
            return

        with self._telem_lock:
            if not self._telem_buf:
                self._last_flush = now
                return
            batch = self._telem_buf[:]
            self._telem_buf.clear()

        self._last_flush = now
        self.on_event("telemetry_batch", {
            "lap":     self._current_lap,
            "samples": batch,
            "count":   len(batch),
        })

    # ── Helpers ────────────────────────────────────────────────

    def _f(self, key) -> float | None:
        """Read a float channel, return rounded value or None."""
        try:
            v = self.ir[key]
            return round(float(v), 4) if v is not None else None
        except Exception:
            return None

    def _raw(self, key):
        """Read a non-float channel (int, etc.)."""
        try:
            return self.ir[key]
        except Exception:
            return None

    def _arr(self, *keys) -> list | None:
        """Read multiple channels into a list. Returns None if all null."""
        vals = [self._f(k) for k in keys]
        return vals if any(v is not None for v in vals) else None

    # ── Driver change ──────────────────────────────────────────

    def _check_driver(self):
        try:
            player_idx = self.ir["PlayerCarIdx"]
            if player_idx is None:
                return
            drivers = self.ir["DriverInfo"]["Drivers"] or []
            current = next((d for d in drivers if d.get("CarIdx") == player_idx), None)
            if not current:
                return
            name    = current.get("UserName", "").strip()
            user_id = str(current.get("UserID", ""))
            if name and name != self._last_driver:
                self._last_driver = name
                self.on_event("driver_change", {
                    "driver_name":  name,
                    "driver_id":    user_id,
                    "session_time": self._f("SessionTime") or 0,
                })
        except Exception as e:
            print(f"[Monitor] Driver check error: {e}")

    # ── Fuel ──────────────────────────────────────────────────

    def _check_fuel(self):
        try:
            fuel      = self._f("FuelLevel")
            fuel_pct  = self._f("FuelLevelPct")
            use_rate  = self._f("FuelUsePerHour")  # L/hour
            if fuel is None:
                return
            mins = round((fuel / use_rate) * 60, 1) if use_rate and use_rate > 0.01 else None
            self.on_event("fuel_update", {
                "fuel_level":     fuel,
                "fuel_pct":       fuel_pct or 0,
                "mins_remaining": mins,
                "session_time":   self._f("SessionTime") or 0,
            })
        except Exception as e:
            print(f"[Monitor] Fuel check error: {e}")

    # ── Position / standings ───────────────────────────────────

    def _check_position(self):
        try:
            player_idx = self.ir["PlayerCarIdx"]
            if player_idx is None:
                return

            pos_arr   = self.ir["CarIdxPosition"]
            cls_arr   = self.ir["CarIdxClassPosition"]
            f2t_arr   = self.ir["CarIdxF2Time"]
            lap_arr   = self.ir["CarIdxLap"]
            last_arr  = self.ir["CarIdxLastLapTime"]
            best_arr  = self.ir["CarIdxBestLapTime"]
            ldp_arr   = self.ir["CarIdxLapDistPct"]

            if not pos_arr:
                return

            drivers    = self.ir["DriverInfo"]["Drivers"] or []
            driver_map = {d["CarIdx"]: d for d in drivers}

            my_pos   = pos_arr[player_idx]
            my_gap   = f2t_arr[player_idx] if f2t_arr else None
            my_ldp   = ldp_arr[player_idx] if ldp_arr else None

            standings = []
            for idx, pos in enumerate(pos_arr):
                if pos <= 0:
                    continue
                d       = driver_map.get(idx, {})
                gap_raw = f2t_arr[idx] if f2t_arr else None
                standings.append({
                    "car_idx":       idx,
                    "position":      pos,
                    "class_pos":     cls_arr[idx] if cls_arr else None,
                    "driver_name":   d.get("UserName", f"Car {idx}"),
                    "car_number":    d.get("CarNumber", "?"),
                    "car_class":     d.get("CarClassShortName", ""),
                    "lap":           lap_arr[idx] if lap_arr else None,
                    "last_lap":      self._fmt_lap(last_arr[idx] if last_arr else None),
                    "best_lap":      self._fmt_lap(best_arr[idx] if best_arr else None),
                    "gap_to_leader": self._fmt_gap(gap_raw),
                    "gap_raw":       gap_raw,
                    "is_player":     idx == player_idx,
                })

            standings.sort(key=lambda x: x["position"])

            nearby = []
            for car in standings:
                delta = car["position"] - my_pos
                if abs(delta) <= NEARBY_WINDOW:
                    gap_to_us = None
                    if car["gap_raw"] is not None and my_gap is not None:
                        raw = car["gap_raw"] - my_gap
                        gap_to_us = f"+{raw:.3f}s" if raw >= 0 else f"{raw:.3f}s"
                    nearby.append({**car, "delta_position": delta, "gap_to_us": gap_to_us})

            self.on_event("position_update", {
                "position":       my_pos,
                "class_position": cls_arr[player_idx] if cls_arr else None,
                "lap":            lap_arr[player_idx] if lap_arr else None,
                "last_lap":       self._fmt_lap(last_arr[player_idx] if last_arr else None),
                "best_lap":       self._fmt_lap(best_arr[player_idx] if best_arr else None),
                "gap_to_leader":  self._fmt_gap(my_gap),
                "lap_dist_pct":   round(my_ldp, 4) if my_ldp is not None else None,
                "nearby":         nearby,
                "standings":      standings,
                "session_time":   self._f("SessionTime") or 0,
            })

        except Exception as e:
            print(f"[Monitor] Position check error: {e}")

    @staticmethod
    def _fmt_lap(seconds) -> str | None:
        if not seconds or seconds <= 0:
            return None
        m = int(seconds // 60)
        s = seconds % 60
        return f"{m}:{s:06.3f}"

    @staticmethod
    def _fmt_gap(f2time) -> str | None:
        if f2time is None or f2time < 0:
            return None
        return "Leader" if f2time == 0 else f"+{f2time:.3f}s"
