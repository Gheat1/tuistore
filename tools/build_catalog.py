#!/usr/bin/env python3
"""Build tuistore/data/catalog.json.

Pipeline:
  1. parse the awesome-tuis README into (name, url, description, category)
  2. pin Gheat's own suite to the top as ★ featured, with verified installs
  3. enrich every GitHub repo in one batched GraphQL sweep
     (stars, language, archived, pushed_at, description, homepage)
  4. scrape READMEs for real install commands — featured + the most-starred
  5. infer install methods from language for everything else
  6. write catalog.json

Re-run any time to refresh:  uv run python tools/build_catalog.py
Add  --scrape N  to scrape the top-N most-starred (default 140), 0 to skip.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tuistore.installer import infer_methods, make, parse_repo  # noqa: E402
from tuistore import scrape  # noqa: E402

AWESOME_URL = "https://raw.githubusercontent.com/rothgar/awesome-tuis/master/README.md"
OUT = ROOT / "tuistore" / "data" / "catalog.json"

# ── Gheat's suite — pinned to the top, hand-verified installs ───────────────
FEATURED = [
    dict(
        name="ltui", url="https://github.com/runpantheon/ltui",
        category="Productivity", language="Python",
        description="A fast, clean TUI for Linear — status-grouped issues, instant startup, full keyboard + mouse control.",
        author_note="by Gheat · the app the whole suite (and ricekit) grew out of",
        methods=[("uv", "uv tool install ltui-linear", "official"),
                 ("uv", "uv tool install git+https://github.com/runpantheon/ltui", "official")],
    ),
    dict(
        name="NaviTui", url="https://github.com/Gheat1/NaviTui",
        category="Multimedia", language="Python",
        description="An animated TUI player for Navidrome — cover art in the terminal, playback via mpv, themes via ricekit.",
        author_note="by Gheat · music + cover art, right in your terminal",
        methods=[("uv", "uv tool install git+https://github.com/Gheat1/NaviTui", "official")],
    ),
    dict(
        name="ricekit", url="https://github.com/Gheat1/ricekit",
        category="Development", language="Python",
        description="🍚 A developer's TUI suite for Textual — themes, widgets, modals, icons, and the design system behind ltui. This store is built on it.",
        author_note="by Gheat · the design system tuistore itself is built on — run ricekit-gallery",
        methods=[("uv", "uv tool install git+https://github.com/Gheat1/ricekit", "official")],
    ),
    dict(
        name="haal", url="https://github.com/indium114/haal",
        category="Dashboards", language="Rust",
        description="A small, configurable system-fetch tool — clean, fast, and Rust.",
        author_note="by @indium114 · a tiny, configurable fetch tool worth a look",
        methods=[("cargo", "cargo install haal", "official"),
                 ("cargo-binstall", "cargo binstall haal", "official")],
    ),
    dict(
        name="rmcl", url="https://github.com/objz/rmcl",
        category="Games", language="Rust",
        description="A Minecraft TUI/CLI launcher written in Rust — launch the game from your terminal.",
        author_note="by @objz · a Minecraft launcher that lives in the terminal",
        methods=[("brew", "brew install objz/tap/rmcl", "official"),
                 ("cargo", "cargo install rmcl", "official"),
                 ("yay", "yay -S rmcl", "official")],
    ),
    dict(
        name="Tomato.C", url="https://github.com/gabrielzschmitz/Tomato.C",
        category="Productivity", language="C",
        description="A pomodoro timer written in pure C — simple, focused, terminal-native.",
        author_note="by @gabrielzschmitz · a tiny pomodoro timer in pure C",
        methods=[("source",
                  "git clone https://github.com/gabrielzschmitz/Tomato.C && cd Tomato.C && sudo ./build.sh --install",
                  "official")],
    ),
    dict(
        name="network-doctor", url="https://github.com/heymaikol/network-doctor",
        category="Dashboards", language="Go",
        description="A cross-platform network-troubleshooting TUI — pinpoints DNS, TCP, TLS, HTTP and proxy failures, and suggests fixes.",
        author_note="by @heymaikol · diagnoses network failures and tells you how to fix them",
        methods=[("brew", "brew install --cask heymaikol/tap/network-doctor", "official"),
                 ("yay", "yay -S network-doctor", "official"),
                 ("go", "go install github.com/heymaikol/network-doctor@latest", "official")],
    ),
    dict(
        name="mcserver-setup", url="https://github.com/NolanCotter/mcserver-setup",
        category="Games", language="Rust",
        description="A polished terminal wizard for creating a reproducible Paper Minecraft server — turns plain-language choices into ready-to-run Docker Compose or native Java server files.",
        author_note="by @NolanCotter · stand up a Minecraft server without the usual yak-shaving",
        # `cargo run --release` from a clone only opens the wizard for that one
        # session and leaves nothing on PATH, so tuistore recorded a phantom
        # install (reported: github.com/Gheat1/tuistore/issues/3). `cargo
        # install --git` puts a real `mcserver-setup` binary on PATH instead.
        methods=[("cargo",
                  "cargo install --git https://github.com/NolanCotter/mcserver-setup --locked",
                  "official")],
    ),
    dict(
        name="cwal", url="https://github.com/nitinbhat972/cwal",
        category="CLI Tools", language="C",
        description="A blazing-fast pywal-like color palette generator, written in C.",
        author_note="by @nitinbhat972 · pywal's speed problem, solved in C",
        methods=[("brew", "brew tap nitinbhat972/cwal && brew install cwal", "official"),
                 ("yay", "yay -S cwal-git", "official"),
                 ("nix", "nix profile install nixpkgs#cwal", "official")],
    ),
    dict(
        name="termheat", url="https://github.com/moeen-mahmud/termheat",
        category="Dashboards", language="TypeScript",
        description="An animated terminal heatmap of your GitHub contributions — zero auth, zero config, npx and done.",
        author_note="by @moeen-mahmud · a satisfying way to watch your commit streak in the terminal",
        methods=[("npm", "npm install -g termheat", "official")],
    ),
]

# ── popular terminal apps beyond awesome-tuis ────────────────────────────────
# The stuff people actually `brew install` — fastfetch, neovim, the modern CLI
# toolkit — that a terminal-app store should carry even though they aren't
# strictly full-screen TUIs. (name, url, category). Descriptions + language are
# filled in by the GitHub enrichment pass; anything already in awesome-tuis is
# de-duped away, and all of these are guaranteed a README scrape.
ESSENTIALS: list[tuple[str, str, str]] = [
    # Editors
    ("Neovim", "https://github.com/neovim/neovim", "Editors"),
    ("Helix", "https://github.com/helix-editor/helix", "Editors"),
    ("micro", "https://github.com/zyedidia/micro", "Editors"),
    ("Kakoune", "https://github.com/mawww/kakoune", "Editors"),
    ("Vim", "https://github.com/vim/vim", "Editors"),
    ("amp", "https://github.com/jmacdonald/amp", "Editors"),
    # Shell & Prompt
    ("Starship", "https://github.com/starship/starship", "Shell & Prompt"),
    ("oh-my-posh", "https://github.com/JanDeDobbeleer/oh-my-posh", "Shell & Prompt"),
    ("zoxide", "https://github.com/ajeetdsouza/zoxide", "Shell & Prompt"),
    ("Atuin", "https://github.com/atuinsh/atuin", "Shell & Prompt"),
    ("McFly", "https://github.com/cantino/mcfly", "Shell & Prompt"),
    ("fzf", "https://github.com/junegunn/fzf", "Shell & Prompt"),
    ("tmux", "https://github.com/tmux/tmux", "Shell & Prompt"),
    ("Zellij", "https://github.com/zellij-org/zellij", "Shell & Prompt"),
    ("fish", "https://github.com/fish-shell/fish-shell", "Shell & Prompt"),
    ("Nushell", "https://github.com/nushell/nushell", "Shell & Prompt"),
    ("thefuck", "https://github.com/nvbn/thefuck", "Shell & Prompt"),
    ("navi", "https://github.com/denisidoro/navi", "Shell & Prompt"),
    # CLI Tools (the modern-unix toolkit)
    ("bat", "https://github.com/sharkdp/bat", "CLI Tools"),
    ("eza", "https://github.com/eza-community/eza", "CLI Tools"),
    ("lsd", "https://github.com/lsd-rs/lsd", "CLI Tools"),
    ("fd", "https://github.com/sharkdp/fd", "CLI Tools"),
    ("ripgrep", "https://github.com/BurntSushi/ripgrep", "CLI Tools"),
    ("sd", "https://github.com/chmln/sd", "CLI Tools"),
    ("jq", "https://github.com/jqlang/jq", "CLI Tools"),
    ("yq", "https://github.com/mikefarah/yq", "CLI Tools"),
    ("fx", "https://github.com/antonmedv/fx", "CLI Tools"),
    ("gron", "https://github.com/tomnomnom/gron", "CLI Tools"),
    ("jless", "https://github.com/PaulJuliusMartinez/jless", "CLI Tools"),
    ("tldr", "https://github.com/tldr-pages/tldr", "CLI Tools"),
    ("tealdeer", "https://github.com/dbrgn/tealdeer", "CLI Tools"),
    ("cheat", "https://github.com/cheat/cheat", "CLI Tools"),
    ("choose", "https://github.com/theryangeary/choose", "CLI Tools"),
    ("dog", "https://github.com/ogham/dog", "CLI Tools"),
    ("doggo", "https://github.com/mr-karan/doggo", "CLI Tools"),
    ("HTTPie", "https://github.com/httpie/cli", "CLI Tools"),
    ("xh", "https://github.com/ducaale/xh", "CLI Tools"),
    ("curlie", "https://github.com/rs/curlie", "CLI Tools"),
    ("glow", "https://github.com/charmbracelet/glow", "CLI Tools"),
    ("gum", "https://github.com/charmbracelet/gum", "CLI Tools"),
    ("vhs", "https://github.com/charmbracelet/vhs", "CLI Tools"),
    ("slides", "https://github.com/maaslalani/slides", "CLI Tools"),
    ("ugrep", "https://github.com/Genivia/ugrep", "CLI Tools"),
    ("croc", "https://github.com/schollz/croc", "CLI Tools"),
    ("rclone", "https://github.com/rclone/rclone", "CLI Tools"),
    ("ouch", "https://github.com/ouch-org/ouch", "CLI Tools"),
    ("miniserve", "https://github.com/svenstaro/miniserve", "CLI Tools"),
    ("termscp", "https://github.com/veeso/termscp", "CLI Tools"),
    # Development / Git
    ("GitHub CLI", "https://github.com/cli/cli", "Development"),
    ("tig", "https://github.com/jonas/tig", "Development"),
    ("delta", "https://github.com/dandavison/delta", "Development"),
    ("onefetch", "https://github.com/o2sh/onefetch", "Development"),
    ("git-cliff", "https://github.com/orhun/git-cliff", "Development"),
    ("gitleaks", "https://github.com/gitleaks/gitleaks", "Development"),
    ("difftastic", "https://github.com/Wilfred/difftastic", "Development"),
    ("serie", "https://github.com/lusingander/serie", "Development"),
    ("scc", "https://github.com/boyter/scc", "Development"),
    ("tokei", "https://github.com/XAMPPRocky/tokei", "Development"),
    ("grex", "https://github.com/pemistahl/grex", "Development"),
    ("just", "https://github.com/casey/just", "Development"),
    ("direnv", "https://github.com/direnv/direnv", "Development"),
    ("watchexec", "https://github.com/watchexec/watchexec", "Development"),
    ("ast-grep", "https://github.com/ast-grep/ast-grep", "Development"),
    ("hyperfine", "https://github.com/sharkdp/hyperfine", "Development"),
    ("topgrade", "https://github.com/topgrade-rs/topgrade", "Development"),
    ("mprocs", "https://github.com/pvolok/mprocs", "Development"),
    ("gitui", "https://github.com/extrawurst/gitui", "Development"),
    ("ghostty-config-cli", "https://github.com/ajr-khll/ghostty-config-cli", "Development"),
    # Dashboards / System
    ("fastfetch", "https://github.com/fastfetch-cli/fastfetch", "Dashboards"),
    ("neofetch", "https://github.com/dylanaraps/neofetch", "Dashboards"),
    ("gtop", "https://github.com/aksakalli/gtop", "Dashboards"),
    ("procs", "https://github.com/dalance/procs", "Dashboards"),
    ("dust", "https://github.com/bootandy/dust", "Dashboards"),
    ("duf", "https://github.com/muesli/duf", "Dashboards"),
    ("macchina", "https://github.com/Macchina-CLI/macchina", "Dashboards"),
    ("cpufetch", "https://github.com/Dr-Noob/cpufetch", "Dashboards"),
    ("ctop", "https://github.com/bcicen/ctop", "Dashboards"),
    ("trippy", "https://github.com/fujiapple852/trippy", "Dashboards"),
    ("zenith", "https://github.com/bvaisvil/zenith", "Dashboards"),
    ("bottom", "https://github.com/ClementTsang/bottom", "Dashboards"),
    ("bandwhich", "https://github.com/imsnif/bandwhich", "Dashboards"),
    ("gping", "https://github.com/orf/gping", "Dashboards"),
    ("wlocks", "https://github.com/programmersd21/wlocks", "Dashboards"),
    ("flow", "https://github.com/programmersd21/flow", "Dashboards"),
    # File Managers
    ("yazi", "https://github.com/sxyazi/yazi", "File Managers"),
    ("nnn", "https://github.com/jarun/nnn", "File Managers"),
    ("lf", "https://github.com/gokcehan/lf", "File Managers"),
    ("ranger", "https://github.com/ranger/ranger", "File Managers"),
    ("joshuto", "https://github.com/kamiyaa/joshuto", "File Managers"),
    ("superfile", "https://github.com/yorukot/superfile", "File Managers"),
    ("xplr", "https://github.com/sayanarijit/xplr", "File Managers"),
    ("felix", "https://github.com/kyoheiu/felix", "File Managers"),
    ("broot", "https://github.com/Canop/broot", "File Managers"),
    # Multimedia
    ("ncspot", "https://github.com/hrkfdn/ncspot", "Multimedia"),
    ("musikcube", "https://github.com/clangen/musikcube", "Multimedia"),
    ("termusic", "https://github.com/tramhao/termusic", "Multimedia"),
    ("spotify-player", "https://github.com/aome510/spotify-player", "Multimedia"),
    ("cava", "https://github.com/karlstav/cava", "Multimedia"),
    ("yt-dlp", "https://github.com/yt-dlp/yt-dlp", "Multimedia"),
    ("chafa", "https://github.com/hpjansson/chafa", "Multimedia"),
    ("viu", "https://github.com/atanunq/viu", "Multimedia"),
    ("timg", "https://github.com/hzeller/timg", "Multimedia"),
    # Productivity
    ("Taskwarrior", "https://github.com/GothenburgBitFactory/taskwarrior", "Productivity"),
    ("calcure", "https://github.com/anufrievroman/calcure", "Productivity"),
    ("buku", "https://github.com/jarun/buku", "Productivity"),
    ("taskell", "https://github.com/smallhadroncollider/taskell", "Productivity"),
    # Miscellaneous
    ("dummiesnmap", "https://github.com/kaizokuv/dummiesnmap", "Miscellaneous"),
    # Screensavers / fun
    ("cmatrix", "https://github.com/abishekvashok/cmatrix", "Screensavers"),
    ("pipes.sh", "https://github.com/pipeseroni/pipes.sh", "Screensavers"),
    ("lolcat", "https://github.com/busyloop/lolcat", "Screensavers"),
    ("genact", "https://github.com/svenstaro/genact", "Screensavers"),
    ("neo", "https://github.com/st3w/neo", "Screensavers"),
]

# Homebrew formula names for essentials where the formula != the repo name.
# Every other essential gets `brew install <repo-name>` inferred (usually right;
# a README-scraped brew command, if any, wins over it on dedupe).
ESSENTIAL_BREW = {  # keyed by owner/repo slug
    "cli/cli": "gh",
    "httpie/cli": "httpie",
    "dandavison/delta": "git-delta",
    "fish-shell/fish-shell": "fish",
    "GothenburgBitFactory/taskwarrior": "task",
    "pipeseroni/pipes.sh": "pipes-sh",
    "aome510/spotify-player": "spotify_player",
}


# ── 1. parse awesome-tuis ───────────────────────────────────────────────────
_H2 = re.compile(r"<h2>(.*?)</h2>", re.IGNORECASE)
_H3 = re.compile(r"<h3>(.*?)</h3>", re.IGNORECASE)
_ENTRY = re.compile(r"^\s*[-*]\s+\[([^\]]+)\]\(([^)\s]+)\)\s*(.*)$")


def _clean_desc(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)   # bold
    text = re.sub(r"[`*_]", "", text)              # stray emphasis/code
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # inline links
    text = re.sub(r"\s+", " ", text).strip()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def parse_awesome(md: str) -> list[dict]:
    category = "Miscellaneous"
    subcat: str | None = None
    in_toc = True
    out: list[dict] = []
    seen: set[str] = set()
    for line in md.splitlines():
        h2 = _H2.search(line)
        if h2:
            category = _clean_desc(h2.group(1))
            subcat = None
            in_toc = category.lower() == "table of contents"
            continue
        h3 = _H3.search(line)
        if h3:
            subcat = _clean_desc(h3.group(1))
            continue
        if in_toc:
            continue
        m = _ENTRY.match(line)
        if not m:
            continue
        name, url, desc = m.group(1).strip(), m.group(2).strip(), _clean_desc(m.group(3))
        if not url.startswith("http"):
            continue
        key = url.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        entry = dict(name=name, url=url, description=desc, category=category)
        # Under "Libraries", the <h3> is a language — a useful default hint.
        if category.lower() == "libraries" and subcat:
            entry["language"] = subcat
        out.append(entry)
    return out


# ── 3. GitHub GraphQL enrichment ────────────────────────────────────────────
async def _graphql(query: str) -> dict | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "api", "graphql", "-f", f"query={query}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if not out:
            if err:
                print("  graphql:", err.decode()[:200], file=sys.stderr)
            return None
        return json.loads(out)
    except Exception as e:
        print("  graphql error:", e, file=sys.stderr)
        return None


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


async def enrich(entries: list[dict], chunk: int = 40) -> None:
    gh_entries = [(i, parse_repo(e["url"])) for i, e in enumerate(entries)]
    gh_entries = [(i, r) for i, r in gh_entries if r]
    total = len(gh_entries)
    print(f"  enriching {total} GitHub repos via GraphQL…")
    for start in range(0, total, chunk):
        batch = gh_entries[start:start + chunk]
        parts = []
        for n, (idx, (owner, repo)) in enumerate(batch):
            parts.append(
                f'r{n}: repository(owner:"{_esc(owner)}", name:"{_esc(repo)}") '
                "{ stargazerCount primaryLanguage { name } isArchived pushedAt "
                "description homepageUrl }"
            )
        data = await _graphql("query { " + " ".join(parts) + " }")
        if not data:
            continue
        payload = data.get("data") or {}
        for n, (idx, _) in enumerate(batch):
            repo = payload.get(f"r{n}")
            if not repo:
                continue
            e = entries[idx]
            e["stars"] = repo.get("stargazerCount")
            lang = (repo.get("primaryLanguage") or {}).get("name")
            if lang:
                e["language"] = lang
            e["archived"] = bool(repo.get("isArchived"))
            e["pushed_at"] = repo.get("pushedAt")
            if repo.get("homepageUrl"):
                e["homepage"] = repo["homepageUrl"]
            if repo.get("description") and not e.get("description"):
                e["description"] = _clean_desc(repo["description"])
        print(f"    {min(start + chunk, total)}/{total}")


# ── 4/5. install methods ─────────────────────────────────────────────────────
async def add_methods(entries: list[dict], scrape_top: int) -> None:
    # rank github entries by stars for scraping budget
    ranked = sorted(
        (e for e in entries if parse_repo(e["url"])),
        key=lambda e: -(e.get("stars") or 0),
    )
    # featured tools keep only their hand-curated official methods — no
    # scraped/inferred noise (e.g. a monorepo README's sibling-package installs)
    scrape_set = {id(e) for e in ranked[:scrape_top] if not e.get("featured")}
    # always scrape the hand-picked essentials so they get verified installs
    scrape_set |= {id(e) for e in entries if e.get("_essential")}

    sem = asyncio.Semaphore(8)
    scraped_count = 0

    async def do(e: dict) -> None:
        nonlocal scraped_count
        methods: list[dict] = []
        # verified/official methods declared for featured tools
        for kind, cmd, source in e.pop("_methods", []):
            methods.append(make(kind, cmd, source=source).to_dict())
        if e.get("featured"):
            e["methods"] = methods
            return
        if id(e) in scrape_set:
            async with sem:
                found = await scrape.scrape_repo(e["url"])
            for m in found:
                methods.append(m.to_dict())
            scraped_count += 1
            if scraped_count % 20 == 0:
                print(f"    scraped {scraped_count}…")
        # inferred fallbacks from language
        for m in infer_methods(e["url"], e.get("language")):
            methods.append(m.to_dict())
        # essentials get a homebrew method — they're almost all in brew, and it's
        # what most people (and Mac users) reach for. Scraped brew, if any, wins.
        if e.get("_essential"):
            owner, repo = parse_repo(e["url"])
            formula = ESSENTIAL_BREW.get(f"{owner}/{repo}", repo.lower())
            methods.append(make("brew", f"brew install {formula}",
                                source="inferred", note="homebrew").to_dict())
        # dedupe by (kind, command), keep first (best source wins by order)
        seen = set()
        deduped = []
        for m in methods:
            k = (m["kind"], m["command"])
            if k not in seen:
                seen.add(k)
                deduped.append(m)
        if deduped:
            e["methods"] = deduped

    print(f"  scraping READMEs for {len(scrape_set)} tools…")
    await asyncio.gather(*(do(e) for e in entries))


# ── main ─────────────────────────────────────────────────────────────────────
def load_awesome(local: str | None) -> str:
    if local and Path(local).exists():
        return Path(local).read_text(encoding="utf-8")
    print("  fetching awesome-tuis README…")
    with urllib.request.urlopen(AWESOME_URL, timeout=30) as r:
        return r.read().decode("utf-8")


async def amain(args) -> None:
    md = load_awesome(args.local)
    parsed = parse_awesome(md)
    print(f"  parsed {len(parsed)} tools from awesome-tuis")

    # featured first; drop any awesome-tuis dupes of featured repos
    featured_keys = {parse_repo(f["url"]) for f in FEATURED}
    parsed = [e for e in parsed if parse_repo(e["url"]) not in featured_keys]

    featured_entries = []
    for f in FEATURED:
        e = dict(f)
        e["featured"] = True
        e["_methods"] = e.pop("methods", [])
        featured_entries.append(e)

    # popular terminal apps (fastfetch, neovim, the modern CLI toolkit…).
    # New ones get added; ones already in awesome-tuis get flagged essential so
    # they still get a guaranteed scrape + homebrew method.
    parsed_by_slug = {parse_repo(e["url"]): e for e in parsed}
    essentials, flagged, added = [], 0, set()
    for name, url, cat in ESSENTIALS:
        r = parse_repo(url)
        if not r or r in featured_keys or r in added:
            continue
        added.add(r)
        if r in parsed_by_slug:
            parsed_by_slug[r]["_essential"] = True  # flag existing awesome-tuis entry
            flagged += 1
        else:
            essentials.append(dict(name=name, url=url, description="",
                                   category=cat, _essential=True))
    print(f"  + {len(essentials)} new terminal apps, {flagged} existing flagged essential")

    entries = featured_entries + parsed + essentials

    await enrich(entries)                 # cheap, batched — always worth it
    await add_methods(entries, args.scrape)

    # sort: featured (in FEATURED order) first, then category, then stars
    feat_order = {parse_repo(f["url"]): i for i, f in enumerate(FEATURED)}
    def key(e):
        r = parse_repo(e["url"])
        if e.get("featured"):
            return (0, feat_order.get(r, 99), 0)
        return (1, 0, -(e.get("stars") or 0))
    entries.sort(key=key)

    for e in entries:
        e.pop("_methods", None)
        e.pop("_essential", None)

    doc = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "awesome-tuis (rothgar/awesome-tuis) + modern terminal essentials + Gheat suite",
        "count": len(entries),
        "entries": entries,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    have_methods = sum(1 for e in entries if e.get("methods"))
    print(f"\n  wrote {OUT.relative_to(ROOT)}")
    print(f"  {len(entries)} tools · {have_methods} with install methods · "
          f"{sum(1 for e in entries if e.get('stars'))} enriched")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", help="path to a local awesome-tuis README.md")
    ap.add_argument("--scrape", type=int, default=140,
                    help="scrape READMEs of the top-N most-starred (0=skip)")
    args = ap.parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
