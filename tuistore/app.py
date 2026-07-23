"""tuistore — the TUI app store, as a Textual app built on ricekit.

Layout is three panes under a search box: categories · results · detail.
Everything is cache-first (render the bundled catalog instantly, hydrate live
GitHub data + scrape installs in background workers) and follows the ricekit
doctrine — rounded borders, focus-recolor, role-based color, one animation.
"""

from __future__ import annotations

import webbrowser
from datetime import datetime, timezone

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Markdown, Static
from textual.widgets.option_list import Option

from ricekit import KitApp, icons, palette
from ricekit.modals import HelpModal, PickerModal, ThemeModal  # noqa: F401
from ricekit.widgets import KitFooter, KitScroll, NavList, Splitter, pop_in

from . import __version__, github, installed as inst, platform
from .catalog import Catalog, Entry, load, refetch, search
from .installer import KINDS, Method, best, rank, run_stream
from .paths import StoreDirs

DIRS = StoreDirs()

# language → truecolor dot (data color: stays truecolor in every theme)
LANG_COLOR = {
    "rust": "#dea584", "go": "#00add8", "python": "#4b8bbe", "c": "#8a929c",
    "c++": "#f34b7d", "javascript": "#f1e05a", "typescript": "#3178c6",
    "shell": "#89e051", "ruby": "#701516", "lua": "#5b7ec7", "zig": "#ec915c",
    "haskell": "#8f6fbf", "nim": "#ffe953", "crystal": "#c8c8c8", "java": "#e07b53",
    "kotlin": "#a97bff", "vim script": "#199f4b", "vim": "#199f4b", "perl": "#7aa6da",
    "ocaml": "#ee8809", "elixir": "#9b6dc1", "clojure": "#5881d8", "d": "#c74f4f",
    "julia": "#a270ba", "swift": "#f05138", "php": "#8892bf", "scala": "#c22d40",
}


def lang_dot(language: str | None) -> Text:
    if not language:
        return Text("○ ", style=palette.faint)
    color = LANG_COLOR.get(language.lower(), palette.dim)
    return Text("● ", style=color)


def star_str(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def rel_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    s = (datetime.now(timezone.utc) - dt).total_seconds()
    for cut, div, suf in (
        (3600, 60, "m"), (86400, 3600, "h"), (604800, 86400, "d"),
        (2629800, 604800, "w"), (31557600, 2629800, "mo"),
    ):
        if s < cut:
            return f"{int(s // div)}{suf} ago"
    return f"{int(s // 31557600)}y ago"


FEATURED_CAT = "★ Featured"
INSTALLED_CAT = "◆ Installed"
ALL_CAT = "All tools"


# ── install modal ────────────────────────────────────────────────────────────
class InstallModal(ModalScreen):
    """Confirm-then-run installer with streamed output. `a` swaps the method."""

    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("enter", "run", show=False),
        Binding("a", "alternatives", show=False),
        Binding("r", "readme", show=False),
        Binding("q", "close", show=False),
    ]

    DEFAULT_CSS = """
    InstallModal { align: center middle; background: $kit-overlay; }
    InstallModal #box {
        width: 84; max-width: 92%; height: auto; max-height: 88%;
        background: $kit-modal-bg; border: round $kit-border-focus; padding: 1 2;
    }
    InstallModal #title { padding: 0 0 1 0; }
    InstallModal #cmd {
        height: auto; padding: 1 2; margin: 0 0 1 0;
        border: round $kit-border; background: $kit-cursor;
    }
    InstallModal #trust { height: auto; padding: 0 1; margin: 0 0 1 0; }
    InstallModal #trust.warn { border-left: thick $kit-border-alt; }
    InstallModal #meta { padding: 0 0 1 0; }
    InstallModal #log { height: auto; max-height: 22; display: none; margin: 1 0 0 0; }
    InstallModal #log.on { display: block; }
    InstallModal #hint { padding: 1 0 0 0; }
    """

    def __init__(self, entry: Entry, method: Method, alternatives: list[Method],
                 force: bool = False) -> None:
        super().__init__()
        self.entry = entry
        self.method = method
        self.alternatives = alternatives
        self.force = force  # reinstall over an existing copy
        self.running = False
        self.done = False

    def _cmd(self) -> str:
        from .installer import force_variant
        return force_variant(self.method.kind, self.method.command) if self.force \
            else self.method.command

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(id="title")
            yield Static(id="cmd")
            yield Static(id="trust")
            yield Static(id="meta")
            with KitScroll(id="log"):
                yield Static(id="logtext")
            yield Static(id="hint")

    def on_mount(self) -> None:
        pop_in(self.query_one("#box"))
        self._refresh_view()

    def _refresh_view(self) -> None:
        e, m = self.entry, self.method
        title = Text()
        title.append(f"{icons.PLUG}  {'reinstall' if self.force else 'install'} ", style=palette.blue)
        title.append(e.name, style=f"bold {palette.text}")
        if self.force:
            title.append("  · already installed", style=palette.dim)
        self.query_one("#title", Static).update(title)

        cmd = Text()
        cmd.append("$ ", style=palette.dim)
        cmd.append(self._cmd(), style=palette.text)
        self.query_one("#cmd", Static).update(cmd)

        env, p = self.app.env, palette
        # trust banner — the security gate: verified vs from-README vs guessed
        trust_w = self.query_one("#trust", Static)
        tb = Text()
        warn = False
        if m.is_script:
            warn = True
            tb.append("⚠  ", style=f"bold {p.peach}")
            tb.append("this runs a remote install script — ", style=p.peach)
            tb.append("read it before you run it", style=f"bold {p.peach}")
        elif m.trust == "unverified":
            warn = True
            tb.append("⚠  ", style=f"bold {p.peach}")
            tb.append("unverified — guessed from the repo; ", style=p.peach)
            tb.append("confirm it's the right package", style=f"bold {p.peach}")
        elif m.trust == "community":
            tb.append(f"{icons.CHECK_CIRCLE}  ", style=p.green)
            tb.append("from the project's own README", style=p.green)
        else:  # verified
            tb.append(f"{icons.CHECK_CIRCLE}  ", style=p.green)
            tb.append("verified — maintainer-checked", style=p.green)
        trust_w.update(tb)
        trust_w.set_class(warn, "warn")

        meta = Text()
        meta.append(f"via {m.label}", style=p.sub)
        if m.note and m.trust != "verified":
            meta.append(f"   {m.note}", style=p.dim)
        if not m.available(env):
            meta.append(f"   ⚠ {m.why_unavailable(env)}", style=p.peach)
        if self.entry.is_github:
            meta.append("      ")
            meta.append("r", style=p.blue)
            meta.append(" read the README first", style=p.dim)
        self.query_one("#meta", Static).update(meta)
        self._hint()

    def _hint(self) -> None:
        h = Text()
        if self.done:
            h.append("enter", style=palette.blue)
            h.append(" / ", style=palette.faint)
            h.append("esc", style=palette.blue)
            h.append(" close", style=palette.dim)
        elif self.running:
            h.append(f"{icons.CLOCK} installing…", style=palette.peach)
        else:
            alts = len(self.alternatives)
            h.append("enter", style=palette.blue)
            h.append(" run   ", style=palette.dim)
            if alts:
                h.append("a", style=palette.blue)
                h.append(f" method ({alts})   ", style=palette.dim)
            h.append("r", style=palette.blue)
            h.append(" readme   ", style=palette.dim)
            h.append("esc", style=palette.blue)
            h.append(" cancel", style=palette.dim)
        self.query_one("#hint", Static).update(h)

    def action_readme(self) -> None:
        if self.entry.is_github:
            self.app.push_screen(ReadmeModal(self.entry))
        else:
            self.app.notify("no GitHub README for this tool", severity="warning")

    def action_alternatives(self) -> None:
        if self.running or self.done or not self.alternatives:
            return
        opts = []
        env = self.app.env
        for i, m in enumerate([self.method] + self.alternatives):
            # Dim text explains why a method cannot run, but styling alone does
            # not stop Textual from highlighting and selecting the row.
            available = m.available(env)
            row = Text()
            row.append("● " if i == 0 else "○ ",
                       style=palette.blue if available else palette.faint)
            row.append(f"{m.label}  ", style=palette.text if available else palette.dim)
            row.append(m.command, style=palette.dim)
            if not available:
                row.append(f"  ({m.why_unavailable(env)})", style=palette.peach)
            # Textual's native disabled state blocks both keyboard and mouse
            # selection while preserving the unavailable method as guidance.
            opts.append(Option(row, id=str(i), disabled=not available))

        def picked(idx: str | None) -> None:
            if idx is None:
                return
            chosen = ([self.method] + self.alternatives)[int(idx)]
            rest = [m for m in [self.method] + self.alternatives if m is not chosen]
            self.method, self.alternatives = chosen, rest
            self._refresh_view()

        self.app.push_screen(PickerModal("choose an install method", opts), picked)

    def action_run(self) -> None:
        if self.done:
            self.dismiss(None)
            return
        if self.running:
            return
        # The selected method may predate an environment change or arrive from
        # another caller, so the picker cannot be the only safety boundary.
        if not self.method.available(self.app.env):
            # Keep Enter harmless and explain exactly what the user must install
            # instead of starting a command that is guaranteed to fail.
            self.app.notify(self.method.why_unavailable(self.app.env), severity="warning")
            return
        self.running = True
        self.query_one("#log").add_class("on")
        self._hint()
        self._install()

    @work(exclusive=True, group="install")
    async def _install(self) -> None:
        logw = self.query_one("#logtext", Static)
        lines: list[str] = []
        code = "?"

        def flush() -> None:
            body = Text()
            for ln in lines[-400:]:
                body.append(ln + "\n", style=palette.sub)
            logw.update(body)
            log = self.query_one("#log")
            log.scroll_end(animate=False)

        async for kind, payload in run_stream(self._cmd()):
            if kind == "out":
                lines.append(payload)
                if len(lines) % 2 == 0 or len(lines) < 12:
                    flush()
            else:
                code = payload
        flush()

        self.running = False
        self.done = True
        result = Text()
        if code == "0" and self.method.is_bare_clone:
            # clone-only: nothing was actually built/installed, so never
            # record it as one — not a warning either, since nothing went
            # wrong, this method just isn't a complete install by design.
            result.append(f"{icons.CHECK_CIRCLE}  cloned ", style=f"bold {palette.blue}")
            result.append(self.entry.name, style=palette.text)
            result.append(" — follow its README to finish building; not marked as installed",
                          style=palette.dim)
            self.app.notify(
                f"cloned {self.entry.name} — this is a source checkout, not a finished "
                f"install; build it per the README", severity="information")
        elif code == "0":
            verified = self.app.on_installed(self.entry, self.method)
            if verified:
                result.append(f"{icons.CHECK_CIRCLE}  installed ", style=f"bold {palette.green}")
                result.append(self.entry.name, style=palette.text)
                self.app.notify(f"installed {self.entry.name}", severity="information")
            else:
                result.append(f"{icons.WARN}  ran, but no {self.entry.name} binary showed up on PATH",
                              style=f"bold {palette.peach}")
                self.app.notify(
                    f"{self.entry.name}: the command exited cleanly but nothing new landed on "
                    f"PATH — double check it actually installed", severity="warning")
        else:
            result.append(f"{icons.CROSS_CIRCLE}  exited with code {code}", style=f"bold {palette.red}")
            self.app.notify(f"{self.entry.name} install failed (code {code})", severity="error")
        lines.append("")
        lines.append(result.plain)
        flush()
        # append the styled result line properly
        body = Text()
        for ln in lines[-400:-2]:
            body.append(ln + "\n", style=palette.sub)
        body.append("\n")
        body.append(result)
        logw.update(body)
        self.query_one("#log").scroll_end(animate=False)
        self._hint()

    def action_close(self) -> None:
        if self.running:
            self.app.notify("install still running — let it finish", severity="warning")
            return
        self.dismiss(None)


# ── features / about screen ──────────────────────────────────────────────────
class FeaturesModal(ModalScreen):
    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("q", "close", show=False),
        Binding("f", "close", show=False),
    ]

    DEFAULT_CSS = """
    FeaturesModal { align: center middle; background: $kit-overlay; }
    FeaturesModal #fbox {
        width: 76; max-width: 92%; height: auto; max-height: 90%;
        background: $kit-modal-bg; border: round $kit-border-focus; padding: 1 3;
    }
    FeaturesModal #fbody { height: auto; max-height: 34; scrollbar-size-vertical: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="fbox"):
            with KitScroll(id="fbody"):
                yield Static(id="ftext")

    def on_mount(self) -> None:
        pop_in(self.query_one("#fbox"))
        p = palette
        t = Text()
        t.append("\U0001f6cd️  tuistore", style=f"bold {p.text}")
        t.append(f"   v{__version__}\n", style=p.dim)
        t.append("the TUI app store — find and install terminal apps without leaving the terminal\n\n",
                 style=p.sub)

        def feat(icon: str, head: str, body: str) -> None:
            t.append(f"  {icon}  ", style=p.blue)
            t.append(f"{head}\n", style=f"bold {p.text}")
            t.append(f"      {body}\n\n", style=p.dim)

        feat(icons.SEARCH, "instant fuzzy search",
             "type anywhere up top — matches names, descriptions and languages, ranked as you go.")
        feat(icons.LIST, "hundreds of tools, curated",
             "seeded from awesome-tuis + Gheat's own suite, grouped into browsable categories.")
        feat(icons.PLUG, "one-key install that fits your box",
             "detects your OS, distro and package managers, then offers only commands you can run —")
        t.append("      pacman on Arch, brew on mac, apt on Debian, cargo where cargo is.\n\n",
                 style=p.dim)
        feat(icons.STAR, "star from the store",
             "found something great? press s to star it on GitHub (via your gh login).")
        feat(icons.PAINTBRUSH, "five ricekit themes",
             "mocha · void · onyx · clear · system — press t to cycle, ctrl+p to preview any.")
        feat(icons.CHECK_CIRCLE, "verified vs unverified installs",
             "official + README-sourced commands are marked ✓; guessed ones are flagged ⚠, and")
        t.append("      remote install scripts get a clear warning — you always confirm before it runs.\n\n",
                 style=p.dim)
        feat(icons.LIST, "read the README in-app",
             "press r on any tool to read its README right here and inspect it before installing.")
        feat(icons.GEAR, "it's a package manager, not just a browser",
             "tuistore remembers what it installed — the ◆ Installed filter shows it all, u updates")
        t.append("      and x uninstalls in place, and , (manage) updates tuistore, refetches the\n"
                 "      catalog, or updates everything you've installed.\n\n", style=p.dim)

        t.append("  keys\n", style=f"bold {p.dim}")
        for k, d in (("/", "search"), ("enter", "open detail"), ("i", "install"),
                     ("r", "read README"), ("u", "update"), ("x", "uninstall"),
                     ("s", "star / unstar"), ("o", "open in browser"), (",", "manage"),
                     ("t", "theme"), ("?", "all keys"), ("q", "quit")):
            t.append(f"    {k.ljust(7)}", style=p.blue)
            t.append(f"{d}\n", style=p.sub)
        t.append("\n  built on ", style=p.dim)
        t.append("ricekit", style=p.mauve)
        t.append(" · made by ", style=p.dim)
        t.append("@Gheat1", style=p.lav)
        t.append("  ·  esc to close", style=p.faint)
        self.query_one("#ftext", Static).update(t)

    def action_close(self) -> None:
        self.dismiss(None)


# ── readme reader ────────────────────────────────────────────────────────────
class ReadmeModal(ModalScreen):
    """Read a tool's README right in the store — inspect it before installing."""

    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("q", "close", show=False),
        Binding("r", "close", show=False),
        Binding("o", "browser", show=False),
    ]

    DEFAULT_CSS = """
    ReadmeModal { align: center middle; background: $kit-overlay; }
    ReadmeModal #rbox {
        width: 92; max-width: 94%; height: 90%;
        background: $kit-modal-bg; border: round $kit-border-focus; padding: 1 2;
    }
    ReadmeModal #rtitle { height: auto; padding: 0 0 1 0; }
    ReadmeModal #rscroll { height: 1fr; scrollbar-size-vertical: 1; }
    ReadmeModal #rmd, ReadmeModal Markdown { background: transparent; }
    ReadmeModal #rhint { height: auto; padding: 1 0 0 0; }
    """

    def __init__(self, entry: Entry) -> None:
        super().__init__()
        self.entry = entry

    def compose(self) -> ComposeResult:
        with Vertical(id="rbox"):
            yield Static(id="rtitle")
            with KitScroll(id="rscroll"):
                yield Markdown("", id="rmd")
            yield Static(id="rhint")

    def on_mount(self) -> None:
        p = palette
        pop_in(self.query_one("#rbox"))
        title = Text()
        title.append(f"{icons.LIST}  ", style=p.blue)
        title.append(self.entry.name, style=f"bold {p.text}")
        title.append(f"   {self.entry.slug}", style=p.faint)
        self.query_one("#rtitle", Static).update(title)
        self.query_one("#rhint", Static).update(
            Text("loading README…", style=p.dim))
        self.query_one("#rscroll").focus()
        self._load()

    @work(exclusive=True, group="readme")
    async def _load(self) -> None:
        p = palette
        e = self.entry
        owner, repo = e.repo
        cached = DIRS.read_cache(f"readme_{owner}_{repo}")
        text = cached.get("text") if cached else None
        if text is None:
            from .scrape import fetch_readme
            text = await fetch_readme(owner, repo)
            if text:
                DIRS.write_cache(f"readme_{owner}_{repo}", {"text": text})
        md = self.query_one("#rmd", Markdown)
        if not text:
            await md.update(
                f"# {e.name}\n\n_Couldn't load a README for this tool._\n\n"
                "Press **o** to open it in your browser.")
        else:
            if len(text) > 60000:  # keep Markdown parsing snappy on huge docs
                text = text[:60000] + "\n\n---\n_… truncated — press **o** to read the rest on GitHub._"
            await md.update(text)
        self.query_one("#rhint", Static).update(
            Text("j/k scroll · o open in browser · esc close", style=p.dim))

    def action_browser(self) -> None:
        webbrowser.open(self.entry.homepage or self.entry.url)
        self.app.notify(f"opened {self.entry.url}")

    def action_close(self) -> None:
        self.dismiss(None)


# ── generic command runner (update / uninstall / self-update / update-all) ────
class RunModal(ModalScreen):
    """Confirm-then-run one shell command, streaming output."""

    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("enter", "run", show=False),
        Binding("q", "close", show=False),
    ]

    DEFAULT_CSS = """
    RunModal { align: center middle; background: $kit-overlay; }
    RunModal #rbox {
        width: 84; max-width: 92%; height: auto; max-height: 88%;
        background: $kit-modal-bg; border: round $kit-border-focus; padding: 1 2;
    }
    RunModal #rtitle { padding: 0 0 1 0; }
    RunModal #rcmd {
        height: auto; padding: 1 2; margin: 0 0 1 0;
        border: round $kit-border; background: $kit-cursor;
    }
    RunModal #rsub { height: auto; padding: 0 1; margin: 0 0 1 0; }
    RunModal #rsub.danger { border-left: thick $kit-border-alt; }
    RunModal #rlog { height: auto; max-height: 20; display: none; margin: 1 0 0 0; }
    RunModal #rlog.on { display: block; }
    RunModal #rhint { padding: 1 0 0 0; }
    """

    def __init__(self, title: str, command: str, *, subtitle: str = "",
                 danger: bool = False, verb: str = "run", on_success=None) -> None:
        super().__init__()
        self.title_txt = title
        self.command = command
        self.subtitle = subtitle
        self.danger = danger
        self.verb = verb
        self.on_success = on_success
        self.running = False
        self.done = False

    def compose(self) -> ComposeResult:
        with Vertical(id="rbox"):
            yield Static(id="rtitle")
            yield Static(id="rcmd")
            yield Static(id="rsub")
            with KitScroll(id="rlog"):
                yield Static(id="rlogtext")
            yield Static(id="rhint")

    def on_mount(self) -> None:
        pop_in(self.query_one("#rbox"))
        self._refresh_view()

    def _refresh_view(self) -> None:
        p = palette
        self.query_one("#rtitle", Static).update(Text(self.title_txt, style=f"bold {p.text}"))
        c = Text()
        c.append("$ ", style=p.dim)
        c.append(self.command, style=p.text)
        self.query_one("#rcmd", Static).update(c)
        sub = Text()
        if self.danger:
            sub.append("⚠  ", style=f"bold {p.peach}")
            sub.append(self.subtitle or "this removes the tool from your system", style=p.peach)
        elif self.subtitle:
            sub.append(self.subtitle, style=p.dim)
        self.query_one("#rsub", Static).update(sub)
        self.query_one("#rsub").set_class(self.danger, "danger")
        self._hint()

    def _hint(self) -> None:
        p = palette
        h = Text()
        if self.done:
            h.append("enter", style=p.blue)
            h.append(" / ", style=p.faint)
            h.append("esc", style=p.blue)
            h.append(" close", style=p.dim)
        elif self.running:
            h.append(f"{icons.CLOCK} working…", style=p.peach)
        else:
            h.append("enter", style=p.blue)
            h.append(f" {self.verb}   ", style=p.dim)
            h.append("esc", style=p.blue)
            h.append(" cancel", style=p.dim)
        self.query_one("#rhint", Static).update(h)

    def action_run(self) -> None:
        if self.done:
            self.dismiss(None)
            return
        if self.running:
            return
        self.running = True
        self.query_one("#rlog").add_class("on")
        self._hint()
        self._go()

    @work(exclusive=True, group="run")
    async def _go(self) -> None:
        logw = self.query_one("#rlogtext", Static)
        lines: list[str] = []
        code = "?"

        def flush() -> None:
            body = Text()
            for ln in lines[-400:]:
                body.append(ln + "\n", style=palette.sub)
            logw.update(body)
            self.query_one("#rlog").scroll_end(animate=False)

        async for kind, payload in run_stream(self.command):
            if kind == "out":
                lines.append(payload)
                if len(lines) % 2 == 0 or len(lines) < 12:
                    flush()
            else:
                code = payload
        self.running = False
        self.done = True
        p = palette
        res = Text()
        if code == "0":
            res.append(f"{icons.CHECK_CIRCLE}  done", style=f"bold {p.green}")
            if self.on_success:
                try:
                    self.on_success()
                except Exception:
                    pass
        else:
            res.append(f"{icons.CROSS_CIRCLE}  exited with code {code}", style=f"bold {p.red}")
        body = Text()
        for ln in lines[-400:]:
            body.append(ln + "\n", style=p.sub)
        body.append("\n")
        body.append(res)
        logw.update(body)
        self.query_one("#rlog").scroll_end(animate=False)
        self._hint()

    def action_close(self) -> None:
        if self.running:
            self.app.notify("still running — let it finish", severity="warning")
            return
        self.dismiss(None)


# ── manage menu (update tuistore · refetch catalog · update all) ──────────────
class ManageModal(ModalScreen):
    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("q", "close", show=False),
        Binding("comma", "close", show=False),
    ]

    DEFAULT_CSS = """
    ManageModal { align: center middle; background: $kit-overlay; }
    ManageModal #mbox {
        width: 60; height: auto; max-height: 85%;
        background: $kit-modal-bg; border: round $kit-border-focus; padding: 1 1;
    }
    ManageModal #mhead { padding: 0 1 1 1; }
    ManageModal #mlist { height: auto; max-height: 16; }
    ManageModal #mfoot { padding: 1 1 0 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="mbox"):
            yield Static(id="mhead")
            yield NavList(id="mlist")
            yield Static(id="mfoot")

    def _opt(self, oid: str, title: str, sub: str) -> Option:
        p = palette
        row = Text()
        row.append("  ")
        row.append(f"{title}  ", style=p.text)
        row.append(sub, style=p.dim)
        return Option(row, id=oid)

    def on_mount(self) -> None:
        p = palette
        pop_in(self.query_one("#mbox"))
        cat = self.app.catalog
        self.query_one("#mhead", Static).update(
            Text(f"{icons.GEAR}  manage", style=f"bold {p.sub}"))
        n = len(self.app.ledger)
        mgrs = inst.upgrade_managers(self.app.env, allow_sudo=False)
        ol = self.query_one("#mlist", NavList)
        ol.add_options([
            self._opt("everything", f"{icons.REFRESH} update everything",
                      " · ".join(mgrs) if mgrs else "no package managers found"),
            self._opt("installed", f"{icons.PLUG} update tuistore-installed", f"{n} tool(s)"),
            self._opt("self", f"{icons.LEVEL_UP} update tuistore", f"v{__version__} → latest"),
            self._opt("catalog", f"{icons.LIST} refetch catalog",
                      f"{len(cat.entries)} tools · {(cat.generated_at or '')[:10]}"),
            self._opt("cache", f"{icons.TRASH} clear cache", "scraped readmes & installs"),
        ])
        ol.highlighted = 0
        ol.focus()
        self.query_one("#mfoot", Static).update(
            Text(f"tuistore {__version__} · {self.app.env.label}", style=p.dim))

    @on(NavList.OptionSelected, "#mlist")
    def _selected(self, event: NavList.OptionSelected) -> None:
        oid = event.option.id or ""
        app = self.app
        self.dismiss(None)
        if oid == "everything":
            app.action_update_everything()
        elif oid == "self":
            app.action_update_self()
        elif oid == "catalog":
            app.refetch_catalog()
        elif oid == "installed":
            app.action_update_all()
        elif oid == "cache":
            count = DIRS.clear_cache()
            app.notify(f"cleared {count} cached file(s)")

    def action_close(self) -> None:
        self.dismiss(None)


# ── first-boot welcome / support ─────────────────────────────────────────────
# the rest of Gheat's suite (tuistore is offered on its own button)
SUITE_REPOS = [
    ("runpantheon", "ltui"),
    ("Gheat1", "NaviTui"),
    ("Gheat1", "ricekit"),
]


class WelcomeModal(ModalScreen):
    """Shown once, on the very first launch. A gentle ask for a star."""

    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("q", "close", show=False),
    ]

    DEFAULT_CSS = """
    WelcomeModal { align: center middle; background: $kit-overlay; }
    WelcomeModal #wbox {
        width: 66; max-width: 92%; height: auto;
        background: $kit-modal-bg; border: round $kit-border-focus; padding: 1 2;
    }
    WelcomeModal #wtitle { padding: 0 0 1 0; }
    WelcomeModal #wbody { height: auto; padding: 0 1 1 1; }
    WelcomeModal #wlist { height: auto; margin: 1 0 0 0; }
    WelcomeModal #whint { padding: 1 1 0 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="wbox"):
            yield Static(id="wtitle")
            yield Static(id="wbody")
            yield NavList(id="wlist")
            yield Static(id="whint")

    def _opt(self, glyph: str, text: str, color: str, oid: str) -> Option:
        row = Text()
        row.append(f"  {glyph}  ", style=color)
        row.append(text, style=palette.text)
        return Option(row, id=oid)

    def on_mount(self) -> None:
        p = palette
        pop_in(self.query_one("#wbox"))
        title = Text()
        title.append("\U0001f44b  ", style=p.peach)
        title.append("welcome to tuistore", style=f"bold {p.text}")
        self.query_one("#wtitle", Static).update(title)
        body = Text()
        body.append("thanks for installing! it's free and open source, made by one person.\n\n",
                    style=p.sub)
        body.append("if it's useful, a ", style=p.sub)
        body.append("★ star", style=p.peach)
        body.append(" genuinely makes my day — it's the single biggest thing that helps "
                    "tuistore grow. the little suite it's built on could use some love too. "
                    "no pressure at all \U0001f49b", style=p.sub)
        self.query_one("#wbody", Static).update(body)
        ol = self.query_one("#wlist", NavList)
        ol.add_options([
            self._opt("★", "star tuistore", p.peach, "star"),
            self._opt("★", "star the rest of the suite (ltui, ricekit, …)", p.peach, "suite"),
            self._opt("✚", "follow @Gheat1 on GitHub", p.blue, "follow"),
        ])
        ol.highlighted = 0
        ol.focus()
        hint = ("enter to do it  ·  esc — maybe later  ·  star any tool anytime with s"
                if github.available()
                else "auth the gh CLI (gh auth login) to do this from here  ·  esc to continue")
        self.query_one("#whint", Static).update(Text(hint, style=p.dim))

    @on(NavList.OptionSelected, "#wlist")
    def _selected(self, event: NavList.OptionSelected) -> None:
        if not github.available():
            self.app.notify("run `gh auth login` first, then use s on any tool",
                            severity="warning")
            return
        oid, idx = event.option.id, event.option_index
        if oid == "star":
            self._star_one("Gheat1", "tuistore", "tuistore", idx)
        elif oid == "suite":
            self._star_suite(idx)
        elif oid == "follow":
            self._follow(idx)

    def _done(self, idx: int, text: str) -> None:
        row = Text()
        row.append("  ✓  ", style=palette.green)
        row.append(text, style=palette.green)
        self.query_one("#wlist", NavList).replace_option_prompt_at_index(idx, row)

    @work(group="welcome")
    async def _star_one(self, owner: str, repo: str, label: str, idx: int) -> None:
        if await github.star(owner, repo):
            self._done(idx, f"starred {label} — thank you!")
            self.app.notify(f"★ starred {label} — you're the best!")
        else:
            self.app.notify(f"couldn't star {label} (check gh auth)", severity="warning")

    @work(group="welcome")
    async def _star_suite(self, idx: int) -> None:
        done = 0
        for owner, repo in SUITE_REPOS:
            if await github.star(owner, repo):
                done += 1
        self._done(idx, f"starred {done} suite repos — legend!")
        self.app.notify(f"★ starred {done} suite repos — thank you so much!")

    @work(group="welcome")
    async def _follow(self, idx: int) -> None:
        if await github.follow("Gheat1"):
            self._done(idx, "following @Gheat1 — thanks!")
            self.app.notify("✓ following @Gheat1 — appreciate you!")
        else:
            self.app.notify("couldn't follow (check gh auth)", severity="warning")

    def action_close(self) -> None:
        self.dismiss(None)


# ── the app ──────────────────────────────────────────────────────────────────
class StoreApp(KitApp):
    TITLE = "tuistore"

    BINDINGS = [
        Binding("slash", "focus_search", "search"),
        Binding("i", "install", "install"),
        Binding("r", "read_readme", "readme"),
        Binding("u", "update", "update", show=False),
        Binding("x", "uninstall", "remove", show=False),
        Binding("s", "toggle_star", "star"),
        Binding("o", "open_browser", "open"),
        Binding("comma", "manage", "manage", show=False),
        Binding("f", "features", "features", show=False),
        Binding("t", "cycle_kit_theme", "theme", show=False),
        Binding("question_mark", "help", "help"),
        Binding("q", "quit", "quit"),
        Binding("escape", "back", show=False),
    ]

    CSS = f"""
    Screen {{ layers: base; }}
    #search {{
        height: 3; margin: 0 1; padding: 0 1;
        border: round $kit-border; background: transparent;
        border-title-color: {palette.sub};
    }}
    #search:focus {{ border: round $kit-border-focus; }}

    #main {{ height: 1fr; padding: 0 1 0 0; }}

    #sidebar {{
        width: 25; margin: 0 0 0 1;
        border: round $kit-border; border-title-color: {palette.sub};
    }}
    #sidebar:focus {{ border: round $kit-border-focus; border-title-color: $kit-border-focus; }}

    #results {{
        width: 1fr; min-width: 16;
        border: round $kit-border;
        border-title-color: {palette.text}; border-subtitle-color: {palette.dim};
    }}
    #results:focus {{ border: round $kit-border-focus; }}

    #detail {{
        width: 46%; min-width: 40;
        border: round $kit-border;
        border-title-color: $kit-border-alt; border-subtitle-color: {palette.dim};
    }}
    #detail:focus-within {{ border: round $kit-border-alt; }}
    #detailscroll {{ height: 1fr; padding: 0 1; scrollbar-size-vertical: 1; }}
    #d-body {{ height: auto; }}

    OptionList {{
        background: transparent; border: none; padding: 0 1;
        scrollbar-size-vertical: 1;
    }}
    OptionList:focus {{ background: transparent; border: none; }}
    OptionList > .option-list--option-highlighted {{ background: $kit-cursor; }}
    OptionList:focus > .option-list--option-highlighted {{ background: $kit-cursor; }}

    CommandPalette {{ background: $kit-overlay; }}
    CommandPalette > Vertical {{ width: 70; max-width: 85%; }}
    CommandPalette #--input {{ background: $kit-modal-bg; }}
    CommandPalette CommandList {{ background: $kit-modal-bg; }}
    """

    def __init__(self) -> None:
        super().__init__()
        self.catalog: Catalog = load()
        self.env = platform.detect()
        self.query = ""
        self.active_category: str | None = None  # None = All
        self.current: Entry | None = None
        self._by_slug: dict[str, Entry] = {e.slug: e for e in self.catalog.entries}
        self._starred: dict[str, bool | None] = {}
        self._scraped: set[str] = set()
        self.ledger: dict = inst.load_ledger()
        self._bins = inst.path_binaries()
        self._pkgs: dict[str, set[str]] = {}  # manager -> installed package names

    # ── installed status ───────────────────────────────────────────────
    def status_of(self, entry: Entry) -> str | None:
        """"managed" (via tuistore), "present" (on PATH or in a package
        manager's installed list), or None."""
        return inst.status(entry.slug, entry.name, entry.methods, self.ledger,
                           self._bins, self._pkgs)

    @work(exclusive=True, group="scan", thread=True)
    def scan_managers(self) -> None:
        """Ask brew/uv/npm/cargo/pipx what they have installed (in a thread —
        the subprocess calls are slow), then refresh the current tool's markers.
        Deliberately does NOT rebuild the results list or sidebar here (that can
        yank a scroll to the top); the Installed count/markers refresh on the
        next interaction."""
        pkgs = inst.scan_installed(self.env)
        self._pkgs = pkgs
        if self.current is not None:
            self.call_from_thread(self.render_detail, self.current)

    def _reload_installed(self) -> None:
        self.ledger = inst.load_ledger()
        inst.refresh_path()
        self._bins = inst.path_binaries()
        self.scan_managers()

    def on_installed(self, entry: Entry, method: Method) -> bool:
        """Called by the install modal on a successful (exit 0) install —
        records it, refreshes state, and returns whether a real binary
        actually landed on PATH (a command can exit 0 without installing
        anything persistent, e.g. `cargo run` in a fresh clone)."""
        inst.record_install(entry.slug, entry.name, method)
        self._reload_installed()
        if self.current is entry:
            self.render_detail(entry)
        self.render_results(preserve=True)
        self._build_sidebar()
        return inst.verify_landed(entry.name, entry.methods)

    # ── compose ────────────────────────────────────────────────────────
    class _Search(Input):
        BINDINGS = [
            Binding("down", "to_results", show=False),
            Binding("escape", "clear_or_back", show=False),
        ]

        def action_to_results(self) -> None:
            self.app.query_one("#results").focus()

        def action_clear_or_back(self) -> None:
            if self.value:
                self.value = ""
            else:
                self.app.query_one("#results").focus()

    def compose(self) -> ComposeResult:
        yield self._Search(placeholder="search tools…  (name, description, language)", id="search")
        with Horizontal(id="main"):
            yield NavList(id="sidebar")
            yield Splitter("#sidebar", on_resized=self._save_layout)
            yield NavList(id="results")
            yield Splitter("#detail", invert=True, on_resized=self._save_layout)
            with Vertical(id="detail"):
                with KitScroll(id="detailscroll"):
                    yield Static(id="d-body")
        yield KitFooter(show_command_palette=False)

    def on_mount(self) -> None:
        state = DIRS.load_state()
        self.init_kit(theme=state.get("theme"))
        self.query_one("#search").border_title = f"{icons.SEARCH}  search"
        self.query_one("#sidebar").border_title = "categories"
        self._apply_layout(state.get("layout", {}))
        self._build_sidebar()
        # default to "All tools" so search up top searches everything; featured
        # still float to the top of the list on an empty query
        self.query_one("#sidebar", NavList).highlighted = 1
        self.active_category = None
        self.render_results()
        self.query_one("#search").focus()
        self.scan_managers()  # background: which brew/uv/npm/… packages are installed
        n = len(self.catalog.entries)
        self.set_timer(0.1, lambda: self.notify(
            f"{n} tools ready · {self.env.label} · type to search, ↓ to browse, ? for keys"))
        # first launch only: a gentle ask for a star (never shown again)
        if not state.get("welcomed"):
            DIRS.save_state({"welcomed": True})
            self.set_timer(0.5, lambda: self.push_screen(WelcomeModal()))

    def on_resize(self, event) -> None:
        # widths settle after the first layout — re-truncate rows to real width
        if self.is_mounted:
            self.render_results(preserve=True)

    def on_kit_theme_changed(self) -> None:
        # re-render Rich chrome so palette-based colors follow the theme
        if self.current:
            self.render_detail(self.current)
        self.render_results(preserve=True)
        self._build_sidebar()
        if not self.kit_theme_previewing:
            DIRS.save_state({"theme": self.theme})

    # ── layout persistence ─────────────────────────────────────────────
    def _save_layout(self, target: str, width: int | None) -> None:
        layout = DIRS.load_state().get("layout", {})
        if width is None:
            layout.pop(target, None)
        else:
            layout[target] = width
        DIRS.save_state({"layout": layout})

    def _apply_layout(self, layout: dict) -> None:
        for target, width in layout.items():
            try:
                self.query_one(target).styles.width = width
            except Exception:
                pass

    # ── sidebar ────────────────────────────────────────────────────────
    def _categories(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for e in self.catalog.entries:
            counts[e.category] = counts.get(e.category, 0) + 1
        cats = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        featured_n = sum(1 for e in self.catalog.entries if e.featured)
        installed_n = sum(1 for e in self.catalog.entries if self.status_of(e))
        head = [(FEATURED_CAT, featured_n), (INSTALLED_CAT, installed_n),
                (ALL_CAT, len(self.catalog.entries))]
        return head + cats

    def _build_sidebar(self) -> None:
        ol = self.query_one("#sidebar", NavList)
        self._building_sidebar = True  # suppress the highlight-restore cascade
        prev = ol.highlighted
        ol.clear_options()
        opts: list[Option] = []
        for name, count in self._categories():
            active = (
                (name == FEATURED_CAT and self.active_category == "__featured__")
                or (name == INSTALLED_CAT and self.active_category == "__installed__")
                or (name == ALL_CAT and self.active_category is None)
                or name == self.active_category
            )
            row = Text(no_wrap=True, overflow="ellipsis")
            if active:
                row.append("▎ ", style=palette.blue)
            else:
                row.append("  ")
            style = palette.text if active else palette.sub
            if name == FEATURED_CAT:
                row.append(name, style=f"bold {palette.peach if not active else palette.text}")
            elif name == INSTALLED_CAT:
                row.append(name, style=f"bold {palette.green if not active else palette.text}")
            else:
                row.append(name, style=style)
            row.append(f"  {count}", style=palette.faint)
            opts.append(Option(row, id=name))
        ol.add_options(opts)
        ol.highlighted = prev if prev is not None else 0
        self._building_sidebar = False

    def _category_key(self, name: str) -> str | None:
        if name == ALL_CAT:
            return None
        if name == FEATURED_CAT:
            return "__featured__"
        if name == INSTALLED_CAT:
            return "__installed__"
        return name

    @on(NavList.OptionSelected, "#sidebar")
    def _sidebar_selected(self, event: NavList.OptionSelected) -> None:
        self.active_category = self._category_key(event.option.id or ALL_CAT)
        if self.active_category == "__installed__":
            self._reload_installed()  # reflect anything installed since launch
        self._build_sidebar()
        self.render_results()
        self.query_one("#results").focus()

    @on(NavList.OptionHighlighted, "#sidebar")
    def _sidebar_highlighted(self, event: NavList.OptionHighlighted) -> None:
        # ignore the highlight-restore fired by a programmatic rebuild — only
        # react to the user actually arrowing through categories
        if getattr(self, "_building_sidebar", False):
            return
        new = self._category_key(event.option.id or ALL_CAT)
        if new != self.active_category:
            self.active_category = new
            self.render_results()

    # ── results ────────────────────────────────────────────────────────
    def _current_pool(self) -> list[Entry]:
        if self.active_category == "__featured__":
            pool = [e for e in self.catalog.entries if e.featured]
            return search(pool, self.query) if self.query else pool
        if self.active_category == "__installed__":
            pool = [e for e in self.catalog.entries if self.status_of(e)]
            return search(pool, self.query) if self.query else pool
        cat = None if self.active_category is None else self.active_category
        return search(self.catalog.entries, self.query, category=cat)

    def _result_row(self, e: Entry, width: int) -> Option:
        row = Text(no_wrap=True, overflow="ellipsis")
        row.append_text(lang_dot(e.language))
        name_style = f"bold {palette.text}" if e.featured else palette.text
        row.append(e.name, style=name_style)
        if e.featured:
            row.append(" ★", style=palette.peach)
        if e.archived:
            row.append(" ⊘", style=palette.faint)
        installed = self.status_of(e)
        if installed:
            row.append(" ✓", style=palette.green)
        pad = max(1, 24 - len(e.name) - (2 if e.featured else 0)
                  - (2 if e.archived else 0) - (2 if installed else 0))
        row.append(" " * pad)
        row.append("★ ", style=palette.dim)  # plain glyph: readable without a nerd font
        row.append(f"{star_str(e.stars):<6}", style=palette.dim)
        if e.description:
            row.append("  ")
            row.append(e.description, style=palette.dim)
        # hard-truncate: OptionList doesn't reliably honor Text.no_wrap, and a
        # wrapped row breaks the one-line-per-tool rhythm and cursor highlight
        row.truncate(max(24, width - 2), overflow="ellipsis")
        return Option(row)  # index-addressed; slugs aren't unique across dupes

    def render_results(self, preserve: bool = False) -> None:
        ol = self.query_one("#results", NavList)
        prev = ol.highlighted if preserve else None
        pool = self._current_pool()
        self._results_pool = pool
        width = ol.size.width or 60
        ol.clear_options()
        if not pool:
            ol.add_options([Option(Text("  no matches", style=palette.dim), disabled=True)])
            ol.border_title = "tools"
            ol.border_subtitle = "0"
            self.query_one("#d-body", Static).update(
                Text("\n  nothing here — try another search", style=palette.dim))
            return
        ol.add_options([self._result_row(e, width) for e in pool])
        label = {None: "all tools", "__featured__": "featured",
                 "__installed__": "installed"}.get(self.active_category, self.active_category)
        ol.border_title = f"{label}"
        ol.border_subtitle = f"{len(pool)}"
        target = prev if (prev is not None and prev < len(pool)) else 0
        ol.highlighted = target

    @on(NavList.OptionHighlighted, "#results")
    def _result_highlighted(self, event: NavList.OptionHighlighted) -> None:
        idx = event.option_index
        pool = getattr(self, "_results_pool", [])
        if idx is None or idx >= len(pool):
            return
        entry = pool[idx]
        if entry is self.current:
            return  # a programmatic re-highlight of the same row — don't re-hydrate
        self.current = entry
        self.render_detail(entry)
        self.hydrate(entry.slug)

    @on(NavList.OptionSelected, "#results")
    def _result_selected(self, event: NavList.OptionSelected) -> None:
        self.query_one("#detailscroll").focus()

    @on(Input.Changed, "#search")
    def _search_changed(self, event: Input.Changed) -> None:
        self.query = event.value
        self.render_results()

    @on(Input.Submitted, "#search")
    def _search_submitted(self) -> None:
        self.query_one("#results").focus()

    # ── detail ─────────────────────────────────────────────────────────
    def _methods_for(self, entry: Entry) -> list[Method]:
        return rank(entry.methods, self.env)

    def render_detail(self, entry: Entry) -> None:
        p = palette
        t = Text()
        # title
        t.append("\n")
        t.append(entry.name, style=f"bold {p.text}")
        if entry.featured:
            t.append("  ★ featured", style=p.peach)
        if entry.archived:
            t.append("  ⊘ archived", style=p.faint)
        t.append("\n")
        # star + meta line
        starred = self._starred.get(entry.slug)
        star_glyph = "★" if starred else "☆"
        star_col = p.peach if starred else p.dim
        meta = Text()
        meta.append(f"{star_glyph} {star_str(entry.stars)}", style=star_col)
        if entry.language:
            meta.append("   ")
            meta.append_text(lang_dot(entry.language))
            meta.append(entry.language, style=p.sub)
        if entry.pushed_at:
            meta.append(f"   {icons.CLOCK} {rel_time(entry.pushed_at)}", style=p.dim)
        t.append_text(meta)
        t.append("\n")
        t.append(entry.slug, style=p.faint)
        t.append("\n\n")
        # installed status
        st = self.status_of(entry)
        if st == "managed":
            rec = self.ledger.get(entry.slug, {})
            t.append(f"{icons.CHECK_CIRCLE} installed", style=f"bold {p.green}")
            if rec.get("kind"):
                t.append(f" · via {rec['kind']}", style=p.dim)
            if rec.get("at"):
                t.append(f" · {rec['at']}", style=p.faint)
            t.append("     ")
            t.append("u", style=p.blue)
            t.append(" update  ", style=p.dim)
            t.append("x", style=p.blue)
            t.append(" uninstall", style=p.dim)
            t.append("\n\n")
        elif st == "present":
            t.append(f"{icons.CHECK_CIRCLE} installed", style=f"bold {p.green}")
            t.append("  · detected on your PATH", style=p.dim)
            if self._manage_candidates(entry, "uninstall"):
                t.append("     ")
                t.append("u", style=p.blue)
                t.append(" update  ", style=p.dim)
                t.append("x", style=p.blue)
                t.append(" uninstall", style=p.dim)
                t.append("\n")
                t.append("  tuistore didn't record this — it'll ask which manager you used",
                         style=p.faint)
            t.append("\n\n")
        # author note (Gheat's suite)
        if entry.author_note:
            t.append(f"{icons.STAR} ", style=p.peach)
            t.append(entry.author_note, style=p.lav)
            t.append("\n\n")
        # description
        if entry.description:
            t.append(entry.description, style=p.sub)
            t.append("\n\n")
        # install section
        t.append("install\n", style=f"bold {p.dim}")
        # one row per manager for readability (the modal still lists every variant)
        methods, seen_kinds = [], set()
        for m in self._methods_for(entry):
            if m.kind in seen_kinds:
                continue
            seen_kinds.add(m.kind)
            methods.append(m)
        chosen = best(entry.methods, self.env)
        if not methods:
            t.append("  no known method yet — ", style=p.dim)
            t.append("press r to read the README", style=p.blue)
            t.append("\n")
        else:
            for m in methods[:7]:
                avail = m.available(self.env)
                is_best = m is chosen or (chosen and m.kind == chosen.kind and m.command == chosen.command)
                bullet = "▸ " if is_best else "  "
                t.append(bullet, style=p.blue if is_best else p.faint)
                t.append(f"{m.label}", style=p.text if avail else p.dim)
                if m.trust == "verified":
                    t.append("  ✓ verified", style=p.green)
                elif m.trust == "community":
                    t.append("  ✓ readme", style=p.green)
                else:
                    t.append("  ⚠ unverified", style=p.peach)
                t.append("\n")
                t.append("     $ ", style=p.faint)
                t.append(m.command, style=p.sub if avail else p.faint)
                if not avail:
                    t.append(f"   {m.why_unavailable(self.env)}", style=p.peach)
                t.append("\n")
            if chosen:
                t.append("\n")
                t.append(f"  {icons.PLUG} press ", style=p.dim)
                t.append("i", style=p.blue)
                t.append(" to install with the ", style=p.dim)
                t.append("▸", style=p.blue)
                t.append(" method\n", style=p.dim)
        t.append("\n")
        # hint bar
        hint = Text()
        for key, lbl in (("i", "install"), ("r", "readme"), ("s", "star"),
                         ("o", "browser"), ("/", "search")):
            hint.append(f"{key} ", style=p.blue)
            hint.append(f"{lbl}   ", style=p.dim)
        t.append_text(hint)

        # keep the reader's place when a background refresh re-renders the SAME
        # tool; only reset to the top when we've switched to a different tool
        scroll = self.query_one("#detailscroll")
        same = getattr(self, "_detail_slug", None) == entry.slug
        keep_y = scroll.scroll_offset.y if same else 0
        self.query_one("#d-body", Static).update(t)
        self._detail_slug = entry.slug
        if keep_y:
            self.call_after_refresh(
                lambda y=keep_y: self.query_one("#detailscroll").scroll_to(y=y, animate=False))
        dv = self.query_one("#detail")
        dv.border_title = f"{icons.INFO_CIRCLE} {entry.name}"
        dv.border_subtitle = entry.category

    @work(exclusive=True, group="hydrate")
    async def hydrate(self, slug: str) -> None:
        """Cache-first: refresh live stars, star-state, and scrape installs."""
        entry = self._by_slug.get(slug)
        if not entry or not entry.is_github:
            return
        owner, repo = entry.repo
        # star state (cached in memory for the session)
        if slug not in self._starred:
            self._starred[slug] = None
            starred = await github.is_starred(owner, repo)
            self._starred[slug] = starred
            if self.current is entry:
                self.render_detail(entry)
        # live star count + freshness
        info = await github.repo_info(owner, repo)
        if info and self.current is entry:
            if info.get("stars") is not None:
                entry.stars = info["stars"]
            if info.get("pushed_at"):
                entry.pushed_at = info["pushed_at"]
            # only re-render the detail pane — NOT the results list. Rebuilding
            # the list here re-fires OptionHighlighted, which restarts hydrate in
            # a loop that clears the list and snaps the scroll back to the top.
            self.render_detail(entry)
        # lazy README scrape when we don't have verified methods yet
        has_verified = any(m.source in ("readme", "official") for m in entry.methods)
        if not has_verified and slug not in self._scraped:
            self._scraped.add(slug)
            cached = DIRS.read_cache(f"methods_{owner}_{repo}")
            if cached and cached.get("methods"):
                found = [Method.from_dict(m) for m in cached["methods"]
                         if m.get("kind") in KINDS]
            else:
                from .scrape import scrape_repo
                found = await scrape_repo(entry.url)
                DIRS.write_cache(f"methods_{owner}_{repo}",
                                 {"methods": [m.to_dict() for m in found]})
            if found:
                have = {(m.kind, m.command) for m in entry.methods}
                entry.methods = [m for m in found if (m.kind, m.command) not in have] + entry.methods
                if self.current is entry:
                    self.render_detail(entry)

    # ── actions ────────────────────────────────────────────────────────
    def action_focus_search(self) -> None:
        s = self.query_one("#search", Input)
        s.focus()

    def action_back(self) -> None:
        focused = self.focused
        if focused and focused.id in ("detailscroll", "detail"):
            self.query_one("#results").focus()
        elif focused and focused.id == "results":
            self.query_one("#sidebar").focus()

    def action_install(self) -> None:
        if not self.current:
            return
        self._reload_installed()  # fresh view of what's already on the machine
        entry = self.current
        methods = self._methods_for(entry)
        if not methods:
            self.notify("no known install method — opening the README", severity="warning")
            self.action_read_readme()
            return
        chosen = best(entry.methods, self.env)
        alternatives = [m for m in methods if m is not chosen]
        force = self.status_of(entry) is not None  # already installed -> reinstall
        self.push_screen(InstallModal(entry, chosen, alternatives, force=force))

    def action_read_readme(self) -> None:
        if not self.current:
            return
        if not self.current.is_github:
            self.notify("no GitHub README — opening in browser", severity="warning")
            self.action_open_browser()
            return
        self.push_screen(ReadmeModal(self.current))

    # ── manage installed tools ─────────────────────────────────────────
    def _manage_candidates(self, entry: Entry, action: str) -> list[tuple[str, str]]:
        """(manager, command) pairs this machine can run to update/uninstall the
        tool — one per manager, best-ranked first. Used for detected tools where
        we don't know which manager was used."""
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for m in rank(entry.methods, self.env):
            if m.kind in seen or not m.available(self.env):
                continue
            pkg = inst.pkg_from_command(m.kind, m.command)
            if not pkg:
                continue
            rec = {"name": entry.name, "kind": m.kind, "pkg": pkg,
                   "bin": pkg, "command": m.command}
            cmd = (inst.uninstall_command if action == "uninstall"
                   else inst.update_command)(rec)
            if cmd:
                seen.add(m.kind)
                out.append((m.kind, cmd))
        # universal last-resort: delete the detected binary — works no matter
        # how it was installed (a script, a package name we don't carry, etc.)
        if action == "uninstall":
            binn = next((c for c in inst.candidate_bins(entry.name, entry.methods)
                         if c in self._bins), None)
            if binn:
                out.append(("binary", f'rm -f "$(command -v {binn})"'))
        return out

    def _run_manage(self, entry: Entry, kind: str, cmd: str, action: str,
                    guessed: bool) -> None:
        if action == "uninstall":
            sub = f"removes {entry.name} ({kind})"
            if guessed:
                sub += " · guessed — check it's how you installed it"
            self.push_screen(RunModal(
                f"{icons.TRASH} uninstall {entry.name}", cmd, subtitle=sub,
                danger=True, verb="uninstall",
                on_success=lambda: self._after_uninstall(entry.slug)))
        else:
            sub = f"via {kind}" + (" · guessed" if guessed else "")
            self.push_screen(RunModal(
                f"{icons.REFRESH} update {entry.name}", cmd, subtitle=sub,
                verb="update", danger=guessed,
                on_success=lambda: self._after_manage(entry.slug)))

    def _manage(self, action: str) -> None:
        e = self.current
        if not e:
            return
        self._reload_installed()  # pick up installs done in another session / the CLI
        st = self.status_of(e)
        if st == "managed":
            rec = self.ledger.get(e.slug, {})
            cmd = (inst.uninstall_command if action == "uninstall"
                   else inst.update_command)(rec)
            if not cmd:
                self.notify(f"no {action} command for a {rec.get('kind','?')} install",
                            severity="warning")
                return
            self._run_manage(e, rec.get("kind", ""), cmd, action, guessed=False)
            return
        if st != "present":
            self.notify(f"{e.name} isn't installed", severity="warning")
            return
        cands = self._manage_candidates(e, action)
        if not cands:
            self.notify(f"no {action} command tuistore can run for {e.name}",
                        severity="warning")
            return
        if len(cands) == 1:
            self._run_manage(e, cands[0][0], cands[0][1], action, guessed=True)
            return
        # detected on PATH, several possible managers — ask which one they used
        opts = []
        for i, (kind, cmd) in enumerate(cands):
            row = Text()
            row.append(f"{kind:<8}", style=palette.text)
            row.append(cmd, style=palette.dim)
            opts.append(Option(row, id=str(i)))

        def picked(idx: str | None) -> None:
            if idx is not None:
                kind, cmd = cands[int(idx)]
                self._run_manage(e, kind, cmd, action, guessed=True)

        self.push_screen(PickerModal(f"how did you install {e.name}?", opts), picked)

    def action_update(self) -> None:
        self._manage("update")

    def action_uninstall(self) -> None:
        self._manage("uninstall")

    def _after_manage(self, slug: str) -> None:
        self._reload_installed()
        e = self._by_slug.get(slug)
        if e and self.current is e:
            self.render_detail(e)

    def _after_uninstall(self, slug: str) -> None:
        inst.forget(slug)
        self._reload_installed()
        e = self._by_slug.get(slug)
        if e:
            self.notify(f"removed {e.name}")
            if self.current is e:
                self.render_detail(e)
        self.render_results(preserve=True)
        self._build_sidebar()

    def action_manage(self) -> None:
        self.push_screen(ManageModal())

    def action_update_self(self) -> None:
        from .__main__ import _how_installed, _install_source, _self_update_manager, _SELF_SRC

        how = _how_installed()
        # if this copy was installed via brew, update through brew instead of
        # creating a second, parallel uv/pipx-managed copy alongside it
        if how == "brew":
            self.push_screen(RunModal(
                f"{icons.REFRESH} update tuistore", "brew upgrade gheat1/tuistore/tuistore",
                subtitle="installed via Homebrew — updating with brew instead",
                verb="update"))
            return

        # a git-sourced install needs --force --refresh to pull the latest
        # commit (a plain upgrade is version-gated); a PyPI-sourced install
        # should use the normal version-gated upgrade so it never silently
        # jumps ahead of an actual release onto git's HEAD.
        from_git = _install_source() == "git"
        src = _SELF_SRC if from_git else "tuistore"
        # match the manager that actually owns this copy (see
        # _self_update_manager) instead of just whichever of uv/pipx happens
        # to be on PATH, so this doesn't create a second, parallel copy.
        mgr = _self_update_manager(how, self.env.has)
        if mgr == "uv":
            cmd = f"uv tool install --force --refresh {src}" if from_git else f"uv tool upgrade {src}"
        elif mgr == "pipx":
            cmd = f"pipx install --force {src}" if from_git else f"pipx upgrade {src}"
        else:
            self.notify("need uv or pipx to self-update", severity="warning")
            return
        self.push_screen(RunModal(
            f"{icons.REFRESH} update tuistore", cmd,
            subtitle="restart tuistore afterwards to use the new version",
            verb="update"))

    def action_update_all(self) -> None:
        parts = []
        for rec in self.ledger.values():
            uc = inst.update_command(rec)
            if uc:
                parts.append(f'echo "== {rec.get("name","?")} =="; {uc}; echo')
        if not parts:
            self.notify("nothing installed via tuistore to update", severity="warning")
            return
        self.push_screen(RunModal(
            f"{icons.PLUG} update tuistore-installed", "\n".join(parts),
            subtitle=f"{len(parts)} tool(s)", verb="update all",
            on_success=self._reload_installed))

    def action_update_everything(self) -> None:
        # sudo-requiring managers are skipped in-app (no TTY for the password);
        # `tuistore upgrade` from the shell runs those too.
        cmd = inst.system_upgrade_command(self.env, allow_sudo=False)
        mgrs = inst.upgrade_managers(self.env, allow_sudo=False)
        if not cmd:
            self.notify("no package managers found to upgrade", severity="warning")
            return
        self.push_screen(RunModal(
            f"{icons.REFRESH} update everything", cmd,
            subtitle=f"upgrades all packages via {', '.join(mgrs)} — "
                     f"including ones installed outside tuistore",
            verb="update all", on_success=self._reload_installed))

    @work(exclusive=True, group="catalog")
    async def refetch_catalog(self) -> None:
        import asyncio
        self.notify("fetching the latest catalog…")
        ok, msg = await asyncio.to_thread(refetch)
        if not ok:
            self.notify(f"catalog update failed: {msg}", severity="error")
            return
        self.catalog = load()
        self._by_slug = {e.slug: e for e in self.catalog.entries}
        self._build_sidebar()
        self.render_results(preserve=True)
        self.notify(f"catalog updated — {msg}")

    @work(group="star")
    async def action_toggle_star(self) -> None:
        if not self.current or not self.current.is_github:
            self.notify("nothing to star", severity="warning")
            return
        if not github.available():
            self.notify("install & auth the gh CLI to star (gh auth login)", severity="warning")
            return
        entry = self.current
        owner, repo = entry.repo
        currently = self._starred.get(entry.slug)
        if currently:
            ok = await github.unstar(owner, repo)
            if ok:
                self._starred[entry.slug] = False
                if entry.stars:
                    entry.stars = max(0, entry.stars - 1)
                self.notify(f"unstarred {entry.name}")
        else:
            ok = await github.star(owner, repo)
            if ok:
                self._starred[entry.slug] = True
                entry.stars = (entry.stars or 0) + 1
                self.notify(f"★ starred {entry.name}")
        if not ok:
            self.notify("couldn't reach GitHub via gh", severity="error")
        if self.current is entry:
            self.render_detail(entry)
            self.render_results(preserve=True)

    def action_open_browser(self) -> None:
        if not self.current:
            return
        url = self.current.homepage or self.current.url
        webbrowser.open(url)
        self.notify(f"opened {url}")

    def action_features(self) -> None:
        self.push_screen(FeaturesModal())

    def action_help(self) -> None:
        self.push_screen(HelpModal(HELP_SECTIONS, title="tuistore — keys"))


HELP_SECTIONS = [
    ("search & browse", [
        ("/", "focus the search box"),
        ("type", "fuzzy-filter by name, description, language"),
        ("j / k  ↓↑", "move through the list"),
        ("g / G", "jump to top / bottom"),
        ("enter", "open a tool's detail / focus it"),
        ("esc", "step back a pane · clear search"),
    ]),
    ("a tool", [
        ("i", "install (verified vs unverified; pick method with a)"),
        ("r", "read the README in-app (inspect before installing)"),
        ("u", "update it (if installed via tuistore)"),
        ("x", "uninstall it (if installed via tuistore)"),
        ("s", "star / unstar on GitHub"),
        ("o", "open repo / homepage in browser"),
    ]),
    ("app", [
        (",", "manage — update tuistore · refetch catalog · update all"),
        ("◆ Installed", "sidebar filter: everything you have installed"),
        ("f", "features / about"),
        ("t", "cycle theme"),
        ("?", "this help"),
        ("q", "quit"),
    ]),
]


def main() -> None:
    StoreApp().run()


if __name__ == "__main__":
    main()
