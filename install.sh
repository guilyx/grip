#!/bin/sh
# grip installer
#
#   curl -fsSL https://raw.githubusercontent.com/guilyx/grip/main/install.sh | sh
#
# Installs grip from PyPI (the grip-hook package) as a command-line tool, with uv when it
# is there and pipx otherwise. You get `grip` and a `git grip` alias. The same as running
# `uv tool install grip-hook` or `pipx install grip-hook` yourself.
#
# Options (pass after `sh -s --` when piping):
#   --version X.Y.Z    install a specific version (default: latest)
#   --uninstall        remove grip
#   -h, --help         show this help
#
# Environment overrides: GRIP_VERSION, GRIP_PACKAGE (a name, wheel path or URL).
set -eu

PACKAGE="${GRIP_PACKAGE:-grip-hook}"
VERSION="${GRIP_VERSION:-}"
UNINSTALL=0

say() { printf '%s\n' "grip: $*"; }
die() { printf '%s\n' "grip: error: $*" >&2; exit 1; }

usage() {
  sed -n '2,15p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//' || true
}

while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ $# -ge 2 ] || die "--version needs a value"; VERSION="$2"; shift 2 ;;
    --version=*) VERSION="${1#*=}"; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
done

VERSION="${VERSION#v}"
spec="$PACKAGE"
[ -n "$VERSION" ] && spec="$PACKAGE==$VERSION"

if command -v uv >/dev/null 2>&1; then
  tool=uv
elif command -v pipx >/dev/null 2>&1; then
  tool=pipx
else
  die "grip installs with uv or pipx and neither is on PATH. Get uv with
    curl -LsSf https://astral.sh/uv/install.sh | sh
then run this again, or run: uv tool install grip-hook"
fi

if [ "$UNINSTALL" = 1 ]; then
  case "$tool" in
    uv) uv tool uninstall grip-hook ;;
    pipx) pipx uninstall grip-hook ;;
  esac
  exit 0
fi

say "installing $spec with $tool"
# --force replaces an older grip on PATH, such as a binary from an earlier install.sh.
case "$tool" in
  uv) uv tool install --force "$spec" ;;
  pipx) pipx install --force "$spec" ;;
esac

if command -v grip >/dev/null 2>&1; then
  say "installed $(grip --version)"
else
  say "installed, but grip is not on your PATH yet. Run '$tool ensurepath' (uv: 'uv tool update-shell') and open a new shell."
fi
say "next: cd into a repository and run 'grip init' (docs: https://guilyx.github.io/grip/)"
