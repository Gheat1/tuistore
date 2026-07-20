import unittest
from unittest.mock import patch

from tuistore.__main__ import _how_installed, _is_homebrew_cellar_path


class IsHomebrewCellarPathTest(unittest.TestCase):
    """Unit tests on the pure path-matching helper (no filesystem/symlink
    resolution involved, so these are stable across platforms)."""

    def test_cellar_substring_outside_homebrew_prefix_is_rejected(self) -> None:
        # "cellar" appears in the path, but it's a user/project directory
        # (e.g. a backups folder), not a real Homebrew Cellar install.
        path = "/users/gheat/mycellar-backups/tuistore/bin/tuistore"
        self.assertFalse(_is_homebrew_cellar_path(path))

    def test_cellar_as_substring_of_unrelated_word_is_rejected(self) -> None:
        path = "/home/gheat/projects/cellardoor/bin/tuistore"
        self.assertFalse(_is_homebrew_cellar_path(path))

    def test_real_cellar_path_apple_silicon_is_accepted(self) -> None:
        path = "/opt/homebrew/cellar/tuistore/1.2.3/bin/tuistore"
        self.assertTrue(_is_homebrew_cellar_path(path))

    def test_real_cellar_path_intel_is_accepted(self) -> None:
        path = "/usr/local/cellar/tuistore/1.2.3/bin/tuistore"
        self.assertTrue(_is_homebrew_cellar_path(path))

    def test_real_cellar_path_linuxbrew_is_accepted(self) -> None:
        path = "/home/linuxbrew/.linuxbrew/cellar/tuistore/1.2.3/bin/tuistore"
        self.assertTrue(_is_homebrew_cellar_path(path))


class HowInstalledTest(unittest.TestCase):
    """End-to-end tests through `_how_installed`, which resolves the
    `which("tuistore")` path before classifying it."""

    def _how_installed_for(self, which_path: str) -> str:
        # `_how_installed` runs the `which()` result through `Path.resolve()`,
        # which normalizes separators/anchoring per host OS (e.g. it would
        # prepend a drive letter on Windows). Stub it to a no-op so these
        # fixed POSIX-style test paths behave the same on every platform
        # this suite runs on (this repo's CI includes windows-latest).
        with (
            patch("tuistore.__main__.shutil.which", return_value=which_path),
            patch("pathlib.Path.resolve", lambda self: self),
        ):
            return _how_installed()

    def test_cellar_substring_outside_homebrew_prefix_is_not_brew(self) -> None:
        path = "/Users/gheat/mycellar-backups/tuistore/bin/tuistore"
        self.assertNotEqual(self._how_installed_for(path), "brew")

    def test_real_homebrew_cellar_path_apple_silicon_is_brew(self) -> None:
        path = "/opt/homebrew/Cellar/tuistore/1.2.3/bin/tuistore"
        self.assertEqual(self._how_installed_for(path), "brew")

    def test_real_homebrew_cellar_path_intel_is_brew(self) -> None:
        path = "/usr/local/Cellar/tuistore/1.2.3/bin/tuistore"
        self.assertEqual(self._how_installed_for(path), "brew")

    def test_real_homebrew_cellar_path_linuxbrew_is_brew(self) -> None:
        path = "/home/linuxbrew/.linuxbrew/Cellar/tuistore/1.2.3/bin/tuistore"
        self.assertEqual(self._how_installed_for(path), "brew")

    def test_uv_tool_path_is_uv(self) -> None:
        path = "/Users/gheat/.local/share/uv/tools/tuistore/bin/tuistore"
        self.assertEqual(self._how_installed_for(path), "uv")

    def test_pipx_path_is_pipx(self) -> None:
        path = "/Users/gheat/.local/pipx/venvs/tuistore/bin/tuistore"
        self.assertEqual(self._how_installed_for(path), "pipx")

    def test_unknown_path_is_unknown(self) -> None:
        path = "/usr/bin/tuistore"
        self.assertEqual(self._how_installed_for(path), "unknown")


if __name__ == "__main__":
    unittest.main()
