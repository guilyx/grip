#!/bin/sh
# Build the single-file grip binary with PyInstaller and pack it as
# dist/grip-<os>-<arch>.tar.gz with a matching .sha256 file.
#
#   scripts/build/package.sh            # uses the active Python (needs pyinstaller)
#
# The tarball layout is flat: grip, LICENSE. install.sh relies on it.
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
cd "$root"

os="$(uname -s)"
case "$os" in
  Linux) os=linux ;;
  Darwin) os=macos ;;
  *) echo "unsupported OS: $os" >&2; exit 1 ;;
esac
arch="$(uname -m)"
case "$arch" in
  x86_64 | amd64) arch=x86_64 ;;
  aarch64 | arm64) arch=aarch64 ;;
  *) echo "unsupported architecture: $arch" >&2; exit 1 ;;
esac

python -m PyInstaller --clean --noconfirm \
  --distpath "$root/dist/bin" --workpath "$root/build/pyinstaller" \
  "$here/grip.spec"

name="grip-$os-$arch"
stage="$root/build/$name"
rm -rf "$stage" && mkdir -p "$stage"
cp "$root/dist/bin/grip" "$stage/grip"
cp "$root/LICENSE" "$stage/LICENSE"
mkdir -p "$root/dist"
tar -C "$stage" -czf "$root/dist/$name.tar.gz" grip LICENSE
(cd "$root/dist" && { sha256sum "$name.tar.gz" 2>/dev/null || shasum -a 256 "$name.tar.gz"; } > "$name.tar.gz.sha256")

"$root/dist/bin/grip" --version
echo "built dist/$name.tar.gz"
cat "$root/dist/$name.tar.gz.sha256"
