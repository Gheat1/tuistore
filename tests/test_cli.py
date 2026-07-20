import unittest
from unittest.mock import patch

import tuistore.__main__ as main_module
from tuistore.__main__ import _cmd_remove, _self_update_manager, _update_self


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


class SelfUpdateManagerTest(unittest.TestCase):
    """_self_update_manager must match the manager _how_installed() says
    actually owns the running copy, not just whichever is available — a
    pipx-managed copy has uv available too (and vice versa), but picking
    the wrong one creates a second, parallel copy instead of updating the
    real one."""

    def test_pipx_owned_copy_prefers_pipx_even_with_uv_available(self) -> None:
        has = lambda tool: tool in ("uv", "pipx")
        self.assertEqual(_self_update_manager("pipx", has), "pipx")

    def test_uv_owned_copy_prefers_uv_even_with_pipx_available(self) -> None:
        has = lambda tool: tool in ("uv", "pipx")
        self.assertEqual(_self_update_manager("uv", has), "uv")

    def test_owned_manager_missing_from_path_yields_none(self) -> None:
        # _how_installed() says pipx, but pipx itself isn't on PATH (e.g. a
        # stale/broken env) — don't silently fall back to uv and create a
        # second copy; report "can't update" instead.
        has = lambda tool: tool == "uv"
        self.assertIsNone(_self_update_manager("pipx", has))

    def test_unknown_falls_back_to_whatever_is_available(self) -> None:
        has = lambda tool: tool == "pipx"
        self.assertEqual(_self_update_manager("unknown", has), "pipx")

    def test_unknown_prefers_uv_when_both_available(self) -> None:
        has = lambda tool: tool in ("uv", "pipx")
        self.assertEqual(_self_update_manager("unknown", has), "uv")

    def test_unknown_with_neither_available_yields_none(self) -> None:
        self.assertIsNone(_self_update_manager("unknown", lambda tool: False))


class UpdateSelfTest(unittest.TestCase):
    """End-to-end: _update_self() must run the command for the manager that
    actually installed this copy, even when a different manager is also on
    PATH."""

    def test_pipx_install_runs_pipx_even_when_uv_on_path(self) -> None:
        with (
            patch("tuistore.__main__._how_installed", return_value="pipx"),
            patch("tuistore.__main__._install_source", return_value="pypi"),
            patch("tuistore.__main__.shutil.which", side_effect=lambda t: f"/usr/bin/{t}" if t in ("uv", "pipx") else None),
            patch("tuistore.__main__._run", return_value=0) as run,
        ):
            _update_self()

        run.assert_called_once_with("pipx upgrade tuistore")

    def test_uv_install_runs_uv_even_when_pipx_on_path(self) -> None:
        with (
            patch("tuistore.__main__._how_installed", return_value="uv"),
            patch("tuistore.__main__._install_source", return_value="pypi"),
            patch("tuistore.__main__.shutil.which", side_effect=lambda t: f"/usr/bin/{t}" if t in ("uv", "pipx") else None),
            patch("tuistore.__main__._run", return_value=0) as run,
        ):
            _update_self()

        run.assert_called_once_with("uv tool upgrade tuistore")


if __name__ == "__main__":
    unittest.main()
