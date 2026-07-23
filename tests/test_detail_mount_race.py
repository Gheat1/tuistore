import unittest
from unittest.mock import patch

from textual.css.query import NoMatches

import tuistore.app as app_module
from tuistore.app import StoreApp


class DetailMountRaceTest(unittest.IsolatedAsyncioTestCase):
    """on_mount's initial highlight can call render_detail() before the
    detail pane has finished mounting -- a real, if rare, race that got
    measurably more likely to trigger once KitFooter added its own extra
    call_after_refresh hop to the startup chain (surfaced as a genuine CI
    flake: NoMatches("#detailscroll") wrapped in WorkerFailed on a loaded
    Windows runner). render_detail() must retry once the tree settles
    instead of crashing the message pump."""

    async def test_render_detail_retries_instead_of_crashing_on_a_mount_race(self) -> None:
        with patch.object(app_module.DIRS, "load_state", return_value={"welcomed": True}), \
             patch.object(app_module.DIRS, "save_state"), \
             patch.object(StoreApp, "scan_managers"):
            app = StoreApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                entry = app.current
                self.assertIsNotNone(entry)

                app.query_one("#detail").border_title = ""
                real_query_one = app.query_one
                raised = {"n": 0}

                def flaky_query_one(selector, *args, **kwargs):
                    if selector == "#detail" and raised["n"] == 0:
                        raised["n"] += 1
                        raise NoMatches("simulated startup race")
                    return real_query_one(selector, *args, **kwargs)

                with patch.object(app, "query_one", side_effect=flaky_query_one):
                    app.render_detail(entry)  # must not raise
                    await pilot.pause()
                    await pilot.pause()

                self.assertEqual(raised["n"], 1)
                # the retry must actually land -- the pane ends up correctly
                # populated, not just silently skipped
                self.assertIn(entry.name, app.query_one("#detail").border_title)


if __name__ == "__main__":
    unittest.main()
