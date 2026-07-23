import unittest
from unittest.mock import patch

from ricekit.widgets import KitFooter

import tuistore.app as app_module
from tuistore.app import StoreApp


async def _settle(pilot) -> None:
    """KitFooter's overflow check chains onto its own call_after_refresh
    hop on top of the app's own mount/bindings-changed refresh — one pause
    isn't reliably enough to drain the whole chain on a loaded CI runner
    (see ricekit's own test_footer.py, which hit the same thing)."""
    for _ in range(5):
        await pilot.pause()


class FooterTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, width: int, height: int = 24):
        with patch.object(app_module.DIRS, "load_state", return_value={"welcomed": True}), \
             patch.object(app_module.DIRS, "save_state"), \
             patch.object(StoreApp, "scan_managers"):
            app = StoreApp()
            async with app.run_test(size=(width, height)) as pilot:
                app.query_one("#results").focus()
                await _settle(pilot)
                return app.query_one(KitFooter)

    async def test_footer_fits_small_screen(self) -> None:
        footer = await self._run(80)
        self.assertEqual(footer.max_scroll_x, 0)

    async def test_footer_fits_narrower_than_ever_tested_before(self) -> None:
        # PR #27 hand-picked the 7 keys shown here and verified them at
        # 80x24 only. KitFooter is a safety net on top of that curation —
        # this proves the same 7 (plus the always-present ? and q) stay
        # overflow-free well below that, not just at the one width that
        # happened to get tested.
        for width in (40, 50, 60):
            with self.subTest(width=width):
                footer = await self._run(width)
                self.assertEqual(footer.max_scroll_x, 0)
