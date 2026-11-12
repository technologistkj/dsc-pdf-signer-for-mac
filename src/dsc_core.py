"""Signing core for PKCS#11 DSC tokens (used by the GUI and CLI).

The signer certificate and its CA chain are read from the token at signing time:
the signer is whichever certificate's public key matches a private key on the
token, so a renewed DSC works without any re-configuration.
"""
import glob
import os
import threading
from dataclasses import dataclass

from pkcs11 import Attribute, ObjectClass, PinIncorrect, PinLocked
from asn1crypto import x509 as _x509
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields, signers, timestamps
from pyhanko.sign.pkcs11 import PKCS11Signer, open_pkcs11_session

import dsc_config
import dsc_stamp

# PKCS#11 library of the token driver. Override with "pkcs11_lib" in
# settings.json or the DSC_PKCS11_LIB environment variable.
LIB_CANDIDATES = [
    "/usr/local/lib/libcastle_v2.1.0.0.dylib",   # HyperPKI HYP2003 (India)
    "/usr/local/lib/libcastle*.dylib",           # other ePass2003-family drivers
]
TSA = "http://timestamp.digicert.com"

# The vendor PKCS#11 library is not safe to drive from two threads at once.
_token_lock = threading.Lock()


class SigningError(Exception):
    pass


@dataclass
class TokenInfo:
    label: str
    serial: str
    signer: str | None = None          # CN of the end-entity certificate
    expires: object | None = None      # datetime (UTC) of that certificate


def pkcs11_lib():
    """Path of the token's PKCS#11 library, or None if no driver is installed."""
    p = os.environ.get("DSC_PKCS11_LIB") or dsc_config.load_settings().get("pkcs11_lib")
    if p:
        return p if os.path.exists(p) else None
    for pat in LIB_CANDIDATES:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[0]
    return None


def token_status():
    """Return TokenInfo for the inserted token, or None. No PIN needed."""
    import pkcs11
    path = pkcs11_lib()
    if not path:
        return None
    with _token_lock:
        try:
            lib = pkcs11.lib(path)  # python-pkcs11 caches this per path
            for slot in lib.get_slots(token_present=True):
                tok = slot.get_token()
                info = TokenInfo(tok.label.strip(), tok.serial.decode(errors="replace").strip())
                try:
                    with tok.open() as s:
                        for c in s.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}):
                            cert = _x509.Certificate.load(bytes(c[Attribute.VALUE]))
                            if not cert.ca:
                                info.signer = cert.subject.native.get("common_name")
                                info.expires = cert.not_valid_after
                                break
                except Exception:
                    pass
                return info
        except Exception:
            return None
    return None


def _stamp_style(signer, box, reason, location, cursive, seal):
    w, h = (box[2] - box[0], box[3] - box[1]) if box else (210, 60)
    try:
        cn = signer.signing_cert.subject.native.get("common_name")
    except Exception:
        cn = None
    return dsc_stamp.make_style(w, h, cn, bool(reason), bool(location),
                                cursive=cursive and bool(cn), seal=seal)


def _unique_field_name(writer):
    existing = {name for name, _, _ in fields.enumerate_sig_fields(writer)}
    n = 1
    while f"Signature{n}" in existing:
        n += 1
    return f"Signature{n}"


def sign_pdf_with(signer, infile, outfile, page_index=0, box=None,
                  reason="", location="", timestamp=True, cursive=True, seal=True):
    """Sign `infile` -> `outfile` with any pyHanko signer.

    page_index: 0-based page for the visible box. box: (x1, y1, x2, y2) in PDF
    user-space points, or None for an invisible signature.
    """
    with open(infile, "rb") as inf:
        w = IncrementalPdfFileWriter(inf, strict=False)
        name = _unique_field_name(w)
        if box is None:
            spec = fields.SigFieldSpec(name)
        else:
            spec = fields.SigFieldSpec(name, on_page=page_index, box=tuple(int(round(v)) for v in box))
        meta = signers.PdfSignatureMetadata(
            field_name=name, reason=reason or None, location=location or None,
            md_algorithm="sha256",
        )
        pdf_signer = signers.PdfSigner(
            meta, signer=signer,
            timestamper=timestamps.HTTPTimeStamper(TSA) if timestamp else None,
            new_field_spec=spec,
            stamp_style=_stamp_style(signer, box, reason, location, cursive, seal),
        )
        tmp = outfile + ".partial"
        try:
            with open(tmp, "wb") as outf:
                pdf_signer.sign_pdf(
                    w, output=outf,
                    appearance_text_params={"reason": reason, "location": location},
                )
            os.replace(tmp, outfile)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return outfile


def sign_pdf(pin, infile, outfile, page_index=0, box=None, reason="", location="",
             timestamp=True, cursive=True, seal=True):
    """Log in to the token with `pin` and sign. Raises SigningError with a
    user-readable message on failure."""
    lib = pkcs11_lib()
    if not lib:
        raise SigningError("The token driver (PKCS#11 library) was not found. Install the "
                           "driver for your DSC token, or set \"pkcs11_lib\" in " + dsc_config.SETTINGS)
    with _token_lock:
        try:
            session = open_pkcs11_session(lib, user_pin=pin)
        except PinIncorrect:
            raise SigningError("Wrong PIN. Each wrong attempt counts toward locking the token.")
        except PinLocked:
            raise SigningError("The token PIN is locked. Contact the certificate issuer (CA) to unblock it.")
        except Exception as e:
            raise SigningError(f"Could not open the token: {e}. Is it plugged in?")
        try:
            keys = list(session.get_objects({Attribute.CLASS: ObjectClass.PRIVATE_KEY}))
            certs = [_x509.Certificate.load(bytes(c[Attribute.VALUE]))
                     for c in session.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE})]
            key_by_mod = {}
            for k in keys:
                try:
                    key_by_mod[int.from_bytes(k[Attribute.MODULUS], "big")] = k
                except Exception:
                    pass
            signer_cert = key = None
            for c in certs:
                pk = c.public_key.native["public_key"]
                mod = pk.get("modulus") if isinstance(pk, dict) else None
                if mod in key_by_mod:
                    signer_cert, key = c, key_by_mod[mod]
                    break
            if signer_cert is None:
                raise SigningError(
                    f"No certificate on the token matches a private key "
                    f"({len(certs)} certificates, {len(keys)} keys found).")
            chain = [c for c in certs if c is not signer_cert]
            key_id = bytes(key[Attribute.ID]) or None
            key_label = None if key_id else (key[Attribute.LABEL] or None)
            signer = PKCS11Signer(
                session, signing_cert=signer_cert, ca_chain=chain,
                key_id=key_id, key_label=key_label,
                other_certs_to_pull=None, bulk_fetch=False,
            )
            return sign_pdf_with(signer, infile, outfile, page_index, box,
                                 reason, location, timestamp, cursive, seal)
        except SigningError:
            raise
        except Exception as e:
            raise SigningError(f"{type(e).__name__}: {e}")
        finally:
            session.close()
