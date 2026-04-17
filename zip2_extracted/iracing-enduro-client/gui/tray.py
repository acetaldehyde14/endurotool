import tkinter as tk
from tkinter import messagebox
import threading
import pystray
from PIL import Image, ImageDraw
import api_client
from config import clear_config


def _make_icon() -> Image.Image:
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=(233, 69, 96))
    draw.ellipse([14, 14, 50, 50], fill=(15, 52, 96))
    return img


class AppWindow:
    """
    Small dashboard window + system tray icon.
    Shows iRacing connection status, current driver, fuel, and race name.
    """

    def __init__(self, username: str, monitor, on_logout):
        self.username  = username
        self.monitor   = monitor
        self.on_logout = on_logout
        self.root      = None
        self.icon      = None

        self._status = tk.StringVar(value="Starting up...")
        self._driver = tk.StringVar(value="—")
        self._fuel   = tk.StringVar(value="—")
        self._mins   = tk.StringVar(value="—")
        self._race   = tk.StringVar(value="No active race")
        self._telem  = tk.StringVar(value="—")

    # ── Build GUI ──────────────────────────────────────────────

    def build(self):
        self.root = tk.Tk()
        self.root.title("iRacing Enduro Monitor")
        self.root.geometry("400x360")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")
        self.root.protocol("WM_DELETE_WINDOW", self._hide)

        # Header
        hdr = tk.Frame(self.root, bg="#0f3460", pady=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🏁  iRacing Enduro Monitor",
                 font=("Segoe UI", 13, "bold"), fg="#e94560", bg="#0f3460").pack()
        tk.Label(hdr, textvariable=self._race,
                 font=("Segoe UI", 9), fg="#aaaacc", bg="#0f3460").pack()

        # Rows
        body = tk.Frame(self.root, bg="#1a1a2e", padx=24, pady=16)
        body.pack(fill="both", expand=True)

        rows = [
            ("Status",         self._status),
            ("Current Driver", self._driver),
            ("Fuel Level",     self._fuel),
            ("Fuel Remaining", self._mins),
            ("Telemetry",      self._telem),
        ]
        for i, (label, var) in enumerate(rows):
            tk.Label(body, text=label + ":", font=("Segoe UI", 9),
                     fg="#aaaacc", bg="#1a1a2e", anchor="w"
                     ).grid(row=i, column=0, sticky="w", pady=3)
            tk.Label(body, textvariable=var, font=("Segoe UI", 10, "bold"),
                     fg="white", bg="#1a1a2e", anchor="w"
                     ).grid(row=i, column=1, sticky="w", pady=3, padx=(12, 0))

        tk.Label(body, text=f"Logged in as: {self.username}",
                 font=("Segoe UI", 8), fg="#666688", bg="#1a1a2e"
                 ).grid(row=len(rows), column=0, columnspan=2,
                        pady=(14, 0), sticky="w")

        # Buttons
        btns = tk.Frame(self.root, bg="#1a1a2e", padx=24, pady=8)
        btns.pack(fill="x")
        tk.Button(btns, text="Logout", font=("Segoe UI", 9),
                  bg="#333355", fg="white", relief="flat", cursor="hand2",
                  command=self._do_logout).pack(side="right")

        self._poll_status()

    # ── Update methods (called from monitor/api threads) ───────

    def update_status(self, msg: str):
        if self.root:
            self.root.after(0, lambda: self._status.set(msg))

    def update_driver(self, name: str):
        if self.root:
            self.root.after(0, lambda: self._driver.set(name))

    def update_fuel(self, data: dict):
        fuel = data.get("fuel_level")
        mins = data.get("mins_remaining")
        if self.root and fuel is not None:
            self.root.after(0, lambda: self._fuel.set(f"{fuel:.2f} L"))
            self.root.after(0, lambda: self._mins.set(
                f"~{int(mins)} min" if mins else "calculating..."
            ))

    def update_telemetry(self, count: int):
        if self.root:
            self.root.after(0, lambda: self._telem.set(f"Uploading ({count} samples/batch)"))

    # ── Status polling ─────────────────────────────────────────

    def _poll_status(self):
        def fetch():
            status = api_client.get_status()
            if status and self.root:
                race   = status.get("active_race")
                driver = status.get("current_driver")
                fuel   = status.get("last_fuel")
                self.root.after(0, lambda: self._race.set(
                    race["name"] if race else "No active race"
                ))
                if driver:
                    self.root.after(0, lambda: self._driver.set(driver))
                if fuel:
                    self.update_fuel(fuel)

        threading.Thread(target=fetch, daemon=True).start()
        if self.root:
            self.root.after(15_000, self._poll_status)

    # ── Window show/hide ───────────────────────────────────────

    def _hide(self):
        if self.root:
            self.root.withdraw()

    def show(self):
        if self.root:
            self.root.deiconify()
            self.root.lift()

    # ── Logout / quit ──────────────────────────────────────────

    def _do_logout(self):
        if messagebox.askyesno("Logout", "Log out and stop monitoring?"):
            clear_config()
            self.on_logout()

    # ── System tray ────────────────────────────────────────────

    def run_tray(self):
        """Blocking — runs the system tray icon. Call from a daemon thread."""
        def on_open(icon, item):
            self.show()

        def on_quit(icon, item):
            self.monitor.stop()
            icon.stop()
            if self.root:
                self.root.after(0, self.root.destroy)

        self.icon = pystray.Icon(
            "iRacing Enduro",
            _make_icon(),
            "iRacing Enduro Monitor",
            pystray.Menu(
                pystray.MenuItem("Open Dashboard", on_open, default=True),
                pystray.MenuItem("Quit", on_quit),
            ),
        )
        if self.root:
            self.root.after(500, self.show)
        self.icon.run()
