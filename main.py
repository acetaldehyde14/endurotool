"""
iRacing Enduro Monitor - Desktop Client.

Run with: python main.py
Or as compiled executable: iRacingEnduro.exe
"""

import os
import sys
import threading
import tkinter as tk

import api_client
from audio_player import AudioPlayer
from coach_manager import CoachManager
from coaching_models import CoachingCue
from config import COACHING_ENABLED
from gui.login import show_login_if_needed
from gui.tray import AppWindow
from iracing_monitor import IRacingMonitor
from updater import check_for_updates

# Add project root to path.
sys.path.insert(0, os.path.dirname(__file__))


def main():
    root = tk.Tk()
    root.withdraw()

    app_window = None
    monitor = None
    coach_manager = None
    audio_player = AudioPlayer()

    def on_event(event_type: str, data: dict):
        """Called from the monitor thread and forwards updates to the GUI."""
        if app_window:
            if event_type == "fuel_update":
                app_window.update_fuel(data)
            elif event_type == "driver_change":
                app_window.update_driver(data.get("driver_name", ""))
            elif event_type == "position_update":
                app_window.update_position(data)
            elif event_type == "telemetry_batch":
                app_window.update_telemetry(data.get("count", 0))
            elif event_type == "telemetry_session_status":
                app_window.update_session_status(data.get("status", ""))
                if data.get("status") == "active":
                    track_id = data.get("track_id", "")
                    car_id = data.get("car_id", "")
                    if track_id and car_id:
                        app_window.update_session_context(track_id, car_id)
            elif event_type == "coaching_status":
                app_window.update_coaching_status(data.get("status", ""))

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

        coach_manager = CoachManager()
        if COACHING_ENABLED:
            coach_manager.start()

        monitor = IRacingMonitor(
            on_event=on_event,
            on_status_change=on_iracing_status,
            coach_manager=coach_manager if COACHING_ENABLED else None,
        )
        monitor.start()

        def build_and_run():
            nonlocal app_window
            coach_overlay = None

            app_window = AppWindow(
                username=username,
                monitor=monitor,
                coach_manager=coach_manager,
                on_logout=on_logout,
            )
            app_window.build()

            try:
                from coach_overlay import CoachOverlay

                coach_overlay = CoachOverlay(app_window.root)
            except Exception as exc:
                print(f"[Main] Coach overlay unavailable: {exc}")

            def on_coach_cue(cue):
                if coach_manager and coach_manager._overlay_enabled and coach_overlay:
                    try:
                        coach_overlay.show_cue(cue)
                    except Exception as exc:
                        print(f"[Main] Coach overlay error: {exc}")
                if coach_manager and coach_manager._voice_enabled:
                    try:
                        audio_player.play(cue.voice_key)
                    except Exception as exc:
                        print(f"[Main] Audio player error: {exc}")

            def on_coach_status(status: str):
                on_event("coaching_status", {"status": status})

            def on_test_overlay():
                cue = CoachingCue(
                    text="Brake here",
                    subtitle="Overlay test",
                    zone_label="Coach",
                    state="urgent_brake",
                )
                if coach_overlay:
                    try:
                        coach_overlay.show_cue(cue)
                        app_window.update_coaching_status("Overlay test sent")
                        return
                    except Exception as exc:
                        print(f"[Main] Coach overlay test error: {exc}")
                app_window.update_coaching_status("Overlay unavailable")

            def on_test_voice():
                voice_key = "reference_brake_now_at_the_marker"
                try:
                    if audio_player.has_voice_key(voice_key):
                        was_enabled = coach_manager._voice_enabled if coach_manager else True
                        if not was_enabled:
                            audio_player.set_enabled(True)
                        audio_player.play(voice_key)
                        if not was_enabled:
                            audio_player.set_enabled(False)
                        app_window.update_coaching_status("Voice test requested")
                    else:
                        app_window.update_coaching_status(audio_player.manifest_summary())
                except Exception as exc:
                    print(f"[Main] Voice test error: {exc}")
                    app_window.update_coaching_status("Voice test failed")

            coach_manager.set_callbacks(
                on_cue=on_coach_cue,
                on_status_change=on_coach_status,
            )

            app_window.set_coach_overlay(coach_overlay)
            app_window.set_audio_player(audio_player)
            app_window.set_test_actions(
                on_test_overlay=on_test_overlay,
                on_test_voice=on_test_voice,
            )
            try:
                audio_player.load_manifest()
            except Exception as exc:
                print(f"[Main] Voice manifest load failed: {exc}")

            tray_thread = threading.Thread(target=app_window.run_tray, daemon=True)
            tray_thread.start()
            app_window.root.mainloop()

        root.after(0, build_and_run)

    def on_logout():
        if coach_manager:
            coach_manager.stop()
        if monitor:
            monitor.stop()
        python = sys.executable
        os.execl(python, python, *sys.argv)

    check_for_updates()
    show_login_if_needed(on_ready, root)

    try:
        root.mainloop()
    except Exception:
        pass


if __name__ == "__main__":
    main()
