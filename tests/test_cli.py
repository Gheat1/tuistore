import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
