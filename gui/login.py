import threading
import tkinter as tk

import api_client
from config import load_config, save_config


class LoginWindow:
    def __init__(self, on_success):
        self.on_success = on_success
        self.root = tk.Tk()
        self._build_ui()

    def _build_ui(self):
        self.root.title("iRacing Enduro Monitor - Login")
        self.root.geometry("360x360")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f"+{x}+{y}")

        header = tk.Frame(self.root, bg="#0f3460", pady=16)
        header.pack(fill="x")
        tk.Label(
            header,
            text="iRacing Enduro Monitor",
            font=("Segoe UI", 14, "bold"),
            fg="#e94560",
            bg="#0f3460",
        ).pack()
        tk.Label(
            header,
            text="Sign in to start tracking",
            font=("Segoe UI", 9),
            fg="#aaaacc",
            bg="#0f3460",
        ).pack()
        tk.Label(
            header,
            text="Use your smcorse.com login",
            font=("Segoe UI", 8),
            fg="#7777aa",
            bg="#0f3460",
        ).pack()

        form = tk.Frame(self.root, bg="#1a1a2e", padx=30, pady=20)
        form.pack(fill="both", expand=True)

        tk.Label(
            form,
            text="Username",
            font=("Segoe UI", 9),
            fg="#ccccdd",
            bg="#1a1a2e",
        ).pack(anchor="w")
        self.username_var = tk.StringVar(master=self.root)
        tk.Entry(
            form,
            textvariable=self.username_var,
            font=("Segoe UI", 11),
            bg="#16213e",
            fg="white",
            insertbackground="white",
            relief="flat",
            bd=5,
        ).pack(fill="x", pady=(2, 12))

        tk.Label(
            form,
            text="Password",
            font=("Segoe UI", 9),
            fg="#ccccdd",
            bg="#1a1a2e",
        ).pack(anchor="w")
        self.password_var = tk.StringVar(master=self.root)
        tk.Entry(
            form,
            textvariable=self.password_var,
            show="*",
            font=("Segoe UI", 11),
            bg="#16213e",
            fg="white",
            insertbackground="white",
            relief="flat",
            bd=5,
        ).pack(fill="x", pady=(2, 16))

        self.status_label = tk.Label(
            form,
            text="",
            font=("Segoe UI", 9),
            fg="#e94560",
            bg="#1a1a2e",
        )
        self.status_label.pack()

        self.login_btn = tk.Button(
            form,
            text="Sign In",
            font=("Segoe UI", 11, "bold"),
            bg="#e94560",
            fg="white",
            activebackground="#c73652",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            command=self._on_login,
        )
        self.login_btn.pack(fill="x", pady=(4, 4))

        signup_btn = tk.Button(
            form,
            text="Create Account",
            font=("Segoe UI", 10),
            bg="#16213e",
            fg="#aaaacc",
            activebackground="#1e2a4a",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            command=self._open_signup,
        )
        signup_btn.pack(fill="x")

        self.root.bind("<Return>", lambda _event: self._on_login())

    def _on_login(self):
        username = self.username_var.get().strip()
        password = self.password_var.get().strip()

        if not username or not password:
            self.status_label.config(text="Please enter username and password")
            return

        self.login_btn.config(state="disabled", text="Signing in...")
        self.status_label.config(text="", fg="#e94560")

        def do_login():
            try:
                result = api_client.login(username, password)
                token = result["token"]
                user = result["user"]
                save_config(
                    {
                        "token": token,
                        "username": user["username"],
                        "user_id": user["id"],
                    }
                )
                self.root.after(0, lambda: self._login_success(user["username"]))
            except Exception as e:
                message = "Invalid username or password"
                if "connect" in str(e).lower() or "timeout" in str(e).lower():
                    message = "Cannot reach server - check your connection"
                self.root.after(0, lambda: self._login_failed(message))

        threading.Thread(target=do_login, daemon=True).start()

    def _login_success(self, username):
        self.root.destroy()
        self.on_success(username)

    def _login_failed(self, msg):
        self.status_label.config(text=msg)
        self.login_btn.config(state="normal", text="Sign In")

    def _open_signup(self):
        SignUpWindow(self.root, self.on_success)

    def run(self):
        self.root.mainloop()


class SignUpWindow:
    def __init__(self, parent, on_success):
        self.on_success = on_success
        self.win = tk.Toplevel(parent)
        self._build_ui()

    def _build_ui(self):
        self.win.title("iRacing Enduro Monitor - Create Account")
        self.win.geometry("360x320")
        self.win.resizable(False, False)
        self.win.configure(bg="#1a1a2e")
        self.win.grab_set()

        self.win.update_idletasks()
        width = self.win.winfo_width()
        height = self.win.winfo_height()
        x = (self.win.winfo_screenwidth() // 2) - (width // 2)
        y = (self.win.winfo_screenheight() // 2) - (height // 2)
        self.win.geometry(f"+{x}+{y}")

        header = tk.Frame(self.win, bg="#0f3460", pady=16)
        header.pack(fill="x")
        tk.Label(
            header,
            text="iRacing Enduro Monitor",
            font=("Segoe UI", 14, "bold"),
            fg="#e94560",
            bg="#0f3460",
        ).pack()
        tk.Label(
            header,
            text="Create a new account",
            font=("Segoe UI", 9),
            fg="#aaaacc",
            bg="#0f3460",
        ).pack()

        form = tk.Frame(self.win, bg="#1a1a2e", padx=30, pady=20)
        form.pack(fill="both", expand=True)

        for label_text, attr_name in (
            ("Username", "username_var"),
            ("Password", "password_var"),
            ("Confirm Password", "confirm_var"),
        ):
            tk.Label(
                form,
                text=label_text,
                font=("Segoe UI", 9),
                fg="#ccccdd",
                bg="#1a1a2e",
            ).pack(anchor="w")
            var = tk.StringVar(master=self.win)
            setattr(self, attr_name, var)
            tk.Entry(
                form,
                textvariable=var,
                show="*" if "Password" in label_text else "",
                font=("Segoe UI", 11),
                bg="#16213e",
                fg="white",
                insertbackground="white",
                relief="flat",
                bd=5,
            ).pack(fill="x", pady=(2, 10))

        self.status_label = tk.Label(
            form,
            text="",
            font=("Segoe UI", 9),
            fg="#e94560",
            bg="#1a1a2e",
        )
        self.status_label.pack()

        self.signup_btn = tk.Button(
            form,
            text="Create Account",
            font=("Segoe UI", 11, "bold"),
            bg="#e94560",
            fg="white",
            activebackground="#c73652",
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            command=self._on_signup,
        )
        self.signup_btn.pack(fill="x", pady=(4, 0))

        self.win.bind("<Return>", lambda _event: self._on_signup())

    def _on_signup(self):
        username = self.username_var.get().strip()
        password = self.password_var.get().strip()
        confirm = self.confirm_var.get().strip()

        if not username or not password or not confirm:
            self.status_label.config(text="Please fill in all fields")
            return
        if password != confirm:
            self.status_label.config(text="Passwords do not match")
            return

        self.signup_btn.config(state="disabled", text="Creating account...")
        self.status_label.config(text="", fg="#e94560")

        def do_register():
            try:
                result = api_client.register(username, password)
                token = result["token"]
                user = result["user"]
                save_config(
                    {
                        "token": token,
                        "username": user["username"],
                        "user_id": user["id"],
                    }
                )
                self.win.after(0, lambda: self._signup_success(user["username"]))
            except Exception as e:
                message = "Registration failed"
                if "connect" in str(e).lower() or "timeout" in str(e).lower():
                    message = "Cannot reach server - check your connection"
                elif "409" in str(e) or "exists" in str(e).lower():
                    message = "Username already taken"
                self.win.after(0, lambda: self._signup_failed(message))

        threading.Thread(target=do_register, daemon=True).start()

    def _signup_success(self, username):
        parent = self.win.master
        self.win.destroy()
        parent.destroy()
        self.on_success(username)

    def _signup_failed(self, msg):
        self.status_label.config(text=msg)
        self.signup_btn.config(state="normal", text="Create Account")


def show_login_if_needed(on_ready, root=None):
    """Check the stored token and show the login window only if needed."""
    cfg = load_config()
    token = cfg.get("token")

    if token:
        def check():
            if api_client.validate_token():
                on_ready(cfg.get("username", "Driver"))
            else:
                if root:
                    root.after(0, lambda: _show_login(on_ready))
                else:
                    _show_login(on_ready)

        threading.Thread(target=check, daemon=True).start()
    else:
        _show_login(on_ready)


def _show_login(on_ready):
    window = LoginWindow(on_ready)
    window.run()
