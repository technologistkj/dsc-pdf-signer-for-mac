"""Find plugged-in DSC tokens and installed token drivers on this Mac.

No hard-coded list is needed for either:
- tokens are USB smart-card devices (USB interface class 11), whatever the brand;
- token drivers are PKCS#11 libraries, which all export `C_GetFunctionList`.
The brand table below only adds friendly names and download pages.
"""
import glob
import json
import os
import plistlib
import subprocess
import sys
import time

SMART_CARD_CLASS = 11

# Friendly names for common Indian DSC tokens. `url` is only set where the
# maker's official macOS download page is known; otherwise we search the web.
BRANDS = [
    dict(name="Hypersecu HyperPKI", vids={0x2CCF}, words=("hypersecu", "hyperpki", "hyp2003"),
         url="https://hypersecu.com/updates-india"),
    dict(name="SafeNet eToken", vids={0x0529}, words=("safenet", "etoken", "aladdin", "thales"), url=None),
    dict(name="ePass2003", vids={0x096E}, words=("epass", "feitian", "ftsafe"), url=None),
    dict(name="Watchdata ProxKey", vids={0x163C}, words=("proxkey", "watchdata"), url=None),
    dict(name="mToken", vids=set(), words=("mtoken", "longmai"), url=None),
    dict(name="Trust Key", vids=set(), words=("trustkey", "trust key"), url=None),
]
TOKEN_WORDS = ("token", "pkcs", "smart card", "smartcard", "dsc") + tuple(
    w for b in BRANDS for w in b["words"])


def _brand_for(vid, text):
    t = text.lower()
    for b in BRANDS:
        if vid in b["vids"] or any(w in t for w in b["words"]):
            return b
    return None


def _ioreg(args):
    try:
        out = subprocess.run(["/usr/sbin/ioreg", *args, "-a"], capture_output=True, timeout=10).stdout
        return plistlib.loads(out) if out.strip() else []
    except Exception:
        return []


def usb_tokens():
    """DSC tokens plugged in: [{name, vendor, product, vid, pid, brand}]."""
    # names of all USB devices, keyed by (vendor id, product id)
    names = {}

    def walk(n):
        if isinstance(n, dict):
            if n.get("idVendor") is not None:
                names[(n.get("idVendor"), n.get("idProduct"))] = (
                    (n.get("USB Vendor Name") or n.get("kUSBVendorString") or "").strip(),
                    (n.get("USB Product Name") or n.get("kUSBProductString") or "").strip())
            for c in n.get("IORegistryEntryChildren", []):
                walk(c)
        elif isinstance(n, list):
            for c in n:
                walk(c)
    walk(_ioreg(["-p", "IOUSB", "-l", "-w0"]))

    smart = set()
    for itf in _ioreg(["-r", "-c", "IOUSBHostInterface", "-l", "-w0"]):
        if isinstance(itf, dict) and itf.get("bInterfaceClass") == SMART_CARD_CLASS:
            smart.add((itf.get("idVendor"), itf.get("idProduct")))

    found = []
    for (vid, pid), (vendor, product) in names.items():
        text = f"{vendor} {product}"
        brand = _brand_for(vid, text)
        if (vid, pid) in smart or brand:
            label = " ".join(x for x in (vendor, product) if x) or "USB token"
            found.append(dict(name=label, vendor=vendor, product=product, vid=vid, pid=pid, brand=brand))
    return found


# ---------- drivers ----------
SEARCH_DIRS = ["/usr/local/lib", "/usr/local/lib/pkcs11", "/opt/homebrew/lib", "/opt/homebrew/lib/pkcs11",
               "/Library/OpenSC/lib", "/Library/OpenSC/lib/pkcs11"]
MAX_SIZE = 64 * 1024 * 1024
_sym_cache = {}


def _has_pkcs11_symbol(path):
    try:
        st = os.stat(path)
        if st.st_size > MAX_SIZE or st.st_size < 1024:
            return False
        key = (path, st.st_mtime, st.st_size)
        if key not in _sym_cache:
            with open(path, "rb") as f:
                _sym_cache[key] = b"C_GetFunctionList" in f.read()
        return _sym_cache[key]
    except OSError:
        return False


def candidate_drivers():
    """Installed files that look like token drivers (cheap: no code is loaded)."""
    paths = []
    for d in SEARCH_DIRS:
        paths += glob.glob(os.path.join(d, "*.dylib")) + glob.glob(os.path.join(d, "*.so"))
    paths += glob.glob("/Library/Frameworks/*.framework/Versions/*/*.dylib")
    paths += glob.glob("/Library/Frameworks/*.framework/*.dylib")
    # token utilities installed as apps (ProxKey, mToken, ...): look inside those only
    for app in glob.glob("/Applications/*.app") + glob.glob("/Applications/*/*.app"):
        if any(w in os.path.basename(app).lower() for w in TOKEN_WORDS):
            paths += glob.glob(os.path.join(app, "Contents", "**", "*.dylib"), recursive=True)
    seen, out = set(), []
    for p in paths:
        real = os.path.realpath(p)
        if real in seen or not os.path.isfile(real):
            continue
        base = os.path.basename(p).lower()
        if "spy" in base or base.startswith("libp11."):   # debugging proxies / helpers, not drivers
            continue
        seen.add(real)
        if _has_pkcs11_symbol(real):
            out.append(p)            # keep the nicer (symlinked) path
    return out


def _self_cmd():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dsc_probe.py")]


def probe(path, timeout=15):
    """Load a driver in a child process and report what it sees."""
    cmd = _self_cmd() + (["--probe-pkcs11", path] if getattr(sys, "frozen", False) else [path])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        line = (r.stdout.strip().splitlines() or ["{}"])[-1]
        res = json.loads(line)
    except subprocess.TimeoutExpired:
        return {"path": path, "ok": False, "hung": True,
                "error": "The driver stopped responding (the Mac's smart-card service may be stuck)."}
    except Exception as e:
        return {"path": path, "ok": False, "error": f"{type(e).__name__}: {e}"}
    err = res.get("error", "")
    if "incompatible architecture" in err or "mach-o file, but is an incompatible" in err:
        res["intel_only"] = True
    return res


def score(res):
    toks = res.get("tokens") or []
    if any(t.get("certs") for t in toks):
        return 3
    if toks:
        return 2
    return 1 if res.get("ok") else 0


def best_driver(results):
    """Pick the driver that works best; prefer a maker's driver over OpenSC."""
    def key(r):
        generic = "opensc" in os.path.basename(r["path"]).lower()
        return (score(r), not generic)
    good = [r for r in results if r.get("ok")]
    return max(good, key=key) if good else None


def recent_installers(max_age_h=24):
    """Driver installers the user has downloaded or opened recently."""
    now = time.time()
    dl = os.path.expanduser("~/Downloads")
    pats = [os.path.join(dl, "*.pkg"), os.path.join(dl, "*.dmg"), os.path.join(dl, "*.zip"),
            os.path.join(dl, "*", "*.pkg"), os.path.join(dl, "*", "*.dmg"),
            os.path.join(dl, "*", "*", "*.pkg"), os.path.join(dl, "*", "*", "*.dmg")]
    files = []
    for pat in pats:
        for p in glob.glob(pat):
            try:
                age = now - os.path.getmtime(p)
            except OSError:
                continue
            name = os.path.basename(p).lower()
            tokenish = any(w in name for w in TOKEN_WORDS + ("driver", "middleware", "sac", "hyp"))
            if tokenish or age < 3600:
                if age < max_age_h * 3600:
                    files.append((not tokenish, age, p))
    # installers inside disk images the user has opened
    for p in glob.glob("/Volumes/*/*.pkg") + glob.glob("/Volumes/*/*/*.pkg"):
        name = os.path.basename(p).lower()
        if any(w in name for w in TOKEN_WORDS + ("driver", "middleware", "hyp")):
            files.append((False, 0, p))
    return [p for _, _, p in sorted(files)][:4]


RESET_SCRIPT = ('do shell script "/usr/bin/pkill -9 -f usbsmartcardreaderd; true" '
                'with prompt "DSC Signer needs to restart the Mac\'s smart-card service so it can see '
                'your token." with administrator privileges')


def reset_reader():
    """Restart macOS's USB smart-card service (it restarts itself). Asks for the Mac password."""
    r = subprocess.run(["/usr/bin/osascript", "-e", RESET_SCRIPT], capture_output=True, text=True)
    if r.returncode != 0 and "-128" not in r.stderr:      # -128 = user cancelled
        raise RuntimeError(r.stderr.strip() or "Could not restart the smart-card service.")
    return r.returncode == 0
