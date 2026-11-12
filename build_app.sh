#!/bin/bash
# Build a self-contained "DSC Signer.app" (Apple Silicon) with PyInstaller.
#
#   ./build_app.sh [build-dir]
#
# The build dir must be on an APFS/HFS+ volume (app bundles need symlinks and
# extended attributes). Default: ./build. Output: <build-dir>/dist/DSC Signer.app
# and a zip ready to attach to a GitHub release.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$SRC/build}"
PY="${PYTHON:-python3}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

"$PY" -m venv "$OUT/.venv"
"$OUT/.venv/bin/pip" install -q --upgrade pip
"$OUT/.venv/bin/pip" install -q -r "$SRC/requirements.txt" "pyinstaller==6.*"

VERSION="$(cat "$SRC/VERSION")"
cd "$OUT"
rm -rf build dist
"$OUT/.venv/bin/pyinstaller" --noconfirm --clean --windowed \
  --name "DSC Signer" \
  --osx-bundle-identifier "local.dsc-signer" \
  --target-arch arm64 \
  --icon "$SRC/assets/AppIcon.icns" \
  --add-data "$SRC/assets/AppIcon.png:." \
  --paths "$SRC/src" \
  --collect-all pypdfium2 --collect-all pypdfium2_raw \
  --collect-data pyhanko \
  --collect-submodules fontTools.ttLib.tables \
  --workpath "$OUT/build" --distpath "$OUT/dist" --specpath "$OUT/build" \
  "$SRC/src/dsc_signer.py"

APP="$OUT/dist/DSC Signer.app"
PL="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PL"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$PL" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$PL"
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 12.0" "$PL" 2>/dev/null || true
# Open PDFs from Finder ("Open With > DSC Signer") and Dock drops
/usr/libexec/PlistBuddy -c "Delete :CFBundleDocumentTypes" "$PL" 2>/dev/null || true
/usr/libexec/PlistBuddy \
  -c "Add :CFBundleDocumentTypes array" \
  -c "Add :CFBundleDocumentTypes:0 dict" \
  -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string PDF document" \
  -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Editor" \
  -c "Add :CFBundleDocumentTypes:0:LSHandlerRank string Alternate" \
  -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array" \
  -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:0 string com.adobe.pdf" "$PL"
# Info.plist changed, so re-apply the ad-hoc signature
codesign --force --deep --sign - "$APP"

ZIP="$OUT/dist/DSC-Signer-$VERSION-macOS-arm64.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"
echo "Built: $APP"
echo "Zip:   $ZIP"
