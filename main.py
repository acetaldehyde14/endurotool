"""
iRacing Enduro Monitor — Desktop Client
Entry point. Run with: python main.py
Or as compiled exe: iRacingEnduro.exe
"""

import sys
import os
import threading
import tkinter as tk

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

import api_client
from iracing_monitor import IRacingMonitor
from gui.login import show_login_if_needed
from gui.tray import AppWindow
from updater import check_for_updates


def main():
    # We need a Tk root to exist before login window (for thread safety)
    # Hide root during login
    _root = tk.Tk()
    _root.withdraw()

    app_window = None
    monitor = None

    def on_event(event_type: str, data: dict):
        """Called from monitor thread — sends to server and updates GUI."""
        # Update GUI
        if app_window:
            if event_type == "fuel_update":
                app_window.update_fuel(data)
            elif event_type == "driver_change":
                app_window.update_driver(data.get("driver_name", ""))
            elif event_type == "telemetry_batch":
                app_window.update_telemetry(data.get("count", 0))

        # Send to server (non-blocking); telemetry is posted directly by the monitor
        if event_type != "telemetry_batch":
            threading.Thread(
                target=api_client.post_event,
                args=(event_type, data),
                daemon=True,
            ).start()

    def on_iracing_status(msg: str):
        if app_window:
            app_window.update_status(msg)

    def on_ready(username: str):
        nonlocal app_window, monitor

        # Start iRacing monitor
        monitor = IRacingMonitor(
            on_event=on_event,
            on_status_change=on_iracing_status,
        )
        monitor.start()

        # Build app window (must happen on main thread)
        def build_and_run():
            nonlocal app_window
            app_window = AppWindow(
                username=username,
                monitor=monitor,
                on_logout=on_logout,
            )
            app_window.build()
            # Run tray in separate thread (it blocks)
            tray_thread = threading.Thread(
                target=app_window.run_tray, daemon=True
            )
            tray_thread.start()
            # Run Tk main loop
            app_window.root.mainloop()

        _root.after(0, build_and_run)

    def on_logout():
        if monitor:
            monitor.stop()
        # Restart to show login
        python = sys.executable
        os.execl(python, python, *sys.argv)

    check_for_updates()  # runs before login check

    # This will either proceed directly (token valid) or show login window
    show_login_if_needed(on_ready, _root)

    # Keep root alive
    try:
        _root.mainloop()
    except Exception:
        pass


if __name__ == "__main__":
    main()
