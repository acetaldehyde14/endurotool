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
    "correction": ("#8e44ad", "#ffffff"),
    "info": ("#0f3460", "#ffffff"),
    "startup": ("#0f3460", "#ffffff"),
}

_DEFAULT_DISPLAY_MS = 3000
_OVERLAY_ALPHA = 0.88
_OVERLAY_WIDTH = 860
_OVERLAY_HEIGHT = 170
_OVERLAY_Y_OFFSET = 60
_INDICATOR_WIDTH = 160
_INDICATOR_HEIGHT = 34
_INDICATOR_Y_OFFSET = 18


class CoachOverlay:
    """
    Lightweight always-on-top coaching banner.

    Construction must happen on the Tk main thread.
    show_cue() is safe to call from any thread.
    """

    def __init__(self, root: tk.Tk):
        self._root = root
        self._window: Optional[tk.Toplevel] = None
        self._indicator: Optional[tk.Toplevel] = None
        self._indicator_var: Optional[tk.StringVar] = None
        self._hide_job = None
        self._text_var: Optional[tk.StringVar] = None
        self._sub_var: Optional[tk.StringVar] = None
        self._label_var: Optional[tk.StringVar] = None
        self._gear_var: Optional[tk.StringVar] = None
        self._brake_var: Optional[tk.StringVar] = None
        self._throttle_var: Optional[tk.StringVar] = None
        self._timing_var: Optional[tk.StringVar] = None
        self._upcoming_var: Optional[tk.StringVar] = None
        self._enabled = True

        self._build_window()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        if enabled:
            self._root.after(0, self._show_indicator)
        else:
            self._root.after(0, self._hide)
            self._root.after(0, self._hide_indicator)

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
            self._gear_var = tk.StringVar()
            self._brake_var = tk.StringVar()
            self._throttle_var = tk.StringVar()
            self._timing_var = tk.StringVar()
            self._upcoming_var = tk.StringVar()

            inner = tk.Frame(self._window, bg="#1a1a2e", padx=18, pady=10)
            inner.pack(fill="both", expand=True)

            self._main_label = tk.Label(
                inner,
                textvariable=self._text_var,
                font=("Segoe UI", 24, "bold"),
                fg="white",
                bg="#1a1a2e",
                anchor="center",
            )
            self._main_label.pack(fill="x")

            detail_frame = tk.Frame(inner, bg="#1a1a2e")
            detail_frame.pack(fill="x", pady=(6, 4))
            for col in range(4):
                detail_frame.columnconfigure(col, weight=1, uniform="coach_details")

            self._gear_label = _detail_label(detail_frame, self._gear_var)
            self._gear_label.grid(row=0, column=0, sticky="ew", padx=3)
            self._brake_label = _detail_label(detail_frame, self._brake_var)
            self._brake_label.grid(row=0, column=1, sticky="ew", padx=3)
            self._throttle_label = _detail_label(detail_frame, self._throttle_var)
            self._throttle_label.grid(row=0, column=2, sticky="ew", padx=3)
            self._timing_label = _detail_label(detail_frame, self._timing_var)
            self._timing_label.grid(row=0, column=3, sticky="ew", padx=3)

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

            self._upcoming_label = tk.Label(
                inner,
                textvariable=self._upcoming_var,
                font=("Segoe UI", 10, "bold"),
                fg="#ffffff",
                bg="#1a1a2e",
                anchor="center",
            )
            self._upcoming_label.pack(fill="x", pady=(5, 0))

            self._window.withdraw()
            self._build_indicator()
            self._show_indicator()
        except Exception as e:
            print(f"[Overlay] Window build failed: {e}")
            self._window = None

    def _build_indicator(self):
        self._indicator = tk.Toplevel(self._root)
        self._indicator.overrideredirect(True)
        self._indicator.attributes("-topmost", True)
        self._indicator.attributes("-alpha", 0.82)
        self._indicator.configure(bg="#0f3460")

        screen_width = self._root.winfo_screenwidth()
        x = screen_width - _INDICATOR_WIDTH - 24
        self._indicator.geometry(
            f"{_INDICATOR_WIDTH}x{_INDICATOR_HEIGHT}+{x}+{_INDICATOR_Y_OFFSET}"
        )

        self._indicator_var = tk.StringVar(value="COACH ON")
        tk.Label(
            self._indicator,
            textvariable=self._indicator_var,
            font=("Segoe UI", 10, "bold"),
            fg="#ffffff",
            bg="#0f3460",
            anchor="center",
        ).pack(fill="both", expand=True)
        self._indicator.withdraw()

    def _show(self, cue: CoachingCue, duration_ms: int):
        if not self._enabled or not self._window:
            return

        try:
            self._text_var.set(cue.display_text or cue.text)
            self._sub_var.set(cue.subtitle)
            self._label_var.set(cue.zone_label)
            self._gear_var.set(f"GEAR {cue.gear}" if cue.gear else "GEAR -")
            self._brake_var.set(cue.brake or "BRAKE -")
            self._throttle_var.set(cue.throttle or "THROTTLE -")
            self._timing_var.set(cue.timing or "MARKER -")
            self._upcoming_var.set(cue.upcoming)
            self._apply_state(cue.state)

            self._window.deiconify()
            self._window.lift()
            self._show_indicator()

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

    def _show_indicator(self):
        if not self._enabled or not self._indicator:
            return
        try:
            self._indicator.deiconify()
            self._indicator.lift()
        except Exception:
            pass

    def _hide_indicator(self):
        if self._indicator:
            try:
                self._indicator.withdraw()
            except Exception:
                pass

    def _apply_state(self, state: str):
        bg, fg = _STATE_COLOURS.get(state, _STATE_COLOURS["neutral"])
        try:
            self._window.configure(bg=bg)
            for widget in (self._main_label,):
                widget.configure(bg=bg, fg=fg)
            for widget in (
                self._sub_label,
                self._zone_label,
                self._upcoming_label,
                self._gear_label,
                self._brake_label,
                self._throttle_label,
                self._timing_label,
            ):
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


def _detail_label(parent, textvariable):
    return tk.Label(
        parent,
        textvariable=textvariable,
        font=("Segoe UI", 12, "bold"),
        fg="#ffffff",
        bg="#1a1a2e",
        anchor="center",
        padx=6,
        pady=4,
    )
