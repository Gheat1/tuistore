"""The catalog: the tool entries, loading them, and searching them.

Search is a small in-house fuzzy matcher (no extra dependency) that ranks by
subsequence quality with bonuses for word-boundary and contiguous hits — the
same shape as a fuzzy file finder. It scales to thousands of entries because
scoring one entry is a handful of string ops.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

from .installer import Method, parse_repo
from .paths import user_data_dir


@dataclass
class Entry:
    name: str
    url: str
    description: str = ""
    category: str = "Miscellaneous"
    language: str | None = None
    stars: int | None = None
    archived: bool = False
    pushed_at: str | None = None
    homepage: str | None = None
    featured: bool = False
    author_note: str = ""            # a line shown for Gheat's own tools
    methods: list[Method] = field(default_factory=list)

    # ── derived ────────────────────────────────────────────────────────
    @property
    def slug(self) -> str:
        parsed = parse_repo(self.url)
        return f"{parsed[0]}/{parsed[1]}" if parsed else self.url

    @property
    def repo(self) -> tuple[str, str] | None:
        return parse_repo(self.url)

    @property
    def is_github(self) -> bool:
        return parse_repo(self.url) is not None

    # ── (de)serialize ──────────────────────────────────────────────────
    def to_dict(self) -> dict:
        d = {"name": self.name, "url": self.url, "description": self.description,
             "category": self.category}
        if self.language:
            d["language"] = self.language
        if self.stars is not None:
            d["stars"] = self.stars
        if self.archived:
            d["archived"] = True
        if self.pushed_at:
            d["pushed_at"] = self.pushed_at
        if self.homepage:
            d["homepage"] = self.homepage
        if self.featured:
            d["featured"] = True
        if self.author_note:
            d["author_note"] = self.author_note
        if self.methods:
            d["methods"] = [m.to_dict() for m in self.methods]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        return cls(
            name=d["name"],
            url=d["url"],
            description=d.get("description", ""),
            category=d.get("category", "Miscellaneous"),
            language=d.get("language"),
            stars=d.get("stars"),
            archived=d.get("archived", False),
            pushed_at=d.get("pushed_at"),
            homepage=d.get("homepage"),
            featured=d.get("featured", False),
            author_note=d.get("author_note", ""),
            methods=[Method.from_dict(m) for m in d.get("methods", [])],
        )


@dataclass
class Catalog:
    entries: list[Entry] = field(default_factory=list)
    generated_at: str = ""
    source: str = ""

    @property
    def categories(self) -> list[str]:
        seen: list[str] = []
        for e in self.entries:
            if e.category not in seen:
                seen.append(e.category)
        return seen

    def by_category(self, category: str) -> list[Entry]:
        return [e for e in self.entries if e.category == category]


# ── fuzzy search ────────────────────────────────────────────────────────────
_WORD_BREAK = set(" -_/.:")


def fuzzy_score(query: str, text: str) -> float | None:
    """Subsequence match score, or None if `query` is not a subsequence of
    `text`. Higher is better. Rewards word-boundary and contiguous matches."""
    if not query:
        return 0.0
    text_l = text.lower()
    ti = 0
    score = 0.0
    streak = 0
    prev = -2
    for qc in query:
        found = text_l.find(qc, ti)
        if found < 0:
            return None
        if found == 0 or text_l[found - 1] in _WORD_BREAK:
            score += 4.0            # start of a word
        if found == prev + 1:
            streak += 1
            score += 2.0 + streak   # contiguous run, accelerating
        else:
            streak = 0
            score += 1.0
        prev = found
        ti = found + 1
    # denser (shorter) matches rank higher
    score += max(0.0, 12.0 - (len(text_l) - len(query)) * 0.04)
    return score


def _rank_key(entry: Entry, score: float) -> tuple:
    return (-score, 0 if entry.featured else 1, -(entry.stars or 0), entry.name.lower())


def search(entries: list[Entry], query: str, *, category: str | None = None,
           limit: int | None = None) -> list[Entry]:
    """Ranked results for `query`. Empty query = browse (featured & stars)."""
    pool = [e for e in entries if category is None or e.category == category]
    query = query.strip().lower()

    if not query:
        ordered = sorted(
            pool, key=lambda e: (0 if e.featured else 1, -(e.stars or 0), e.name.lower())
        )
        return ordered[:limit] if limit else ordered

    scored: list[tuple[Entry, float]] = []
    for e in pool:
        name_s = fuzzy_score(query, e.name)
        desc_s = fuzzy_score(query, e.description)
        lang_s = fuzzy_score(query, e.language or "")
        best = None
        for s, weight in ((name_s, 2.4), (desc_s, 1.0), (lang_s, 0.7)):
            if s is not None:
                cand = s * weight
                best = cand if best is None else max(best, cand)
        # also match against the whole "name description" haystack so
        # multi-word queries ("git dashboard") still land
        if best is None:
            hay = fuzzy_score(query, f"{e.name} {e.description}")
            if hay is not None:
                best = hay * 0.6
        if best is not None:
            scored.append((e, best))

    scored.sort(key=lambda pair: _rank_key(pair[0], pair[1]))
    result = [e for e, _ in scored]
    return result[:limit] if limit else result


# ── loading ──────────────────────────────────────────────────────────────────
# a fresher catalog fetched by `tuistore refetch-catalog` lands here and, if
# newer than the bundled one, is used instead — so the store updates without
# reinstalling the whole package.
from pathlib import Path  # noqa: E402

USER_CATALOG = user_data_dir() / "catalog.json"
CATALOG_URL = (
    "https://raw.githubusercontent.com/Gheat1/tuistore/main/tuistore/data/catalog.json"
)


def _bundled_text() -> str | None:
    try:
        return resources.files("tuistore.data").joinpath("catalog.json").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return None


_SRC_ORDER = {"official": 0, "readme": 1, "inferred": 2}


def _prefer_uv(entry: Entry) -> None:
    """Make `uv tool install` the default for Python tools. If any pip/pipx/uv
    method names a package, canonicalize a single uv method that inherits the
    most-trusted package name + source — so uv wins over pip/pipx/brew and uses
    the README-verified package name rather than a guess."""
    from .installed import pkg_from_command
    from .installer import make

    best_src, best_pkg = None, None
    for m in entry.methods:
        if m.kind in ("uv", "pipx", "pip"):
            pkg = pkg_from_command(m.kind, m.command)
            if pkg and (best_src is None
                        or _SRC_ORDER.get(m.source, 9) < _SRC_ORDER.get(best_src, 9)):
                best_src, best_pkg = m.source, pkg
    if not best_pkg:
        return
    entry.methods = [m for m in entry.methods if m.kind != "uv"]
    entry.methods.insert(0, make("uv", f"uv tool install {best_pkg}",
                                 source=best_src, note="python tool"))


# ── known-incompatible package/runtime combos ────────────────────────────────
# A handful of catalog packages install cleanly but crash at runtime with an
# otherwise-correct choice of manager — usually an unmaintained package that
# hard-depends on something a later language runtime removed. Rather than
# guess generically, the exact fix is pinned per package as reports come in.
# Keyed by "owner/repo" slug -> {kind: replacement command}. Applied AFTER
# _prefer_uv, since that unconditionally regenerates the uv method's command
# and would otherwise clobber any fix baked in earlier.
QUIRKS: dict[str, dict[str, str]] = {
    # thefuck 3.32 imports distutils, removed from the stdlib in Python 3.12
    # (see PEP 632, verified empirically: present on 3.11, gone on 3.12+).
    # uv resolves the newest available interpreter by default, so
    # `uv tool install thefuck` installs cleanly but crashes on first run
    # with "ModuleNotFoundError: No module named 'distutils'". Pinning uv to
    # 3.11 (the last version that still ships distutils) keeps uv as the
    # installer while sidestepping the incompatibility.
    # reported: https://github.com/Gheat1/tuistore/issues/3
    "nvbn/thefuck": {"uv": "uv tool install --python 3.11 thefuck"},
}


def _apply_quirks(entry: Entry) -> None:
    fixes = QUIRKS.get(entry.slug)
    if not fixes:
        return
    for m in entry.methods:
        replacement = fixes.get(m.kind)
        if replacement and m.command != replacement:
            m.command = replacement
            m.note = (m.note + " " if m.note else "") + "(pinned: known runtime incompatibility)"


def _parse(raw: str) -> Catalog:
    data = json.loads(raw)
    entries = _dedupe([Entry.from_dict(e) for e in data.get("entries", [])])
    for e in entries:
        _prefer_uv(e)
        _apply_quirks(e)
    return Catalog(
        entries=entries,
        generated_at=data.get("generated_at", ""),
        source=data.get("source", ""),
    )


@lru_cache(maxsize=1)
def load() -> Catalog:
    """Load the catalog — the user-refreshed copy if it's newer, else bundled."""
    bundled = _bundled_text()
    user = None
    try:
        if USER_CATALOG.exists():
            user = USER_CATALOG.read_text(encoding="utf-8")
    except OSError:
        user = None

    def gen_at(raw: str | None) -> str:
        try:
            return json.loads(raw).get("generated_at", "") if raw else ""
        except Exception:
            return ""

    chosen = bundled
    if user and gen_at(user) >= gen_at(bundled):
        chosen = user
    if not chosen:
        return Catalog()
    try:
        return _parse(chosen)
    except Exception:
        return _parse(bundled) if bundled else Catalog()


def refetch(url: str = CATALOG_URL, dest: Path = USER_CATALOG) -> tuple[bool, str]:
    """Download the latest catalog to the user path. Returns (ok, message)."""
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310
            raw = r.read().decode("utf-8")
        doc = json.loads(raw)  # validate before writing
        n = len(doc.get("entries", []))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(raw, encoding="utf-8")
        load.cache_clear()
        return True, f"{n} tools · updated {doc.get('generated_at', '')}"
    except Exception as e:
        return False, str(e)


def _dedupe(entries: list[Entry]) -> list[Entry]:
    """Keep the first entry per slug (featured come first, so they win)."""
    seen: set[str] = set()
    out: list[Entry] = []
    for e in entries:
        if e.slug in seen:
            continue
        seen.add(e.slug)
        out.append(e)
    return out


def load_from(path) -> Catalog:
    with open(path, encoding="utf-8") as f:
        return _parse(f.read())
