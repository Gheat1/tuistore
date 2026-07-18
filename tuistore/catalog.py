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
@lru_cache(maxsize=1)
def load() -> Catalog:
    """Load the bundled catalog.json (package data)."""
    try:
        raw = resources.files("tuistore.data").joinpath("catalog.json").read_text()
    except (FileNotFoundError, ModuleNotFoundError):
        return Catalog()
    data = json.loads(raw)
    entries = _dedupe([Entry.from_dict(e) for e in data.get("entries", [])])
    return Catalog(
        entries=entries,
        generated_at=data.get("generated_at", ""),
        source=data.get("source", ""),
    )


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
    data = json.loads(open(path).read())
    return Catalog(
        entries=[Entry.from_dict(e) for e in data.get("entries", [])],
        generated_at=data.get("generated_at", ""),
        source=data.get("source", ""),
    )
