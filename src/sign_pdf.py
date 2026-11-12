"""Command-line PDF signing with the DSC token (same engine as the DSC Signer app).

Usage: python -I sign_pdf.py <in.pdf> <out.pdf> [--page N] [--box x1,y1,x2,y2]
                                [--invisible] [--reason TEXT] [--location TEXT]
                                [--no-timestamp]
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dsc_core  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("outfile")
    ap.add_argument("--page", type=int, default=1, help="1-based; -1 = last page")
    ap.add_argument("--box", default="360,40,560,110")
    ap.add_argument("--invisible", action="store_true")
    ap.add_argument("--reason", default="I am the author of this document")
    ap.add_argument("--location", default="")
    ap.add_argument("--no-timestamp", action="store_true")
    a = ap.parse_args()

    from pyhanko.pdf_utils.reader import PdfFileReader
    with open(a.infile, "rb") as f:
        n = int(PdfFileReader(f, strict=False).root["/Pages"]["/Count"])
    page = a.page - 1 if a.page > 0 else n + a.page
    box = None if a.invisible else tuple(float(v) for v in a.box.split(","))

    pin = getpass.getpass("Token PIN: ")
    try:
        out = dsc_core.sign_pdf(pin, a.infile, a.outfile, page, box, a.reason, a.location,
                                timestamp=not a.no_timestamp)
    except dsc_core.SigningError as e:
        sys.exit(f"Error: {e}")
    print(f"Signed: {out}")


if __name__ == "__main__":
    main()
