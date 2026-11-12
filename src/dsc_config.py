"""Per-user settings and data location for DSC Signer.

Everything the app stores (settings.json, the extracted cursive font) lives in
~/Library/Application Support/DSC Signer, or $DSC_SIGNER_HOME if set.
The token PIN is never stored.
"""
import json
import os
import sys

APP_DIR = os.environ.get("DSC_SIGNER_HOME") or os.path.expanduser(
    "~/Library/Application Support/DSC Signer")
SETTINGS = os.path.join(APP_DIR, "settings.json")


def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(SETTINGS, "w") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


def resource(name):
    """Path to a bundled resource (works from source and inside the .app)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "assets")
    return os.path.normpath(os.path.join(base, name))
