import unittest
from unittest.mock import patch

from textual.widgets import Footer

import tuistore.app as app_module
from tuistore.app import StoreApp


class FooterTest(unittest.IsolatedAsyncioTestCase):
    async def test_footer_fits_small_screen(self) -> None:
        with patch.object(app_module.DIRS, "load_state", return_value={"welcomed": True}), \
             patch.object(app_module.DIRS, "save_state"), \
             patch.object(StoreApp, "scan_managers"):
            app = StoreApp()
            async with app.run_test(size=(80, 24)) as pilot:
                app.query_one("#results").focus()
                await pilot.pause()

                self.assertEqual(app.query_one(Footer).max_scroll_x, 0)
