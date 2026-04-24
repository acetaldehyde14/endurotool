"""
CoachOverlay - borderless always-on-top Tkinter coaching banner.

Designed for use with iRacing in windowed or borderless-windowed mode.
All public methods are thread-safe and schedule work on the Tk main thread.
"""

import tkinter as tk
from typing import Optional

from coaching_models import CoachingCue

# Visual state -> (background accent color, text color)
_STATE_COLOURS = {
    "urgent_brake": ("#c0392b", "#ffffff"),
    "caution_lift": ("#e67e22", "#ffffff"),
    "throttle_go": ("#27ae60", "#ffffff"),
    "neutral": ("#1a1a2e", "#ffffff"),
}

_DEFAULT_DISPLAY_MS = 3000
_OVERLAY_ALPHA = 0.88
_OVERLAY_WIDTH = 460
_OVERLAY_HEIGHT = 90
_OVERLAY_Y_OFFSET = 60


class CoachOverlay:
    """
    Lightweight always-on-top coaching banner.

    Construction must happen on the Tk main thread.
    show_cue() is safe to call from any thread.
    """

    def __init__(self, root: tk.Tk):
        self._root = root
        self._window: Optional[tk.Toplevel] = None
        self._hide_job = None
        self._text_var: Optional[tk.StringVar] = None
        self._sub_var: Optional[tk.StringVar] = None
        self._label_var: Optional[tk.StringVar] = None
        self._enabled = True

        self._build_window()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        if not enabled and self._window:
            self._root.after(0, self._hide)

    # Public thread-safe API
    def show_cue(self, cue: CoachingCue, duration_ms: int = _DEFAULT_DISPLAY_MS):
        """Show a coaching cue. Safe to call from any thread."""
        self._root.after(0, lambda: self._show(cue, duration_ms))

    def hide(self):
        """Hide the overlay immediately. Safe to call from any thread."""
        self._root.after(0, self._hide)

    # Internal; must run on the main thread
    def _build_window(self):
        try:
            self._window = tk.Toplevel(self._root)
            self._window.overrideredirect(True)
            self._window.attributes("-topmost", True)
            self._window.attributes("-alpha", _OVERLAY_ALPHA)
            self._window.configure(bg="#1a1a2e")

            screen_width = self._root.winfo_screenwidth()
            x = (screen_width - _OVERLAY_WIDTH) // 2
            self._window.geometry(
                f"{_OVERLAY_WIDTH}x{_OVERLAY_HEIGHT}+{x}+{_OVERLAY_Y_OFFSET}"
            )

            self._text_var = tk.StringVar()
            self._sub_var = tk.StringVar()
            self._label_var = tk.StringVar()

            inner = tk.Frame(self._window, bg="#1a1a2e", padx=16, pady=8)
            inner.pack(fill="both", expand=True)

            self._main_label = tk.Label(
                inner,
                textvariable=self._text_var,
                font=("Segoe UI", 22, "bold"),
                fg="white",
                bg="#1a1a2e",
                anchor="center",
            )
            self._main_label.pack(fill="x")

            sub_frame = tk.Frame(inner, bg="#1a1a2e")
            sub_frame.pack(fill="x")

            self._sub_label = tk.Label(
                sub_frame,
                textvariable=self._sub_var,
                font=("Segoe UI", 10),
                fg="#ccccee",
                bg="#1a1a2e",
                anchor="w",
            )
            self._sub_label.pack(side="left")

            self._zone_label = tk.Label(
                sub_frame,
                textvariable=self._label_var,
                font=("Segoe UI", 9),
                fg="#888899",
                bg="#1a1a2e",
                anchor="e",
            )
            self._zone_label.pack(side="right")

            self._window.withdraw()
        except Exception as e:
            print(f"[Overlay] Window build failed: {e}")
            self._window = None

    def _show(self, cue: CoachingCue, duration_ms: int):
        if not self._enabled or not self._window:
            return

        try:
            self._text_var.set(cue.text)
            self._sub_var.set(cue.subtitle)
            self._label_var.set(cue.zone_label)
            self._apply_state(cue.state)

            self._window.deiconify()
            self._window.lift()

            if self._hide_job:
                self._root.after_cancel(self._hide_job)
            self._hide_job = self._root.after(duration_ms, self._hide)
        except Exception as e:
            print(f"[Overlay] Show error: {e}")

    def _hide(self):
        if self._window:
            try:
                self._window.withdraw()
            except Exception:
                pass

    def _apply_state(self, state: str):
        bg, fg = _STATE_COLOURS.get(state, _STATE_COLOURS["neutral"])
        try:
            self._window.configure(bg=bg)
            for widget in (self._main_label,):
                widget.configure(bg=bg, fg=fg)
            for widget in (self._sub_label, self._zone_label):
                widget.configure(bg=bg)
            for child in self._window.winfo_children():
                _set_bg_recursive(child, bg)
        except Exception:
            pass


def _set_bg_recursive(widget, bg: str):
    try:
        widget.configure(bg=bg)
    except Exception:
        pass
    for child in widget.winfo_children():
        _set_bg_recursive(child, bg)
