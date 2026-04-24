"""
Auto-updater - checks server version on startup and offers a download if newer.
"""

import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox

import requests

from config import SERVER_URL

CURRENT_VERSION = "1.0.6"


def check_for_updates():
    """Call this at startup. Silently skips if the server is unreachable."""
    try:
        response = requests.get(f"{SERVER_URL}/api/client/version", timeout=5)
        if response.status_code != 200:
            return
        data = response.json()
        latest = data.get("version", "")
        if latest and latest != CURRENT_VERSION and _is_newer(latest, CURRENT_VERSION):
            changelog = data.get("changelog", "")
            download_url = data.get("download_url", "")
            _prompt_update(latest, changelog, download_url)
    except Exception:
        pass


def _is_newer(remote: str, local: str) -> bool:
    """Simple semver comparison."""
    try:
        remote_parts = tuple(int(x) for x in remote.split("."))
        local_parts = tuple(int(x) for x in local.split("."))
        return remote_parts > local_parts
    except Exception:
        return False


def _prompt_update(version: str, changelog: str, url: str):
    root = tk.Tk()
    root.withdraw()
    message = f"A new version ({version}) is available.\n"
    if changelog:
        message += f"\n{changelog}\n"
    message += "\nDownload and install now?"
    if messagebox.askyesno("Update Available", message, parent=root):
        root.destroy()
        _download_and_install(url)
    else:
        root.destroy()


def _download_and_install(url: str):
    window = tk.Tk()
    window.title("Downloading update...")
    window.geometry("320x90")
    window.resizable(False, False)
    window.configure(bg="#1a1a2e")

    label = tk.Label(
        window,
        text="Downloading update, please wait...",
        font=("Segoe UI", 10),
        fg="white",
        bg="#1a1a2e",
    )
    label.pack(pady=12)

    progress_var = tk.StringVar(value="0%")
    progress_label = tk.Label(
        window,
        textvariable=progress_var,
        font=("Segoe UI", 9),
        fg="#aaaacc",
        bg="#1a1a2e",
    )
    progress_label.pack()
    window.update()

    def do_download():
        try:
            response = requests.get(url, stream=True, timeout=120)
            total = int(response.headers.get("content-length", 0))
            downloaded = 0

            temp_installer = tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".exe",
                prefix="iRacingEnduro-Setup-",
            )
            for chunk in response.iter_content(chunk_size=16384):
                temp_installer.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = int(downloaded / total * 100)
                    window.after(0, lambda p=pct: progress_var.set(f"{p}%"))
            temp_installer.close()

            window.after(0, window.destroy)

            pid = os.getpid()
            batch_file = tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".bat",
                mode="w",
            )
            batch_file.write(
                f"""@echo off
:waitloop
tasklist /fi "PID eq {pid}" 2>NUL | find "{pid}" >NUL
if not errorlevel 1 (
    timeout /t 1 /nobreak >NUL
    goto waitloop
)
start "" /wait "{temp_installer.name}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
del "%~f0"
"""
            )
            batch_file.close()

            subprocess.Popen(
                ["cmd", "/c", batch_file.name],
                creationflags=subprocess.CREATE_NO_WINDOW,
                shell=False,
            )
            sys.exit(0)
        except Exception as e:
            window.after(0, window.destroy)
            messagebox.showerror("Update Failed", f"Download failed:\n{e}")

    threading.Thread(target=do_download, daemon=True).start()
    window.mainloop()
