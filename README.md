# DSC Signer

Sign PDFs on macOS with a **DSC (Digital Signature Certificate) USB token**: open a PDF, drag a box where the signature should go, click **Sign**, enter the token PIN.

The signature is a standard PAdES/PKCS#7 signature (SHA-256, with an optional RFC 3161 trusted timestamp). It validates in Adobe Acrobat/Reader and other PDF viewers. The visible stamp looks like Acrobat's: the signer's name in cursive on the left over a faint seal, and the signing details on the right.

It exists because Adobe Acrobat on macOS often can't use these tokens: Acrobat's sandbox blocks the token driver.

## Requirements

- macOS 12 or later on Apple Silicon (M1 or later).
- A DSC USB token and its vendor driver (the PKCS#11 library). Install the driver from the token vendor's site.
  - Tested with: HyperPKI HYP2003 (driver library `/usr/local/lib/libcastle_v2.1.0.0.dylib`) holding an Indian CCA-chain Class 3 signing certificate.
  - Other PKCS#11 tokens should work. See [Using a different token](#using-a-different-token).

## Install (binary)

1. Download `DSC-Signer-<version>-macOS-arm64.zip` from [Releases](../../releases), unzip it, and move **DSC Signer.app** to your Applications folder.
2. The app is not notarised by Apple, so the first launch is blocked. Open it once, then go to **System Settings → Privacy & Security** and click **Open Anyway**. Alternatively, run:
   ```bash
   xattr -dr com.apple.quarantine "/Applications/DSC Signer.app"
   ```

## Usage

1. Plug in the token. The sidebar shows the certificate holder's name and the certificate's expiry date.
2. **Open PDF…** (⌘O), or right-click a PDF in Finder and choose **Open With → DSC Signer**. The last page opens by default.
3. Place the signature box:
   - drag on the page to draw it, or click to drop a default-size box;
   - drag inside the box to move it;
   - drag its corner to resize it.
   The box shows a live preview of the real stamp.
4. Set the reason and location (optional), then click **Sign…** (⌘↩). Choose where to save, then enter the PIN.

You can sign a document again to add more signatures. Each signature is saved as an incremental update, so earlier signatures stay valid.

## Command line

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -I src/sign_pdf.py in.pdf out.pdf --page -1 --box 360,40,560,110 \
    --reason "I approve this document" --location "City"
```

Options:

| Option | Meaning |
|---|---|
| `--page N` | Page for the signature box (1-based; `-1` is the last page). |
| `--box x1,y1,x2,y2` | Box position in PDF points, origin at the bottom left. |
| `--invisible` | Sign without a visible box. |
| `--no-timestamp` | Skip the trusted timestamp. |

The PIN is read from the terminal.

## Using a different token

By default the app looks for the HyperPKI/ePass2003 driver (`/usr/local/lib/libcastle*.dylib`). For another token, point the app at its PKCS#11 library in one of two ways:

- In `~/Library/Application Support/DSC Signer/settings.json`:
  ```json
  { "pkcs11_lib": "/path/to/vendor-pkcs11.dylib" }
  ```
- With the `DSC_PKCS11_LIB` environment variable (command line).

The signing certificate is found automatically: the app picks whichever certificate on the token matches a private key. A renewed certificate works without any setup.

## Privacy

- **PIN:** never stored or logged. It is held in memory only while signing.
- **Certificate and key:** the app reads the certificate from the token each time and doesn't copy it anywhere. The private key never leaves the token.
- **Network:** the only network request is the optional trusted timestamp. It sends a hash of the signature to `timestamp.digicert.com`, never the document.
- **Local files:** settings (last folder, reason, location, box size) are saved to `~/Library/Application Support/DSC Signer/`.

## Fonts

The cursive name uses **Snell Roundhand**. The app takes this font from the user's own macOS installation at run time and doesn't include it, because Apple's fonts may not be redistributed. The font's embedding permissions allow it to be embedded in signed PDFs.

## Troubleshooting

- **"No token detected" while the token is plugged in:**
  - macOS's USB smart-card service can hang and keep hold of the token.
  - Run `ps -ax | grep usbsmartcardreaderd`, then `sudo kill -9 <pid>`, then unplug the token and plug it back in.
  - `killall` and a normal `kill` don't work on that service.
- **Wrong PIN:** each wrong attempt counts toward locking the token. A locked token can only be unblocked by the certificate issuer.
- **Rotated pages:** visible signatures aren't supported on rotated pages. Sign invisibly, or choose another page.

## Build from source

```bash
./build_app.sh /path/on/an/APFS/volume   # writes dist/DSC Signer.app and a release .zip
```

Needs Python 3.12 from python.org (set `PYTHON=` to choose the interpreter). Build on an APFS or HFS+ volume: app bundles can't be built on FAT32 or exFAT drives.

Before publishing a release, run `scripts/check_release.sh` (see the script for usage). It searches the repo and the built app for personal details.

## Licence

_Choose a licence before publishing (for example, MIT)._ Third-party components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
