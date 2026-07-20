"""Pull real install commands out of a project's README.

The catalog ships with scraped commands for the featured + most-starred
tools; for everything else the app scrapes lazily the first time you open a
tool and caches the result. Either way the rule is the same: only keep a
command that (a) classifies as a known installer and (b) actually mentions
this tool — so we grab ``brew install lazygit`` but skip the project's
``apt install build-essential`` dependency lines.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re

from .installer import Method, arch_gated, classify, make, parse_repo

# fenced ``` blocks and single-backtick inline code
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")
# an optional `~` or `~/some/path` cwd-indicator (a common shell-prompt
# convention, e.g. `~$ cargo install ...`) directly before the prompt glyph —
# without it, "~$ cargo install --git ...--force" scrapes with "~$" glued
# onto the command, which then gets treated as a literal token downstream.
_PROMPT = re.compile(r"^\s*(?:~[\w./-]*)?(?:\$|#|>|\xe2\x9d\xaf|❯|»)\s+")
_MAXLEN = 400


def _clean(line: str) -> str:
    line = line.strip()
    line = _PROMPT.sub("", line)
    return line.strip()


def _iter_command_lines(readme: str):
    for block in _FENCE.findall(readme):
        for raw in block.splitlines():
            line = _clean(raw)
            if line and not line.startswith("#"):
                yield line
    for inline in _INLINE.findall(readme):
        line = _clean(inline)
        if line:
            yield line


def extract_methods(readme: str, url: str) -> list[Method]:
    parsed = parse_repo(url)
    if not parsed or not readme:
        return []
    owner, repo = parsed
    repo_l, owner_l = repo.lower(), owner.lower()
    # tokens that mean "this line is about *this* tool"
    tokens = {repo_l, repo_l.replace("-", ""), repo_l.replace("_", "-"), f"{owner_l}/{repo_l}"}

    found: dict[tuple[str, str], Method] = {}
    for line in _iter_command_lines(readme):
        if len(line) > _MAXLEN:
            continue
        kind = classify(line, at_start=True)
        if not kind:
            continue
        low = line.lower()
        mentions = any(t and t in low for t in tokens)
        if kind == "script":
            # keep an install script only if it points at this repo/owner
            if owner_l not in low and repo_l not in low:
                continue
        elif not mentions:
            continue
        cmd = re.sub(r"\s+", " ", line).strip()
        key = (kind, cmd)
        if key not in found:
            method = make(kind, cmd, source="readme", note="from README")
            # a macOS `arch -arm64 ...` wrapper only runs on macOS — gate it.
            if arch_gated(cmd):
                method.os = ["macos"]
            found[key] = method
        if len(found) >= 8:
            break
    return list(found.values())


# ── fetching a README ──────────────────────────────────────────────────────
async def _gh_readme(owner: str, repo: str) -> str | None:
    """Fetch via the authenticated gh CLI — handles any branch / filename."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "api", f"repos/{owner}/{repo}/readme",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0 or not out:
            return None
        data = json.loads(out)
        content = data.get("content", "")
        if data.get("encoding") == "base64":
            return base64.b64decode(content).decode("utf-8", "replace")
        return content
    except Exception:
        return None


async def _http_readme(owner: str, repo: str) -> str | None:
    try:
        import httpx
    except Exception:
        return None
    branches = ("HEAD", "main", "master")
    names = ("README.md", "readme.md", "README.rst", "README")
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        for branch in branches:
            for name in names:
                url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{name}"
                try:
                    r = await client.get(url)
                    if r.status_code == 200 and r.text.strip():
                        return r.text
                except Exception:
                    continue
    return None


async def fetch_readme(owner: str, repo: str) -> str | None:
    return await _gh_readme(owner, repo) or await _http_readme(owner, repo)


async def scrape_repo(url: str) -> list[Method]:
    parsed = parse_repo(url)
    if not parsed:
        return []
    readme = await fetch_readme(*parsed)
    if not readme:
        return []
    return extract_methods(readme, url)
