import tkinter as tk
from tkinter import messagebox
import threading
import api_client
from config import save_config, load_config


class LoginWindow:
    def __init__(self, on_success):
        self.on_success = on_success
        self.root = tk.Tk()
        self._build_ui()

    def _build_ui(self):
        self.root.title("iRacing Enduro Monitor — Login")
        self.root.geometry("360x280")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        # Center on screen
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(
            f"+{sw // 2 - 180}+{sh // 2 - 140}"
        )

        # Header
        hdr = tk.Frame(self.root, bg="#0f3460", pady=16)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🏁  iRacing Enduro Monitor",
                 font=("Segoe UI", 14, "bold"), fg="#e94560", bg="#0f3460").pack()
        tk.Label(hdr, text="Sign in to start tracking",
                 font=("Segoe UI", 9), fg="#aaaacc", bg="#0f3460").pack()

        # Form
        form = tk.Frame(self.root, bg="#1a1a2e", padx=30, pady=20)
        form.pack(fill="both", expand=True)

        def field(label, var, show=None):
            tk.Label(form, text=label, font=("Segoe UI", 9),
                     fg="#ccccdd", bg="#1a1a2e").pack(anchor="w")
            kw = dict(textvariable=var, font=("Segoe UI", 11),
                      bg="#16213e", fg="white", insertbackground="white",
                      relief="flat", bd=5)
            if show:
                kw["show"] = show
            tk.Entry(form, **kw).pack(fill="x", pady=(2, 12))

        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        field("Username", self.username_var)
        field("Password", self.password_var, show="•")

        self.status_lbl = tk.Label(form, text="", font=("Segoe UI", 9),
                                   fg="#e94560", bg="#1a1a2e")
        self.status_lbl.pack()

        self.login_btn = tk.Button(
            form, text="Sign In", font=("Segoe UI", 11, "bold"),
            bg="#e94560", fg="white", activebackground="#c73652",
            activeforeground="white", relief="flat", cursor="hand2",
            command=self._on_login,
        )
        self.login_btn.pack(fill="x", pady=(4, 0))
        self.root.bind("<Return>", lambda e: self._on_login())

    def _on_login(self):
        username = self.username_var.get().strip()
        password = self.password_var.get().strip()
        if not username or not password:
            self.status_lbl.config(text="Please enter username and password")
            return

        self.login_btn.config(state="disabled", text="Signing in...")
        self.status_lbl.config(text="")

        def do():
            try:
                result = api_client.login(username, password)
                token  = result["token"]
                user   = result["user"]
                save_config({
                    "token":    token,
                    "username": user["username"],
                    "user_id":  user["id"],
                })
                self.root.after(0, lambda: self._success(user["username"]))
            except Exception as e:
                msg = "Invalid username or password"
                s   = str(e).lower()
                if "connect" in s or "timeout" in s:
                    msg = "Cannot reach server — check your connection"
                self.root.after(0, lambda: self._fail(msg))

        threading.Thread(target=do, daemon=True).start()

    def _success(self, username: str):
        self.root.destroy()
        self.on_success(username)

    def _fail(self, msg: str):
        self.status_lbl.config(text=msg)
        self.login_btn.config(state="normal", text="Sign In")

    def run(self):
        self.root.mainloop()


def show_login_if_needed(on_ready):
    """Skip login if a valid token is already stored."""
    cfg   = load_config()
    token = cfg.get("token")

    if token:
        def check():
            if api_client.validate_token():
                on_ready(cfg.get("username", "Driver"))
            else:
                _show_login(on_ready)
        threading.Thread(target=check, daemon=True).start()
    else:
        _show_login(on_ready)


def _show_login(on_ready):
    LoginWindow(on_ready).run()
