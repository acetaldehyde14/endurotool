"""
Auto-updater — checks server version on startup, prompts to download if newer.
"""
import requests
import subprocess
import sys
import os
import time
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox
from config import SERVER_URL

CURRENT_VERSION = "1.0.5"  # bump this string with every release


def check_for_updates():
    """Call this at startup (before login). Silently skips if server unreachable."""
    try:
        r = requests.get(f"{SERVER_URL}/api/client/version", timeout=5)
        if r.status_code != 200:
            return
        data = r.json()
        latest = data.get("version", "")
        if latest and latest != CURRENT_VERSION and _is_newer(latest, CURRENT_VERSION):
            changelog = data.get("changelog", "")
            download_url = data.get("download_url", "")
            _prompt_update(latest, changelog, download_url)
    except Exception:
        pass  # silently skip — server might be offline


def _is_newer(remote: str, local: str) -> bool:
    """Simple semver comparison — returns True if remote > local."""
    try:
        r = tuple(int(x) for x in remote.split("."))
        l = tuple(int(x) for x in local.split("."))
        return r > l
    except Exception:
        return False


def _prompt_update(version: str, changelog: str, url: str):
    root = tk.Tk()
    root.withdraw()
    msg = f"A new version ({version}) is available.\n"
    if changelog:
        msg += f"\n{changelog}\n"
    msg += "\nDownload and install now?"
    if messagebox.askyesno("Update Available", msg, parent=root):
        root.destroy()
        _download_and_install(url)
    else:
        root.destroy()


def _download_and_install(url: str):
    win = tk.Tk()
    win.title("Downloading update...")
    win.geometry("320x90")
    win.resizable(False, False)
    win.configure(bg="#1a1a2e")

    label = tk.Label(win, text="Downloading update, please wait...",
                     font=("Segoe UI", 10), fg="white", bg="#1a1a2e")
    label.pack(pady=12)

    progress_var = tk.StringVar(value="0%")
    progress_label = tk.Label(win, textvariable=progress_var,
                              font=("Segoe UI", 9), fg="#aaaacc", bg="#1a1a2e")
    progress_label.pack()
    win.update()

    def do_download():
        try:
            r = requests.get(url, stream=True, timeout=120)
            total = int(r.headers.get("content-length", 0))
            downloaded = 0

            tmp = tempfile.NamedTemporaryFile(
                delete=False, suffix=".exe", prefix="iRacingEnduro-Setup-"
            )
            for chunk in r.iter_content(chunk_size=16384):
                tmp.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = int(downloaded / total * 100)
                    win.after(0, lambda p=pct: progress_var.set(f"{p}%"))
            tmp.close()

            win.after(0, win.destroy)

            import tempfile as tf2

            # Write a small bat that waits for this process to exit, then runs installer
            pid = os.getpid()
            bat = tf2.NamedTemporaryFile(
                delete=False, suffix='.bat', mode='w'
            )
            bat.write(f"""@echo off
:waitloop
tasklist /fi "PID eq {pid}" 2>NUL | find "{pid}" >NUL
if not errorlevel 1 (
    timeout /t 1 /nobreak >NUL
    goto waitloop
)
start "" /wait "{tmp.name}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
del "%~f0"
""")
            bat.close()

            # Launch the bat hidden, then exit
            subprocess.Popen(
                ['cmd', '/c', bat.name],
                creationflags=subprocess.CREATE_NO_WINDOW,
                shell=False
            )
            sys.exit(0)

        except Exception as e:
            win.after(0, win.destroy)
            messagebox.showerror("Update Failed", f"Download failed:\n{e}")

    threading.Thread(target=do_download, daemon=True).start()
    win.mainloop()
