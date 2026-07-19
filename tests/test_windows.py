import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tuistore import installed, paths, shell
from tuistore.installer import make
from tuistore.platform import Env


class TestPaths(unittest.TestCase):
    @mock.patch("tuistore.paths.os.name", "nt")
    @mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"}, clear=True)
    def test_user_data_dir_uses_localappdata(self):
        self.assertEqual(paths.user_data_dir(), Path(r"C:\Users\Test\AppData\Local\tuistore"))

    @mock.patch("tuistore.paths.os.name", "nt")
    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch("pathlib.Path.home", return_value=Path(r"C:\Users\Test"))
    def test_user_data_dir_falls_back_to_home_appdata(self, _home):
        self.assertEqual(paths.user_data_dir(), Path(r"C:\Users\Test\AppData\Local\tuistore"))

    @mock.patch("tuistore.paths.os.name", "nt")
    @mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"}, clear=True)
    def test_user_cache_dir_uses_localappdata(self):
        self.assertEqual(paths.user_cache_dir(), Path(r"C:\Users\Test\AppData\Local\tuistore\cache"))

    @mock.patch("tuistore.paths.os.name", "posix")
    @mock.patch("pathlib.Path.home", return_value=Path("/home/test"))
    def test_user_data_dir_uses_xdg_on_unix(self, _home):
        self.assertTrue(str(paths.user_data_dir()).replace("\\", "/").endswith("/home/test/.local/state/tuistore"))

    @mock.patch("tuistore.paths.os.name", "posix")
    @mock.patch("pathlib.Path.home", return_value=Path("/home/test"))
    def test_user_cache_dir_uses_xdg_cache_on_unix(self, _home):
        self.assertTrue(str(paths.user_cache_dir()).replace("\\", "/").endswith("/home/test/.cache/tuistore"))

    def test_store_dirs_persists_utf8_state_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch("tuistore.paths.user_data_dir", return_value=root / "state"), \
                 mock.patch("tuistore.paths.user_cache_dir", return_value=root / "cache"):
                dirs = paths.StoreDirs()
                dirs.save_state({"theme": "mocha ✨"})
                dirs.write_cache("readme", {"text": "🛍️ café"})
                self.assertEqual(dirs.load_state()["theme"], "mocha ✨")
                self.assertEqual(dirs.read_cache("readme"), {"text": "🛍️ café"})


class TestShellCommand(unittest.TestCase):
    @mock.patch("tuistore.shell.sys.platform", "win32")
    @mock.patch("tuistore.shell.shutil.which", side_effect=lambda name: r"C:\pwsh\pwsh.exe" if name == "pwsh" else None)
    def test_prefers_pwsh_on_windows(self, _which):
        exe, args = shell.shell_command()
        self.assertEqual(exe, r"C:\pwsh\pwsh.exe")
        self.assertEqual(args, ["-NoProfile", "-Command"])

    @mock.patch("tuistore.shell.sys.platform", "win32")
    @mock.patch("tuistore.shell.shutil.which", side_effect=lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" if name == "powershell" else None)
    def test_falls_back_to_windows_powershell(self, _which):
        exe, args = shell.shell_command()
        self.assertTrue(exe.endswith("powershell.exe"))
        self.assertEqual(args, ["-NoProfile", "-Command"])

    @mock.patch("tuistore.shell.sys.platform", "win32")
    @mock.patch("tuistore.shell.shutil.which", return_value=None)
    def test_falls_back_to_cmd_when_no_powershell(self, _which):
        exe, args = shell.shell_command()
        self.assertEqual(exe, "cmd.exe")
        self.assertEqual(args, ["/c"])

    @mock.patch("tuistore.shell.sys.platform", "linux")
    @mock.patch("tuistore.shell.shutil.which", side_effect=lambda name: "/bin/bash" if name == "bash" else None)
    def test_uses_bash_on_linux(self, _which):
        exe, args = shell.shell_command()
        self.assertEqual(exe, "/bin/bash")
        self.assertEqual(args, ["-lc"])

    @mock.patch("tuistore.shell.sys.platform", "darwin")
    @mock.patch("tuistore.shell.shutil.which", side_effect=lambda name: "/bin/zsh" if name == "zsh" else None)
    def test_prefers_bash_then_zsh_on_macos(self, _which):
        # bash is checked first; since it is missing, zsh should be selected
        exe, args = shell.shell_command()
        self.assertEqual(exe, "/bin/zsh")
        self.assertEqual(args, ["-lc"])

    @mock.patch("tuistore.shell.sys.platform", "darwin")
    @mock.patch("tuistore.shell.shutil.which", return_value=None)
    def test_falls_back_to_sh_on_macos(self, _which):
        exe, args = shell.shell_command()
        self.assertEqual(exe, "/bin/sh")
        self.assertEqual(args, ["-lc"])


class TestWindowsInstallEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.env = Env("windows", "", set(), tools={"winget", "scoop", "choco"})

    def test_windows_managers_are_available_only_on_windows(self):
        for kind, command in (
            ("winget", "winget install --id BurntSushi.ripgrep --exact"),
            ("scoop", "scoop install ripgrep"),
            ("choco", "choco install ripgrep"),
        ):
            method = make(kind, command)
            self.assertTrue(method.available(self.env))
            self.assertFalse(method.available(Env("linux", "ubuntu", {"debian"}, tools={kind})))

    def test_posix_scripts_are_not_offered_on_windows(self):
        method = make("script", "curl -fsSL https://example.test/install | bash")
        self.assertFalse(method.available(self.env))
        self.assertEqual(method.why_unavailable(self.env), "macos/linux only")

    def test_powershell_scripts_are_windows_only(self):
        method = make("script", "iwr https://example.test/install.ps1 | iex")
        self.assertTrue(method.available(self.env))
        self.assertFalse(method.available(Env("linux", "ubuntu", {"debian"}, tools={"curl"})))

    @mock.patch("tuistore.installed._run")
    def test_windows_manager_scanners_parse_package_names(self, run):
        run.side_effect = [
            "Installed apps:\nName Version Source\n---- ------- ------\nripgrep 14.1 main\n",
            "Name                         Id                    Version\n"
            "-----------------------------------------------------------\n"
            "ripgrep                      BurntSushi.ripgrep    14.1\n",
        ]
        self.assertEqual(installed._scoop_installed(), {"ripgrep"})
        self.assertEqual(installed._winget_installed(), {"ripgrep", "burntsushi.ripgrep"})

    @mock.patch("tuistore.installed.os.scandir")
    @mock.patch("tuistore.installed.os.name", "nt")
    @mock.patch.dict(os.environ, {"PATH": r"C:\\bin"}, clear=True)
    def test_path_binaries_normalizes_windows_case_and_extensions(self, scan):
        entry = mock.Mock()
        entry.name = "RG.EXE"
        scan.return_value = [entry]
        installed.path_binaries.cache_clear()
        try:
            self.assertIn("rg.exe", installed.path_binaries())
            self.assertIn("rg", installed.path_binaries())
        finally:
            installed.path_binaries.cache_clear()
