# contributing

## suggesting or fixing a catalog tool

The easiest way to help: [open an issue](https://github.com/Gheat1/tuistore/issues/new/choose)
with a "suggest a tool" or "catalog install problem" — a repo URL, and (for a
fix) what's wrong and what it should be instead. The catalog is regenerated
from `tools/build_catalog.py`, not hand-edited, so a PR against the generated
`tuistore/data/catalog.json` directly won't stick — see below if you'd rather
submit a fix yourself.

## running it locally

```sh
git clone https://github.com/Gheat1/tuistore
cd tuistore
uv run tuistore
```

## running the tests

```sh
uv run python -m unittest discover tests -v
```

CI runs this on Python 3.11, 3.12, and 3.13 for every PR.

## fixing the install engine

The interesting logic lives in:

- `tuistore/installer.py` — `Method`, trust tiers, ranking, `infer_methods()`
- `tuistore/installed.py` — the install ledger, `pkg_from_command()` (parses a
  package name out of a raw shell command — this is the part most likely to
  need a fix for a specific tool's install command shape)
- `tuistore/scrape.py` — pulls install commands out of a project's README
- `tools/build_catalog.py` — regenerates `tuistore/data/catalog.json` from
  awesome-tuis + a curated `ESSENTIALS` list + a `FEATURED` list

To regenerate the catalog after a change:

```sh
uv run python tools/build_catalog.py
```

This re-fetches awesome-tuis and re-scrapes READMEs, so it takes a minute or
two and needs a `gh`-authenticated shell for the GitHub API calls.

## UI changes

tuistore is built on [ricekit](https://github.com/Gheat1/ricekit) — read its
`DESIGN.md` before touching `tuistore/app.py`. The short version: rounded
borders, role-based chrome color (never bake truecolor into new widgets),
vim + mouse everywhere, and exactly one animation (`pop_in`, a 150ms fade).

## a PR is welcome for

- a fixed/improved install command for a specific catalog tool
- a genuine bug in the app or install engine (with a way to reproduce it)
- a new tool for the `ESSENTIALS` list in `tools/build_catalog.py`

Please don't open a PR to rebrand, rename, or fork this into a different
project — see [`LICENSE`](LICENSE).
