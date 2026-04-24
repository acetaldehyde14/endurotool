"""
ReferenceLapSelector — Tkinter widget for choosing a coaching reference lap.

Shows a dropdown of the fastest available laps for the current track/car,
marks the currently active one, and lets the user activate a different lap
with a single button press.

Thread safety: all Tk calls run on the main thread via .after().
Network calls happen on daemon threads.
"""

import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

import api_client


# ── Helpers ────────────────────────────────────────────────────────────────

def _fmt_time(seconds) -> str:
    if seconds is None:
        return "No time"
    m   = int(seconds // 60)
    rem = seconds - m * 60
    return f"{m}:{rem:06.3f}"


def _fmt_option(lap: dict) -> str:
    active  = " [ACTIVE]" if lap.get("is_active_reference") else ""
    t       = _fmt_time(lap.get("lap_time_s"))
    driver  = lap.get("driver_name") or "Unknown"
    lap_num = lap.get("lap_number", "?")
    ses_id  = lap.get("session_id", "?")
    return f"{t} | Lap {lap_num} | {driver} | Session {ses_id}{active}"


# ── Widget ─────────────────────────────────────────────────────────────────

class ReferenceLapSelector(ttk.LabelFrame):
    """
    Drop-in Tkinter frame.  Embed it in any container:

        sel = ReferenceLapSelector(
            parent,
            get_context=lambda: (track_id, car_id),   # returns (str, str) or (None, None)
            on_activated=coach_manager.reload_profile, # optional; called after activate
        )
        sel.pack(fill="x", padx=12, pady=6)

    Call sel.set_context(track_id, car_id) when the session changes to
    automatically reload the candidate list.
    """

    def __init__(self, parent,
                 get_context: Callable[[], tuple],
                 on_activated: Optional[Callable] = None,
                 **kwargs):
        super().__init__(parent, text="Reference Lap", **kwargs)
        self._get_context   = get_context       # () -> (track_id, car_id)
        self._on_activated  = on_activated      # called after a successful activate

        self._laps: list[dict] = []             # current candidate list
        self._selected_id: Optional[int] = None # lap_id matching the combobox selection

        self._build()

    # ── Public ──────────────────────────────────────────────────────

    def set_context(self, track_id: Optional[str], car_id: Optional[str]):
        """Called when the session changes. Triggers a background reload."""
        if track_id and car_id:
            self._reload(track_id, car_id)
        else:
            self._set_status("No session")
            self._combo.set("")
            self._combo["values"] = ()
            self._btn_activate.config(state="disabled")

    def refresh(self):
        """Manually re-fetch candidates for the current context."""
        track_id, car_id = self._get_context()
        if track_id and car_id:
            self._reload(track_id, car_id)
        else:
            self._set_status("No track/car detected yet")

    # ── Build ────────────────────────────────────────────────────────

    def _build(self):
        self.configure(
            style="Dark.TLabelframe",
            padding=(12, 6),
        )

        # Status line
        self._status_var = tk.StringVar(value="—")
        tk.Label(
            self, textvariable=self._status_var,
            font=("Segoe UI", 8), fg="#888899", bg="#1a1a2e", anchor="w",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        # Combobox
        self._combo_var = tk.StringVar()
        self._combo = ttk.Combobox(
            self,
            textvariable=self._combo_var,
            state="readonly",
            width=46,
            font=("Segoe UI", 9),
        )
        self._combo.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self._combo.bind("<<ComboboxSelected>>", self._on_combo_select)

        # Activate button
        self._btn_activate = tk.Button(
            self, text="Set Active",
            font=("Segoe UI", 9),
            bg="#0f3460", fg="white", relief="flat", cursor="hand2",
            activebackground="#1a4a80", activeforeground="white",
            disabledforeground="#555577",
            state="disabled",
            command=self._on_activate,
        )
        self._btn_activate.grid(row=1, column=1, padx=(0, 4))

        # Refresh button
        tk.Button(
            self, text="↻",
            font=("Segoe UI", 10),
            bg="#1a1a2e", fg="#aaaacc", relief="flat", cursor="hand2",
            command=self.refresh,
        ).grid(row=1, column=2)

        self.columnconfigure(0, weight=1)

    # ── Network ──────────────────────────────────────────────────────

    def _reload(self, track_id: str, car_id: str):
        self._set_status("Loading…")
        self._btn_activate.config(state="disabled")
        threading.Thread(
            target=self._fetch_candidates,
            args=(track_id, car_id),
            daemon=True,
        ).start()

    def _fetch_candidates(self, track_id: str, car_id: str):
        data = api_client.get_reference_lap_candidates(track_id, car_id)
        if data is None:
            self._ui(self._handle_no_data)
            return
        laps = data.get("laps", [])
        self._ui(lambda: self._populate(laps, track_id, car_id))

    def _populate(self, laps: list, track_id: str, car_id: str):
        self._laps = laps
        if not laps:
            self._set_status(f"No reference laps for {track_id} / {car_id}")
            self._combo.set("")
            self._combo["values"] = ()
            self._btn_activate.config(state="disabled")
            return

        options = [_fmt_option(lap) for lap in laps]
        self._combo["values"] = options

        # Pre-select the active reference if one is marked
        active_idx = next(
            (i for i, lap in enumerate(laps) if lap.get("is_active_reference")),
            0,
        )
        self._combo.current(active_idx)
        self._selected_id = laps[active_idx].get("lap_id")

        active_lap = laps[active_idx]
        self._set_status(
            f"{track_id} / {car_id} — {len(laps)} lap(s) available"
        )
        self._btn_activate.config(
            state="normal" if not active_lap.get("is_active_reference") else "disabled"
        )

    def _handle_no_data(self):
        track_id, car_id = self._get_context()
        self._set_status("Backend unavailable or no laps found")
        self._combo.set("")
        self._combo["values"] = ()
        self._btn_activate.config(state="disabled")

    # ── Interaction ──────────────────────────────────────────────────

    def _on_combo_select(self, _event=None):
        idx = self._combo.current()
        if idx < 0 or idx >= len(self._laps):
            return
        lap = self._laps[idx]
        self._selected_id = lap.get("lap_id")
        already_active = lap.get("is_active_reference", False)
        self._btn_activate.config(state="disabled" if already_active else "normal")

    def _on_activate(self):
        if self._selected_id is None:
            return
        self._btn_activate.config(state="disabled")
        self._set_status("Activating…")
        lap_id = self._selected_id
        threading.Thread(
            target=self._do_activate,
            args=(lap_id,),
            daemon=True,
        ).start()

    def _do_activate(self, lap_id: int):
        ok = api_client.activate_reference_lap(lap_id)
        if ok:
            self._ui(self._after_activate)
        else:
            self._ui(lambda: self._set_status("Activation failed — check connection"))
            self._ui(lambda: self._btn_activate.config(state="normal"))

    def _after_activate(self):
        self._set_status("Activated! Reloading…")
        # Mark the newly active lap in the local list so the UI updates
        # immediately, then re-fetch from the server to confirm.
        for lap in self._laps:
            lap["is_active_reference"] = (lap.get("lap_id") == self._selected_id)
        options = [_fmt_option(lap) for lap in self._laps]
        self._combo["values"] = options
        # Re-render the selected item label
        idx = self._combo.current()
        if 0 <= idx < len(self._laps):
            self._combo.set(options[idx])
        self._btn_activate.config(state="disabled")

        if self._on_activated:
            try:
                self._on_activated()
            except Exception as e:
                print(f"[RefLapSelector] on_activated callback error: {e}")

        # Re-fetch to get the confirmed server state
        track_id, car_id = self._get_context()
        if track_id and car_id:
            self._reload(track_id, car_id)

    # ── Utilities ────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self._status_var.set(msg)

    def _ui(self, fn: Callable):
        """Schedule fn on the Tk main thread."""
        try:
            self.after(0, fn)
        except Exception:
            pass
