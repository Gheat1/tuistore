import unittest
from unittest.mock import Mock

from ricekit import KitApp
from textual.widgets import OptionList

from tuistore.app import InstallModal
from tuistore.catalog import Entry
from tuistore.installer import make
from tuistore.platform import Env


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


if __name__ == "__main__":
    unittest.main()
