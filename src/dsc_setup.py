"""Token Setup assistant: three checks, each with a button that fixes it.

1. Is a DSC token plugged in?      (and which make it is)
2. Is its driver installed?        Download page, open the installer, find it automatically
3. Can the driver see the token?   Reset the Mac's smart-card service if it's stuck
"""
import os
import queue
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import dsc_core
import dsc_tokens

OK, WAIT, BAD = "#188038", "#5f6368", "#d93025"
ICON = {OK: "✓", WAIT: "…", BAD: "✕"}


class TokenSetup(tk.Toplevel):
    _open = None   # only one assistant window at a time

    @classmethod
    def show(cls, parent, on_driver):
        if cls._open is not None and cls._open.winfo_exists():
            cls._open.lift()
            return cls._open
        cls._open = cls(parent, on_driver)
        return cls._open

    def __init__(self, parent, on_driver):
        super().__init__(parent)
        self.on_driver = on_driver
        self.title("Set up your DSC token")
        self.resizable(False, False)
        self.transient(parent)
        self.q = queue.Queue()
        self.alive = True
        self.state = {}
        self.probed = {}          # driver path -> probe result
        self.last_usb = None
        self.busy = False
        self.retest = False        # set by the UI; the worker clears self.probed

        f = ttk.Frame(self, padding=20)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Set up your DSC token", font=("Helvetica", 17, "bold")).pack(anchor="w")
        ttk.Label(f, wraplength=520, justify="left", foreground="gray", text=(
            "Your Mac needs a small program from the token's maker, called a driver, to talk to "
            "the token (like a printer needs a printer driver). You install it once. "
            "Follow the three steps below; each one turns green when it's done.")).pack(anchor="w", pady=(6, 14))

        self.s1 = self._step(f, "1", "Plug in your DSC token")
        self.s2 = self._step(f, "2", "Install the token's driver")
        self.s2_actions = ttk.Frame(self.s2["body"])      # hidden once the driver is installed
        self.s2_actions.pack(anchor="w")
        b2 = ttk.Frame(self.s2_actions)
        b2.pack(anchor="w", pady=(6, 0))
        self.dl_btn = ttk.Button(b2, text="Download the driver", command=self._download)
        self.dl_btn.pack(side="left")
        self.inst_btn = ttk.Button(b2, text="Open the downloaded installer", command=self._open_installer)
        self.inst_btn.pack(side="left", padx=8)
        self.s2_help = ttk.Label(self.s2_actions, wraplength=470, justify="left", foreground="gray", text="")
        self.s2_help.pack(anchor="w", pady=(6, 0))
        b2b = ttk.Frame(self.s2_actions)
        b2b.pack(anchor="w", pady=(4, 0))
        ttk.Button(b2b, text="I've installed it: check again", command=self._rescan).pack(side="left")
        ttk.Button(b2b, text="Choose driver file…", command=self._choose).pack(side="left", padx=8)

        self.s3 = self._step(f, "3", "Check that the token works")
        self.fix_btn = ttk.Button(self.s3["body"], text="Reset token connection", command=self._reset)
        self.s3_help = ttk.Label(self.s3["body"], wraplength=470, justify="left", foreground="gray", text="")

        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(16, 0))
        self.done_btn = ttk.Button(bar, text="Close", command=self._close, default="active")
        self.done_btn.pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _: self._close())

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + 60
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self._set(self.s1, WAIT, "Looking for your token…")
        self._set(self.s2, WAIT, "Looking for the driver…")
        self._set(self.s3, WAIT, "")
        threading.Thread(target=self._worker, daemon=True).start()
        self.after(150, self._poll)

    # ---------- layout helpers ----------
    def _step(self, parent, num, title):
        fr = ttk.Frame(parent)
        fr.pack(fill="x", pady=(0, 12))
        icon = tk.Label(fr, text="…", width=2, font=("Helvetica", 18, "bold"), fg=WAIT)
        icon.pack(side="left", anchor="n")
        body = ttk.Frame(fr)
        body.pack(side="left", fill="x", expand=True)
        ttk.Label(body, text=f"Step {num}.  {title}", font=("Helvetica", 13, "bold")).pack(anchor="w")
        status = ttk.Label(body, text="", wraplength=470, justify="left")
        status.pack(anchor="w", pady=(2, 0))
        return dict(icon=icon, status=status, body=body)

    def _set(self, step, color, text):
        step["icon"].config(text=ICON[color], fg=color)
        step["status"].config(text=text, foreground=color if color == BAD else "")

    # ---------- background checks ----------
    def _worker(self):
        rescan_every, n = 4, 0
        while self.alive:
            if not self.busy:
                try:
                    self.q.put(("state", self._check(force=(n % rescan_every == 0))))
                except Exception as e:
                    self.q.put(("state", {"error": str(e)}))
            n += 1
            for _ in range(30):            # ~3 s, but wake quickly when closed or asked to retest
                if not self.alive:
                    return
                if self.retest:
                    break
                time.sleep(0.1)

    def _check(self, force=False):
        st = {"usb": dsc_tokens.usb_tokens()}
        usb_key = tuple((t["vid"], t["pid"]) for t in st["usb"])
        if self.retest:
            self.retest = False
            self.probed.clear()
        if usb_key != self.last_usb:
            self.probed.clear()            # token plugged/unplugged: test drivers again
            self.last_usb = usb_key
        current = dsc_core.pkcs11_lib()
        cands = dsc_tokens.candidate_drivers()
        if current and current not in cands:
            cands.insert(0, current)
        todo = [p for p in cands
                if p not in self.probed or (force and dsc_tokens.score(self.probed[p]) < 3)]
        if todo:                       # each test can take a few seconds: run them side by side
            with ThreadPoolExecutor(max_workers=min(6, len(todo))) as ex:
                for p, r in zip(todo, ex.map(dsc_tokens.probe, todo)):
                    self.probed[p] = r
        results = [self.probed[p] for p in cands]
        best = dsc_tokens.best_driver(results)
        cur = self.probed.get(current) if current else None
        # switch only when the current driver is missing or a different one sees the token better
        if best and (cur is None or dsc_tokens.score(best) > dsc_tokens.score(cur)):
            st["new_driver"] = best["path"]
            cur = best
        st["driver"] = cur
        st["results"] = results
        st["installers"] = dsc_tokens.recent_installers()
        return st

    def _poll(self):
        if not self.alive:
            return
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "state":
                    self._show(val)
                elif kind == "reset":
                    self._after_reset(val)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    # ---------- show state ----------
    def _brand(self):
        for t in self.state.get("usb", []):
            if t.get("brand"):
                return t["brand"]
        return None

    def _show(self, st):
        self.state = st
        if st.get("new_driver"):
            self.on_driver(st["new_driver"])
        usb = st.get("usb", [])
        drv = st.get("driver")
        results = st.get("results", [])

        # step 1
        if usb:
            t = usb[0]
            b = t.get("brand")
            extra = f" (a {b['name']} token)" if b and b["name"].lower() not in t["name"].lower() else ""
            self._set(self.s1, OK, f"Found: {t['name']}{extra}")
        else:
            self._set(self.s1, WAIT,
                      "No token found yet. Plug it into a USB port (use a USB-C adapter if your Mac "
                      "has only USB-C ports). If it's already plugged in, unplug it and plug it back in.")

        # step 2
        brand = self._brand()
        who = brand["name"] if brand else (usb[0]["name"] if usb else "your token")
        intel = [r for r in results if r.get("intel_only")]
        if drv and drv.get("ok"):
            maker = drv.get("maker") or os.path.basename(drv["path"])
            self._set(self.s2, OK, f"Installed: {maker} driver")
            self.s2_help.config(text="")
        elif intel and not drv:
            self._set(self.s2, BAD, "A driver is installed, but it only works on older Intel Macs. "
                                    f"Ask the company that issued your DSC for the Apple Silicon (M1/M2/M3/M4) "
                                    f"driver for {who}.")
        else:
            self._set(self.s2, BAD if usb else WAIT, f"Not installed yet. You need the macOS driver for {who}.")
        installers = st.get("installers", [])
        have_driver = bool(drv and drv.get("ok"))
        if have_driver:
            self.s2_actions.pack_forget()
        elif not self.s2_actions.winfo_ismapped():
            self.s2_actions.pack(anchor="w")
        self.dl_btn.state(["disabled"] if have_driver else ["!disabled"])
        if installers and not have_driver:
            if not self.inst_btn.winfo_ismapped():
                self.inst_btn.pack(side="left", padx=8)
        else:
            self.inst_btn.pack_forget()
        if not have_driver:
            if installers:
                self.s2_help.config(text=(
                    f"Found in your Downloads: {os.path.basename(installers[0])}. Click "
                    "“Open the downloaded installer”, then Continue → Install, and type your Mac "
                    "password when asked. This window notices automatically when it's done."))
            elif brand and brand.get("url"):
                self.s2_help.config(text=(
                    f"“Download the driver” opens {brand['name']}'s official download page. "
                    "Download the macOS version. Then come back here."))
            else:
                self.s2_help.config(text=(
                    "Your DSC provider (the company that sold you the DSC) gives the driver for your token; "
                    "it's often on the same website. “Download the driver” searches the web for it. "
                    "Choose the macOS version."))

        # step 3
        self.fix_btn.pack_forget()
        self.s3_help.pack_forget()
        toks = (drv or {}).get("tokens") or []
        hung = any(r.get("hung") for r in results)
        if have_driver and any(t.get("certs") for t in toks):
            self._set(self.s3, OK, f"Ready. Your certificate is on the token “{toks[0].get('label') or 'token'}”. "
                                   "You can close this window and sign.")
            self.done_btn.config(text="Done")
        elif have_driver and toks:
            self._set(self.s3, BAD, "The token is connected but no certificate was found on it. "
                                    "Contact your DSC provider: the certificate may not have been downloaded to the token yet.")
        elif usb and (have_driver or hung):
            self._set(self.s3, BAD, "Your Mac sees the token, but the driver can't reach it. This happens when "
                                    "the Mac's smart-card service gets stuck.")
            self.fix_btn.pack(anchor="w", pady=(6, 0))
            self.s3_help.config(text="This restarts that service. You'll be asked for your Mac password. "
                                     "Then unplug the token and plug it back in.")
            self.s3_help.pack(anchor="w", pady=(4, 0))
        else:
            self._set(self.s3, WAIT, "Waiting for steps 1 and 2.")

    # ---------- actions ----------
    def _download(self):
        brand = self._brand()
        if brand and brand.get("url"):
            url = brand["url"]
        else:
            usb = self.state.get("usb", [])
            name = brand["name"] if brand else (usb[0]["name"] if usb else "DSC USB token")
            url = "https://www.google.com/search?q=" + urllib.parse.quote(f"{name} macOS driver download")
        webbrowser.open(url)

    def _open_installer(self):
        inst = self.state.get("installers") or []
        if inst:
            subprocess.run(["/usr/bin/open", inst[0]])
            self.s2_help.config(text=(
                "The installer is opening. Click Continue → Install and type your Mac password. "
                "If your Mac says it can't open the installer, Control-click the file in Finder and "
                "choose Open. (A .dmg or .zip opens a window or folder: open the installer inside it.)"))

    def _rescan(self):
        self.retest = True
        self._set(self.s2, WAIT, "Checking…")

    def _choose(self):
        p = filedialog.askopenfilename(parent=self, title="Choose the token driver (PKCS#11 library)",
                                       initialdir="/usr/local/lib",
                                       filetypes=[("Driver libraries", "*.dylib *.so"), ("All files", "*")])
        if not p:
            return
        r = dsc_tokens.probe(p)
        if not r.get("ok"):
            msg = ("That driver only works on Intel Macs." if r.get("intel_only") else
                   f"That file isn't a working token driver.\n\n{r.get('error', '')}")
            messagebox.showerror("Not a token driver", msg, parent=self)
            return
        self.on_driver(p)
        self._rescan()

    def _reset(self):
        self.busy = True
        self.fix_btn.state(["disabled"])
        self._set(self.s3, WAIT, "Restarting the smart-card service…")

        def work():
            try:
                self.q.put(("reset", dsc_tokens.reset_reader()))
            except Exception as e:
                self.q.put(("reset", e))
        threading.Thread(target=work, daemon=True).start()

    def _after_reset(self, res):
        self.busy = False
        self.fix_btn.state(["!disabled"])
        if isinstance(res, Exception):
            messagebox.showerror("Couldn't reset", str(res), parent=self)
        elif res:
            self.retest = True
            messagebox.showinfo("Now unplug and replug",
                                "Done. Now unplug the token, wait 3 seconds, and plug it back in.", parent=self)

    def _close(self):
        self.alive = False
        TokenSetup._open = None
        self.destroy()
