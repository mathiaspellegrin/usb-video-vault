#!/usr/bin/env python3
"""One window: watch existing videos, or compress new ones dropped in
incoming/ straight into the encrypted vault. Double-click to run.

The Compress button turns red and shows a count when it spots new files in
incoming/ -- it never compresses on its own, only when you click it, so a
long ffmpeg run (which freezes this window) only ever starts when you choose.

ponytail: no real "clear on USB unplug" detection -- that's OS-specific
(udev/WMI/IOKit) and not portable. Instead the password lives in memory only
for as long as this window stays open, and is never written to disk. Close
the window before you unplug the drive; open it again next time and re-enter
the password. Add real unplug detection only if this genuinely isn't enough.
"""
import glob
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import messagebox, simpledialog

import vaultlib

VAULT_DIR = "vault"
INCOMING = "incoming"
CRF = 28


class VaultApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.password = b""
        self.temp_files = []

        root.title("Videos")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.listbox = tk.Listbox(root, width=60, height=15)
        self.listbox.pack(padx=10, pady=10)
        self.listbox.bind("<Double-Button-1>", lambda e: self.watch_selected())

        btns = tk.Frame(root)
        btns.pack(pady=(0, 10))
        tk.Button(btns, text="Watch", command=self.watch_selected).pack(side=tk.LEFT, padx=5)
        self.compress_btn = tk.Button(btns, text="Compress", command=self.compress_now)
        self.compress_btn.pack(side=tk.LEFT, padx=5)
        self._compress_btn_default_bg = self.compress_btn.cget("bg")
        tk.Button(btns, text="Scan incoming/", command=self.scan_incoming).pack(side=tk.LEFT, padx=5)
        tk.Button(btns, text="Refresh", command=self.refresh).pack(side=tk.LEFT, padx=5)

        self.status = tk.Label(root, text="", fg="gray")
        self.status.pack(pady=(0, 10))

        self.unlock()
        self.refresh()
        self.scan_incoming()

    def set_status(self, msg: str) -> None:
        self.status.config(text=msg)
        self.root.update_idletasks()

    def unlock(self) -> None:
        is_new = not os.path.isdir(VAULT_DIR)
        while True:
            prompt = "Set a new vault password:" if is_new else "Password:"
            pw = simpledialog.askstring("Videos", prompt, show="*", parent=self.root)
            if pw is None:
                sys.exit(0)
            if is_new:
                confirm = simpledialog.askstring("Videos", "Confirm password:", show="*", parent=self.root)
                if confirm != pw:
                    messagebox.showerror("Videos", "Passwords did not match.")
                    continue
                self.password = pw.encode()
                return

            password = pw.encode()
            try:
                vaultlib.list_entries(VAULT_DIR, password)
            except ValueError:
                messagebox.showerror("Videos", "Wrong password.")
                continue
            self.password = password
            return

    def refresh(self) -> None:
        self.listbox.delete(0, tk.END)
        if os.path.isdir(VAULT_DIR):
            for name in vaultlib.list_entries(VAULT_DIR, self.password):
                self.listbox.insert(tk.END, name)
        self.set_status(f"{self.listbox.size()} video(s) in the vault.")

    def watch_selected(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        name = self.listbox.get(sel[0])
        fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(name)[1] or ".mp4")
        os.close(fd)
        try:
            vaultlib.extract_to(VAULT_DIR, self.password, name, tmp)
        except ValueError as e:
            os.remove(tmp)
            messagebox.showerror("Videos", str(e))
            return
        except OSError as e:
            os.remove(tmp)
            messagebox.showerror("Videos", f"Could not read {name} (is the drive still connected?):\n{e}")
            return
        self.temp_files.append(tmp)

        system = platform.system()
        if system == "Windows":
            os.startfile(tmp)  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.run(["open", tmp])
        else:
            subprocess.run(["xdg-open", tmp])
        self.set_status(f"Playing {name} -- pick another one anytime, temp files clean up when you close this window.")

    def scan_incoming(self) -> None:
        """Manual, one-shot check of incoming/ -- just flags the Compress button,
        never compresses by itself. Click Scan again after dropping new files."""
        os.makedirs(INCOMING, exist_ok=True)
        found = glob.glob(os.path.join(INCOMING, "*.mp4"))
        if found:
            self.compress_btn.config(text=f"Compress ({len(found)} new)", bg="#e74c3c", fg="white",
                                      activebackground="#c0392b", activeforeground="white")
        else:
            self.compress_btn.config(text="Compress", bg=self._compress_btn_default_bg, fg="black",
                                      activebackground=self._compress_btn_default_bg, activeforeground="black")

    def compress_now(self) -> None:
        found = glob.glob(os.path.join(INCOMING, "*.mp4"))
        if not found:
            messagebox.showinfo("Videos", f"Nothing waiting in '{INCOMING}/' right now.")
            return
        if not shutil.which("ffmpeg"):
            messagebox.showerror("Videos", "ffmpeg not found. Install it first (apt/brew/choco install ffmpeg).")
            return
        try:
            self.add_new(found)
        finally:
            # always resets the status/button, even if something below raised an
            # exception we didn't anticipate (e.g. the drive dropping mid-write)
            self.refresh()
            self.scan_incoming()

    def add_new(self, found: list) -> None:
        existing = set(vaultlib.list_entries(VAULT_DIR, self.password)) if os.path.isdir(VAULT_DIR) else set()
        added = []
        for path in found:
            name = os.path.basename(path)

            if name in existing:
                choice = messagebox.askyesnocancel(
                    "Videos",
                    f"'{name}' already exists in the vault.\n\n"
                    "Yes = replace it\nNo = rename the new one\nCancel = skip it for now",
                )
                if choice is None:
                    continue
                if choice is False:
                    base, ext = os.path.splitext(name)
                    n = 2
                    suggestion = f"{base} ({n}){ext}"
                    while suggestion in existing:
                        n += 1
                        suggestion = f"{base} ({n}){ext}"
                    new_name = simpledialog.askstring(
                        "Videos", "New name:", initialvalue=suggestion, parent=self.root
                    )
                    if not new_name:
                        continue
                    name = new_name

            self.set_status(f"Compressing {name}...")
            before = os.path.getsize(path)
            fd, tmp = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", path,
                     "-vcodec", "libx265", "-crf", str(CRF), "-preset", "medium",
                     "-tag:v", "hvc1", "-c:a", "copy", tmp],
                    check=True, capture_output=True, text=True,
                )
            except subprocess.CalledProcessError as e:
                os.remove(tmp)
                messagebox.showerror("Videos", f"ffmpeg failed on {name}:\n{e.stderr}")
                continue
            except OSError as e:
                if os.path.exists(tmp):
                    os.remove(tmp)
                messagebox.showerror("Videos", f"Compressing {name} failed (is the drive still connected?):\n{e}")
                continue
            after = os.path.getsize(tmp)
            try:
                vaultlib.put(VAULT_DIR, self.password, name, tmp)
                os.remove(tmp)
                os.remove(path)
            except OSError as e:
                messagebox.showerror("Videos", f"Could not save {name} to the vault (is the drive still connected?):\n{e}")
                continue
            existing.add(name)
            added.append(f"{name}: {before:,} -> {after:,} bytes ({100 * after / before:.0f}%)")

        if added:
            messagebox.showinfo("Videos", "Added:\n" + "\n".join(added))

    def on_close(self) -> None:
        for tmp in self.temp_files:
            try:
                os.remove(tmp)
            except OSError:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    VaultApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
