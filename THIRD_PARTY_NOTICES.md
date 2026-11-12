# Third-party components

The binary release of DSC Signer bundles the following open-source components.
Each one's licence text is included in the app bundle (`Contents/Resources/*.dist-info`), apart from Python, Tcl/Tk and PDFium, whose licences are linked in the table.

| Component | Licence |
|---|---|
| [Python 3.12](https://www.python.org/) | [PSF-2.0](https://docs.python.org/3/license.html) |
| [Tcl/Tk 8.6](https://www.tcl.tk/) | [BSD-style](https://www.tcl.tk/software/tcltk/license.html) |
| [pyHanko](https://github.com/MatthiasValvekens/pyHanko) and pyhanko-certvalidator | MIT |
| [python-pkcs11](https://github.com/pyauth/python-pkcs11) | MIT |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) and [PDFium](https://pdfium.googlesource.com/pdfium/) | Apache-2.0 / BSD-3-Clause ([PDFium licence](https://pdfium.googlesource.com/pdfium/+/main/LICENSE)) |
| [Pillow](https://python-pillow.org/) | MIT-CMU |
| [fontTools](https://github.com/fonttools/fonttools) | MIT |
| [uharfbuzz](https://github.com/harfbuzz/uharfbuzz) / HarfBuzz | Apache-2.0 / MIT |
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 OR BSD-3-Clause |
| asn1crypto, oscrypto, attrs, tzlocal, uritools, cffi | MIT |
| lxml, idna, pycparser | BSD-3-Clause |
| aiohttp, aiosignal, frozenlist, multidict, propcache, yarl | Apache-2.0 |
| aiohappyeyeballs, typing_extensions | PSF-2.0 |
| certifi | MPL-2.0 |

The app is packaged with [PyInstaller](https://pyinstaller.org/) (GPL-2.0-or-later with the bootloader exception, which allows distributing apps built with it under any licence).

The token's vendor driver (PKCS#11 library) and the Snell Roundhand font are **not** included. Both are used from the user's own system.
