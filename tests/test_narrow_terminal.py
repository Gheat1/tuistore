import unittest
from unittest.mock import patch

import tuistore.app as app_module
from tuistore.app import StoreApp


class NarrowTerminalTest(unittest.IsolatedAsyncioTestCase):
    """Below ~80 columns, #results (width: 1fr) used to get squeezed to a
    zero-or-negative content width, which crashed deep in Textual/Rich's
    text-wrapping code (ValueError: range() arg 3 must not be zero) as soon
    as the option list tried to measure its own auto height. #results now
    has a min-width floor so it can never be handed a non-positive width."""

    async def test_narrow_width_does_not_crash(self) -> None:
        with patch.object(app_module.DIRS, "load_state", return_value={"welcomed": True}), \
             patch.object(app_module.DIRS, "save_state"), \
             patch.object(StoreApp, "scan_managers"):
            app = StoreApp()
            async with app.run_test(size=(60, 24)) as pilot:
                app.query_one("#results").focus()
                await pilot.pause()

                results = app.query_one("#results")
                self.assertGreater(results.option_count, 0)
                self.assertGreater(results.size.width, 0)

    async def test_very_narrow_widths_do_not_crash(self) -> None:
        for width in (50, 60, 70):
            with patch.object(app_module.DIRS, "load_state", return_value={"welcomed": True}), \
                 patch.object(app_module.DIRS, "save_state"), \
                 patch.object(StoreApp, "scan_managers"):
                app = StoreApp()
                async with app.run_test(size=(width, 24)) as pilot:
                    app.query_one("#results").focus()
                    await pilot.pause()

                    results = app.query_one("#results")
                    self.assertGreater(results.option_count, 0, f"width={width}")
                    self.assertGreater(results.size.width, 0, f"width={width}")


if __name__ == "__main__":
    unittest.main()
