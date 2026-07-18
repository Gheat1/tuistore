"""What's installed, and how to update / remove it.

Two sources of truth:

  * the **ledger** — a record of every tool tuistore installed for you (slug →
    manager + exact command + package). Because we know *how* it went on, we
    can update or uninstall it precisely.
  * **detection** — a fast scan of your ``PATH``; if a tool's binary is there
    we mark it installed even if tuistore didn't put it there (but we won't
    guess an uninstall command for something we didn't install).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from .installer import Method

LEDGER = Path.home() / ".local/state/tuistore/installed.json"


# ── the ledger ──────────────────────────────────────────────────────────────
def load_ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text())
    except Exception:
        return {}


def save_ledger(data: dict) -> None:
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(data, indent=1))
    except Exception:
        pass


def _today() -> str:
    try:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


def record_install(slug: str, name: str, method: Method) -> None:
    data = load_ledger()
    pkg = pkg_from_command(method.kind, method.command) or ""
    data[slug] = {
        "name": name,
        "kind": method.kind,
        "command": method.command,
        "pkg": pkg,
        "bin": pkg or name.lower(),
        "at": _today(),
    }
    save_ledger(data)


def forget(slug: str) -> None:
    data = load_ledger()
    if slug in data:
        data.pop(slug, None)
        save_ledger(data)


# ── pull the package/binary name out of an install command ──────────────────
# words that are managers/subcommands/flags, never the package itself
_NOISE = {
    "sudo", "install", "tool", "add", "in", "profile", "ref", "-s", "-S", "-g",
    "--global", "--force", "--locked", "-i", "cargo", "binstall", "brew", "uv",
    "uvx", "pipx", "pip", "pip3", "go", "npm", "pnpm", "yarn", "bun", "gem",
    "pacman", "yay", "paru", "apt", "apt-get", "nala", "dnf", "yum", "zypper",
    "xbps-install", "apk", "nix", "nix-env", "flatpak", "snap", "emerge",
    "python", "python3", "-m", "i",
}


def pkg_from_command(kind: str, command: str) -> str | None:
    """Best-effort package / target name from an install command."""
    if kind in ("script", "source"):
        return None
    tokens = command.replace("&&", " ").split()
    cands = [
        t for t in tokens
        if t.lower() not in _NOISE and not t.startswith("-")
    ]
    if kind == "go":
        # go install github.com/owner/repo/cmd/tool@latest -> "tool"
        for t in cands:
            if "/" in t or "@" in t:
                return t.split("@")[0].rstrip("/").split("/")[-1]
        return cands[-1].split("@")[0] if cands else None
    # drop bare URLs for non-go managers
    cands = [t for t in cands if not t.startswith("http")]
    if not cands:
        return None
    pkg = cands[0].split("@")[0]
    # a tap-qualified brew formula (user/tap/formula) installs with the full
    # path but updates/uninstalls with the bare formula name.
    if kind == "brew" and "/" in pkg:
        pkg = pkg.split("/")[-1]
    return pkg


# ── update / uninstall command derivation ───────────────────────────────────
_UNINSTALL = {
    "cargo": "cargo uninstall {pkg}",
    "cargo-binstall": "cargo uninstall {pkg}",
    "uv": "uv tool uninstall {pkg}",
    "pipx": "pipx uninstall {pkg}",
    "pip": "pip uninstall -y {pkg}",
    "brew": "brew uninstall {pkg}",
    "npm": "npm uninstall -g {pkg}",
    "pnpm": "pnpm remove -g {pkg}",
    "yarn": "yarn global remove {pkg}",
    "bun": "bun remove -g {pkg}",
    "gem": "gem uninstall {pkg}",
    "pacman": "sudo pacman -R {pkg}",
    "yay": "yay -R {pkg}",
    "paru": "paru -R {pkg}",
    "apt": "sudo apt remove {pkg}",
    "dnf": "sudo dnf remove {pkg}",
    "zypper": "sudo zypper remove {pkg}",
    "xbps": "sudo xbps-remove {pkg}",
    "apk": "sudo apk del {pkg}",
    "nix": "nix profile remove {pkg}",
    "flatpak": "flatpak uninstall {pkg}",
    "snap": "sudo snap remove {pkg}",
    "go": 'rm -f "$(go env GOPATH)/bin/{bin}"',
}

_UPDATE = {
    "cargo": "cargo install {pkg} --force",
    "cargo-binstall": "cargo binstall {pkg} --force",
    "uv": "uv tool upgrade {pkg}",
    "pipx": "pipx upgrade {pkg}",
    "pip": "pip install -U {pkg}",
    "brew": "brew upgrade {pkg}",
    "npm": "npm install -g {pkg}@latest",
    "pnpm": "pnpm add -g {pkg}@latest",
    "gem": "gem update {pkg}",
    "pacman": "sudo pacman -S {pkg}",
    "yay": "yay -S {pkg}",
    "paru": "paru -S {pkg}",
    "apt": "sudo apt install --only-upgrade {pkg}",
    "dnf": "sudo dnf upgrade {pkg}",
    "zypper": "sudo zypper update {pkg}",
    "xbps": "sudo xbps-install -Su {pkg}",
    "apk": "sudo apk add -u {pkg}",
    "nix": "nix profile upgrade {pkg}",
    "flatpak": "flatpak update {pkg}",
    "snap": "sudo snap refresh {pkg}",
}


def uninstall_command(rec: dict) -> str | None:
    kind, pkg, binn = rec.get("kind", ""), rec.get("pkg", ""), rec.get("bin", "")
    tmpl = _UNINSTALL.get(kind)
    if not tmpl or not (pkg or binn):
        return None
    return tmpl.format(pkg=pkg or binn, bin=binn or pkg)


def update_command(rec: dict) -> str | None:
    kind, pkg = rec.get("kind", ""), rec.get("pkg", "")
    if kind == "go":
        # re-run the original `go install …@latest`
        return rec.get("command")
    if kind == "source":
        return None
    tmpl = _UPDATE.get(kind)
    if not tmpl or not pkg:
        return None
    return tmpl.format(pkg=pkg)


# ── detection ───────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def path_binaries() -> frozenset[str]:
    """Every executable name on PATH — scanned once, cheap to test against."""
    found: set[str] = set()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        try:
            for entry in os.scandir(d):
                found.add(entry.name)
        except OSError:
            continue
    return frozenset(found)


def refresh_path() -> None:
    path_binaries.cache_clear()


def candidate_bins(name: str, methods: list[Method]) -> set[str]:
    cands = {name.lower()}
    for m in methods:
        p = pkg_from_command(m.kind, m.command)
        if p:
            cands.add(p.lower())
    return {c for c in cands if len(c) >= 2}


def status(slug: str, name: str, methods: list[Method], ledger: dict,
           bins: frozenset[str] | None = None) -> str | None:
    """"managed" (installed by tuistore), "present" (on PATH), or None."""
    if slug in ledger:
        return "managed"
    if bins is None:
        bins = path_binaries()
    if any(c in bins for c in candidate_bins(name, methods)):
        return "present"
    return None
