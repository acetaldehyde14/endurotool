"""
ReferenceLapSelector - Tkinter widget for choosing a coaching reference lap.
"""

import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

import api_client


def _fmt_time(seconds) -> str:
    if seconds is None:
        return "No time"
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes}:{remainder:06.3f}"


def _fmt_option(lap: dict) -> str:
    active = " [ACTIVE]" if lap.get("is_active_reference") else ""
    lap_time = _fmt_time(lap.get("lap_time_s"))
    driver = lap.get("driver_name") or "Unknown"
    lap_num = lap.get("lap_number", "?")
    session_id = lap.get("session_id", "?")
    return f"{lap_time} | Lap {lap_num} | {driver} | Session {session_id}{active}"


class ReferenceLapSelector(ttk.LabelFrame):
    """Drop-in Tkinter frame for selecting the active reference lap."""

    def __init__(
        self,
        parent,
        get_context: Callable[[], tuple],
        on_activated: Optional[Callable] = None,
        **kwargs,
    ):
        super().__init__(parent, text="Reference Lap", **kwargs)
        self._get_context = get_context
        self._on_activated = on_activated
        self._laps: list[dict] = []
        self._selected_id: Optional[int] = None
        self._build()

    # Public
    def set_context(self, track_id: Optional[str], car_id: Optional[str]):
        if track_id and car_id:
            self._reload(track_id, car_id)
        else:
            self._set_status("No session")
            self._combo.set("")
            self._combo["values"] = ()
            self._btn_activate.config(state="disabled")

    def refresh(self):
        track_id, car_id = self._get_context()
        if track_id and car_id:
            self._reload(track_id, car_id)
        else:
            self._set_status("No track/car detected yet")

    # Build
    def _build(self):
        self.configure(style="Dark.TLabelframe", padding=(12, 6))

        self._status_var = tk.StringVar(value="Reference lap selector loaded")
        tk.Label(
            self,
            textvariable=self._status_var,
            font=("Segoe UI", 8),
            fg="#888899",
            bg="#1a1a2e",
            anchor="w",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        self._combo_var = tk.StringVar()
        self._combo = ttk.Combobox(
            self,
            textvariable=self._combo_var,
            state="readonly",
            width=52,
            font=("Segoe UI", 9),
        )
        self._combo.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self._combo.bind("<<ComboboxSelected>>", self._on_combo_select)

        self._btn_activate = tk.Button(
            self,
            text="Set Active",
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
        self._btn_activate.grid(row=1, column=1, padx=(0, 4))

        tk.Button(
            self,
            text="Refresh",
            font=("Segoe UI", 9),
            bg="#1a1a2e",
            fg="#aaaacc",
            relief="flat",
            cursor="hand2",
            command=self.refresh,
        ).grid(row=1, column=2)

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

    def _populate(self, laps: list, track_id: str, car_id: str):
        self._laps = laps
        if not laps:
            print(f"[RefLapSelector] No reference laps available for {track_id} / {car_id}")
            self._set_status(f"No reference laps for {track_id} / {car_id}")
            self._combo.set("")
            self._combo["values"] = ()
            self._btn_activate.config(state="disabled")
            return

        options = [_fmt_option(lap) for lap in laps]
        self._combo["values"] = options

        active_idx = next(
            (i for i, lap in enumerate(laps) if lap.get("is_active_reference")),
            0,
        )
        self._combo.current(active_idx)
        self._selected_id = laps[active_idx].get("lap_id")

        active_lap = laps[active_idx]
        print(
            "[RefLapSelector] Selected reference lap "
            f"id={self._selected_id} time={_fmt_time(active_lap.get('lap_time_s'))}"
        )
        self._set_status(f"{track_id} / {car_id} - {len(laps)} lap(s) available")
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

    def _do_activate(self, lap_id: int):
        ok = api_client.activate_reference_lap(lap_id)
        if ok:
            self._ui(self._after_activate)
        else:
            self._ui(lambda: self._set_status("Activation failed - check connection"))
            self._ui(lambda: self._btn_activate.config(state="normal"))

    def _after_activate(self):
        self._set_status("Activated. Reloading...")
        for lap in self._laps:
            lap["is_active_reference"] = lap.get("lap_id") == self._selected_id
        options = [_fmt_option(lap) for lap in self._laps]
        self._combo["values"] = options
        idx = self._combo.current()
        if 0 <= idx < len(self._laps):
            self._combo.set(options[idx])
        self._btn_activate.config(state="disabled")

        if self._on_activated:
            try:
                self._on_activated()
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
