import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import pystray
from PIL import Image, ImageDraw

import api_client
from config import clear_config
from gui.reference_lap_selector import ReferenceLapSelector


def _make_tray_icon():
    """Create a simple colored circle as the tray icon."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=(233, 69, 96))
    draw.ellipse([14, 14, 50, 50], fill=(15, 52, 96))
    return img


class AppWindow:
    """
    Main status window showing connection state, current driver, and fuel.
    """

    def __init__(self, username: str, monitor, on_logout, coach_manager=None):
        self.username = username
        self.monitor = monitor
        self.on_logout = on_logout
        self._coach = coach_manager
        self._coach_overlay = None
        self._status_text = None
        self._driver_text = None
        self._fuel_text = None
        self._mins_text = None
        self._race_text = None
        self._telem_text = None
        self._coaching_text = None
        self._position_text = None
        self._class_pos_text = None
        self._lap_text = None
        self._last_lap_text = None
        self._best_lap_text = None
        self._gap_text = None
        self._nearby_text = None
        self._ref_selector = None
        self._audio_player = None
        self._on_test_overlay = None
        self._on_test_voice = None
        self._on_test_correction = None
        self._track_id: str | None = None
        self._car_id: str | None = None
        self.root = None
        self._window_visible = False

    def build(self):
        self.root = tk.Tk()
        self._status_text = tk.StringVar(master=self.root, value="Starting up...")
        self._driver_text = tk.StringVar(master=self.root, value="-")
        self._fuel_text = tk.StringVar(master=self.root, value="-")
        self._mins_text = tk.StringVar(master=self.root, value="-")
        self._race_text = tk.StringVar(master=self.root, value="No active race")
        self._telem_text = tk.StringVar(master=self.root, value="Waiting for iRacing...")
        self._coaching_text = tk.StringVar(master=self.root, value="Waiting for session")
        self._position_text = tk.StringVar(master=self.root, value="-")
        self._class_pos_text = tk.StringVar(master=self.root, value="-")
        self._lap_text = tk.StringVar(master=self.root, value="-")
        self._last_lap_text = tk.StringVar(master=self.root, value="-")
        self._best_lap_text = tk.StringVar(master=self.root, value="-")
        self._gap_text = tk.StringVar(master=self.root, value="-")
        self._nearby_text = tk.StringVar(master=self.root, value="No nearby cars yet")
        self.root.title("iRacing Enduro Monitor")
        self.root.geometry("560x680")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")
        self.root.protocol("WM_DELETE_WINDOW", self._hide_window)

        header = tk.Frame(self.root, bg="#0f3460", pady=12)
        header.pack(fill="x")
        tk.Label(
            header,
            text="iRacing Enduro Monitor",
            font=("Segoe UI", 13, "bold"),
            fg="#e94560",
            bg="#0f3460",
        ).pack()
        tk.Label(
            header,
            textvariable=self._race_text,
            font=("Segoe UI", 9),
            fg="#aaaacc",
            bg="#0f3460",
        ).pack()

        top_panel = tk.Frame(self.root, bg="#1a1a2e", padx=16, pady=10)
        top_panel.pack(fill="x")
        top_panel.columnconfigure(0, weight=1)

        self._ref_selector = ReferenceLapSelector(
            top_panel,
            get_context=lambda: (self._track_id, self._car_id),
            on_activated=self._on_reference_activated,
        )
        self._ref_selector.grid(row=0, column=0, sticky="ew")
        self._style_ref_selector()
        self.update_coaching_status("Reference lap selector loaded")
        print("[UI] Reference lap selector loaded into AppWindow")

        body = tk.Frame(self.root, bg="#1a1a2e", padx=24, pady=16)
        body.pack(fill="both", expand=True)

        self._row(body, "Status", self._status_text, 0)
        self._row(body, "Current Driver", self._driver_text, 1)
        self._row(body, "Fuel Level", self._fuel_text, 2)
        self._row(body, "Fuel Remaining", self._mins_text, 3)
        self._row(body, "Telemetry", self._telem_text, 4)
        self._row(body, "Coaching", self._coaching_text, 5)
        self._row(body, "Position", self._position_text, 6)
        self._row(body, "Class Pos", self._class_pos_text, 7)
        self._row(body, "Lap", self._lap_text, 8)
        self._row(body, "Last Lap", self._last_lap_text, 9)
        self._row(body, "Best Lap", self._best_lap_text, 10)
        self._row(body, "Gap", self._gap_text, 11)

        tk.Label(
            body,
            text=f"Logged in as: {self.username}",
            font=("Segoe UI", 8),
            fg="#666688",
            bg="#1a1a2e",
        ).grid(row=12, column=0, columnspan=2, pady=(12, 0), sticky="w")

        nearby_frame = tk.Frame(body, bg="#1a1a2e", pady=8)
        nearby_frame.grid(row=13, column=0, columnspan=2, sticky="ew")
        nearby_frame.columnconfigure(0, weight=1)
        tk.Label(
            nearby_frame,
            text="Nearby Cars",
            font=("Segoe UI", 8, "bold"),
            fg="#aaaacc",
            bg="#1a1a2e",
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            nearby_frame,
            textvariable=self._nearby_text,
            justify="left",
            wraplength=470,
            font=("Segoe UI", 8),
            fg="#cfd3ea",
            bg="#1a1a2e",
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(4, 0))

        coach_frame = tk.Frame(self.root, bg="#1a1a2e", padx=24, pady=4)
        coach_frame.pack(fill="x")
        tk.Label(
            coach_frame,
            text="Coaching:",
            font=("Segoe UI", 8),
            fg="#888899",
            bg="#1a1a2e",
        ).pack(side="left")
        tk.Button(
            coach_frame,
            text="On/Off",
            font=("Segoe UI", 8),
            bg="#333355",
            fg="white",
            relief="flat",
            cursor="hand2",
            command=self._toggle_coaching,
        ).pack(side="left", padx=(6, 4))
        tk.Button(
            coach_frame,
            text="Overlay",
            font=("Segoe UI", 8),
            bg="#333355",
            fg="white",
            relief="flat",
            cursor="hand2",
            command=self._toggle_overlay,
        ).pack(side="left", padx=4)
        tk.Button(
            coach_frame,
            text="Voice",
            font=("Segoe UI", 8),
            bg="#333355",
            fg="white",
            relief="flat",
            cursor="hand2",
            command=self._toggle_voice,
        ).pack(side="left", padx=4)
        tk.Button(
            coach_frame,
            text="Test Overlay",
            font=("Segoe UI", 8),
            bg="#333355",
            fg="white",
            relief="flat",
            cursor="hand2",
            command=self._run_overlay_test,
        ).pack(side="left", padx=(12, 4))
        tk.Button(
            coach_frame,
            text="Test Voice",
            font=("Segoe UI", 8),
            bg="#333355",
            fg="white",
            relief="flat",
            cursor="hand2",
            command=self._run_voice_test,
        ).pack(side="left", padx=4)
        tk.Button(
            coach_frame,
            text="Test Correction",
            font=("Segoe UI", 8),
            bg="#333355",
            fg="white",
            relief="flat",
            cursor="hand2",
            command=self._run_correction_test,
        ).pack(side="left", padx=4)

        btn_frame = tk.Frame(self.root, bg="#1a1a2e", padx=24, pady=8)
        btn_frame.pack(fill="x")
        tk.Button(
            btn_frame,
            text="Logout",
            font=("Segoe UI", 9),
            bg="#333355",
            fg="white",
            relief="flat",
            cursor="hand2",
            command=self._do_logout,
        ).pack(side="right")

        self._poll_status()

    def _row(self, parent, label, var, row_idx):
        tk.Label(
            parent,
            text=label + ":",
            font=("Segoe UI", 9),
            fg="#aaaacc",
            bg="#1a1a2e",
            anchor="w",
        ).grid(row=row_idx, column=0, sticky="w", pady=4)
        tk.Label(
            parent,
            textvariable=var,
            font=("Segoe UI", 10, "bold"),
            fg="white",
            bg="#1a1a2e",
            anchor="w",
        ).grid(row=row_idx, column=1, sticky="w", pady=4, padx=(12, 0))

    def update_status(self, msg: str):
        if self.root:
            self.root.after(0, lambda: self._status_text.set(msg))

    def update_fuel(self, data: dict):
        fuel = data.get("fuel_level")
        mins = data.get("mins_remaining")
        if self.root and fuel is not None:
            self.root.after(0, lambda: self._fuel_text.set(f"{fuel:.2f} L"))
            self.root.after(
                0,
                lambda: self._mins_text.set(f"~{int(mins)} min" if mins else "calculating..."),
            )

    def update_driver(self, name: str):
        if self.root:
            self.root.after(0, lambda: self._driver_text.set(name))

    def update_telemetry(self, count: int):
        if self.root:
            self.root.after(0, lambda: self._telem_text.set(f"Active - {count} samples/batch"))

    def update_session_status(self, status: str):
        labels = {
            "starting": "Starting session...",
            "active": "Session active",
            "waiting": "Waiting for active race session",
            "failed": "Session start failed - retrying",
        }
        text = labels.get(status, status)
        if self.root:
            self.root.after(0, lambda: self._telem_text.set(text))

    def update_coaching_status(self, status: str):
        if self.root and self._coaching_text:
            self.root.after(0, lambda: self._coaching_text.set(status))

    def update_position(self, data: dict):
        if not self.root:
            return

        position = data.get("position")
        class_position = data.get("class_position")
        lap = data.get("lap")
        last_lap = data.get("last_lap") or "-"
        best_lap = data.get("best_lap") or "-"
        gap_to_leader = data.get("gap_to_leader") or "-"
        nearby = data.get("nearby") or []

        nearby_lines = []
        for car in nearby[:4]:
            delta = car.get("delta_position")
            marker = "You" if car.get("is_player") else car.get("driver_name", "Unknown")
            pos = car.get("position", "?")
            gap = car.get("gap_to_us") or "-"
            if car.get("is_player"):
                nearby_lines.append(f"P{pos}: {marker}")
            else:
                relation = f"{delta:+d}" if isinstance(delta, int) else "?"
                nearby_lines.append(f"P{pos} ({relation}): {marker} [{gap}]")
        nearby_text = "\n".join(nearby_lines) if nearby_lines else "No nearby cars yet"

        self.root.after(0, lambda: self._position_text.set(f"P{position}" if position else "-"))
        self.root.after(
            0,
            lambda: self._class_pos_text.set(
                f"P{class_position}" if class_position else "-"
            ),
        )
        self.root.after(0, lambda: self._lap_text.set(str(lap) if lap is not None else "-"))
        self.root.after(0, lambda: self._last_lap_text.set(last_lap))
        self.root.after(0, lambda: self._best_lap_text.set(best_lap))
        self.root.after(0, lambda: self._gap_text.set(gap_to_leader))
        self.root.after(0, lambda: self._nearby_text.set(nearby_text))

    def update_session_context(self, track_id: str, car_id: str):
        self._track_id = track_id
        self._car_id = car_id
        if self.root and self._ref_selector:
            self.root.after(0, lambda: self._ref_selector.set_context(track_id, car_id))

    def _style_ref_selector(self):
        if not self._ref_selector:
            return
        try:
            style = ttk.Style(self.root)
            style.configure(
                "Dark.TLabelframe",
                background="#1a1a2e",
                bordercolor="#252a35",
                relief="solid",
            )
            style.configure(
                "Dark.TLabelframe.Label",
                background="#1a1a2e",
                foreground="#aaaacc",
                font=("Segoe UI", 8, "bold"),
            )
            self._ref_selector.configure(style="Dark.TLabelframe")
        except Exception:
            pass

    def _on_reference_activated(self, lap=None):
        if self._coach:
            threading.Thread(target=self._coach.reload_profile, daemon=True).start()
        if self._coach_overlay and lap:
            try:
                from coaching_models import CoachingCue
                from gui.reference_lap_selector import _fmt_time

                lap_time = _fmt_time(lap.get("lap_time_s"))
                track = lap.get("track_name") or lap.get("track") or "Unknown track"
                car = lap.get("car_name") or lap.get("car") or "Unknown car"
                self._coach_overlay.show_cue(
                    CoachingCue(
                        text="Reference lap selected",
                        subtitle=f"{lap_time} | {track}",
                        zone_label=car,
                        state="neutral",
                        timing="Loaded",
                        upcoming="First lap guidance active",
                    ),
                    duration_ms=4500,
                )
            except Exception as exc:
                print(f"[UI] Reference overlay confirmation failed: {exc}")

    def set_coach_overlay(self, overlay):
        self._coach_overlay = overlay

    def set_audio_player(self, audio_player):
        self._audio_player = audio_player

    def set_test_actions(
        self,
        on_test_overlay=None,
        on_test_voice=None,
        on_test_correction=None,
    ):
        self._on_test_overlay = on_test_overlay
        self._on_test_voice = on_test_voice
        self._on_test_correction = on_test_correction

    def _toggle_coaching(self):
        if not self._coach:
            return
        new_state = not self._coach._enabled
        self._coach.set_enabled(new_state)
        label = "Active" if new_state else "Disabled"
        if self._coaching_text and self.root:
            self.root.after(0, lambda: self._coaching_text.set(label))

    def _toggle_overlay(self):
        if not self._coach:
            return
        new_state = not self._coach._overlay_enabled
        self._coach.set_overlay_enabled(new_state)
        if self._coach_overlay:
            self._coach_overlay.set_enabled(new_state)

    def _toggle_voice(self):
        if not self._coach:
            return
        new_state = not self._coach._voice_enabled
        self._coach.set_voice_enabled(new_state)
        if self._audio_player:
            self._audio_player.set_enabled(new_state)
        self.update_coaching_status(
            "Voice enabled" if new_state else "Voice disabled"
        )

    def _run_overlay_test(self):
        if self._on_test_overlay:
            self._on_test_overlay()
        else:
            self.update_coaching_status("Overlay test unavailable")

    def _run_voice_test(self):
        if self._on_test_voice:
            self._on_test_voice()
        else:
            self.update_coaching_status("Voice test unavailable")

    def _run_correction_test(self):
        if self._on_test_correction:
            self._on_test_correction()
        else:
            self.update_coaching_status("Correction test unavailable")

    def _poll_status(self):
        def fetch():
            status = api_client.get_status()
            if status and self.root:
                race = status.get("active_race")
                driver = status.get("current_driver")
                fuel = status.get("last_fuel")
                self.root.after(
                    0,
                    lambda: self._race_text.set(race["name"] if race else "No active race"),
                )
                if driver:
                    self.root.after(0, lambda: self._driver_text.set(driver))
                if fuel:
                    self.update_fuel(fuel)

        threading.Thread(target=fetch, daemon=True).start()
        if self.root:
            self.root.after(15000, self._poll_status)

    def _hide_window(self):
        self.monitor.stop()
        if hasattr(self, "icon"):
            self.icon.stop()
        self.root.destroy()
        os._exit(0)

    def show_window(self):
        if self.root:
            self.root.deiconify()
            self.root.lift()
            self._window_visible = True

    def _do_logout(self):
        if messagebox.askyesno("Logout", "Log out and stop monitoring?"):
            clear_config()
            self.on_logout()

    def run_tray(self):
        """Run the system tray icon. Call from a background thread."""
        icon_image = _make_tray_icon()

        def on_open(icon, item):
            del icon, item
            self.show_window()

        def on_quit(icon, item):
            del item
            self.monitor.stop()
            icon.stop()
            if self.root:
                self.root.after(0, self.root.destroy)
            os._exit(0)

        menu = pystray.Menu(
            pystray.MenuItem("Open Dashboard", on_open, default=True),
            pystray.MenuItem("Quit", on_quit),
        )
        self.icon = pystray.Icon(
            "iRacing Enduro",
            icon_image,
            "iRacing Enduro Monitor",
            menu,
        )

        if self.root:
            self.root.after(500, self.show_window)

        self.icon.run()
