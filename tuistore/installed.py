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
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from .installer import Method
from .paths import user_data_dir

LEDGER = user_data_dir() / "installed.json"


# ── the ledger ──────────────────────────────────────────────────────────────
def load_ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_ledger(data: dict) -> None:
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(data, indent=1), encoding="utf-8")
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
    "scoop", "choco", "winget",
    # bare (non-flag) "global" is yarn's subcommand in its only recognized
    # install shape, "yarn global add <pkg>" (see installer.py's yarn
    # pattern) — without this, pkg_from_command('yarn', ...) always returns
    # "global" instead of the package name. There IS a real npm package
    # literally named "global" (an env-var helper library, not a CLI), so
    # `yarn global add global` would still be misparsed — but it was already
    # broken before this fix (it resolved to the same wrong answer, "global",
    # either way), no such package appears in tuistore's catalog of CLI/TUI
    # tools, and this is by far the common case yarn is actually used for.
    "global",
}


# cargo flags that take a following value — the value is never the crate name
_CARGO_VALUE_FLAGS = {"--git", "--branch", "--tag", "--rev", "--path", "--version", "--features"}

# pip/uv/pipx VCS install specs (PEP 440 direct references), e.g.
# "git+https://github.com/owner/repo", "git+ssh://git@host/owner/repo.git@branch"
_VCS_PREFIXES = ("git+", "hg+", "svn+", "bzr+")


def _vcs_repo_name(url: str) -> str | None:
    """Repo name from a VCS install spec — the same "best available guess"
    used for `cargo install --git`, since a VCS URL carries no package name
    of its own on the command line (that lives in the remote project's
    metadata, which we don't fetch). Strips the `git+` scheme prefix, any
    `#egg=...`/query fragment, and an `@ref` (branch/tag/commit) suffix —
    but not an `ssh://user@host` authority, which sits before the final
    `/repo` segment and is left untouched."""
    for prefix in _VCS_PREFIXES:
        if url.startswith(prefix):
            url = url[len(prefix):]
            break
    url = url.split("#", 1)[0].split("?", 1)[0]
    head, sep, tail = url.rpartition("/")
    if sep and "@" in tail:
        tail = tail.split("@", 1)[0]
    url = f"{head}/{tail}" if sep else tail
    repo = url.rstrip("/").rsplit("/", 1)[-1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return repo or None


def _strip_version_pin(token: str) -> str:
    """Drop a trailing "@version" pin without mistaking a leading "@" npm/jsr
    scope marker for one, e.g. "pkg@1.2.3" -> "pkg", "@openai/codex" ->
    unchanged, "@openai/codex@1.2.3" -> "@openai/codex" (strip only the
    *second* "@", not the first)."""
    if token.startswith("@"):
        at = token.find("@", 1)
        return token if at == -1 else token[:at]
    return token.split("@", 1)[0]


def _cargo_pkg(command: str) -> str | None:
    """Crate name from a `cargo install` command. Git-aware: `--git <url>`
    installs have no crate name in the command line at all (that lives in the
    remote Cargo.toml, which we don't fetch) — the repo name is the best
    available guess, e.g. `--git .../lsd-rs/lsd.git --branch main` -> "lsd",
    never "main" (a naive flag-strip treats a value-taking flag's value as a
    free token and picks whatever value happens to sit first)."""
    tokens = command.split()
    if "--git" in tokens:
        idx = tokens.index("--git")
        if idx + 1 >= len(tokens):
            return None
        url = tokens[idx + 1].rstrip("/")
        repo = url.rsplit("/", 1)[-1]
        return repo[:-4] if repo.endswith(".git") else (repo or None)
    skip_next = False
    for t in tokens:
        if skip_next:
            skip_next = False
            continue
        if t in _CARGO_VALUE_FLAGS:
            skip_next = True
            continue
        if t.lower() in _NOISE or t.startswith("-"):
            continue
        return _strip_version_pin(t)
    return None


def pkg_from_command(kind: str, command: str) -> str | None:
    """Best-effort package / target name from an install command."""
    if kind in ("script", "source"):
        return None
    if kind in ("cargo", "cargo-binstall"):
        return _cargo_pkg(command)
    tokens = command.replace("&&", " ").split()
    cands = [
        t for t in tokens
        if t.lower() not in _NOISE and not t.startswith("-")
    ]
    if kind == "go":
        # go install github.com/owner/repo/cmd/tool@latest -> "tool"
        for t in cands:
            if "/" in t or "@" in t:
                return _strip_version_pin(t).rstrip("/").split("/")[-1]
        return _strip_version_pin(cands[-1]) if cands else None
    # drop bare URLs and VCS install specs (git+https://..., git+ssh://...)
    # for non-go managers — neither is a valid package name to feed back into
    # an uninstall/upgrade command. A VCS spec still gets a best-effort repo
    # name (see _vcs_repo_name); a bare URL just falls through to the next
    # candidate, or None if it was the only token.
    urls = [t for t in cands if t.startswith("http") or t.startswith(_VCS_PREFIXES)]
    cands = [t for t in cands if t not in urls]
    if not cands:
        for t in urls:
            if t.startswith(_VCS_PREFIXES):
                return _vcs_repo_name(t)
        return None
    pkg = _strip_version_pin(cands[0])
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
    "pip": "python3 -m pip uninstall -y {pkg}",  # portable: many boxes have pip3, not pip
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
    "yum": "sudo yum remove {pkg}",
    "zypper": "sudo zypper remove {pkg}",
    "xbps": "sudo xbps-remove {pkg}",
    "apk": "sudo apk del {pkg}",
    "emerge": "sudo emerge --unmerge {pkg}",
    "eopkg": "sudo eopkg remove {pkg}",
    "nix": "nix profile remove {pkg}",
    "flatpak": "flatpak uninstall {pkg}",
    "snap": "sudo snap remove {pkg}",
    "go": 'rm -f "$(go env GOPATH)/bin/{bin}"',
    "scoop": "scoop uninstall {pkg}",
    "choco": "choco uninstall {pkg}",
    "winget": "winget uninstall {pkg} --disable-interactivity",
}

_UPDATE = {
    "cargo": "cargo install {pkg} --force",
    "cargo-binstall": "cargo binstall {pkg} --force",
    "uv": "uv tool upgrade {pkg}",
    "pipx": "pipx upgrade {pkg}",
    "pip": "python3 -m pip install -U {pkg}",
    "brew": "brew upgrade {pkg}",
    "npm": "npm install -g {pkg}@latest",
    "pnpm": "pnpm add -g {pkg}@latest",
    "yarn": "yarn global add {pkg}@latest",
    "gem": "gem update {pkg}",
    "pacman": "sudo pacman -S {pkg}",
    "yay": "yay -S {pkg}",
    "paru": "paru -S {pkg}",
    "apt": "sudo apt install --only-upgrade {pkg}",
    "dnf": "sudo dnf upgrade {pkg}",
    "yum": "sudo yum upgrade {pkg}",
    "zypper": "sudo zypper update {pkg}",
    "xbps": "sudo xbps-install -Su {pkg}",
    "apk": "sudo apk add -u {pkg}",
    "emerge": "sudo emerge --update {pkg}",
    "eopkg": "sudo eopkg upgrade {pkg}",
    "nix": "nix profile upgrade {pkg}",
    "flatpak": "flatpak update {pkg}",
    "snap": "sudo snap refresh {pkg}",
    "scoop": "scoop update {pkg}",
    "choco": "choco upgrade {pkg}",
    "winget": "winget upgrade {pkg} --disable-interactivity",
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
    if kind in ("cargo", "cargo-binstall") and "--git" in rec.get("command", ""):
        # a --git install has no crate name on the command line to template
        # with — re-run the exact original invocation instead, which is
        # always correct regardless of what pkg_from_command could guess.
        cmd = rec["command"]
        return cmd if "--force" in cmd else f"{cmd} --force"
    tmpl = _UPDATE.get(kind)
    if not tmpl or not pkg:
        return None
    return tmpl.format(pkg=pkg)


# ── detection ───────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def path_binaries() -> frozenset[str]:
    """Every executable name on PATH — scanned once, cheap to test against.

    On Windows we also strip common executable extensions so that a binary
    named ``rg.exe`` is recorded simply as ``rg``.
    """
    found: set[str] = set()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        try:
            for entry in os.scandir(d):
                name = entry.name
                normalized = name.lower() if os.name == "nt" else name
                found.add(normalized)
                # On Windows, executables often have extensions; keep both the
                # raw name and the extension-stripped name for matching.
                if os.name == "nt" and "." in name:
                    base, _ext = normalized.rsplit(".", 1)
                    if base:
                        found.add(base)
        except OSError:
            continue
    return frozenset(found)


def refresh_path() -> None:
    try:
        path_binaries.cache_clear()
    except AttributeError:
        pass  # path_binaries was replaced (e.g. in tests) — nothing to clear


def candidate_bins(name: str, methods: list[Method]) -> set[str]:
    cands = {name.lower()}
    for m in methods:
        p = pkg_from_command(m.kind, m.command)
        if p:
            cands.add(p.lower())
    return {c for c in cands if len(c) >= 2}


def verify_landed(name: str, methods: list[Method]) -> bool:
    """True if a real binary shows up on PATH after an install exits 0.

    A command exiting 0 only means *that command* succeeded — e.g. `cargo
    run --release` from a fresh clone exits 0 after just opening a wizard for
    one session, leaving nothing installed (github.com/Gheat1/tuistore/issues/3).
    This is a light, non-executing safety net: it checks the binary actually
    resolves, it never runs the tool itself (which would risk false positives
    on tools with no --version flag, interactive prompts, etc).
    """
    refresh_path()
    bins = path_binaries()
    return any(c in bins for c in candidate_bins(name, methods))


def status(slug: str, name: str, methods: list[Method], ledger: dict,
           bins: frozenset[str] | None = None,
           pkgs: dict[str, set[str]] | None = None) -> str | None:
    """"managed" (installed by tuistore), "present" (on PATH or in a package
    manager's installed list), or None."""
    if slug in ledger:
        return "managed"
    if bins is None:
        bins = path_binaries()
    if any(c in bins for c in candidate_bins(name, methods)):
        return "present"
    # manager-aware: a tool whose binary name differs from its package name
    # (bottom->btm, ripgrep->rg, git-delta->delta) still shows as installed
    if pkgs:
        for m in methods:
            installed = pkgs.get(m.kind)
            if installed:
                pkg = pkg_from_command(m.kind, m.command)
                if pkg and pkg.lower() in installed:
                    return "present"
    return None


# ── what each package manager reports as installed ───────────────────────────
def _run(cmd: list[str], timeout: float = 25.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _scoop_installed() -> set[str]:
    names = set()
    for ln in _run(["scoop", "list"]).splitlines():
        ln = ln.strip()
        if (ln and not ln.startswith("-") and not ln.startswith("Name")
                and ln.lower() not in {"installed apps:", "installed apps"}):
            names.add(ln.split()[0].lower())
    return names


def _choco_installed() -> set[str]:
    names = set()
    for ln in _run(["choco", "list", "--local-only", "--limit-output"]).splitlines():
        if ln.strip() and "|" in ln:
            names.add(ln.split("|")[0].lower())
    return names


def _winget_installed() -> set[str]:
    names = set()
    id_column: int | None = None
    for ln in _run(["winget", "list", "--disable-interactivity"]).splitlines():
        lower = ln.lower()
        if id_column is None and lower.lstrip().startswith("name") and "id" in lower:
            id_column = lower.index("id")
            continue
        if not ln.strip() or set(ln.strip()) <= {"-", " ", "\u2500"}:
            continue
        if id_column is not None:
            # `winget list` is a fixed-width table. Store both display name
            # and package ID because catalog commands commonly use `--id`.
            display = ln[:id_column].strip().lower()
            fields = ln[id_column:].split()
            if display:
                names.add(display)
            if fields:
                names.add(fields[0].lower())
            continue
        # Older winget versions do not reliably expose a table header.
        parts = ln.split()
        if parts:
            names.add(parts[0].lower())
    return names


def _brew_installed() -> set[str]:
    out = _run(["brew", "list", "--formula", "-1"])
    out += "\n" + _run(["brew", "list", "--cask", "-1"])
    return {ln.strip().lower() for ln in out.splitlines() if ln.strip()}


def _uv_installed() -> set[str]:
    names = set()
    for ln in _run(["uv", "tool", "list"]).splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("-"):
            names.add(ln.split()[0].lower())
    return names


def _pipx_installed() -> set[str]:
    return {ln.split()[0].lower() for ln in _run(["pipx", "list", "--short"]).splitlines()
            if ln.strip()}


def _npm_installed() -> set[str]:
    names = set()
    for ln in _run(["npm", "ls", "-g", "--depth=0", "--parseable"]).splitlines():
        if "/node_modules/" in ln:
            names.add(ln.rsplit("/node_modules/", 1)[-1].lower())
    return names


def _cargo_installed() -> set[str]:
    names = set()
    for ln in _run(["cargo", "install", "--list"]).splitlines():
        if ln and not ln.startswith(" "):
            names.add(ln.split()[0].lower())  # "name vX.Y.Z:"
    return names


def scan_installed(env) -> dict[str, set[str]]:
    """Ask each present package manager what it has installed. Slow-ish
    (subprocess per manager) — call from a background worker."""
    out: dict[str, set[str]] = {}
    if "brew" in env.tools:
        out["brew"] = _brew_installed()
    if "uv" in env.tools:
        out["uv"] = _uv_installed()
    if "pipx" in env.tools:
        out["pipx"] = _pipx_installed()
    if "npm" in env.tools:
        out["npm"] = _npm_installed()
    if env.has("cargo"):
        out["cargo"] = _cargo_installed()
    if "scoop" in env.tools:
        out["scoop"] = _scoop_installed()
    if "choco" in env.tools:
        out["choco"] = _choco_installed()
    if "winget" in env.tools:
        out["winget"] = _winget_installed()
    return out


# ── update everything on the machine ─────────────────────────────────────────
# (tool, bulk-upgrade command, needs_sudo)
_BULK_UPGRADE = [
    ("brew", "brew update && brew upgrade", False),
    ("uv", "uv tool upgrade --all", False),
    ("pipx", "pipx upgrade-all", False),
    ("npm", "npm update -g", False),
    ("pnpm", "pnpm update -g", False),
    ("gem", "gem update", False),
    ("flatpak", "flatpak update -y", False),
    ("nix", "nix profile upgrade --all", False),
    ("snap", "sudo snap refresh", True),
    ("pacman", "sudo pacman -Syu --noconfirm", True),
    ("apt", "sudo apt update && sudo apt upgrade -y", True),
    ("dnf", "sudo dnf upgrade -y", True),
    ("zypper", "sudo zypper update -y", True),
    ("xbps-install", "sudo xbps-install -Su", True),
    ("apk", "sudo apk update && sudo apk upgrade", True),
    ("emerge", "sudo emerge --sync && sudo emerge -uDN @world", True),
    ("eopkg", "sudo eopkg update-repo && sudo eopkg upgrade", True),
    # Windows package managers
    ("scoop", "scoop update *", False),
    ("choco", "choco upgrade all -y", False),
    ("winget", "winget upgrade --all --disable-interactivity --accept-source-agreements --accept-package-agreements", False),
]


def system_upgrade_command(env, allow_sudo: bool = True) -> str:
    """A shell script that upgrades every package manager on the machine —
    including things installed outside tuistore. `allow_sudo=False` drops the
    sudo-requiring managers (they'd hang with no TTY for the password)."""
    parts = []
    for tool, cmd, needs_sudo in _BULK_UPGRADE:
        if tool in env.tools and (allow_sudo or not needs_sudo):
            parts.append(f'echo "\\n== {tool} =="; {cmd}')
    return "\n".join(parts)


def upgrade_managers(env, allow_sudo: bool = True) -> list[str]:
    return [t for t, _c, s in _BULK_UPGRADE if t in env.tools and (allow_sudo or not s)]
