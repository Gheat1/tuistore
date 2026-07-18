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
        name="jtui", url="https://github.com/Gheat1/jtui",
        category="Productivity", language="Python",
        description="A fast, beautiful TUI for Jira — status-grouped tickets, instant startup, full keyboard + mouse control.",
        author_note="by Gheat · Jira, done right in the terminal",
        methods=[("uv", "uv tool install git+https://github.com/Gheat1/jtui", "official")],
    ),
    dict(
        name="sctui", url="https://github.com/Gheat1/sctui",
        category="Productivity", language="Python",
        description="A fast, beautiful TUI for Shortcut — status-grouped stories, instant startup, full keyboard + mouse control.",
        author_note="by Gheat · Shortcut stories at terminal speed",
        methods=[("uv", "uv tool install git+https://github.com/Gheat1/sctui", "official")],
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
]


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
        return Path(local).read_text()
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

    entries = featured_entries + parsed

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

    doc = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "awesome-tuis (rothgar/awesome-tuis) + Gheat suite",
        "count": len(entries),
        "entries": entries,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
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
