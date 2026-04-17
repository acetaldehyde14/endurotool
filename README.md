# iRacing Enduro Monitor — Desktop Client

Python desktop app that connects to iRacing and sends telemetry to your team server.

## For Team Members — Quick Install

1. Download and run `iRacingEnduro-Setup.exe`
2. Click through the installer — it will start automatically
3. Enter your team username and password
4. The app runs silently in your system tray (bottom-right taskbar)
5. It starts automatically every time Windows boots

That's it! You'll receive alerts on Telegram and Discord automatically.

---

## For Developers — Building from Source

### Requirements
- Python 3.10+ (64-bit)
- iRacing installed (the `irsdk` library needs the game installed)
- Windows (iRacing is Windows-only)

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 2. Set your server URL
Edit `config.py` and change:
```python
SERVER_URL = "https://your-server.com"
```

### 3. Run directly (for testing)
```bash
python main.py
```

### 4. Build the exe

Install PyInstaller:
```bash
pip install pyinstaller
```

Build:
```bash
pyinstaller iRacingEnduro.spec
```

The exe will be at `dist\iRacingEnduro.exe`

### 5. Build the installer

1. Download and install [Inno Setup](https://jrsoftware.org/isinfo.php)
2. Open `installer.iss` in Inno Setup Compiler
3. Click Build → the installer `iRacingEnduro-Setup.exe` will appear in the project folder
4. Distribute this to teammates

---

## How It Works

1. The app polls iRacing shared memory every 2 seconds
2. On driver change: immediately POSTs to server → server sends notifications
3. On fuel update: POSTs current fuel level and estimated time remaining
4. Server handles deduplication (all teammates can run simultaneously — only one notification fires)
5. App runs as a system tray icon — double-click to open dashboard

## Files

| File | Purpose |
|---|---|
| `main.py` | Entry point |
| `config.py` | Settings & stored credentials |
| `api_client.py` | HTTP calls to server |
| `iracing_monitor.py` | Reads iRacing shared memory |
| `gui/login.py` | Login window (first run only) |
| `gui/tray.py` | System tray & dashboard window |
| `iRacingEnduro.spec` | PyInstaller build config |
| `installer.iss` | Inno Setup installer config |
