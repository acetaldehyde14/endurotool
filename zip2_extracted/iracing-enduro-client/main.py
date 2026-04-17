"""
iRacing Enduro Monitor — Desktop Client
Entry point.

Run directly:  python main.py
As compiled:   iRacingEnduro.exe
"""

import sys
import os
import threading
import tkinter as tk

sys.path.insert(0, os.path.dirname(__file__))

from updater import check_for_updates
from gui.login import show_login_if_needed
from gui.tray import AppWindow
from iracing_monitor import IRacingMonitor
import api_client


def main():
    # Check for updates before anything else
    check_for_updates()

    # Hidden root needed for thread-safe Tk operations during login
    root = tk.Tk()
    root.withdraw()

    app_window: AppWindow | None = None
    monitor:    IRacingMonitor | None = None

    def on_event(event_type: str, data: dict):
        """Fired from monitor threads — update GUI and upload to server."""
        # Update dashboard
        if app_window:
            if event_type == "fuel_update":
                app_window.update_fuel(data)
            elif event_type == "driver_change":
                app_window.update_driver(data.get("driver_name", ""))
            elif event_type == "telemetry_batch":
                app_window.update_telemetry(data.get("count", 0))

        # Upload to server in background (never block the monitor threads)
        threading.Thread(
            target=api_client.post_event,
            args=(event_type, data),
            daemon=True,
        ).start()

    def on_status(msg: str):
        if app_window:
            app_window.update_status(msg)

    def on_ready(username: str):
        nonlocal app_window, monitor

        monitor = IRacingMonitor(on_event=on_event, on_status_change=on_status)
        monitor.start()

        def build():
            nonlocal app_window
            app_window = AppWindow(
                username=username,
                monitor=monitor,
                on_logout=on_logout,
            )
            app_window.build()

            # Tray blocks — run it in a daemon thread
            threading.Thread(target=app_window.run_tray, daemon=True).start()

            # Tk main loop on this thread
            app_window.root.mainloop()

        root.after(0, build)

    def on_logout():
        if monitor:
            monitor.stop()
        # Restart the process to show the login screen again
        os.execl(sys.executable, sys.executable, *sys.argv)

    show_login_if_needed(on_ready)

    try:
        root.mainloop()
    except Exception:
        pass


if __name__ == "__main__":
    main()
