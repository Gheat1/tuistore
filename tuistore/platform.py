"""Detect the machine we're on: OS, Linux distro family, arch, and which
package managers / toolchains are actually installed.

The install engine uses this to gate methods — `pacman -S` only on Arch,
`brew` only where brew exists, `apt` only on Debian-likes, and so on — so a
user is never offered a command their box can't run.
"""

from __future__ import annotations

import platform as _platform
import shutil
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Every binary we care about probing. Keep this list in sync with the method
# `requires` fields in installer.py.
PROBE = [
    # language toolchains
    "cargo", "cargo-binstall", "go", "npm", "pnpm", "yarn", "bun",
    "uv", "pipx", "pip", "pip3", "python3", "gem", "cabal", "stack",
    # system package managers
    "brew", "port",
    "pacman", "yay", "paru",
    "apt", "apt-get", "nala",
    "dnf", "yum", "zypper",
    "xbps-install", "emerge", "eopkg", "apk", "pkg",
    "nix", "nix-env", "snap", "flatpak",
    # Windows package managers
    "scoop", "choco", "winget",
    # fetchers + build
    "curl", "wget", "git", "make", "gcc", "cc",
    # shells (used for running commands)
    "pwsh", "powershell",
    # our own helpers
    "gh",
]

# Which distro IDs map to which "family" (so ID_LIKE gaps don't hurt us).
_FAMILY = {
    "arch": {"arch"},
    "manjaro": {"arch"},
    "endeavouros": {"arch"},
    "cachyos": {"arch"},
    "artix": {"arch"},
    "garuda": {"arch"},
    "debian": {"debian"},
    "ubuntu": {"debian"},
    "pop": {"debian"},
    "linuxmint": {"debian"},
    "elementary": {"debian"},
    "raspbian": {"debian"},
    "kali": {"debian"},
    "fedora": {"fedora", "rhel"},
    "rhel": {"rhel"},
    "centos": {"rhel"},
    "rocky": {"rhel"},
    "almalinux": {"rhel"},
    "opensuse": {"suse"},
    "opensuse-leap": {"suse"},
    "opensuse-tumbleweed": {"suse"},
    "suse": {"suse"},
    "void": {"void"},
    "gentoo": {"gentoo"},
    "alpine": {"alpine"},
    "nixos": {"nixos"},
    "solus": {"solus"},
}


@dataclass
class Env:
    """A snapshot of the current machine, used to gate install methods."""

    os: str  # "macos" | "linux" | "windows" | "unknown"
    distro: str  # e.g. "arch", "ubuntu", "fedora"; "" off Linux
    families: set[str] = field(default_factory=set)  # {"arch"}, {"debian"}, ...
    arch: str = ""  # "arm64" | "x86_64" | ...
    tools: set[str] = field(default_factory=set)  # installed binaries from PROBE

    def has(self, *tools: str) -> bool:
        """True if every named binary is on PATH."""
        return all(t in self.tools for t in tools)

    def has_any(self, *tools: str) -> bool:
        return any(t in self.tools for t in tools)

    @property
    def is_arch(self) -> bool:
        return "arch" in self.families

    @property
    def is_debian(self) -> bool:
        return "debian" in self.families

    @property
    def label(self) -> str:
        if self.os == "macos":
            return f"macOS ({self.arch})"
        if self.os == "linux":
            return f"{self.distro or 'Linux'} ({self.arch})"
        if self.os == "windows":
            return f"Windows ({self.arch})"
        return self.os


def _read_os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    data[k.strip()] = v.strip().strip('"').strip("'")
            if data:
                break
        except OSError:
            continue
    return data


def _families(distro_id: str, id_like: str) -> set[str]:
    fams: set[str] = set(_FAMILY.get(distro_id, set()))
    for like in id_like.replace(",", " ").split():
        fams |= _FAMILY.get(like, {like})
    return fams


@lru_cache(maxsize=1)
def detect() -> Env:
    """Probe the machine once; cached for the process lifetime."""
    system = _platform.system().lower()
    if system == "darwin":
        os_name = "macos"
    elif system == "linux":
        os_name = "linux"
    elif system in ("windows", ""):
        os_name = "windows" if system == "windows" else "unknown"
    else:
        os_name = system or "unknown"

    machine = _platform.machine().lower()
    arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}.get(
        machine, machine
    )

    distro, families = "", set()
    if os_name == "linux":
        rel = _read_os_release()
        distro = (rel.get("ID") or "").lower()
        families = _families(distro, (rel.get("ID_LIKE") or "").lower())
        if not distro:
            distro = "linux"

    tools = {t for t in PROBE if shutil.which(t)}

    return Env(os=os_name, distro=distro, families=families, arch=arch, tools=tools)


def refresh() -> Env:
    """Re-probe (e.g. after an install added a new manager)."""
    detect.cache_clear()
    return detect()


if __name__ == "__main__":  # `python -m tuistore.platform` for a quick dump
    e = detect()
    print(f"os       {e.os}")
    print(f"distro   {e.distro}  families={sorted(e.families) or '-'}")
    print(f"arch     {e.arch}")
    print(f"managers {' '.join(sorted(e.tools)) or '(none found)'}")
    sys.exit(0)
