"""The install engine.

A `Method` is one concrete way to install a tool — a shell command plus the
constraints that decide whether *this* machine can run it (which binaries it
needs, which OS, which distro family). Methods come from three places:

  * ``official``  — an install command the project itself documents
  * ``readme``    — a command scraped out of the repo's README
  * ``inferred``  — a best-guess from the repo's primary language

At runtime we filter methods to the ones the current `Env` can actually run,
rank them (verified-from-README before guessed; clean managers before
`curl | sh`), and offer the winner as the default with the rest one key away.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterable

from .platform import Env

# ── kind metadata ──────────────────────────────────────────────────────────
# kind -> (label, preference[lower=better], requires, os allow, family allow)
KINDS: dict[str, dict] = {
    "uv":       dict(label="uv tool install",     pref=5,  requires=["uv"]),
    "cargo-binstall": dict(label="cargo binstall", pref=6, requires=["cargo-binstall"]),
    "brew":     dict(label="brew install",       pref=8,  requires=["brew"]),
    "cargo":    dict(label="cargo install",       pref=10, requires=["cargo"]),
    "pipx":     dict(label="pipx install",        pref=13, requires=["pipx"]),
    "go":       dict(label="go install",          pref=15, requires=["go"]),
    "pip":      dict(label="pip install",         pref=17, requires=["pip3"]),
    "npm":      dict(label="npm install -g",      pref=18, requires=["npm"]),
    "bun":      dict(label="bun add -g",          pref=19, requires=["bun"]),
    "pnpm":     dict(label="pnpm add -g",         pref=19, requires=["pnpm"]),
    "yarn":     dict(label="yarn global add",     pref=20, requires=["yarn"]),
    "gem":      dict(label="gem install",         pref=22, requires=["gem"]),
    "pacman":   dict(label="pacman -S",           pref=24, requires=["pacman"], os=["linux"], families=["arch"]),
    "yay":      dict(label="yay -S · AUR",        pref=25, requires=["yay"],    os=["linux"], families=["arch"]),
    "paru":     dict(label="paru -S · AUR",       pref=25, requires=["paru"],   os=["linux"], families=["arch"]),
    "apt":      dict(label="apt install",         pref=26, requires=["apt"],    os=["linux"], families=["debian"]),
    "dnf":      dict(label="dnf install",         pref=27, requires=["dnf"],    os=["linux"], families=["fedora", "rhel"]),
    "zypper":   dict(label="zypper install",      pref=28, requires=["zypper"], os=["linux"], families=["suse"]),
    "xbps":     dict(label="xbps-install",        pref=28, requires=["xbps-install"], os=["linux"], families=["void"]),
    "apk":      dict(label="apk add",             pref=28, requires=["apk"],    os=["linux"], families=["alpine"]),
    "emerge":   dict(label="emerge",              pref=29, requires=["emerge"], os=["linux"], families=["gentoo"]),
    "nix":      dict(label="nix profile install", pref=30, requires=["nix"]),
    "flatpak":  dict(label="flatpak install",     pref=31, requires=["flatpak"], os=["linux"]),
    "snap":     dict(label="snap install",        pref=32, requires=["snap"],    os=["linux"]),
    "docker":   dict(label="docker",              pref=35, requires=["docker"]),
    "podman":   dict(label="podman",              pref=36, requires=["podman"]),
    "script":   dict(label="install script",      pref=40, requires=["curl"]),
    "source":   dict(label="build from source",   pref=50, requires=["git"]),
    "manual":   dict(label="see README",          pref=99, requires=[]),
}

_SOURCE_RANK = {"official": 0, "readme": 1, "inferred": 3}


@dataclass
class Method:
    kind: str
    command: str
    source: str = "inferred"        # official | readme | inferred
    requires: list[str] = field(default_factory=list)
    os: list[str] | None = None      # allowed OSes, None = any
    families: list[str] | None = None  # allowed distro families, None = any
    note: str = ""

    # ── derived ────────────────────────────────────────────────────────
    @property
    def label(self) -> str:
        return KINDS.get(self.kind, {}).get("label", self.kind)

    @property
    def trust(self) -> str:
        """How much to trust this command:
        - "verified"   maintainer-curated (featured tools)
        - "community"  taken verbatim from the project's own README
        - "unverified" guessed from the repo (name may be wrong / squattable)
        """
        if self.source == "official":
            return "verified"
        if self.source == "readme":
            return "community"
        return "unverified"

    @property
    def is_script(self) -> bool:
        """A remote install script (curl|sh) — highest-risk, always warn."""
        return self.kind == "script"

    def available(self, env: Env) -> bool:
        if self.os and env.os not in self.os:
            return False
        if self.families and not (set(self.families) & env.families):
            return False
        return env.has(*self.requires)

    def score(self, env: Env) -> tuple:
        pref = KINDS.get(self.kind, {}).get("pref", 60)
        src = _SOURCE_RANK.get(self.source, 2)
        # uv is the preferred installer for a python CLI — rank it in the
        # trusted tier so it's the default whenever it's runnable. Its honest
        # verified/unverified label (`.trust`) is unchanged, so the install
        # screen still warns when the package name is only a guess.
        if self.kind == "uv":
            src = 0
        # available first, then verified-before-guessed, then niceness of kind
        return (0 if self.available(env) else 1, src, pref)

    def why_unavailable(self, env: Env) -> str:
        if self.os and env.os not in self.os:
            return f"{'/'.join(self.os)} only"
        if self.families and not (set(self.families) & env.families):
            return f"{'/'.join(self.families)} only"
        missing = [r for r in self.requires if r not in env.tools]
        if missing:
            return f"needs {' + '.join(missing)}"
        return ""

    # ── (de)serialize ──────────────────────────────────────────────────
    def to_dict(self) -> dict:
        d = {"kind": self.kind, "command": self.command, "source": self.source}
        if self.requires:
            d["requires"] = self.requires
        if self.os:
            d["os"] = self.os
        if self.families:
            d["families"] = self.families
        if self.note:
            d["note"] = self.note
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Method":
        return cls(
            kind=d["kind"],
            command=d["command"],
            source=d.get("source", "inferred"),
            requires=d.get("requires") or list(KINDS.get(d["kind"], {}).get("requires", [])),
            os=d.get("os") or KINDS.get(d["kind"], {}).get("os"),
            families=d.get("families") or KINDS.get(d["kind"], {}).get("families"),
            note=d.get("note", ""),
        )


def _meta(kind: str) -> dict:
    return KINDS.get(kind, {})


def make(kind: str, command: str, source: str = "inferred", note: str = "") -> Method:
    """Build a Method, inheriting requires/os/families defaults from its kind."""
    m = _meta(kind)
    return Method(
        kind=kind,
        command=command,
        source=source,
        requires=list(m.get("requires", [])),
        os=m.get("os"),
        families=m.get("families"),
        note=note,
    )


def force_variant(kind: str, command: str) -> str:
    """Rewrite an install command to force a reinstall over an existing copy."""
    if kind in ("uv", "pipx", "cargo", "cargo-binstall") and "--force" not in command:
        return f"{command} --force"
    if kind == "pip" and "--force-reinstall" not in command:
        return f"{command} --force-reinstall"
    if kind == "brew" and command.strip().startswith("brew install"):
        return command.replace("brew install", "brew reinstall", 1)
    return command  # go/npm/gem/distro managers reinstall on re-run anyway


# ── classify a raw command line into a kind ────────────────────────────────
_CLASSIFY = [
    ("cargo-binstall", re.compile(r"\bcargo\s+binstall\b")),
    ("cargo", re.compile(r"\bcargo\s+install\b")),
    ("uv", re.compile(r"\buv\s+tool\s+install\b|\buvx?\s+install\b")),
    ("pipx", re.compile(r"\bpipx\s+install\b")),
    ("pip", re.compile(r"\bpip3?\s+install\b|\bpython3?\s+-m\s+pip\s+install\b")),
    ("go", re.compile(r"\bgo\s+install\b")),
    ("npm", re.compile(r"\bnpm\s+(?:i|install)\s+(?:-g|--global)\b")),
    ("pnpm", re.compile(r"\bpnpm\s+(?:add|install)\s+-g\b")),
    ("yarn", re.compile(r"\byarn\s+global\s+add\b")),
    ("bun", re.compile(r"\bbun\s+(?:add|install)\s+-g\b")),
    ("gem", re.compile(r"\bgem\s+install\b")),
    ("yay", re.compile(r"\byay\s+-S\b")),
    ("paru", re.compile(r"\bparu\s+-S\b")),
    ("pacman", re.compile(r"\bpacman\s+-S\b")),
    ("apt", re.compile(r"\bapt(?:-get)?\s+install\b")),
    ("dnf", re.compile(r"\bdnf\s+install\b|\byum\s+install\b")),
    ("zypper", re.compile(r"\bzypper\s+(?:in|install)\b")),
    ("xbps", re.compile(r"\bxbps-install\b")),
    ("apk", re.compile(r"\bapk\s+add\b")),
    ("emerge", re.compile(r"\bemerge\b")),
    ("nix", re.compile(r"\bnix\s+profile\s+install\b|\bnix-env\s+-i\b")),
    ("flatpak", re.compile(r"\bflatpak\s+install\b")),
    ("snap", re.compile(r"\bsnap\s+install\b")),
    ("brew", re.compile(r"\bbrew\s+install\b")),
    ("docker", re.compile(r"\bdocker\s+(?:run|pull)\b")),
    ("podman", re.compile(r"\bpodman\s+(?:run|pull)\b")),
    ("script", re.compile(r"\bcurl\b.*\|\s*(?:sudo\s+)?(?:sh|bash)\b|\bwget\b.*\|\s*(?:sh|bash)\b")),
]


def classify(command: str) -> str | None:
    """Best-guess the kind of a raw shell install command, or None."""
    for kind, rx in _CLASSIFY:
        if rx.search(command):
            return kind
    return None


# ── infer install methods from repo language ───────────────────────────────
def parse_repo(url: str) -> tuple[str, str] | None:
    m = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    repo = repo.removesuffix(".git").strip("/")
    return owner, repo


def _pkg_guess(repo: str) -> str:
    # crate / pypi / npm names are lowercase, hyphenated; drop a "-tui"? no —
    # keep the repo name, just normalize case/underscores conservatively.
    return repo.lower()


def infer_methods(url: str, language: str | None) -> list[Method]:
    """Guess install methods from the repo's primary language. Best-effort,
    always tagged ``inferred`` and noted as a guess, so the UI is honest."""
    parsed = parse_repo(url)
    if not parsed:
        return []
    owner, repo = parsed
    pkg = _pkg_guess(repo)
    lang = (language or "").lower()
    guess = "guessed from repo name"
    out: list[Method] = []

    if lang in ("rust",):
        out.append(make("cargo", f"cargo install {pkg}", note=guess))
        out.append(make("cargo-binstall", f"cargo binstall {pkg}", note=guess))
    elif lang in ("go",):
        # go module paths are derivable from the URL and usually correct
        out.append(make("go", f"go install github.com/{owner}/{repo}@latest",
                        source="inferred", note="module path from repo"))
    elif lang in ("python",):
        out.append(make("uv", f"uv tool install {pkg}", note=guess))
        out.append(make("pipx", f"pipx install {pkg}", note=guess))
    elif lang in ("javascript", "typescript"):
        out.append(make("npm", f"npm install -g {pkg}", note=guess))
    elif lang in ("ruby",):
        out.append(make("gem", f"gem install {pkg}", note=guess))
    # a universal, honest fallback: clone and build per the README
    out.append(make("source",
                    f"git clone https://github.com/{owner}/{repo} && cd {repo}",
                    note="then build per the README"))
    return out


# ── ranking ────────────────────────────────────────────────────────────────
def rank(methods: Iterable[Method], env: Env) -> list[Method]:
    """Sorted best-first; available methods come before unavailable ones."""
    uniq: dict[tuple[str, str], Method] = {}
    for m in methods:
        uniq.setdefault((m.kind, m.command), m)
    return sorted(uniq.values(), key=lambda m: m.score(env))


def best(methods: Iterable[Method], env: Env) -> Method | None:
    ranked = rank(methods, env)
    for m in ranked:
        if m.available(env):
            return m
    return ranked[0] if ranked else None


# ── run an install, streaming output ───────────────────────────────────────
async def run_stream(command: str) -> AsyncIterator[tuple[str, str]]:
    """Execute `command` in a login shell, yielding ("out", line) as it runs
    and finally ("exit", returncode). A login shell is used so the user's
    package managers are on PATH exactly as in their normal terminal."""
    shell = shutil.which("bash") or shutil.which("zsh") or "/bin/sh"
    try:
        proc = await asyncio.create_subprocess_exec(
            shell, "-lc", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as e:  # pragma: no cover - shell missing is pathological
        yield ("out", f"could not start shell: {e}")
        yield ("exit", "127")
        return
    assert proc.stdout is not None
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        yield ("out", raw.decode("utf-8", "replace").rstrip("\n"))
    code = await proc.wait()
    yield ("exit", str(code))
