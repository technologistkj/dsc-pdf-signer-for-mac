"""DSC Signer: sign PDFs with a PKCS#11 DSC (digital signature certificate)
USB token, with a visual signature-box picker.

Open a PDF, go to the page, drag a box where the signature should appear,
set reason/location, click Sign, enter the token PIN, choose where to save.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "--probe-pkcs11":
    import dsc_probe            # driver test in a child process (see dsc_tokens.probe)
    dsc_probe.main(sys.argv[2])

import datetime as _dt
import queue
import subprocess
import threading

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pypdfium2 as pdfium
from PIL import ImageTk

import dsc_config
import dsc_core
import dsc_setup
import dsc_stamp
from dsc_config import load_settings, save_settings

REASONS = [
    "I am the author of this document",
    "I approve this document",
    "Certified true copy",
    "I have reviewed this document",
]
DEFAULT_W, DEFAULT_H = 240, 70      # points
MIN_W, MIN_H = 60, 24               # points
HANDLE = 9                          # px
ACCENT = "#1a73e8"


class PinDialog(tk.Toplevel):
    def __init__(self, parent, signer):
        super().__init__(parent)
        self.title("Token PIN")
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        f = ttk.Frame(self, padding=18)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"Sign as {signer}" if signer else "Sign with DSC token",
                  font=("Helvetica", 13, "bold")).pack(anchor="w")
        ttk.Label(f, text="Enter the token PIN:").pack(anchor="w", pady=(10, 0))
        self.var = tk.StringVar()
        e = ttk.Entry(f, textvariable=self.var, show="•", width=30)
        e.pack(fill="x", pady=6)
        ttk.Label(f, text="Wrong attempts count toward locking the token.",
                  foreground="gray").pack(anchor="w")
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(14, 0))
        ttk.Button(b, text="Sign", command=self._ok, default="active").pack(side="right")
        ttk.Button(b, text="Cancel", command=self._cancel).pack(side="right", padx=6)
        self.bind("<Return>", lambda _: self._ok())
        self.bind("<Escape>", lambda _: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        e.focus_set()
        self.grab_set()
        self.wait_window()

    def _ok(self):
        if self.var.get():
            self.result = self.var.get()
            self.var.set("")
            self.destroy()

    def _cancel(self):
        self.var.set("")
        self.destroy()


class App:
    def __init__(self, root, initial=None):
        self.root = root
        self.s = load_settings()
        self.doc = None
        self.path = None
        self.page_index = 0
        self.scale = 1.0
        self.crop = (0, 0, 612, 792)
        self.ox = self.oy = 0
        self.photo = None
        self.rotated = False
        self.box = None            # (x1, y1, x2, y2) in PDF points, y up
        self.drag = None
        self.token = None
        self.busy = False
        self.q = queue.Queue()
        self._render_job = None
        self._box_job = None
        self._pv_key = self._pv_photo = None
        self._setup_shown = False

        root.title("DSC Signer")
        root.geometry(self.s.get("geometry", "1180x900"))
        root.minsize(820, 600)
        self._build()
        root.after(100, self._poll)
        self._refresh_token()
        if initial:
            root.after(200, lambda: self.open(initial))
        root.protocol("WM_DELETE_WINDOW", self._quit)

    # ---------- UI ----------
    def _build(self):
        r = self.root
        bar = ttk.Frame(r, padding=(10, 8))
        bar.pack(side="top", fill="x")
        ttk.Button(bar, text="Open PDF…", command=self.choose).pack(side="left")
        self.file_lbl = ttk.Label(bar, text="No document", foreground="gray")
        self.file_lbl.pack(side="left", padx=12)
        nav = ttk.Frame(bar)
        nav.pack(side="right")
        self.prev_btn = ttk.Button(nav, text="◀", width=3, command=lambda: self.goto(self.page_index - 1))
        self.prev_btn.pack(side="left")
        self.page_lbl = ttk.Label(nav, text="– / –", width=10, anchor="center")
        self.page_lbl.pack(side="left")
        self.next_btn = ttk.Button(nav, text="▶", width=3, command=lambda: self.goto(self.page_index + 1))
        self.next_btn.pack(side="left")

        body = ttk.Frame(r)
        body.pack(side="top", fill="both", expand=True)

        side = ttk.Frame(body, padding=(12, 4, 12, 12), width=310)
        side.pack(side="right", fill="y")
        side.pack_propagate(False)

        self.canvas = tk.Canvas(body, background="#6b6f76", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self._schedule_render())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._hover)

        tf = ttk.LabelFrame(side, text="Token", padding=10)
        tf.pack(fill="x")
        self.token_lbl = ttk.Label(tf, text="Checking…", wraplength=260, justify="left")
        self.token_lbl.pack(anchor="w")
        self.cert_lbl = ttk.Label(tf, text="", wraplength=260, justify="left", foreground="gray")
        self.cert_lbl.pack(anchor="w", pady=(4, 0))
        ttk.Button(tf, text="Token setup…", command=self.token_setup).pack(anchor="w", pady=(8, 0))

        sf = ttk.LabelFrame(side, text="Signature", padding=10)
        sf.pack(fill="x", pady=(12, 0))
        self.visible = tk.BooleanVar(value=self.s.get("visible", True))
        ttk.Checkbutton(sf, text="Visible signature box", variable=self.visible,
                        command=self._draw_box).pack(anchor="w")
        self.cursive = tk.BooleanVar(value=self.s.get("cursive", True))
        ttk.Checkbutton(sf, text="Signer's name in cursive (left side)", variable=self.cursive,
                        command=self._draw_box).pack(anchor="w", pady=(4, 0))
        self.seal = tk.BooleanVar(value=self.s.get("seal", True))
        ttk.Checkbutton(sf, text="Seal behind the name", variable=self.seal,
                        command=self._draw_box).pack(anchor="w", pady=(4, 0))
        ttk.Label(sf, text="Reason").pack(anchor="w", pady=(10, 2))
        self.reason = tk.StringVar(value=self.s.get("reason", REASONS[0]))
        ttk.Combobox(sf, textvariable=self.reason, values=REASONS).pack(fill="x")
        ttk.Label(sf, text="Location").pack(anchor="w", pady=(10, 2))
        self.location = tk.StringVar(value=self.s.get("location", ""))
        ttk.Entry(sf, textvariable=self.location).pack(fill="x")
        self.tsa = tk.BooleanVar(value=self.s.get("timestamp", True))
        ttk.Checkbutton(sf, text="Trusted timestamp (needs internet)",
                        variable=self.tsa).pack(anchor="w", pady=(10, 0))
        for v in (self.reason, self.location):
            v.trace_add("write", lambda *_: self._schedule_box())

        self.box_lbl = ttk.Label(side, text="", foreground="gray", wraplength=280, justify="left")
        self.box_lbl.pack(anchor="w", pady=(12, 0))
        ttk.Label(side, wraplength=280, justify="left", foreground="gray",
                  text="Drag on the page to draw the box. Click to drop a default-size box. "
                       "Drag inside the box to move it, or its corner to resize.").pack(anchor="w", pady=(6, 0))

        self.sign_btn = ttk.Button(side, text="Sign…", command=self.sign)
        self.sign_btn.pack(side="bottom", fill="x", ipady=6)
        self.status = ttk.Label(side, text="", wraplength=280, justify="left")
        self.status.pack(side="bottom", anchor="w", pady=(0, 8))

        menubar = tk.Menu(r)
        fm = tk.Menu(menubar, tearoff=0)
        fm.add_command(label="Open PDF…", accelerator="Command-O", command=self.choose)
        fm.add_command(label="Sign…", accelerator="Command-Return", command=self.sign)
        menubar.add_cascade(label="File", menu=fm)
        tm = tk.Menu(menubar, tearoff=0)
        tm.add_command(label="Token Setup…", command=self.token_setup)
        menubar.add_cascade(label="Token", menu=tm)
        r.config(menu=menubar)
        r.bind_all("<Command-o>", lambda _: self.choose())
        r.bind_all("<Command-Return>", lambda _: self.sign())
        r.bind("<Left>", lambda e: self._key_nav(e, -1))
        r.bind("<Right>", lambda e: self._key_nav(e, 1))
        try:  # files opened from Finder / dropped on the Dock icon
            r.createcommand("::tk::mac::OpenDocument", lambda *paths: paths and self.open(paths[0]))
        except Exception:
            pass
        self._update_controls()

    def _key_nav(self, e, d):
        if isinstance(e.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            return
        self.goto(self.page_index + d)

    def _update_controls(self):
        n = len(self.doc) if self.doc else 0
        self.page_lbl.config(text=f"{self.page_index + 1} / {n}" if n else "– / –")
        self.prev_btn.state(["!disabled"] if n and self.page_index > 0 else ["disabled"])
        self.next_btn.state(["!disabled"] if n and self.page_index < n - 1 else ["disabled"])
        ok = self.doc is not None and not self.busy
        self.sign_btn.state(["!disabled"] if ok else ["disabled"])

    def _set_status(self, text, color=None):
        self.status.config(text=text, foreground=color or "")

    # ---------- token ----------
    def _refresh_token(self):
        if not self.busy:
            threading.Thread(target=lambda: self.q.put(("token", dsc_core.token_status())),
                             daemon=True).start()
        self.root.after(5000, self._refresh_token)

    def _show_token(self, info):
        self.token = info
        if info is None:
            if dsc_core.pkcs11_lib():
                msg = "No token detected. Plug in your DSC token."
                hint = "Plugged in already? Click Token setup… to fix it."
            else:
                msg = "Your token's driver isn't installed yet."
                hint = "Click Token setup… and follow the three steps."
                if not self._setup_shown:          # guide first-time users straight away
                    self._setup_shown = True
                    self.root.after(300, self.token_setup)
            self.token_lbl.config(text=msg, foreground="#d93025")
            self.cert_lbl.config(text=hint, foreground="gray")
            return
        self.token_lbl.config(text=info.signer or info.label, foreground="")
        if info.expires:
            days = (info.expires - _dt.datetime.now(_dt.timezone.utc)).days
            exp = info.expires.astimezone().strftime("%d %b %Y")
            if days < 0:
                self.cert_lbl.config(text=f"Certificate EXPIRED on {exp}", foreground="#d93025")
            elif days <= 30:
                self.cert_lbl.config(text=f"Certificate expires {exp} ({days} days)", foreground="#e37400")
            else:
                self.cert_lbl.config(text=f"Valid until {exp} · token {info.serial}", foreground="gray")
        else:
            self.cert_lbl.config(text=f"Token {info.serial}", foreground="gray")
        self._draw_box()

    def token_setup(self):
        dsc_setup.TokenSetup.show(self.root, self._use_driver)

    def _use_driver(self, path):
        if self.s.get("pkcs11_lib") != path:
            self.s["pkcs11_lib"] = path
            save_settings(self.s)
        self._refresh_now()

    def _refresh_now(self):
        if not self.busy:
            threading.Thread(target=lambda: self.q.put(("token", dsc_core.token_status())),
                             daemon=True).start()

    # ---------- document ----------
    def choose(self):
        if self.busy:
            return
        p = filedialog.askopenfilename(
            title="Choose a PDF to sign",
            initialdir=self.s.get("last_dir", os.path.expanduser("~/Downloads")),
            filetypes=[("PDF documents", "*.pdf"), ("All files", "*")])
        if p:
            self.open(p)

    def open(self, path):
        try:
            doc = pdfium.PdfDocument(path)
            doc.init_forms()
        except Exception as e:
            messagebox.showerror("Can't open PDF", f"{os.path.basename(path)}\n\n{e}")
            return
        if self.doc is not None:
            self.doc.close()
        self.doc, self.path = doc, path
        self.s["last_dir"] = os.path.dirname(path)
        self.file_lbl.config(text=os.path.basename(path), foreground="")
        self.root.title(f"DSC Signer — {os.path.basename(path)}")
        self.page_index = len(doc) - 1          # signatures usually go on the last page
        self.box = None
        self._set_status("")
        self.render()

    def goto(self, i):
        if not self.doc or not (0 <= i < len(self.doc)):
            return
        self.page_index = i
        self.render()

    def _schedule_render(self):
        if self._render_job:
            self.root.after_cancel(self._render_job)
        self._render_job = self.root.after(120, self.render)

    def render(self):
        self._render_job = None
        c = self.canvas
        c.delete("all")
        cw, ch = max(c.winfo_width(), 100), max(c.winfo_height(), 100)
        if not self.doc:
            c.create_text(cw / 2, ch / 2, fill="white", font=("Helvetica", 15),
                          text="Open a PDF to sign  (⌘O)")
            self._update_controls()
            return
        page = self.doc[self.page_index]
        l, b, r, t = page.get_cropbox()
        self.crop = (l, b, r, t)
        self.rotated = page.get_rotation() % 360 != 0
        pw, ph = page.get_size()
        self.scale = max(0.1, min((cw - 40) / pw, (ch - 40) / ph))
        img = page.render(scale=self.scale, may_draw_forms=True).to_pil()
        self.photo = ImageTk.PhotoImage(img)
        self.ox, self.oy = (cw - img.width) // 2, (ch - img.height) // 2
        c.create_rectangle(self.ox + 3, self.oy + 3, self.ox + img.width + 3, self.oy + img.height + 3,
                           fill="#4a4d52", outline="")
        c.create_image(self.ox, self.oy, anchor="nw", image=self.photo)
        if self.box is None:
            w, h = self.s.get("box_size", [DEFAULT_W, DEFAULT_H])
            w, h = min(w, (r - l) - 20), min(h, (t - b) - 20)
            x2, y1 = r - 50, b + 70
            self.box = (x2 - w, y1, x2, y1 + h)
        self.box = self._clamp(self.box)
        self._update_controls()
        self._draw_box()

    # ---------- coordinates ----------
    def _to_canvas(self, x, y):
        l, b, r, t = self.crop
        return self.ox + (x - l) * self.scale, self.oy + (t - y) * self.scale

    def _to_pdf(self, cx, cy):
        l, b, r, t = self.crop
        x = l + (cx - self.ox) / self.scale
        y = t - (cy - self.oy) / self.scale
        return min(max(x, l), r), min(max(y, b), t)

    def _clamp(self, box):
        l, b, r, t = self.crop
        x1, y1, x2, y2 = box
        w, h = min(x2 - x1, r - l), min(y2 - y1, t - b)
        x1 = min(max(x1, l), r - w)
        y1 = min(max(y1, b), t - h)
        return (x1, y1, x1 + w, y1 + h)

    def _box_canvas(self):
        x1, y1, x2, y2 = self.box
        cx1, cy1 = self._to_canvas(x1, y2)
        cx2, cy2 = self._to_canvas(x2, y1)
        return cx1, cy1, cx2, cy2

    # ---------- box drawing / interaction ----------
    def _stamp_lines(self):
        who = (self.token.signer if self.token and self.token.signer else "<signer>")
        lines = [f"Digitally signed by {who}", "Date: <signing time>"]
        if self.reason.get().strip():
            lines.append(f"Reason: {self.reason.get().strip()}")
        if self.location.get().strip():
            lines.append(f"Location: {self.location.get().strip()}")
        return lines

    def _schedule_box(self):
        if self._box_job:
            self.root.after_cancel(self._box_job)
        self._box_job = self.root.after(200, self._draw_box)

    def _preview_photo(self, wpt, hpt):
        """Render the real stamp (same code that signs) at screen scale; cached."""
        signer = self.token.signer if self.token and self.token.signer else "Your Name"
        reason, location = self.reason.get().strip(), self.location.get().strip()
        key = (wpt, hpt, round(self.scale, 3), signer, reason, location,
               self.cursive.get(), self.seal.get())
        if key != self._pv_key:
            st = dsc_stamp.make_style(wpt, hpt, signer, bool(reason), bool(location),
                                      cursive=self.cursive.get(), seal=self.seal.get())
            ts = _dt.datetime.now().astimezone().strftime(st.timestamp_format)
            im = dsc_stamp.render_preview(wpt, hpt, st, {"signer": signer, "ts": ts,
                                                         "reason": reason, "location": location},
                                          scale=self.scale)
            self._pv_photo, self._pv_key = ImageTk.PhotoImage(im), key
        return self._pv_photo

    def _draw_box(self):
        self._box_job = None
        c = self.canvas
        c.delete("box")
        if not self.doc or self.box is None:
            self.box_lbl.config(text="")
            return
        if not self.visible.get():
            self.box_lbl.config(text="Invisible signature: no box will be drawn on the page.")
            return
        if self.rotated:
            self.box_lbl.config(text="This page is rotated; visible boxes aren't supported here. "
                                     "Untick 'Visible signature box' or choose another page.")
            return
        x1, y1, x2, y2 = self._box_canvas()
        bx1, by1, bx2, by2 = self.box
        sizing = self.drag is not None and self.drag[0] in ("resize", "new")
        drawn = False
        if not sizing:   # re-rendering on every mouse move while resizing is too slow
            try:
                ph = self._preview_photo(int(round(bx2 - bx1)), int(round(by2 - by1)))
                c.create_image(x1, y1, anchor="nw", image=ph, tags="box")
                drawn = True
            except Exception:
                pass
        if not drawn:
            lines = self._stamp_lines()
            longest = max(len(s) for s in lines)
            fs = int(min(12, (y2 - y1 - 8) / (len(lines) * 1.35), (x2 - x1 - 10) / (longest * 0.56)))
            if fs >= 5:
                c.create_text(x1 + 5, (y1 + y2) / 2, anchor="w", text="\n".join(lines),
                              font=("Helvetica", fs), fill="#202124", tags="box")
        c.create_rectangle(x1, y1, x2, y2, outline=ACCENT, width=2, tags="box")
        c.create_rectangle(x2 - HANDLE, y2 - HANDLE, x2, y2, fill=ACCENT, outline="", tags="box")
        self.box_lbl.config(text=f"Page {self.page_index + 1} · box at ({bx1:.0f}, {by1:.0f}) · "
                                 f"{bx2 - bx1:.0f} × {by2 - by1:.0f} pt")

    def _hit(self, cx, cy):
        if not self.box or not self.visible.get() or self.rotated:
            return None
        x1, y1, x2, y2 = self._box_canvas()
        if x2 - HANDLE - 3 <= cx <= x2 + 3 and y2 - HANDLE - 3 <= cy <= y2 + 3:
            return "resize"
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            return "move"
        return None

    def _hover(self, e):
        if not self.doc:
            return
        hit = self._hit(e.x, e.y)
        cur = {"resize": "bottom_right_corner", "move": "fleur"}.get(hit, "crosshair")
        try:
            self.canvas.config(cursor=cur)
        except tk.TclError:
            self.canvas.config(cursor="")

    def _press(self, e):
        if not self.doc or not self.visible.get() or self.rotated or self.busy:
            return
        hit = self._hit(e.x, e.y)
        if hit == "move":
            self.drag = ("move", e.x, e.y, self.box)
        elif hit == "resize":
            _, _, cx2, cy2 = self._box_canvas()
            self.drag = ("resize", cx2 - e.x, cy2 - e.y)   # keep the grab offset
        else:
            self.drag = ("new", e.x, e.y, self.box)

    def _motion(self, e):
        if not self.drag:
            return
        kind = self.drag[0]
        if kind == "move":
            _, sx, sy, (x1, y1, x2, y2) = self.drag
            dx, dy = (e.x - sx) / self.scale, -(e.y - sy) / self.scale
            self.box = self._clamp((x1 + dx, y1 + dy, x2 + dx, y2 + dy))
        elif kind == "resize":
            x1, _, _, y2 = self.box
            px, py = self._to_pdf(e.x + self.drag[1], e.y + self.drag[2])
            self.box = (x1, min(py, y2 - MIN_H), max(px, x1 + MIN_W), y2)
        else:
            _, sx, sy, _ = self.drag
            ax, ay = self._to_pdf(sx, sy)
            bx, by = self._to_pdf(e.x, e.y)
            self.box = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
        self._draw_box()

    def _release(self, e):
        if not self.drag:
            return
        kind = self.drag[0]
        if kind == "new":
            x1, y1, x2, y2 = self.box
            if x2 - x1 < MIN_W or y2 - y1 < MIN_H:
                if abs(e.x - self.drag[1]) < 4 and abs(e.y - self.drag[2]) < 4:
                    # simple click: drop a default-size box with its top-left at the click
                    w, h = self.s.get("box_size", [DEFAULT_W, DEFAULT_H])
                    px, py = self._to_pdf(e.x, e.y)
                    self.box = self._clamp((px, py - h, px + w, py))
                else:
                    self.box = self.drag[3]        # too small: keep the previous box
        x1, y1, x2, y2 = self.box
        self.s["box_size"] = [round(x2 - x1), round(y2 - y1)]
        self.drag = None
        self._draw_box()

    # ---------- signing ----------
    def sign(self):
        if not self.doc or self.busy:
            return
        visible = self.visible.get()
        if visible and self.rotated:
            messagebox.showwarning("Rotated page", "Visible signatures aren't supported on rotated pages. "
                                   "Untick 'Visible signature box' or pick another page.")
            return
        if self.token is None:
            if not messagebox.askokcancel("No token", "No DSC token detected. Plug it in, then click OK."):
                return
        stem, _ = os.path.splitext(os.path.basename(self.path))
        out = filedialog.asksaveasfilename(
            title="Save signed PDF as", initialdir=os.path.dirname(self.path),
            initialfile=f"{stem}-signed.pdf", defaultextension=".pdf",
            filetypes=[("PDF documents", "*.pdf")])
        if not out:
            return
        pin = PinDialog(self.root, self.token.signer if self.token else None).result
        if not pin:
            return
        args = dict(
            infile=self.path, outfile=out, page_index=self.page_index,
            box=self.box if visible else None,
            reason=self.reason.get().strip(), location=self.location.get().strip(),
            timestamp=self.tsa.get(), cursive=self.cursive.get(), seal=self.seal.get(),
        )
        self.busy = True
        self._update_controls()
        self._set_status("Signing… keep the token plugged in.")
        self.root.config(cursor="watch")

        def work(pin=pin):
            try:
                self.q.put(("signed", dsc_core.sign_pdf(pin, **args)))
            except dsc_core.SigningError as e:
                self.q.put(("error", str(e)))
            except Exception as e:
                self.q.put(("error", f"{type(e).__name__}: {e}"))
        threading.Thread(target=work, daemon=True).start()
        del pin

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "token":
                    self._show_token(val)
                elif kind == "signed":
                    self._done(val)
                elif kind == "error":
                    self._failed(val)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finish(self):
        self.busy = False
        self.root.config(cursor="")
        self._update_controls()
        self._save()

    def _done(self, out):
        self._finish()
        self._set_status(f"Signed ✓  {os.path.basename(out)}", "#188038")
        page = self.page_index
        self.open(out)
        self.goto(min(page, len(self.doc) - 1))
        self._set_status(f"Signed ✓  saved as {os.path.basename(out)}", "#188038")
        if messagebox.askyesno("Signed", f"Saved:\n{out}\n\nOpen it in your PDF viewer to check the signature?"):
            subprocess.run(["open", out])

    def _failed(self, msg):
        self._finish()
        self._set_status("Signing failed.", "#d93025")
        messagebox.showerror("Signing failed", msg)

    # ---------- lifecycle ----------
    def _save(self):
        self.s.update(reason=self.reason.get(), location=self.location.get(),
                      visible=self.visible.get(), timestamp=self.tsa.get(),
                      cursive=self.cursive.get(), seal=self.seal.get(),
                      geometry=self.root.geometry())
        save_settings(self.s)

    def _quit(self):
        if self.busy and not messagebox.askokcancel("Signing in progress", "Quit anyway?"):
            return
        self._save()
        self.root.destroy()


def main():
    root = tk.Tk(className="DSC Signer")
    try:
        ttk.Style(root).theme_use("aqua")
    except tk.TclError:
        pass
    # Dock / app-switcher icon (the process is Python, so set it explicitly)
    icon = dsc_config.resource("AppIcon.png")
    if os.path.exists(icon):
        try:
            root._icon = tk.PhotoImage(file=icon)
            root.iconphoto(True, root._icon)
        except tk.TclError:
            pass
    initial = next((a for a in sys.argv[1:] if a.lower().endswith(".pdf") and os.path.exists(a)), None)
    App(root, initial)
    root.mainloop()


if __name__ == "__main__":
    main()
