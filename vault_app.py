#!/usr/bin/env python3
"""One window: watch existing videos, or compress new ones dropped in
incoming/ straight into the encrypted vault. Double-click to run.

The Compress button turns red and shows a count when it spots new files in
incoming/ -- it never compresses on its own, only when you click it. Once
clicked, ffmpeg runs in a background thread with a progress bar so the
window stays responsive; name-conflict dialogs are all resolved up front
on the main thread before that background work starts.

ponytail: no real "clear on USB unplug" detection -- that's OS-specific
(udev/WMI/IOKit) and not portable. Instead the password lives in memory only
for as long as this window stays open, and is never written to disk. Close
the window before you unplug the drive; open it again next time and re-enter
the password. Add real unplug detection only if this genuinely isn't enough.
"""
import glob
import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import vaultlib

VAULT_DIR = "vault"
INCOMING = "incoming"
CRF = 28
PRESET = "faster"  # ~40% faster than "medium" with similar output size on our own benchmark


class VaultApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.password = b""
        self.temp_files = []
        self.compressing = False
        self._current_proc = None

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

        self.progress = ttk.Progressbar(root, orient="horizontal", length=300, mode="determinate", maximum=100)
        self.progress.pack(pady=(0, 10))
        self._progress_queue = queue.Queue()

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

        # name-conflict dialogs need the main thread, so resolve all of them
        # up front -- only the (slow, freeze-prone) ffmpeg pass runs in the
        # background thread below.
        existing = set(vaultlib.list_entries(VAULT_DIR, self.password)) if os.path.isdir(VAULT_DIR) else set()
        jobs = []
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
            existing.add(name)
            jobs.append((path, name))

        if not jobs:
            return

        self.compress_btn.config(state=tk.DISABLED)
        self.compressing = True
        threading.Thread(target=self._compress_worker, args=(jobs,), daemon=True).start()
        self.root.after(100, self._poll_progress)

    def _probe_duration_seconds(self, path: str):
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, check=True,
            )
            return float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError, OSError):
            return None

    def _compress_worker(self, jobs: list) -> None:
        """Runs in a background thread -- never touch Tkinter widgets directly
        here, only push updates through self._progress_queue."""
        added = []
        for path, name in jobs:
            # a bug in one file's handling must never take the rest of the
            # batch down with it, so every per-file failure mode funnels
            # through here instead of being allowed to escape the loop.
            try:
                if self._compress_one(path, name, added):
                    self._progress_queue.put(("refresh",))
            except Exception as e:
                self._progress_queue.put(("error", f"Unexpected error compressing {name}, skipping it:\n{e}"))

        self._progress_queue.put(("done", added))

    def _compress_one(self, path: str, name: str, added: list) -> bool:
        before = os.path.getsize(path)
        duration = self._probe_duration_seconds(path)
        self._progress_queue.put(("status", name, duration is None))

        fd, tmp = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        try:
            proc = subprocess.Popen(
                ["ffmpeg", "-y", "-i", path,
                 "-vcodec", "libx265", "-crf", str(CRF), "-preset", PRESET,
                 "-tag:v", "hvc1", "-c:a", "copy",
                 "-progress", "pipe:1", "-nostats", "-loglevel", "error",
                 tmp],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            self._current_proc = proc
            for line in proc.stdout:
                if duration and line.startswith("out_time_ms="):
                    # ffmpeg emits "N/A" (and briefly a small negative value)
                    # before encoding actually starts producing timestamps
                    raw = line.strip().split("=", 1)[1]
                    try:
                        elapsed_seconds = int(raw) / 1_000_000
                    except ValueError:
                        continue
                    percent = max(0, min(100, elapsed_seconds / duration * 100))
                    self._progress_queue.put(("progress", percent))
            stderr = proc.stderr.read()
            code = proc.wait()
            self._current_proc = None
            if code != 0:
                raise subprocess.CalledProcessError(code, "ffmpeg", stderr=stderr)
        except subprocess.CalledProcessError as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            self._progress_queue.put(("error", f"ffmpeg failed on {name}:\n{e.stderr}"))
            return False
        except OSError as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            self._progress_queue.put(("error", f"Compressing {name} failed (is the drive still connected?):\n{e}"))
            return False

        after = os.path.getsize(tmp)
        try:
            vaultlib.put(VAULT_DIR, self.password, name, tmp)
            os.remove(tmp)
            os.remove(path)
        except OSError as e:
            self._progress_queue.put(("error", f"Could not save {name} to the vault (is the drive still connected?):\n{e}"))
            return False
        added.append(f"{name}: {before:,} -> {after:,} bytes ({100 * after / before:.0f}%)")
        return True

    def _poll_progress(self) -> None:
        try:
            while True:
                kind, *payload = self._progress_queue.get_nowait()
                if kind == "status":
                    name, indeterminate = payload
                    self.set_status(f"Compressing {name}...")
                    if indeterminate:
                        self.progress.config(mode="indeterminate")
                        self.progress.start(10)
                    else:
                        self.progress.stop()
                        self.progress.config(mode="determinate")
                        self.progress["value"] = 0
                elif kind == "progress":
                    self.progress["value"] = payload[0]
                elif kind == "refresh":
                    self.refresh()  # a video just landed in the vault -- show it now, don't wait for the batch
                elif kind == "error":
                    messagebox.showerror("Videos", payload[0])
                elif kind == "done":
                    self.progress.stop()
                    self.progress.config(mode="determinate")
                    self.progress["value"] = 0
                    self.compress_btn.config(state=tk.NORMAL)
                    self.compressing = False
                    self.refresh()
                    self.scan_incoming()
                    added = payload[0]
                    if added:
                        messagebox.showinfo("Videos", "Added:\n" + "\n".join(added))
                    return  # worker thread is done, stop polling
        except queue.Empty:
            pass
        self.root.after(100, self._poll_progress)

    def on_close(self) -> None:
        if self.compressing:
            if not messagebox.askyesno(
                "Videos",
                "A compression is still running. Closing now abandons it -- "
                "the video currently being compressed won't be saved. Close anyway?",
            ):
                return
            if self._current_proc is not None:
                self._current_proc.terminate()

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
