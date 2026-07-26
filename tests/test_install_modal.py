import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from ricekit import KitApp
from textual.widgets import OptionList

from tuistore.app import InstallModal
from tuistore.catalog import Entry
from tuistore.installer import make
from tuistore.platform import Env


@contextmanager
def _noop_suspend():
    """Stand-in for `App.suspend()` -- real suspension takes over the actual
    terminal, which Textual's headless `run_test()` harness can't exercise
    in CI, so tests patch it out with a context manager that does nothing."""
    yield


class Harness(KitApp):
    def __init__(self) -> None:
        super().__init__()
        self.env = Env("linux", "arch", {"arch"}, tools={"cargo"})


class InstallModalAvailabilityTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.entry = Entry("haal", "https://github.com/example/haal")
        self.cargo = make("cargo", "cargo install haal")
        self.binstall = make("cargo-binstall", "cargo binstall haal")

    async def test_unavailable_picker_option_is_disabled(self) -> None:
        modal = InstallModal(self.entry, self.cargo, [self.binstall])
        app = Harness()

        async with app.run_test() as pilot:
            app.push_screen(modal)
            await pilot.pause()
            modal.action_alternatives()
            await pilot.pause()

            options = app.screen.query_one(OptionList)
            self.assertFalse(options.get_option_at_index(0).disabled)
            self.assertTrue(options.get_option_at_index(1).disabled)

    async def test_enter_does_not_run_unavailable_method(self) -> None:
        modal = InstallModal(self.entry, self.binstall, [])
        app = Harness()
        app.notify = Mock()

        async with app.run_test() as pilot:
            app.push_screen(modal)
            await pilot.pause()
            modal._install = Mock()

            modal.action_run()

            self.assertFalse(modal.running)
            modal._install.assert_not_called()
            app.notify.assert_called_once_with("needs cargo-binstall", severity="warning")


class InstallModalTerminalHandoffTest(unittest.IsolatedAsyncioTestCase):
    """github.com/Gheat1/tuistore/issues/31: `run_stream`'s piped stdout
    never wires up the child's stdin, so a command that prompts on the
    terminal (a sudo password, an AUR helper confirmation) can't receive
    it. A command flagged by `is_interactive_command` must instead hand the
    real terminal over via `App.suspend()` and run with fully inherited
    stdio -- no captured pipes at all -- while a plain, non-interactive
    command must keep using the normal streamed `run_stream` path and must
    never suspend the app."""

    def setUp(self) -> None:
        self.entry = Entry("haal", "https://github.com/example/haal")

    async def test_sudo_command_suspends_and_runs_with_inherited_stdio(self) -> None:
        method = make("pacman", "sudo pacman -S haal")
        modal = InstallModal(self.entry, method, [])
        app = Harness()
        app.env = Env("linux", "arch", {"arch"}, tools={"pacman"})
        app.notify = Mock()
        # exit non-zero so `_install` never reaches `self.app.on_installed`,
        # which this bare test harness (unlike the real StoreApp) doesn't
        # implement -- irrelevant to what this test is checking.
        fake_proc = Mock(returncode=1)

        with patch.object(app, "suspend", side_effect=_noop_suspend) as suspend_mock, \
             patch("tuistore.app.subprocess.run", return_value=fake_proc) as run_mock, \
             patch("tuistore.app.run_stream") as stream_mock:
            async with app.run_test() as pilot:
                app.push_screen(modal)
                await pilot.pause()
                modal.action_run()
                await pilot.pause()
                await pilot.pause()
                await pilot.pause()

        suspend_mock.assert_called_once()
        run_mock.assert_called_once()
        stream_mock.assert_not_called()
        # fully inherited stdio -- no captured pipes of any kind
        _, kwargs = run_mock.call_args
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("stderr", kwargs)
        self.assertNotIn("stdin", kwargs)
        argv = run_mock.call_args.args[0]
        self.assertIn("sudo pacman -S haal", argv[-1])
        self.assertFalse(modal.running)
        self.assertTrue(modal.done)

    async def test_yay_command_suspends_even_without_sudo(self) -> None:
        method = make("yay", "yay -S haal")
        modal = InstallModal(self.entry, method, [])
        app = Harness()
        app.env = Env("linux", "arch", {"arch"}, tools={"yay"})
        app.notify = Mock()
        fake_proc = Mock(returncode=1)

        with patch.object(app, "suspend", side_effect=_noop_suspend) as suspend_mock, \
             patch("tuistore.app.subprocess.run", return_value=fake_proc) as run_mock, \
             patch("tuistore.app.run_stream") as stream_mock:
            async with app.run_test() as pilot:
                app.push_screen(modal)
                await pilot.pause()
                modal.action_run()
                await pilot.pause()
                await pilot.pause()
                await pilot.pause()

        suspend_mock.assert_called_once()
        run_mock.assert_called_once()
        stream_mock.assert_not_called()

    async def test_non_interactive_command_streams_and_never_suspends(self) -> None:
        method = make("cargo", "cargo install haal")
        modal = InstallModal(self.entry, method, [])
        app = Harness()
        app.env = Env("linux", "arch", {"arch"}, tools={"cargo"})
        app.notify = Mock()

        async def fake_stream(command):
            yield ("out", "compiling...")
            yield ("exit", "1")

        with patch.object(app, "suspend", side_effect=_noop_suspend) as suspend_mock, \
             patch("tuistore.app.subprocess.run") as run_mock, \
             patch("tuistore.app.run_stream", side_effect=fake_stream) as stream_mock:
            async with app.run_test() as pilot:
                app.push_screen(modal)
                await pilot.pause()
                modal.action_run()
                await pilot.pause()
                await pilot.pause()
                await pilot.pause()

                logtext = modal.query_one("#logtext")
                self.assertIn("compiling...", logtext.content.plain)

        suspend_mock.assert_not_called()
        run_mock.assert_not_called()
        stream_mock.assert_called_once_with("cargo install haal")
        self.assertFalse(modal.running)
        self.assertTrue(modal.done)


if __name__ == "__main__":
    unittest.main()
