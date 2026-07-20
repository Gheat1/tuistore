import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

from tuistore import installed, paths, shell
from tuistore.installer import make
from tuistore.platform import Env


class TestPaths(unittest.TestCase):
    WINDOWS_LOCAL_APP_DATA = PureWindowsPath(r"C:\Users\Test\AppData\Local")

    @mock.patch("tuistore.paths._is_windows", return_value=True)
    @mock.patch("tuistore.paths._windows_local_dir", return_value=WINDOWS_LOCAL_APP_DATA)
    def test_user_data_dir_uses_localappdata(self, _local_dir, _is_windows):
        self.assertEqual(paths.user_data_dir(), self.WINDOWS_LOCAL_APP_DATA / "tuistore")

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch("tuistore.paths.Path.home", return_value=PureWindowsPath(r"C:\Users\Test"))
    def test_user_data_dir_falls_back_to_home_appdata(self, _home):
        self.assertEqual(paths._windows_local_dir(), self.WINDOWS_LOCAL_APP_DATA)

    @mock.patch("tuistore.paths._is_windows", return_value=True)
    @mock.patch("tuistore.paths._windows_local_dir", return_value=WINDOWS_LOCAL_APP_DATA)
    def test_user_cache_dir_uses_localappdata(self, _local_dir, _is_windows):
        self.assertEqual(paths.user_cache_dir(), self.WINDOWS_LOCAL_APP_DATA / "tuistore" / "cache")

    @mock.patch("tuistore.paths._is_windows", return_value=False)
    @mock.patch("pathlib.Path.home", return_value=Path("/home/test"))
    def test_user_data_dir_uses_xdg_on_unix(self, _home, _is_windows):
        self.assertEqual(paths.user_data_dir(), Path("/home/test/.local/state/tuistore"))

    @mock.patch("tuistore.paths._is_windows", return_value=False)
    @mock.patch("pathlib.Path.home", return_value=Path("/home/test"))
    def test_user_cache_dir_uses_xdg_cache_on_unix(self, _home, _is_windows):
        self.assertEqual(paths.user_cache_dir(), Path("/home/test/.cache/tuistore"))

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


class TestShellPathRestoration(unittest.TestCase):
    """`shell_command()` runs install commands through a POSIX *login*
    shell so the user's package managers are on PATH exactly as in their
    normal terminal. On Debian/Ubuntu that backfires: the stock
    ``/etc/profile`` *overwrites* PATH rather than merging into it, and the
    file most tools (nvm, etc.) actually append PATH in -- ``~/.bashrc`` --
    starts with ``case $- in *i*) ;; *) return;; esac`` and so is skipped
    entirely by a non-interactive login shell. A tool `platform.py` already
    found via `shutil.which()` moments earlier can become invisible to the
    very install command about to run.

    We can't safely swap in the real `/etc/profile` here, so this
    simulates the same shape of bug with a throwaway $HOME: a ``.profile``
    that resets PATH the way Debian's ``/etc/profile`` does and then
    chains into a ``.bashrc`` carrying the exact real-world nvm-style
    "bail if not interactive" guard, with the PATH addition living after
    that guard. `shell_env`/`wrap_command` must restore that addition
    regardless.
    """

    def setUp(self):
        if os.name == "nt":
            # shell_command() never returns bash on Windows (pwsh/powershell/
            # cmd.exe instead, where wrap_command() is a documented no-op) —
            # this class tests POSIX login-shell startup semantics that
            # simply don't apply there. A "bash" may still be on PATH via
            # Git Bash on Windows runners, but shell_command() ignores it,
            # so checking for its mere presence isn't the right gate.
            self.skipTest("POSIX-only: shell_command() doesn't use bash on Windows")
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash not available")
        self.bash = bash
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        # Mimic Debian's default ~/.profile chaining into ~/.bashrc, plus
        # an explicit PATH reset standing in for /etc/profile's overwrite
        # (the real /etc/profile isn't ours to rewrite in a test).
        (home / ".profile").write_text(
            'PATH="/usr/bin:/bin"\n'
            "export PATH\n"
            'if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n'
        )
        # The exact convention tools like nvm use: append to PATH at the
        # bottom of .bashrc, past the stock "not interactive? bail" guard.
        (home / ".bashrc").write_text(
            "case $- in\n"
            "    *i*) ;;\n"
            "      *) return;;\n"
            "esac\n"
            'export PATH="$HOME/late-bashrc-addition:$PATH"\n'
        )
        self.marker_dir = str(home / "late-bashrc-addition")
        self.base_env = {"HOME": str(home), "PATH": f"{self.marker_dir}:/usr/bin:/bin"}

    def _run(self, command, env):
        exe, args = shell.shell_command()
        return subprocess.run(
            [exe, *args, command], env=env, capture_output=True, text=True, timeout=10,
        )

    def test_plain_login_shell_loses_bashrc_only_path_addition(self):
        """Demonstrates the bug: without the fix, a PATH entry that only
        lives past ~/.bashrc's interactive guard doesn't survive a
        non-interactive login shell, even though it's already in the
        current process's own (inherited) PATH."""
        result = self._run('echo "$PATH"', dict(self.base_env))
        self.assertNotIn(self.marker_dir, result.stdout)

    def test_wrap_command_restores_bashrc_only_path_addition(self):
        """The fix: shell_env()/wrap_command() carry the current PATH
        through a side-channel var and restore it after the login shell's
        own startup files have had their say."""
        env = shell.shell_env(self.base_env)
        wrapped = shell.wrap_command('echo "$PATH"')
        result = self._run(wrapped, env)
        self.assertIn(self.marker_dir, result.stdout)

    def test_wrap_command_never_introduces_empty_path_component(self):
        """A missing/blank marker must never turn into a leading `:` --
        POSIX shells treat an empty PATH component as `.` (cwd), which
        would be a directory-planted-binary foothold."""
        env = dict(self.base_env)
        env.pop("_TUISTORE_LAUNCH_PATH", None)
        result = self._run(shell.wrap_command('echo "$PATH"'), env)
        self.assertFalse(result.stdout.startswith(":"))
        self.assertNotIn("::", result.stdout)


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

    def test_chocolatey_install_alias_is_classified(self):
        from tuistore.installer import classify
        self.assertEqual(classify("chocolatey install ripgrep"), "choco")

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
