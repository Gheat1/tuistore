#!/bin/sh
# tuistore installer — picks the best available Python tool installer and
# uses it. Safe to re-run (always installs/upgrades to the latest commit).
#
#   curl -fsSL https://raw.githubusercontent.com/Gheat1/tuistore/main/install.sh | sh
set -e

REPO="git+https://github.com/Gheat1/tuistore"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
ok()    { printf '\033[1;32m✓\033[0m %s\n' "$1"; }
err()   { printf '\033[1;31m✗\033[0m %s\n' "$1" >&2; }

if command -v uv >/dev/null 2>&1; then
    info "installing with uv…"
    uv tool install --force --python 3.11 "$REPO"
    ok "installed. run: tuistore"
    exit 0
fi

if command -v pipx >/dev/null 2>&1; then
    info "installing with pipx…"
    pipx install --force "$REPO"
    ok "installed. run: tuistore"
    exit 0
fi

if command -v python3 >/dev/null 2>&1; then
    info "no uv or pipx found — installing uv first (recommended, isolated installs)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv installs itself to ~/.local/bin or ~/.cargo/bin depending on platform;
    # add the common locations for this run without requiring a new shell
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if command -v uv >/dev/null 2>&1; then
        uv tool install --force --python 3.11 "$REPO"
        ok "installed. run: tuistore"
        ok "(uv was just installed too — open a new shell so it's on PATH permanently)"
        exit 0
    fi
    err "uv install didn't complete — falling back to pip --user"
    python3 -m pip install --user --force-reinstall "$REPO"
    ok "installed. make sure your Python user bin directory is on PATH, then run: tuistore"
    exit 0
fi

err "no uv, pipx, or python3 found. install one of those first:"
err "  uv:    https://docs.astral.sh/uv/getting-started/installation/"
err "  pipx:  https://pipx.pypa.io/stable/installation/"
exit 1
