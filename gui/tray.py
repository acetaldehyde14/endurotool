import os
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import pystray
from PIL import Image, ImageDraw
import api_client
from config import clear_config, load_config


def _make_tray_icon():
    """Create a simple coloured circle as the tray icon."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=(233, 69, 96))   # red-ish circle
    draw.ellipse([14, 14, 50, 50], fill=(15, 52, 96))  # dark blue inner
    return img


class AppWindow:
    """
    Main status window — shows connection state, current driver, fuel.
    Lives in the system tray; click tray icon to show/hide.
    """

    def __init__(self, username: str, monitor, on_logout):
        self.username = username
        self.monitor = monitor
        self.on_logout = on_logout
        self._status_text = tk.StringVar(value="Starting up...")
        self._driver_text = tk.StringVar(value="—")
        self._fuel_text = tk.StringVar(value="—")
        self._mins_text = tk.StringVar(value="—")
        self._race_text = tk.StringVar(value="No active race")
        self._telem_text = tk.StringVar(value="—")
        self.root = None
        self._window_visible = False

    def build(self):
        self.root = tk.Tk()
        self.root.title("iRacing Enduro Monitor")
        self.root.geometry("400x360")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")
        self.root.protocol("WM_DELETE_WINDOW", self._hide_window)

        # Header
        header = tk.Frame(self.root, bg="#0f3460", pady=12)
        header.pack(fill="x")
        tk.Label(
            header, text="🏁  iRacing Enduro Monitor",
            font=("Segoe UI", 13, "bold"), fg="#e94560", bg="#0f3460",
        ).pack()
        tk.Label(
            header, textvariable=self._race_text,
            font=("Segoe UI", 9), fg="#aaaacc", bg="#0f3460",
        ).pack()

        # Status rows
        body = tk.Frame(self.root, bg="#1a1a2e", padx=24, pady=16)
        body.pack(fill="both", expand=True)

        self._row(body, "Status",         self._status_text, 0)
        self._row(body, "Current Driver", self._driver_text, 1)
        self._row(body, "Fuel Level",     self._fuel_text,   2)
        self._row(body, "Fuel Remaining", self._mins_text,   3)
        self._row(body, "Telemetry",      self._telem_text,  4)

        tk.Label(body, text=f"Logged in as: {self.username}",
                 font=("Segoe UI", 8), fg="#666688", bg="#1a1a2e").grid(
            row=5, column=0, columnspan=2, pady=(16, 0), sticky="w"
        )

        # Buttons
        btn_frame = tk.Frame(self.root, bg="#1a1a2e", padx=24, pady=8)
        btn_frame.pack(fill="x")
        tk.Button(
            btn_frame, text="Logout", font=("Segoe UI", 9),
            bg="#333355", fg="white", relief="flat", cursor="hand2",
            command=self._do_logout,
        ).pack(side="right")

        # Start polling server status
        self._poll_status()

    def _row(self, parent, label, var, row_idx):
        tk.Label(
            parent, text=label + ":",
            font=("Segoe UI", 9), fg="#aaaacc", bg="#1a1a2e", anchor="w",
        ).grid(row=row_idx, column=0, sticky="w", pady=4)
        tk.Label(
            parent, textvariable=var,
            font=("Segoe UI", 10, "bold"), fg="white", bg="#1a1a2e", anchor="w",
        ).grid(row=row_idx, column=1, sticky="w", pady=4, padx=(12, 0))

    def update_status(self, msg: str):
        if self.root:
            self.root.after(0, lambda: self._status_text.set(msg))

    def update_fuel(self, data: dict):
        fuel = data.get("fuel_level")
        mins = data.get("mins_remaining")
        if self.root and fuel is not None:
            self.root.after(0, lambda: self._fuel_text.set(f"{fuel:.2f} L"))
            self.root.after(0, lambda: self._mins_text.set(
                f"~{int(mins)} min" if mins else "calculating..."
            ))

    def update_driver(self, name: str):
        if self.root:
            self.root.after(0, lambda: self._driver_text.set(name))

    def update_telemetry(self, count: int):
        if self.root:
            self.root.after(0, lambda: self._telem_text.set(f"Active — {count} samples/batch"))

    def update_session_status(self, status: str):
        labels = {
            "starting": "Starting session...",
            "active":   "Session active",
            "failed":   "Session start failed — retrying",
        }
        text = labels.get(status, status)
        if self.root:
            self.root.after(0, lambda: self._telem_text.set(text))

    def _poll_status(self):
        """Fetch server status every 15 seconds to sync race info."""
        def fetch():
            status = api_client.get_status()
            if status and self.root:
                race = status.get("active_race")
                driver = status.get("current_driver")
                fuel = status.get("last_fuel")
                self.root.after(0, lambda: self._race_text.set(
                    race["name"] if race else "No active race"
                ))
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
        """Run the system tray icon (blocking). Call from main thread."""
        icon_image = _make_tray_icon()

        def on_open(icon, item):
            self.show_window()

        def on_quit(icon, item):
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
            "iRacing Enduro", icon_image, "iRacing Enduro Monitor", menu
        )

        # Show main window initially
        if self.root:
            self.root.after(500, self.show_window)

        self.icon.run()
