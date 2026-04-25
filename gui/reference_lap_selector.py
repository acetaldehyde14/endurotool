"""
ReferenceLapSelector - Tkinter widget for choosing a coaching reference lap.
"""

import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Optional

import api_client


def _fmt_time(seconds) -> str:
    if seconds is None:
        return "No time"
    seconds = float(seconds)
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes}:{remainder:06.3f}"


def _normalise_lap(lap: dict) -> dict:
    normalised = dict(lap)
    if "lap_id" not in normalised and "id" in normalised:
        normalised["lap_id"] = normalised["id"]
    if "lap_time_s" not in normalised and "lap_time" in normalised:
        normalised["lap_time_s"] = normalised["lap_time"]
    return normalised


def _fmt_option(lap: dict) -> str:
    active = " [ACTIVE]" if lap.get("is_active_reference") else ""
    lap_time = _fmt_time(lap.get("lap_time_s"))
    track = lap.get("track_name") or lap.get("track") or "Unknown track"
    car = lap.get("car_name") or lap.get("car") or "Unknown car"
    lap_num = lap.get("lap_number", "?")
    session_id = lap.get("session_id", "?")
    return f"{lap_time} | Lap {lap_num} | {track} | {car} | Session {session_id}{active}"


def _fmt_selected(lap: dict) -> str:
    lap_time = _fmt_time(lap.get("lap_time_s"))
    track = lap.get("track_name") or lap.get("track") or "Unknown track"
    car = lap.get("car_name") or lap.get("car") or "Unknown car"
    lap_num = lap.get("lap_number", "?")
    return f"Using: {lap_time} | Lap {lap_num} | {track} | {car}"


class ReferenceLapSelector(ttk.LabelFrame):
    """Drop-in Tkinter frame for selecting the active reference lap."""

    def __init__(
        self,
        parent,
        get_context: Callable[[], tuple],
        on_activated: Optional[Callable] = None,
        **kwargs,
    ):
        super().__init__(parent, text="Coach Lap Library", **kwargs)
        self._get_context = get_context
        self._on_activated = on_activated
        self._laps: list[dict] = []
        self._selected_id: Optional[int] = None
        self._active_lap: Optional[dict] = None
        self._build()
        self.after(250, self._load_all_laps)

    # Public
    def set_context(self, track_id: Optional[str], car_id: Optional[str]):
        if track_id and car_id:
            self._reload(track_id, car_id)
        else:
            self._load_all_laps()

    def refresh(self):
        track_id, car_id = self._get_context()
        if track_id and car_id:
            self._reload(track_id, car_id)
        else:
            self._load_all_laps()

    # Build
    def _build(self):
        self.configure(style="Dark.TLabelframe", padding=(12, 6))

        self._status_var = tk.StringVar(value="Loading backend lap library...")
        self._active_var = tk.StringVar(value="Using: no reference lap selected")
        tk.Label(
            self,
            textvariable=self._status_var,
            font=("Segoe UI", 8),
            fg="#888899",
            bg="#1a1a2e",
            anchor="w",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        tk.Label(
            self,
            textvariable=self._active_var,
            font=("Segoe UI", 9, "bold"),
            fg="#ffffff",
            bg="#1a1a2e",
            anchor="w",
            wraplength=510,
        ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 6))

        self._combo_var = tk.StringVar()
        self._combo = ttk.Combobox(
            self,
            textvariable=self._combo_var,
            state="readonly",
            width=52,
            font=("Segoe UI", 9),
        )
        self._combo.grid(row=2, column=0, sticky="ew", padx=(0, 6))
        self._combo.bind("<<ComboboxSelected>>", self._on_combo_select)

        self._btn_activate = tk.Button(
            self,
            text="Use Lap",
            font=("Segoe UI", 9),
            bg="#0f3460",
            fg="white",
            relief="flat",
            cursor="hand2",
            activebackground="#1a4a80",
            activeforeground="white",
            disabledforeground="#555577",
            state="disabled",
            command=self._on_activate,
        )
        self._btn_activate.grid(row=2, column=1, padx=(0, 4))

        tk.Button(
            self,
            text="Current Car",
            font=("Segoe UI", 9),
            bg="#1a1a2e",
            fg="#aaaacc",
            relief="flat",
            cursor="hand2",
            command=self.refresh,
        ).grid(row=2, column=2)

        tk.Button(
            self,
            text="Reload Library",
            font=("Segoe UI", 9),
            bg="#1a1a2e",
            fg="#aaaacc",
            relief="flat",
            cursor="hand2",
            command=self._load_all_laps,
        ).grid(row=3, column=1, sticky="ew", pady=(6, 0), padx=(0, 4))

        self._btn_upload = tk.Button(
            self,
            text="Upload to Library",
            font=("Segoe UI", 9),
            bg="#0f3460",
            fg="white",
            relief="flat",
            cursor="hand2",
            activebackground="#1a4a80",
            activeforeground="white",
            command=self._choose_upload,
        )
        self._btn_upload.grid(row=3, column=2, sticky="ew", pady=(6, 0))

        self.columnconfigure(0, weight=1)
        print("[RefLapSelector] Reference lap selector loaded")

    # Network
    def _reload(self, track_id: str, car_id: str):
        self._set_status("Loading...")
        self._btn_activate.config(state="disabled")
        print(f"[RefLapSelector] Loading reference laps for {track_id} / {car_id}")
        threading.Thread(
            target=self._fetch_candidates,
            args=(track_id, car_id),
            daemon=True,
        ).start()

    def _fetch_candidates(self, track_id: str, car_id: str):
        data = api_client.get_reference_lap_candidates(track_id, car_id)
        if data is None:
            print(f"[RefLapSelector] No reference lap response for {track_id} / {car_id}")
            self._ui(self._handle_no_data)
            return

        laps = data.get("laps", [])
        print(f"[RefLapSelector] Found {len(laps)} reference lap(s) for {track_id} / {car_id}")
        self._ui(lambda: self._populate(laps, track_id, car_id))

    def _load_all_laps(self):
        self._set_status("Loading all backend laps...")
        self._btn_activate.config(state="disabled")
        threading.Thread(target=self._fetch_all_laps, daemon=True).start()

    def _fetch_all_laps(self):
        data = api_client.get_all_laps()
        if data is None:
            self._ui(self._handle_no_data)
            return

        laps = data.get("laps", [])
        print(f"[RefLapSelector] Found {len(laps)} total lap(s) in backend")
        self._ui(lambda: self._populate(laps, None, None))

    def _populate(
        self,
        laps: list,
        track_id: Optional[str],
        car_id: Optional[str],
    ):
        self._laps = [_normalise_lap(lap) for lap in laps]
        if not laps:
            if track_id and car_id:
                print(f"[RefLapSelector] No reference laps available for {track_id} / {car_id}")
                self._set_status(f"No reference laps for {track_id} / {car_id}")
            else:
                print("[RefLapSelector] No laps available in backend")
                self._set_status("No backend laps found")
            self._combo.set("")
            self._combo["values"] = ()
            self._btn_activate.config(state="disabled")
            return

        options = [_fmt_option(lap) for lap in self._laps]
        self._combo["values"] = options

        active_idx = next(
            (i for i, lap in enumerate(self._laps) if lap.get("is_active_reference")),
            0,
        )
        self._combo.current(active_idx)
        self._selected_id = self._laps[active_idx].get("lap_id")

        active_lap = self._laps[active_idx]
        if active_lap.get("is_active_reference"):
            self._active_lap = active_lap
            self._active_var.set(_fmt_selected(active_lap))
        print(
            "[RefLapSelector] Selected reference lap "
            f"id={self._selected_id} time={_fmt_time(active_lap.get('lap_time_s'))}"
        )
        if track_id and car_id:
            self._set_status(f"{track_id} / {car_id} - {len(self._laps)} lap(s) available")
        else:
            self._set_status(f"{len(self._laps)} backend lap(s) available")
        self._btn_activate.config(
            state="normal" if not active_lap.get("is_active_reference") else "disabled"
        )

    def _handle_no_data(self):
        print("[RefLapSelector] Backend unavailable or no laps found")
        self._set_status("Backend unavailable or no laps found")
        self._combo.set("")
        self._combo["values"] = ()
        self._btn_activate.config(state="disabled")

    # Interaction
    def _on_combo_select(self, _event=None):
        idx = self._combo.current()
        if idx < 0 or idx >= len(self._laps):
            return

        lap = self._laps[idx]
        self._selected_id = lap.get("lap_id")
        already_active = lap.get("is_active_reference", False)
        self._set_status(_fmt_selected(lap).replace("Using:", "Selected:", 1))
        print(
            "[RefLapSelector] Combobox selected "
            f"id={self._selected_id} time={_fmt_time(lap.get('lap_time_s'))}"
        )
        self._btn_activate.config(state="disabled" if already_active else "normal")

    def _on_activate(self):
        if self._selected_id is None:
            return
        self._btn_activate.config(state="disabled")
        self._set_status("Activating...")
        lap_id = self._selected_id
        threading.Thread(target=self._do_activate, args=(lap_id,), daemon=True).start()

    def _choose_upload(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Upload iRacing telemetry lap",
            filetypes=[
                ("iRacing telemetry", "*.ibt *.blap *.olap"),
                ("IBT files", "*.ibt"),
                ("BLAP files", "*.blap"),
                ("OLAP files", "*.olap"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        self._btn_upload.config(state="disabled")
        self._set_status("Uploading telemetry file...")
        threading.Thread(target=self._do_upload, args=(path,), daemon=True).start()

    def _do_upload(self, path: str):
        result = api_client.upload_telemetry_file(path)
        if result is None:
            self._ui(lambda: self._set_status("Upload failed - check connection/file"))
            self._ui(lambda: self._btn_upload.config(state="normal"))
            return

        session = result.get("session", {})
        lap_count = session.get("lap_count", 0)
        track = session.get("track", "Unknown track")
        car = session.get("car", "Unknown car")
        self._ui(
            lambda: self._set_status(
                f"Uploaded {lap_count} lap(s): {track} / {car}"
            )
        )
        self._ui(lambda: self._btn_upload.config(state="normal"))
        self._ui(self._load_all_laps)

    def _do_activate(self, lap_id: int):
        ok = api_client.activate_reference_lap(lap_id)
        if ok:
            self._ui(self._after_activate)
        else:
            self._ui(lambda: self._set_status("Activation failed - check connection"))
            self._ui(lambda: self._btn_activate.config(state="normal"))

    def _after_activate(self):
        active_lap = next(
            (lap for lap in self._laps if lap.get("lap_id") == self._selected_id),
            None,
        )
        self._set_status("Activated. Reloading...")
        for lap in self._laps:
            lap["is_active_reference"] = lap.get("lap_id") == self._selected_id
        if active_lap:
            active_lap["is_active_reference"] = True
            self._active_lap = active_lap
            self._active_var.set(_fmt_selected(active_lap))
        options = [_fmt_option(lap) for lap in self._laps]
        self._combo["values"] = options
        idx = self._combo.current()
        if 0 <= idx < len(self._laps):
            self._combo.set(options[idx])
        self._btn_activate.config(state="disabled")

        if self._on_activated:
            try:
                self._on_activated(active_lap)
            except Exception as e:
                print(f"[RefLapSelector] on_activated callback error: {e}")

        track_id, car_id = self._get_context()
        if track_id and car_id:
            self._reload(track_id, car_id)

    # Utilities
    def _set_status(self, msg: str):
        self._status_var.set(msg)

    def _ui(self, fn: Callable):
        try:
            self.after(0, fn)
        except Exception:
            pass
