# changelog

## 0.4.2

- Published to PyPI: `pip install tuistore` / `uv tool install tuistore` /
  `pipx install tuistore` — no git URL needed. Required publishing
  [ricekit](https://pypi.org/project/ricekit/) to PyPI first (PyPI rejects
  packages with a direct git dependency) and switching tuistore's
  dependency to a normal version pin.

## 0.4.1

- Easier installs: a one-line installer script
  (`curl -fsSL .../install.sh | sh`, picks the best of uv/pipx/pip and
  installs uv first if none are found) and a real Homebrew tap
  (`brew install gheat1/tuistore/tuistore`).
- `tuistore update` / the manage menu's "update tuistore" now detect a
  Homebrew-installed copy and run `brew upgrade` instead of creating a
  second, parallel uv/pipx-managed install alongside it.

## 0.4.0

- Fixed `cargo install --git <url> --branch/--tag/--rev/--locked` package
  parsing — it was grabbing a flag's value as if it were the package name
  (e.g. `--branch main` → `pkg=main`), which broke update/uninstall for
  git-based cargo installs. Now derives the crate name from the `--git` URL
  itself, and `update` re-runs the original command with `--force` rather
  than reconstructing one from a guess.
- Fixed a class of "phantom installs": a bare `git clone <url> && cd <dir>`
  fallback (691 catalog entries) exits 0 without installing anything, but
  was recorded as a successful install anyway. Now detected precisely and
  shown as "cloned — not marked as installed" instead.
- Fixed the featured `mcserver-setup` entry recording a phantom install
  (`cargo run --release` only opens the wizard for one session).
- Fixed `thefuck` crashing on startup after a `uv`-installed run
  (`ModuleNotFoundError: No module named 'distutils'`, removed from the
  stdlib in Python 3.12) — pinned to `--python 3.11`. Root cause: `uv`
  installs were being force-ranked above better-documented alternatives
  even when they were pure guesses; fixed the ranking logic generally.
- Added a general safety net: after any install exits 0, tuistore verifies
  a real binary actually landed on `PATH` before calling it a success.
- Fixed the scraper's shell-prompt stripping for a leading `~` (e.g.
  `~$ command`), which was leaking into scraped install commands.
- Catalog grew to 751 tools (wlocks, flow, and others).

## 0.3.3

- First-boot welcome modal — a one-time, dismissible ask to star tuistore
  and the rest of the suite, and follow on GitHub. Never shown again after.

## 0.3.2

- `tuistore install <tool> --force` / `-f` reinstalls an already-installed
  tool, rewriting the command appropriately per manager (`brew reinstall`,
  `uv tool install --force`, etc.) in both the CLI and the app.

## 0.3.1

- The Installed view is now manager-aware: it asks brew/uv/npm/cargo/pipx
  what they have installed and matches by package name, so tools whose
  binary differs from their package (`ripgrep`→`rg`, `bottom`→`btm`) show
  up correctly.
- Added an "update everything" command — one shot that upgrades every
  package manager on the machine, not just what tuistore installed.
  `tuistore upgrade` from the shell does the same.

## 0.3.0

- Catalog expanded beyond awesome-tuis to include the modern terminal
  toolkit — fastfetch, neovim, ripgrep, bat, eza, starship, zoxide, fzf,
  and more — growing the catalog to 745 tools. New categories: CLI Tools,
  Shell & Prompt.

## 0.2.2

- `uv tool install` is now the preferred installer for Python tools when
  it's genuinely at least as trustworthy as the alternative (i.e. a
  project's own README documents a pip/pipx/uv install to inherit trust
  from) — never promoted ahead of a verified non-uv alternative.

## 0.2.1

- Portable `pip` commands (`python3 -m pip`, not a bare `pip`).
- A tool detected on `PATH` but not installed via tuistore can now be
  managed too — `u`/`x` fall back to a labelled best-guess command, or ask
  which manager you used when there's more than one plausible option.
- A universal "remove the binary" uninstall fallback for tools with no
  matching package-manager entry.

## 0.2.0

- tuistore became a real package manager, not just a browser: an install
  ledger tracks what it installed, `u`/`x` update and uninstall tools in
  place, and a manage menu (`,`) handles updating tuistore itself and
  refetching the catalog.
- CLI package-manager verbs: `tuistore install/remove/update/search/info`.
- Fixed `tuistore update` being a no-op (was version-gated; now always
  force-reinstalls from the latest commit).
- Fixed the results list scrolling snapping back to the top.

## 0.1.0

Initial release — a Textual TUI app store built on
[ricekit](https://github.com/Gheat1/ricekit): fuzzy search across a catalog
seeded from [awesome-tuis](https://github.com/rothgar/awesome-tuis), a
platform-aware install engine that only offers commands your machine can
actually run, GitHub starring, and Gheat's own suite (ltui, NaviTui,
ricekit) pinned to the top.
