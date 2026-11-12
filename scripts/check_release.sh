#!/bin/bash
# Pre-release check: look for personal details in the repo and in a built app.
#
#   scripts/check_release.sh PATTERNS_FILE [path/to/DSC\ Signer.app]
#
# PATTERNS_FILE holds one string per line (your name, email, phone, address
# words, certificate serial, home-folder name...). KEEP IT OUTSIDE THE REPO.
# It is never printed; only which pattern matched where.
# The app's compressed Python archive is unpacked and searched too.
# Exit status 1 if anything matches.
set -uo pipefail
PAT="${1:?usage: $0 PATTERNS_FILE [app]}"
APP="${2:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
hits=0

echo "== Repo files"
if git -C "$ROOT" rev-parse >/dev/null 2>&1; then
  FILES=$(cd "$ROOT" && git ls-files --cached --others --exclude-standard)
else
  FILES=$(cd "$ROOT" && find . -type f -not -path '*/__pycache__/*' -not -path './build/*' -not -path './dist/*' -not -path './.git/*')
fi
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if m=$(grep -a -i -o -F -f "$PAT" "$ROOT/$f" | sort -u); then
    echo "  $f: $(echo "$m" | wc -l | tr -d ' ') pattern(s) matched"; hits=1
  fi
done <<< "$FILES"
for f in $(cd "$ROOT" && find . -type f \( -iname '*.pem' -o -iname '*.der' -o -iname '*.p12' -o -iname '*.pfx' -o -iname '*.key' -o -iname '*.pdf' -o -iname 'settings.json' \) -not -path './build/*' -not -path './dist/*'); do
  echo "  personal-looking file in repo: $f"; hits=1
done

if [ -n "$APP" ]; then
  echo "== App bundle: $APP"
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  EXE="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
  PYI="${PYI_PYTHON:-$ROOT/build/.venv/bin/python}"
  if [ -x "$PYI" ] && "$PYI" -c "import PyInstaller" 2>/dev/null; then
    "$PYI" -I - "$EXE" "$TMP" <<'EOF'
import os, sys
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
exe, out = sys.argv[1], sys.argv[2]
pkg = CArchiveReader(exe)
for name in pkg.toc:
    data = pkg.extract(name)
    if not data:
        continue
    p = os.path.join(out, name.replace("/", "_"))
    open(p, "wb").write(data)
    if name.endswith(".pyz"):
        z = ZlibArchiveReader(p)
        for mod in z.toc:
            code = z.extract(mod, raw=True)
            if code:
                open(os.path.join(out, "pyz_" + mod), "wb").write(code)
EOF
  else
    echo "  (PyInstaller not found; set PYI_PYTHON to a python with PyInstaller to unpack the archive)"
  fi
  # whole-word matches only, so ordinary words inside library binaries don't count
  if m=$(grep -a -r -i -o -w -F -f "$PAT" "$APP" "$TMP" 2>/dev/null | cut -d: -f1 | sort | uniq -c); then
    echo "$m" | sed 's/^/  /'; hits=1
  fi
  if m=$(grep -a -r -o -h -E "/Users/$(id -un)\b" "$APP" "$TMP" 2>/dev/null | sort -u); then
    echo "  your home-folder path is embedded"; hits=1
  fi
fi

if [ "$hits" = 0 ]; then echo "OK: nothing personal found."; else echo "REVIEW the matches above before publishing."; exit 1; fi
