# Repository Instructions

## Project Overview

This is a Windows-only Python desktop client for iRacing endurance monitoring and coaching. The app reads iRacing shared-memory telemetry, posts events to the team server, shows a Tkinter/pystray UI, and can run as a packaged executable/installer.

## Important Files

- `main.py`: application entry point and wiring between monitor, GUI, API client, and coach.
- `config.py`: constants and persisted `%APPDATA%\iRacingEnduro\config.json` helpers.
- `iracing_monitor.py`: iRacing SDK polling, telemetry extraction, and event generation.
- `coach_manager.py`, `coaching_models.py`, `coach_overlay.py`, `audio_player.py`: coaching logic, models, overlay, and audio.
- `api_client.py`: HTTP calls to `SERVER_URL`.
- `gui/login.py`, `gui/tray.py`, `gui/reference_lap_selector.py`: user-facing Tkinter UI.
- `iRacingEnduro.spec`, `iRacingEnduro_debug.spec`: PyInstaller builds.
- `installer.iss`: Inno Setup installer.
- `Output/`, `dist/`, `build/`, `__pycache__/`: generated outputs; avoid touching unless explicitly rebuilding or cleaning.

## Development Commands

- Install dependencies: `pip install -r requirements.txt`
- Run locally: `python main.py`
- Syntax-check core files:
  `python -m py_compile config.py api_client.py coaching_models.py coach_manager.py coach_overlay.py audio_player.py iracing_monitor.py main.py updater.py`
- Build release exe: `python -m PyInstaller --noconfirm --onefile --noconsole --name iRacingEnduro --icon NONE --collect-all PIL main.py`
- Build debug exe: `python -m PyInstaller --noconfirm --onefile --name iRacingEnduro_debug --icon NONE --collect-all PIL main.py`
- Build via spec: `pyinstaller iRacingEnduro.spec`

## Runtime Assumptions

- The app targets Windows because iRacing and `pyirsdk` require it.
- Config and runtime data are stored under `%APPDATA%\iRacingEnduro`.
- The production server URL is currently `https://smcorse.com` in `config.py`.
- The app may run in the system tray and may have compiled executables active during local testing.

## Working Rules

- Preserve user-generated/generated artifacts unless the task explicitly involves rebuilding or cleaning them.
- Do not revert existing worktree changes unless the user asks.
- Keep edits small and consistent with the existing plain-Python/Tkinter style.
- Prefer standard-library APIs and the existing helper functions in this repo over new dependencies.
- Treat `.claude/settings.local.json` as historical permission context only. Codex should follow this `AGENTS.md` plus current user instructions.
- If adding settings, wire them through `config.py` and the existing config JSON helpers rather than hard-coding UI state.
- If changing coaching behavior, check both the coach manager path and GUI controls so the feature is usable without editing code.

## Verification

Run `python -m py_compile ...` for Python changes. For UI or tray behavior, also run the app manually on Windows when practical, because Tkinter tray/iRacing interactions are not fully covered by static checks.
