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
from coach_manager import CoachManager
from audio_player import AudioPlayer
from config import COACHING_ENABLED


def main():
    # We need a Tk root to exist before login window (for thread safety)
    # Hide root during login
    _root = tk.Tk()
    _root.withdraw()

    app_window    = None
    monitor       = None
    coach_manager = None
    audio_player  = AudioPlayer()

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
            elif event_type == "telemetry_session_status":
                app_window.update_session_status(data.get("status", ""))
                if data.get("status") == "active":
                    track_id = data.get("track_id", "")
                    car_id   = data.get("car_id", "")
                    if track_id and car_id:
                        app_window.update_session_context(track_id, car_id)
            elif event_type == "coaching_status":
                app_window.update_coaching_status(data.get("status", ""))

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
        nonlocal app_window, monitor, coach_manager

        # Create coaching manager (callbacks are wired in build_and_run once GUI exists)
        coach_manager = CoachManager()
        if COACHING_ENABLED:
            coach_manager.start()

        # Start iRacing monitor, passing in the coach manager
        monitor = IRacingMonitor(
            on_event=on_event,
            on_status_change=on_iracing_status,
            coach_manager=coach_manager if COACHING_ENABLED else None,
        )
        monitor.start()

        # Build app window (must happen on main thread)
        def build_and_run():
            nonlocal app_window
            from coach_overlay import CoachOverlay

            app_window = AppWindow(
                username=username,
                monitor=monitor,
                coach_manager=coach_manager,
                on_logout=on_logout,
            )
            app_window.build()

            # Wire coaching callbacks now that the Tk root is live
            coach_overlay = CoachOverlay(app_window.root)

            def on_coach_cue(cue):
                if coach_manager and coach_manager._overlay_enabled:
                    coach_overlay.show_cue(cue)
                if coach_manager and coach_manager._voice_enabled:
                    audio_player.play(cue.voice_key)

            def on_coach_status(status: str):
                on_event("coaching_status", {"status": status})

            coach_manager.set_callbacks(
                on_cue=on_coach_cue,
                on_status_change=on_coach_status,
            )

            app_window.set_coach_overlay(coach_overlay)
            audio_player.load_manifest()

            # Run tray in separate thread (it blocks)
            tray_thread = threading.Thread(
                target=app_window.run_tray, daemon=True
            )
            tray_thread.start()
            # Run Tk main loop
            app_window.root.mainloop()

        _root.after(0, build_and_run)

    def on_logout():
        if coach_manager:
            coach_manager.stop()
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
