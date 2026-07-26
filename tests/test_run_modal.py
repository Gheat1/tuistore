import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from ricekit import KitApp

from tuistore.app import RunModal
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
        self.env = Env("linux", "arch", {"arch"}, tools={"pacman"})


class RunModalTerminalHandoffTest(unittest.IsolatedAsyncioTestCase):
    """RunModal drives uninstall/update/update-all/self-update -- the same
    piped-stdout-with-no-stdin problem that hits InstallModal
    (github.com/Gheat1/tuistore/issues/31) hits these too, e.g. `sudo
    pacman -R {pkg}` for an uninstall or `yay -S {pkg}` for an update. A
    command flagged by `is_interactive_command` must hand the real terminal
    over via `App.suspend()` with fully inherited stdio instead of going
    through the normal streamed `run_stream` path, and vice versa."""

    async def test_sudo_uninstall_suspends_and_runs_with_inherited_stdio(self) -> None:
        modal = RunModal("uninstall haal", "sudo pacman -R haal", danger=True, verb="uninstall")
        app = Harness()
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
        _, kwargs = run_mock.call_args
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("stderr", kwargs)
        self.assertNotIn("stdin", kwargs)
        argv = run_mock.call_args.args[0]
        self.assertIn("sudo pacman -R haal", argv[-1])
        self.assertFalse(modal.running)
        self.assertTrue(modal.done)

    async def test_multi_tool_update_all_script_with_later_yay_entry_suspends(self) -> None:
        # shape of action_update_all's joined script: several
        # `echo "== name =="; cmd; echo` lines, none of which need sudo, but
        # one of which is a yay update -- must still trigger the handoff
        # even though yay isn't the first statement.
        script = (
            'echo "== cargo-tool ==" ; cargo install cargo-tool --force ; echo\n'
            'echo "== aur-tool ==" ; yay -S aur-tool ; echo'
        )
        modal = RunModal("update tuistore-installed", script, verb="update all")
        app = Harness()
        app.notify = Mock()
        fake_proc = Mock(returncode=0)

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

    async def test_non_interactive_update_streams_and_never_suspends(self) -> None:
        modal = RunModal("update haal", "cargo install haal --force", verb="update")
        app = Harness()
        app.notify = Mock()
        on_success = Mock()
        modal.on_success = on_success

        async def fake_stream(command):
            yield ("out", "updating...")
            yield ("exit", "0")

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

                logtext = modal.query_one("#rlogtext")
                self.assertIn("updating...", logtext.content.plain)

        suspend_mock.assert_not_called()
        run_mock.assert_not_called()
        stream_mock.assert_called_once_with("cargo install haal --force")
        self.assertFalse(modal.running)
        self.assertTrue(modal.done)
        on_success.assert_called_once()


if __name__ == "__main__":
    unittest.main()
