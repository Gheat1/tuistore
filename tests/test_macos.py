"""macOS/Homebrew-specific behavior — mirrors tests/test_windows.py.

None of this logic depends on actually running on a Mac (it's all mocked),
but it exercises the code paths that only matter on macOS: Homebrew
detection/parsing and the "installed via brew, update with brew instead"
self-update branch. Historically these paths ran on Linux/Windows CI only,
so a real macOS-only regression here could ship without any red build.
"""

import unittest
from unittest import mock

from tuistore import installed, platform


class TestPlatformDetectsMacOS(unittest.TestCase):
    def tearDown(self) -> None:
        platform.detect.cache_clear()

    @mock.patch("tuistore.platform.shutil.which", return_value=None)
    @mock.patch("tuistore.platform._platform.machine", return_value="arm64")
    @mock.patch("tuistore.platform._platform.system", return_value="Darwin")
    def test_darwin_system_maps_to_macos(self, _system, _machine, _which):
        platform.detect.cache_clear()
        env = platform.detect()
        self.assertEqual(env.os, "macos")
        self.assertEqual(env.arch, "arm64")
        self.assertEqual(env.label, "macOS (arm64)")

    @mock.patch("tuistore.platform.shutil.which", return_value=None)
    @mock.patch("tuistore.platform._platform.machine", return_value="x86_64")
    @mock.patch("tuistore.platform._platform.system", return_value="Darwin")
    def test_intel_mac_arch_is_normalized(self, _system, _machine, _which):
        platform.detect.cache_clear()
        env = platform.detect()
        self.assertEqual(env.os, "macos")
        self.assertEqual(env.arch, "x86_64")

    @mock.patch("tuistore.platform.shutil.which", return_value=None)
    @mock.patch("tuistore.platform._platform.machine", return_value="arm64")
    @mock.patch("tuistore.platform._platform.system", return_value="Darwin")
    def test_macos_has_no_distro_or_families(self, _system, _machine, _which):
        platform.detect.cache_clear()
        env = platform.detect()
        self.assertEqual(env.distro, "")
        self.assertEqual(env.families, set())


class TestHowInstalledDetectsHomebrew(unittest.TestCase):
    @mock.patch("tuistore.__main__.shutil.which",
                return_value="/opt/homebrew/Cellar/tuistore/1.2.3/bin/tuistore")
    def test_apple_silicon_cellar_path_is_brew(self, _which):
        from tuistore.__main__ import _how_installed
        self.assertEqual(_how_installed(), "brew")

    @mock.patch("tuistore.__main__.shutil.which",
                return_value="/usr/local/Cellar/tuistore/1.2.3/bin/tuistore")
    def test_intel_mac_cellar_path_is_brew(self, _which):
        from tuistore.__main__ import _how_installed
        self.assertEqual(_how_installed(), "brew")

    @mock.patch("tuistore.__main__.shutil.which",
                return_value="/home/linuxbrew/.linuxbrew/bin/tuistore")
    def test_linuxbrew_path_is_brew(self, _which):
        from tuistore.__main__ import _how_installed
        self.assertEqual(_how_installed(), "brew")

    @mock.patch("tuistore.__main__.shutil.which",
                return_value="/Users/me/.local/bin/tuistore")
    def test_non_brew_path_is_not_brew(self, _which):
        from tuistore.__main__ import _how_installed
        self.assertNotEqual(_how_installed(), "brew")


class TestUpdateSelfPrefersBrew(unittest.TestCase):
    @mock.patch("tuistore.__main__._run", return_value=0)
    @mock.patch("tuistore.__main__._how_installed", return_value="brew")
    def test_brew_installed_copy_updates_via_brew_upgrade(self, _how, run):
        from tuistore.__main__ import _update_self
        rc = _update_self()
        run.assert_called_once_with("brew upgrade gheat1/tuistore/tuistore")
        self.assertEqual(rc, 0)


class TestBrewInstalledScanner(unittest.TestCase):
    @mock.patch("tuistore.installed._run")
    def test_parses_and_merges_formula_and_cask_listings(self, run):
        run.side_effect = ["ripgrep\nlazygit\n", "iterm2\n"]
        names = installed._brew_installed()
        self.assertEqual(names, {"ripgrep", "lazygit", "iterm2"})

    @mock.patch("tuistore.installed._run")
    def test_lowercases_and_skips_blank_lines(self, run):
        run.side_effect = ["Ripgrep\n\n", "\n"]
        names = installed._brew_installed()
        self.assertEqual(names, {"ripgrep"})


if __name__ == "__main__":
    unittest.main()
