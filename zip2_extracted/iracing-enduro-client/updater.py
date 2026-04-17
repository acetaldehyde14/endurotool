"""
Auto-updater.
Called at startup before login. Downloads and silently installs new versions.
Uses a temporary batch file to wait for this process to fully exit before
running the installer — avoids the "file in use" conflict.
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

CURRENT_VERSION = "1.0.0"   # ← bump this string with every release


def check_for_updates():
    """Silently checks for updates. Shows prompt only if a newer version exists."""
    try:
        r = requests.get(f"{SERVER_URL}/api/client/version", timeout=5)
        if r.status_code != 200:
            return
        data    = r.json()
        latest  = data.get("version", "")
        url     = data.get("download_url", "")
        log     = data.get("changelog", "")
        if latest and url and _is_newer(latest, CURRENT_VERSION):
            _prompt_update(latest, log, url)
    except Exception:
        pass   # server offline or no version endpoint — silently continue


def _is_newer(remote: str, local: str) -> bool:
    try:
        return tuple(int(x) for x in remote.split(".")) > \
               tuple(int(x) for x in local.split("."))
    except Exception:
        return False


def _prompt_update(version: str, changelog: str, url: str):
    root = tk.Tk()
    root.withdraw()
    msg = f"Version {version} is available.\n"
    if changelog:
        msg += f"\n{changelog}\n"
    msg += "\nDownload and install now?"
    if messagebox.askyesno("Update Available", msg, parent=root):
        root.destroy()
        _download_and_install(url)
    else:
        root.destroy()


def _download_and_install(url: str):
    """Download installer, then launch it via a bat file that waits for us to exit."""
    win = tk.Tk()
    win.title("Downloading update...")
    win.geometry("320x90")
    win.resizable(False, False)
    win.configure(bg="#1a1a2e")
    tk.Label(win, text="Downloading update, please wait...",
             font=("Segoe UI", 10), fg="white", bg="#1a1a2e").pack(pady=10)
    pct_var = tk.StringVar(value="0%")
    tk.Label(win, textvariable=pct_var,
             font=("Segoe UI", 9), fg="#aaaacc", bg="#1a1a2e").pack()
    win.update()

    def do_download():
        try:
            r = requests.get(url, stream=True, timeout=120)
            total      = int(r.headers.get("content-length", 0))
            downloaded = 0

            tmp = tempfile.NamedTemporaryFile(
                delete=False, suffix=".exe", prefix="iRacingEnduro-Setup-"
            )
            for chunk in r.iter_content(chunk_size=16384):
                tmp.write(chunk)
                downloaded += len(chunk)
                if total:
                    win.after(0, lambda p=int(downloaded / total * 100):
                               pct_var.set(f"{p}%"))
            tmp.close()
            win.after(0, win.destroy)

            # Write a bat that waits for this PID to die, then runs installer
            pid = os.getpid()
            bat = tempfile.NamedTemporaryFile(
                delete=False, suffix=".bat", mode="w"
            )
            bat.write(
                f"@echo off\n"
                f":wait\n"
                f"tasklist /fi \"PID eq {pid}\" 2>NUL | find \"{pid}\" >NUL\n"
                f"if not errorlevel 1 (timeout /t 1 /nobreak >NUL & goto wait)\n"
                f"start \"\" /wait \"{tmp.name}\" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART\n"
                f"del \"%~f0\"\n"
            )
            bat.close()

            # Launch bat hidden, then exit this process
            subprocess.Popen(
                ["cmd", "/c", bat.name],
                creationflags=subprocess.CREATE_NO_WINDOW,
                shell=False,
            )
            time.sleep(0.5)
            sys.exit(0)

        except Exception as e:
            win.after(0, win.destroy)
            messagebox.showerror("Update Failed", f"Download failed:\n{e}")

    threading.Thread(target=do_download, daemon=True).start()
    win.mainloop()
