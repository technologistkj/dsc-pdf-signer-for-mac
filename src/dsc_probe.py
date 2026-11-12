"""Test a token driver (PKCS#11 library) in a throwaway process.

Run as:  <app> --probe-pkcs11 /path/to/driver.dylib
Prints one line of JSON. Loading a driver runs the vendor's code; some hang
when the Mac's smart-card service is stuck, so the caller runs this in a child
process with a timeout instead of loading untested drivers into the app.
"""
import json
import os
import sys


def main(path):
    res = {"path": path, "ok": False, "tokens": []}
    try:
        import pkcs11
        from pkcs11 import Attribute, ObjectClass
        lib = pkcs11.lib(path)
        res["ok"] = True
        res["maker"] = str(getattr(lib, "manufacturer_id", "") or "").strip()
        res["desc"] = str(getattr(lib, "library_description", "") or "").strip()
        for slot in lib.get_slots(token_present=True):
            entry = {"label": "", "certs": 0}
            try:
                tok = slot.get_token()
                entry["label"] = tok.label.strip()
                with tok.open() as s:
                    entry["certs"] = sum(1 for _ in s.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}))
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {e}"
            res["tokens"].append(entry)
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    sys.stdout.write(json.dumps(res) + "\n")
    sys.stdout.flush()
    os._exit(0)          # skip vendor-library teardown, which can hang


if __name__ == "__main__":
    main(sys.argv[1])
