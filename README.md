# DSC Signer

Sign PDFs on macOS with a **DSC (Digital Signature Certificate) USB token**: open a PDF, drag a box where the signature should go, click **Sign**, enter the token PIN.

The signature is a standard PAdES/PKCS#7 signature (SHA-256, with an optional RFC 3161 trusted timestamp). It validates in Adobe Acrobat/Reader and other PDF viewers. The visible stamp looks like Acrobat's: the signer's name in cursive on the left over a faint seal, and the signing details on the right.

It exists because Adobe Acrobat on macOS often can't use these tokens: Acrobat's sandbox blocks the token driver.

## What you need

- **A Mac with an Apple chip** (M1 or newer: most Macs sold since late 2020), running macOS 12 or later. To check, open the Apple menu  → **About This Mac** and look for "Chip: Apple M…".
- **Your DSC USB token**: the USB stick your Digital Signature Certificate came on.
- **The token's driver**, installed once. DSC Signer walks you through this (see [Setting up your token](#setting-up-your-token)).

## Install (binary)

1. Download `DSC-Signer-<version>-macOS-arm64.dmg` from [Releases](../../releases), open it, and drag **DSC Signer** onto the **Applications** folder.
2. The app is not notarised by Apple, so the first launch is blocked. Open it once, then go to **System Settings → Privacy & Security** and click **Open Anyway**. Alternatively, run:
   ```bash
   xattr -dr com.apple.quarantine "/Applications/DSC Signer.app"
   ```

## Setting up your token

This is done once per Mac and takes about five minutes.

**Why it's needed.** Each DSC token brand talks to the Mac through a small program from its maker, called a *driver*, much as a printer needs a printer driver. macOS doesn't include these drivers, so it has to be installed once. DSC Signer can't include it either: each maker publishes its own.

**What DSC Signer does for you.** The first time you open it, or whenever you click **Token setup…**, it opens **Set up your DSC token** with three steps. Each step turns green when it's done.

1. **Plug in your DSC token.** DSC Signer recognises the token and tells you its make, for example "a Hypersecu HyperPKI token".
2. **Install the token's driver.**
   - **Download the driver** opens the maker's official download page if DSC Signer knows it. Otherwise it searches the web for that exact token's Mac driver.
   - Once the download finishes, an **Open the downloaded installer** button appears. Click it, then click **Continue** and **Install**, and type your Mac password when asked (it's the password you use to log in to the Mac).
   - DSC Signer notices the driver by itself as soon as the installation finishes. There's nothing to configure.
3. **Check that the token works.** DSC Signer confirms it can read your certificate. If the Mac sees the token but the driver can't reach it, the step offers **Reset token connection**. This happens when a macOS service gets stuck. The button asks for your Mac password, then tells you to unplug the token and plug it back in.

**Where the driver comes from.** The company that issued your DSC, or sold you the token, provides the Mac driver. It's usually on their website, often under "Downloads" or "Token drivers".

| Token | Mac driver |
|---|---|
| Hypersecu HyperPKI (HYP2003) | [hypersecu.com/updates-india](https://hypersecu.com/updates-india). Tested with DSC Signer. |
| Other tokens (ePass2003, Watchdata ProxKey, mToken, SafeNet eToken, Trust Key, …) | From your DSC provider or the token maker. These should work if the maker offers a driver for Macs with Apple chips, but they haven't been tested yet. |

**If something goes wrong:**

- **"The installer can't be opened because Apple cannot check it":** in Finder, hold **Control**, click the installer file, choose **Open**, then **Open** again.
- **"A driver is installed, but it only works on older Intel Macs":** the maker's driver is out of date. Ask your DSC provider for the version for Apple-chip Macs (M1, M2, M3, M4).
- **The token isn't found at all:** try another USB port or adapter. If the token has a light, it should come on.

<details>
<summary>For IT staff: driver locations and manual setup</summary>

DSC Signer looks for PKCS#11 libraries in:
- `/usr/local/lib`
- `/opt/homebrew/lib`
- `/Library/OpenSC/lib`
- `/Library/Frameworks/*.framework`
- the bundles of token utilities in `/Applications`

It identifies a driver by its `C_GetFunctionList` export, never by file name. Each candidate is loaded in a separate process with a timeout. The app then picks the driver that sees a token with certificates on it, preferring the maker's driver over OpenSC.

Known paths:
- HyperPKI: `/usr/local/lib/libcastle_v2.1.0.0.dylib`
- SafeNet Authentication Client: `/usr/local/lib/libeTPkcs11.dylib`

To set a driver by hand, either:
- use **Token setup… → Choose driver file…**;
- add `"pkcs11_lib": "/path/to/driver.dylib"` to `~/Library/Application Support/DSC Signer/settings.json`;
- or set the `DSC_PKCS11_LIB` environment variable (command line).

The signing certificate is the one whose public key matches a private key on the token, so a renewed DSC works with no extra setup.
</details>

## Usage

1. Plug in the token. The sidebar shows the certificate holder's name and the certificate's expiry date. The first time, the **Set up your DSC token** window opens instead (see below).
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

## Privacy

- **PIN:** never stored or logged. It is held in memory only while signing.
- **Certificate and key:** the app reads the certificate from the token each time and doesn't copy it anywhere. The private key never leaves the token.
- **Network:** the only network request is the optional trusted timestamp. It sends a hash of the signature to `timestamp.digicert.com`, never the document.
- **Local files:** settings (last folder, reason, location, box size) are saved to `~/Library/Application Support/DSC Signer/`.

## Fonts

The cursive name uses **Snell Roundhand**. The app takes this font from the user's own macOS installation at run time and doesn't include it, because Apple's fonts may not be redistributed. The font's embedding permissions allow it to be embedded in signed PDFs.

## Troubleshooting

- **"No token detected" while the token is plugged in:** click **Token setup…** and follow step 3 (**Reset token connection**). IT staff: this restarts macOS's `usbsmartcardreaderd` with `pkill -9`. That service can hang and keep hold of the token, and `killall` or a normal `kill` won't stop it.
- **Wrong PIN:** each wrong attempt counts toward locking the token. A locked token can only be unblocked by the certificate issuer.
- **Rotated pages:** visible signatures aren't supported on rotated pages. Sign invisibly, or choose another page.

## Build from source

```bash
./build_app.sh /path/on/an/APFS/volume   # writes dist/DSC Signer.app and a release .dmg
```

Needs Python 3.12 from python.org (set `PYTHON=` to choose the interpreter). Build on an APFS or HFS+ volume: app bundles can't be built on FAT32 or exFAT drives.

Before publishing a release, run `scripts/check_release.sh` (see the script for usage). It searches the repo and the built app for personal details.

## Licence

[MIT](LICENSE). Third-party components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
