"""Entry point and CLI — tuistore as a real package manager.

    tuistore                       launch the store (TUI)

    tuistore install <name>…       install a tool (platform-aware, confirmed)
    tuistore remove  <name>…       uninstall a tool you installed via tuistore
    tuistore update  <name>        update one tool
    tuistore search  <query>       search the catalog from the shell
    tuistore info    <name>        show a tool's details + install methods

    tuistore installed             list what tuistore installed
    tuistore upgrade               update EVERYTHING (brew, uv, npm, … — all
                                   packages, incl. ones installed outside)
    tuistore update                update tuistore itself
    tuistore update installed      update only what tuistore installed
    tuistore refetch catalog       pull the latest catalog

    tuistore --doctor              what your machine looks like to the engine
    tuistore --version

install flags:  -y/--yes (no prompt)   -f/--force (reinstall)   --dry-run   --method <kind>
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def _run(cmd: str) -> int:
    print(f"$ {cmd}")
    return subprocess.run(["/bin/sh", "-lc", cmd]).returncode


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes or not sys.stdin.isatty():
        return True
    try:
        return (input(f"{prompt} [Y/n] ").strip().lower() or "y") in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _stars(n) -> str:
    if not n:
        return "—"
    return f"{n/1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


# ── resolving a name to a catalog entry ─────────────────────────────────────
def _resolve(name: str, *, quiet: bool = False):
    from .catalog import load, search
    cat = load()
    low = name.lower()
    exact = [e for e in cat.entries if e.name.lower() == low or e.slug.lower() == low]
    if exact:
        return exact[0]
    matches = search(cat.entries, name, limit=8)
    if matches and matches[0].name.lower() == low:
        return matches[0]
    if len(matches) == 1:
        return matches[0]
    if not quiet:
        if matches:
            print(f"no exact match for '{name}'. did you mean:")
            for e in matches:
                print(f"  {e.name:<22} ★{_stars(e.stars):<6} {e.description[:54]}")
        else:
            print(f"no tool matching '{name}' in the catalog.")
    return None


# ── install ─────────────────────────────────────────────────────────────────
def _install_one(name: str, *, yes: bool, dry_run: bool, method_kind: str | None,
                 force: bool = False) -> int:
    from .installer import best, rank, force_variant
    from .installed import record_install, status, load_ledger, path_binaries, scan_installed, verify_landed
    from .platform import detect

    entry = _resolve(name)
    if not entry:
        return 1

    env = detect()
    if not entry.methods:
        print(f"{entry.name}: no known install method. see {entry.homepage or entry.url}")
        return 1

    if not force and status(entry.slug, entry.name, entry.methods, load_ledger(),
                            path_binaries(), scan_installed(env)):
        print(f"{entry.name} is already installed.  (use --force to reinstall)")
        return 0

    ranked = rank(entry.methods, env)
    if method_kind:
        picks = [m for m in ranked if m.kind == method_kind]
        chosen = next((m for m in picks if m.available(env)), picks[0] if picks else None)
        if not chosen:
            print(f"{entry.name}: no '{method_kind}' method. options: "
                  f"{', '.join(sorted({m.kind for m in ranked}))}")
            return 1
    else:
        chosen = best(entry.methods, env)

    if not chosen.available(env):
        print(f"{entry.name}: nothing installable on this machine ({env.label}).")
        print("  known methods:")
        for m in ranked[:6]:
            print(f"    {m.label:<18} {m.command}   [{m.why_unavailable(env)}]")
        return 1

    cmd = force_variant(chosen.kind, chosen.command) if force else chosen.command
    trust = {"verified": "✓ verified", "community": "✓ from README",
             "unverified": "⚠ unverified — guessed"}[chosen.trust]
    if chosen.is_script:
        trust = "⚠ remote install script — review it"
    print(f"{entry.name}  ({entry.slug})" + ("  · reinstall" if force else ""))
    print(f"  {cmd}")
    print(f"  via {chosen.label} · {trust}")

    if dry_run:
        return 0
    if not _confirm("reinstall?" if force else "install?", yes):
        print("cancelled.")
        return 1

    code = _run(cmd)
    if code == 0 and chosen.is_bare_clone:
        # clone-only: nothing was actually built, so never record it as an
        # install — the user still has to build it themselves per the README
        print(f"cloned {entry.name} — that's just a source checkout, follow its README to "
              f"finish building; not marked as installed")
    elif code == 0:
        record_install(entry.slug, entry.name, chosen)
        if verify_landed(entry.name, entry.methods):
            print(f"✓ installed {entry.name} — manage with `tuistore update/remove {entry.name}`")
        else:
            print(f"⚠ {entry.name}: the command exited cleanly but no new binary showed up on "
                  f"PATH — double check it actually installed")
    else:
        print(f"✗ {entry.name} failed (exit {code})")
    return code


def _cmd_install(args: list[str]) -> int:
    yes = dry_run = force = False
    method_kind = None
    names: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-y", "--yes"):
            yes = True
        elif a in ("-f", "--force"):
            force = True
        elif a == "--dry-run":
            dry_run = True
        elif a == "--method" and i + 1 < len(args):
            method_kind = args[i + 1]
            i += 1
        elif a.startswith("-"):
            print(f"install: unknown flag {a}")
            return 2
        else:
            names.append(a)
        i += 1
    if not names:
        print("usage: tuistore install <name>…  [-y] [-f/--force] [--dry-run] [--method <kind>]")
        return 2
    rc = 0
    for n in names:
        rc |= _install_one(n, yes=yes, dry_run=dry_run, method_kind=method_kind, force=force)
    return rc


# ── remove ──────────────────────────────────────────────────────────────────
def _cmd_remove(args: list[str]) -> int:
    from .installed import load_ledger, uninstall_command, forget
    yes = "-y" in args or "--yes" in args
    names = [a for a in args if not a.startswith("-")]
    if not names:
        print("usage: tuistore remove <name>…  [-y]")
        return 2
    ledger = load_ledger()
    rc = 0
    for name in names:
        rec = None
        entry = _resolve(name, quiet=True)
        slug = entry.slug if entry else None
        if slug and slug in ledger:
            rec = ledger[slug]
        else:  # match by recorded name
            for s, r in ledger.items():
                if r.get("name", "").lower() == name.lower():
                    slug, rec = s, r
                    break
        if not rec:
            print(f"{name}: not installed via tuistore (only those can be removed here).")
            rc = 1
            continue
        cmd = uninstall_command(rec)
        if not cmd:
            print(f"{rec['name']}: no uninstall command for a {rec.get('kind')} install.")
            rc = 1
            continue
        print(f"remove {rec['name']}:  {cmd}")
        if not _confirm("uninstall?", yes):
            print("cancelled.")
            continue
        code = _run(cmd)
        if code == 0:
            forget(slug)
            print(f"✓ removed {rec['name']}")
        else:
            rc = code
    return rc


# ── update a specific tool ──────────────────────────────────────────────────
def _update_named(name: str) -> int:
    from .installed import load_ledger, update_command
    ledger = load_ledger()
    entry = _resolve(name, quiet=True)
    slug = entry.slug if entry else None
    rec = ledger.get(slug) if slug else None
    if not rec:
        for s, r in ledger.items():
            if r.get("name", "").lower() == name.lower():
                rec = r
                break
    if not rec:
        print(f"{name}: not installed via tuistore.")
        return 1
    cmd = update_command(rec)
    if not cmd:
        print(f"{rec['name']}: no update command for a {rec.get('kind')} install.")
        return 1
    return _run(cmd)


# ── search / info ───────────────────────────────────────────────────────────
def _cmd_search(args: list[str]) -> int:
    from .catalog import load, search
    from .installed import status, load_ledger, path_binaries
    q = " ".join(a for a in args if not a.startswith("-"))
    if not q:
        print("usage: tuistore search <query>")
        return 2
    cat = load()
    ledger, bins = load_ledger(), path_binaries()
    results = search(cat.entries, q, limit=20)
    if not results:
        print(f"no matches for '{q}'.")
        return 1
    for e in results:
        mark = "✓" if status(e.slug, e.name, e.methods, ledger, bins) else " "
        print(f" {mark} {e.name:<22} ★{_stars(e.stars):<6} {e.description[:60]}")
    return 0


def _cmd_info(args: list[str]) -> int:
    from .installer import rank
    from .installed import status, load_ledger, path_binaries
    from .platform import detect
    names = [a for a in args if not a.startswith("-")]
    if not names:
        print("usage: tuistore info <name>")
        return 2
    entry = _resolve(names[0])
    if not entry:
        return 1
    env = detect()
    st = status(entry.slug, entry.name, entry.methods, load_ledger(), path_binaries())
    print(f"{entry.name}  ({entry.slug})")
    print(f"  {entry.description}")
    meta = [f"★ {_stars(entry.stars)}"]
    if entry.language:
        meta.append(entry.language)
    if st:
        meta.append("installed" + (" via tuistore" if st == "managed" else ""))
    print("  " + " · ".join(meta))
    print(f"  {entry.homepage or entry.url}")
    print("  install methods:")
    seen: set[str] = set()
    for m in rank(entry.methods, env):
        if m.kind in seen:
            continue
        seen.add(m.kind)
        tag = {"verified": "✓", "community": "✓", "unverified": "⚠"}[m.trust]
        ok = "" if m.available(env) else f"   [{m.why_unavailable(env)}]"
        print(f"    {tag} {m.label:<18} {m.command}{ok}")
        if len(seen) >= 8:
            break
    return 0


# ── existing verbs ──────────────────────────────────────────────────────────
_SELF_SRC = "git+https://github.com/Gheat1/tuistore"


def _update_self() -> int:
    # force + refresh so it pulls the latest commit even when the version
    # string hasn't changed (a plain `uv tool upgrade` is version-gated and
    # no-ops on same-version updates).
    if shutil.which("uv"):
        return _run(f"uv tool install --force --refresh {_SELF_SRC}")
    if shutil.which("pipx"):
        return _run(f"pipx install --force {_SELF_SRC}")
    print(f"couldn't find uv or pipx — reinstall with:\n"
          f"  uv tool install --force --refresh {_SELF_SRC}")
    return 1


def _system_upgrade() -> int:
    from .installed import system_upgrade_command, upgrade_managers
    from .platform import detect
    env = detect()
    cmd = system_upgrade_command(env, allow_sudo=True)
    if not cmd:
        print("no package managers found to upgrade.")
        return 0
    print("upgrading everything via:", ", ".join(upgrade_managers(env, allow_sudo=True)))
    return _run(cmd)


def _update_installed() -> int:
    from .installed import load_ledger, update_command
    ledger = load_ledger()
    updatable = [(r["name"], update_command(r)) for r in ledger.values()]
    updatable = [(n, c) for n, c in updatable if c]
    if not updatable:
        print("nothing installed through tuistore to update.")
        return 0
    print(f"updating {len(updatable)} tool(s)…\n")
    failed = 0
    for name, cmd in updatable:
        print(f"── {name} ──")
        if _run(cmd) != 0:
            failed += 1
        print()
    print(f"done — {len(updatable) - failed} ok, {failed} failed")
    return 1 if failed else 0


def _refetch_catalog() -> int:
    from . import catalog
    print("fetching latest catalog…")
    ok, msg = catalog.refetch()
    print(("✓ " if ok else "✗ ") + msg)
    return 0 if ok else 1


def _list_installed() -> int:
    from .installed import load_ledger
    ledger = load_ledger()
    if not ledger:
        print("nothing installed through tuistore yet.")
        return 0
    print(f"{len(ledger)} tool(s) installed via tuistore:\n")
    for slug, r in sorted(ledger.items(), key=lambda kv: kv[1]["name"].lower()):
        print(f"  {r['name']:<22} {r.get('kind',''):<8} {r.get('at','')}  ({slug})")
    return 0


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        from .app import main as run
        run()
        return

    a0 = argv[0].lower()
    rest = argv[1:]
    a1 = rest[0].lower() if rest else ""

    if a0 in ("--version", "-v"):
        from . import __version__
        print(f"tuistore {__version__}")
    elif a0 == "--doctor":
        from .platform import detect
        e = detect()
        print(f"tuistore doctor — {e.label}")
        print(f"  package managers: {' '.join(sorted(e.tools)) or '(none found)'}")
    elif a0 == "install":
        sys.exit(_cmd_install(rest))
    elif a0 in ("remove", "uninstall", "rm"):
        sys.exit(_cmd_remove(rest))
    elif a0 == "search":
        sys.exit(_cmd_search(rest))
    elif a0 in ("info", "show"):
        sys.exit(_cmd_info(rest))
    elif a0 in ("update", "upgrade"):
        if a1 in ("installed", "tools"):
            sys.exit(_update_installed())
        if a1 in ("everything", "system", "all"):
            sys.exit(_system_upgrade())
        if a1 and not a1.startswith("-"):
            sys.exit(_update_named(rest[0]))
        # bare `upgrade` = everything on the machine; bare `update` = tuistore
        sys.exit(_system_upgrade() if a0 == "upgrade" else _update_self())
    elif a0 in ("update-installed", "update_installed"):
        sys.exit(_update_installed())
    elif a0 in ("refetch", "refresh", "catalog", "refetch-catalog"):
        sys.exit(_refetch_catalog())
    elif a0 in ("installed", "list", "ls"):
        sys.exit(_list_installed())
    elif a0 in ("-h", "--help", "help"):
        print(__doc__)
    else:
        print(f"tuistore: unknown command '{argv[0]}'\n{__doc__}")
        sys.exit(2)


if __name__ == "__main__":
    main()
