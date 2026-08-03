#!/usr/bin/env bash
# ShortsAI installer — Linux, macOS and WSL.
#
#   curl -fsSL https://raw.githubusercontent.com/ashishprajapat0604/short_aiv2/version2/install.sh | bash
#
# Clones (or updates) the repo, then hands over to run.py which installs the
# Python deps, ffmpeg and the caption fonts, and starts the web UI.

set -euo pipefail

REPO="${SHORTSAI_REPO:-https://github.com/ashishprajapat0604/short_aiv2.git}"
BRANCH="${SHORTSAI_BRANCH:-version2}"
DEST="${SHORTSAI_DIR:-$HOME/shortsai}"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '\033[36m•\033[0m %s\n' "$1"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$1"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$1" >&2; exit 1; }

bold ""
bold "  ShortsAI installer"
echo ""

command -v git >/dev/null 2>&1 || die "git is required. Install it and re-run:
    Debian/Ubuntu : sudo apt-get install -y git
    Fedora        : sudo dnf install -y git
    Arch          : sudo pacman -S git
    macOS         : xcode-select --install"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    # 3.10+ is required by the pinned FastAPI/pydantic versions.
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done
[ -n "$PY" ] || die "Python 3.10+ is required but was not found.
    Debian/Ubuntu : sudo apt-get install -y python3 python3-venv
    Fedora        : sudo dnf install -y python3
    macOS         : brew install python
    Or download from https://www.python.org/downloads/"
ok "Using $($PY --version)"

if [ -d "$DEST/.git" ]; then
  info "Updating existing install at $DEST …"
  git -C "$DEST" fetch --depth 1 origin "$BRANCH"
  # Never clobber local edits: stop and let the user decide.
  if ! git -C "$DEST" diff --quiet || ! git -C "$DEST" diff --cached --quiet; then
    die "You have uncommitted changes in $DEST.
    Commit or stash them first, then re-run this installer."
  fi
  git -C "$DEST" checkout -q "$BRANCH" 2>/dev/null || git -C "$DEST" checkout -qb "$BRANCH" "origin/$BRANCH"
  git -C "$DEST" reset --hard -q "origin/$BRANCH"
  ok "Updated to the latest $BRANCH"
elif [ -e "$DEST" ]; then
  die "$DEST already exists but is not a git checkout.
    Move it aside, or set a different location:  SHORTSAI_DIR=~/somewhere-else bash install.sh"
else
  info "Cloning into $DEST …"
  git clone --depth 1 -b "$BRANCH" "$REPO" "$DEST"
  ok "Cloned"
fi

echo ""
info "Handing over to run.py (installs dependencies, ffmpeg and fonts, then starts) …"
echo ""
cd "$DEST"
exec "$PY" run.py --install-ffmpeg "$@"
