#!/bin/sh
# grip installer
#
#   curl -fsSL https://raw.githubusercontent.com/guilyx/grip/main/install.sh | sh
#
# Downloads the prebuilt grip binary for this machine from the GitHub release,
# verifies its SHA-256 checksum, and installs it to ~/.local/bin (plus a
# `git-grip` alias so `git grip` works). No Python, pip or PyPI involved.
#
# Options (pass after `sh -s --` when piping):
#   --version vX.Y.Z   install a specific release (default: latest)
#   --dir DIR          install directory (default: ~/.local/bin)
#   --uninstall        remove grip and git-grip from the install directory
#   -h, --help         show this help
#
# Environment overrides: GRIP_VERSION, GRIP_INSTALL_DIR, GRIP_DOWNLOAD_BASE.
set -eu

REPO="guilyx/grip"
VERSION="${GRIP_VERSION:-latest}"
INSTALL_DIR="${GRIP_INSTALL_DIR:-$HOME/.local/bin}"
BASE_URL="${GRIP_DOWNLOAD_BASE:-https://github.com/$REPO/releases}"
UNINSTALL=0

say() { printf '%s\n' "grip: $*"; }
die() { printf '%s\n' "grip: error: $*" >&2; exit 1; }

usage() {
  sed -n '2,17p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//' || true
}

while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ $# -ge 2 ] || die "--version needs a value"; VERSION="$2"; shift 2 ;;
    --version=*) VERSION="${1#*=}"; shift ;;
    --dir) [ $# -ge 2 ] || die "--dir needs a value"; INSTALL_DIR="$2"; shift 2 ;;
    --dir=*) INSTALL_DIR="${1#*=}"; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

if [ "$UNINSTALL" = 1 ]; then
  removed=0
  for f in grip git-grip; do
    if [ -e "$INSTALL_DIR/$f" ] || [ -L "$INSTALL_DIR/$f" ]; then
      rm -f "$INSTALL_DIR/$f" && removed=1
    fi
  done
  [ "$removed" = 1 ] && say "removed grip from $INSTALL_DIR" || say "nothing to remove in $INSTALL_DIR"
  exit 0
fi

os="$(uname -s)"
case "$os" in
  Linux) os=linux ;;
  Darwin) os=macos ;;
  MINGW* | MSYS* | CYGWIN* | Windows_NT)
    die "no prebuilt Windows binary yet; use the pre-commit framework hook instead (see the README)" ;;
  *) die "unsupported operating system: $os" ;;
esac

arch="$(uname -m)"
case "$arch" in
  x86_64 | amd64) arch=x86_64 ;;
  aarch64 | arm64) arch=aarch64 ;;
  *) die "unsupported architecture: $arch" ;;
esac

asset="grip-$os-$arch.tar.gz"
if [ "$VERSION" = latest ]; then
  url_dir="$BASE_URL/latest/download"
else
  case "$VERSION" in v*) ;; *) VERSION="v$VERSION" ;; esac
  url_dir="$BASE_URL/download/$VERSION"
fi

if command -v curl >/dev/null 2>&1; then
  fetch() { curl -fsSL --retry 3 -o "$2" "$1"; }
elif command -v wget >/dev/null 2>&1; then
  fetch() { wget -q -O "$2" "$1"; }
else
  die "need curl or wget"
fi

if command -v sha256sum >/dev/null 2>&1; then
  checksum() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
  checksum() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
  die "need sha256sum or shasum to verify the download"
fi

tmp="$(mktemp -d 2>/dev/null || mktemp -d -t grip)"
trap 'rm -rf "$tmp"' EXIT INT TERM

say "downloading $asset ($VERSION)"
fetch "$url_dir/$asset" "$tmp/$asset" || die "could not download $url_dir/$asset"
fetch "$url_dir/SHA256SUMS" "$tmp/SHA256SUMS" || die "could not download $url_dir/SHA256SUMS"

expected="$(grep " $asset\$" "$tmp/SHA256SUMS" | cut -d' ' -f1 || true)"
[ -n "$expected" ] || die "$asset is not listed in SHA256SUMS"
actual="$(checksum "$tmp/$asset")"
[ "$expected" = "$actual" ] || die "checksum mismatch for $asset (expected $expected, got $actual)"

tar -xzf "$tmp/$asset" -C "$tmp"
[ -f "$tmp/grip" ] || die "archive did not contain a grip binary"

mkdir -p "$INSTALL_DIR"
install -m 755 "$tmp/grip" "$INSTALL_DIR/grip"
ln -sf grip "$INSTALL_DIR/git-grip"

version_line="$("$INSTALL_DIR/grip" --version 2>/dev/null || true)"
say "installed ${version_line:-grip} to $INSTALL_DIR/grip"

case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *)
    say "$INSTALL_DIR is not on your PATH. Add this to your shell profile:"
    printf '\n    export PATH="%s:$PATH"\n\n' "$INSTALL_DIR"
    ;;
esac
say "next: cd into a repository and run 'grip install' (docs: https://guilyx.github.io/grip/)"
