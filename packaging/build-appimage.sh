#!/usr/bin/env bash
# Build a portable AppImage from the PyInstaller onedir bundle.
#
#   pyinstaller packaging/hub-moon.spec         # → dist/hub-moon/
#   packaging/build-appimage.sh [version]       # → dist/HubMoon-<version>-x86_64.AppImage
#
# Needs `appimagetool` on PATH (grab it from
# https://github.com/AppImage/appimagetool/releases). mksquashfs is used by it.
# APPIMAGE_EXTRACT_AND_RUN=1 lets appimagetool run without FUSE (CI-friendly).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
ver="${1:-0.2.0}"

bundle="$repo/dist/hub-moon"
appdir="$repo/dist/HubMoon.AppDir"
out="$repo/dist/HubMoon-${ver}-x86_64.AppImage"

[ -d "$bundle" ] || { echo "error: $bundle not found — run pyinstaller first" >&2; exit 1; }

rm -rf "$appdir"
mkdir -p "$appdir/usr/bin" \
         "$appdir/usr/share/applications" \
         "$appdir/usr/share/icons/hicolor/scalable/apps" \
         "$appdir/usr/share/icons/hicolor/256x256/apps"

# the whole self-contained bundle lives under usr/bin (binary + _internal next to it)
cp -a "$bundle/." "$appdir/usr/bin/"

# .desktop — required at the AppDir root and under usr/share/applications
cp "$here/hub-moon.desktop" "$appdir/usr/share/applications/hub-moon.desktop"
cp "$here/hub-moon.desktop" "$appdir/hub-moon.desktop"

# icons: scalable svg + a 256px png (AppImage wants a real raster icon + .DirIcon)
cp "$here/hub-moon.svg" "$appdir/usr/share/icons/hicolor/scalable/apps/hub-moon.svg"
png="$appdir/usr/share/icons/hicolor/256x256/apps/hub-moon.png"
if command -v rsvg-convert >/dev/null; then
  rsvg-convert -w 256 -h 256 "$here/hub-moon.svg" -o "$png"
elif command -v magick >/dev/null; then
  magick -background none "$here/hub-moon.svg" -resize 256x256 "$png"
else
  echo "error: need rsvg-convert or imagemagick to rasterize the icon" >&2; exit 1
fi
cp "$png" "$appdir/hub-moon.png"
cp "$png" "$appdir/.DirIcon"

# AppRun: exec the bundled binary, forwarding args
cat > "$appdir/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/hub-moon" "$@"
EOF
chmod +x "$appdir/AppRun"

ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 appimagetool "$appdir" "$out"
rm -rf "$appdir"
echo "built $out"
