import unittest
from unittest.mock import patch

import tuistore.__main__ as main_module
from tuistore.__main__ import _cmd_remove


class ConfirmationTest(unittest.TestCase):
    def test_redirected_stdin_does_not_remove_without_yes(self) -> None:
        ledger = {"tool": {"name": "Tool", "kind": "cargo", "pkg": "tool"}}
        with (
            patch("tuistore.__main__.sys.stdin.isatty", return_value=False),
            patch("tuistore.__main__._resolve", return_value=None),
            patch("tuistore.installed.load_ledger", return_value=ledger),
            patch("tuistore.__main__._run") as run,
        ):
            _cmd_remove(["tool"])

        run.assert_not_called()


class UpdateDispatchTest(unittest.TestCase):
    """`tuistore update`/`upgrade` must find the tool name even when a flag
    like `-y` precedes it, instead of silently falling back to self-update
    or a full system upgrade."""

    def _run(self, argv: list[str]):
        with (
            patch("tuistore.__main__.sys.argv", argv),
            patch("tuistore.__main__._update_self", return_value=0) as self_,
            patch("tuistore.__main__._update_named", return_value=0) as named,
            patch("tuistore.__main__._system_upgrade", return_value=0) as system,
            patch("tuistore.__main__._update_installed", return_value=0) as installed,
        ):
            with self.assertRaises(SystemExit):
                main_module.main()
            return self_, named, system, installed

    def test_update_with_leading_flag_resolves_named_tool(self) -> None:
        self_, named, system, installed = self._run(["tuistore", "update", "-y", "ripgrep"])
        named.assert_called_once_with("ripgrep")
        self_.assert_not_called()
        system.assert_not_called()
        installed.assert_not_called()

    def test_upgrade_with_leading_flag_resolves_named_tool(self) -> None:
        self_, named, system, installed = self._run(["tuistore", "upgrade", "-y", "ripgrep"])
        named.assert_called_once_with("ripgrep")
        self_.assert_not_called()
        system.assert_not_called()
        installed.assert_not_called()

    def test_bare_update_still_self_updates(self) -> None:
        self_, named, system, installed = self._run(["tuistore", "update"])
        self_.assert_called_once()
        named.assert_not_called()
        system.assert_not_called()
        installed.assert_not_called()

    def test_bare_upgrade_still_updates_everything(self) -> None:
        self_, named, system, installed = self._run(["tuistore", "upgrade"])
        system.assert_called_once()
        named.assert_not_called()
        self_.assert_not_called()
        installed.assert_not_called()

    def test_special_token_after_flag_still_resolves(self) -> None:
        self_, named, system, installed = self._run(["tuistore", "update", "-y", "installed"])
        installed.assert_called_once()
        named.assert_not_called()

    def test_update_without_flags_still_resolves_named_tool(self) -> None:
        self_, named, system, installed = self._run(["tuistore", "update", "ripgrep"])
        named.assert_called_once_with("ripgrep")


if __name__ == "__main__":
    unittest.main()
