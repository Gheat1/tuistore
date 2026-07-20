import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tuistore.installed import (
    load_ledger,
    pkg_from_command,
    record_install,
    system_upgrade_command,
    uninstall_command,
    update_command,
    upgrade_managers,
)
from tuistore.installer import Method
from tuistore.platform import Env


class TestPkgFromCommandScopedNpm(unittest.TestCase):
    """A leading "@" in an npm-style scoped package (e.g. "@openai/codex")
    must not be mistaken for a version-pin separator — only a *second* "@"
    (a trailing "@version") should be stripped."""

    def test_npm_scoped_package_keeps_scope(self):
        pkg = pkg_from_command("npm", "npm install -g @openai/codex")
        self.assertEqual(pkg, "@openai/codex")

    def test_pnpm_scoped_package_keeps_scope(self):
        pkg = pkg_from_command("pnpm", "pnpm add -g @openai/codex")
        self.assertEqual(pkg, "@openai/codex")

    def test_bun_scoped_package_keeps_scope(self):
        pkg = pkg_from_command("bun", "bun add -g @openai/codex")
        self.assertEqual(pkg, "@openai/codex")

    def test_npm_scoped_package_with_version_pin_strips_only_pin(self):
        pkg = pkg_from_command("npm", "npm install -g @openai/codex@1.2.3")
        self.assertEqual(pkg, "@openai/codex")

    def test_npm_unscoped_package_with_version_pin_still_strips(self):
        # unrelated regression guard: plain "pkg@version" pins must still work
        pkg = pkg_from_command("npm", "npm install -g ripgrep@14.1.0")
        self.assertEqual(pkg, "ripgrep")

    def test_npm_scoped_package_update_command(self):
        pkg = pkg_from_command("npm", "npm install -g @openai/codex")
        rec = {"kind": "npm", "command": "npm install -g @openai/codex", "pkg": pkg or ""}
        self.assertEqual(update_command(rec), "npm install -g @openai/codex@latest")

    def test_npm_scoped_package_uninstall_command(self):
        pkg = pkg_from_command("npm", "npm install -g @openai/codex")
        rec = {"kind": "npm", "command": "npm install -g @openai/codex", "pkg": pkg or ""}
        self.assertEqual(uninstall_command(rec), "npm uninstall -g @openai/codex")

    def test_pnpm_scoped_package_update_command(self):
        pkg = pkg_from_command("pnpm", "pnpm add -g @openai/codex")
        rec = {"kind": "pnpm", "command": "pnpm add -g @openai/codex", "pkg": pkg or ""}
        self.assertEqual(update_command(rec), "pnpm add -g @openai/codex@latest")

    def test_bun_scoped_package_uninstall_command(self):
        pkg = pkg_from_command("bun", "bun add -g @openai/codex")
        rec = {"kind": "bun", "command": "bun add -g @openai/codex", "pkg": pkg or ""}
        self.assertEqual(uninstall_command(rec), "bun remove -g @openai/codex")


class TestPkgFromCommandVcsUrls(unittest.TestCase):
    """git+https://... / git+ssh://... is the exact syntax uv/pip/pipx use
    for VCS installs, and the only install method tuistore's own FEATURED
    catalog entries (NaviTui, ricekit) ship with. `pkg_from_command` must
    never hand the raw URL back as a "package name" — a real package
    manager rejects it outright when it's later fed into an uninstall or
    upgrade command.
    """

    def test_uv_git_https_install_does_not_return_raw_url(self):
        cmd = "uv tool install git+https://github.com/Gheat1/NaviTui"
        pkg = pkg_from_command("uv", cmd)
        self.assertNotEqual(pkg, "git+https://github.com/Gheat1/NaviTui")
        # best-effort repo-name guess, matching the featured catalog's real
        # install target
        self.assertEqual(pkg, "NaviTui")

    def test_uv_git_https_uninstall_command_is_runnable(self):
        cmd = "uv tool install git+https://github.com/Gheat1/NaviTui"
        pkg = pkg_from_command("uv", cmd) or ""
        rec = {"kind": "uv", "command": cmd, "pkg": pkg}
        out = uninstall_command(rec)
        self.assertNotIn("git+", out or "")
        self.assertEqual(out, "uv tool uninstall NaviTui")

    def test_uv_git_https_update_command_is_runnable(self):
        cmd = "uv tool install git+https://github.com/Gheat1/ricekit"
        pkg = pkg_from_command("uv", cmd) or ""
        rec = {"kind": "uv", "command": cmd, "pkg": pkg}
        out = update_command(rec)
        self.assertNotIn("git+", out or "")
        self.assertEqual(out, "uv tool upgrade ricekit")

    def test_pipx_git_ssh_install_with_ref_and_dot_git_suffix(self):
        cmd = "pipx install git+ssh://git@github.com/owner/repo.git@main"
        pkg = pkg_from_command("pipx", cmd)
        self.assertNotIn("git+", pkg or "")
        self.assertNotIn("@", pkg or "")
        self.assertEqual(pkg, "repo")

    def test_pip_git_https_install(self):
        cmd = "python3 -m pip install git+https://github.com/owner/repo"
        pkg = pkg_from_command("pip", cmd)
        self.assertNotEqual(pkg, "git+https://github.com/owner/repo")
        self.assertEqual(pkg, "repo")

    def test_bare_http_url_still_yields_no_package(self):
        # unchanged pre-existing behavior: a plain (non git+) URL is not a
        # package name and we don't guess one either.
        cmd = "pip install https://example.com/pkg.whl"
        self.assertIsNone(pkg_from_command("pip", cmd))


class TestPkgFromCommandRegularPackages(unittest.TestCase):
    """Sanity checks that the VCS-URL handling didn't disturb ordinary
    (non-URL) install commands."""

    def test_uv_plain_package(self):
        self.assertEqual(pkg_from_command("uv", "uv tool install ripgrep"), "ripgrep")

    def test_npm_plain_package(self):
        self.assertEqual(pkg_from_command("npm", "npm install -g cowsay"), "cowsay")

    def test_go_install_url(self):
        cmd = "go install github.com/owner/repo/cmd/tool@latest"
        self.assertEqual(pkg_from_command("go", cmd), "tool")

    def test_brew_tap_qualified_formula(self):
        self.assertEqual(
            pkg_from_command("brew", "brew install user/tap/formula"), "formula"
        )


class TestYarnPackageParsing(unittest.TestCase):
    """yarn's only recognized install shape is `yarn global add <pkg>`
    (see installer.py's yarn regex/kind). The bare word "global" used to be
    missing from _NOISE, so it was picked up as the package name instead of
    the real target — every yarn install recorded "global" as its package."""

    def test_pkg_from_command_ignores_bare_global(self):
        self.assertEqual(
            pkg_from_command("yarn", "yarn global add ripgrep-cli"), "ripgrep-cli"
        )

    def test_pkg_from_command_ignores_bare_global_other_pkg(self):
        self.assertEqual(
            pkg_from_command("yarn", "yarn global add fkill-cli"), "fkill-cli"
        )

    def test_record_install_stores_real_package_not_global(self):
        method = Method(kind="yarn", command="yarn global add ripgrep-cli")
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "installed.json"
            with mock.patch("tuistore.installed.LEDGER", ledger_path):
                record_install("ripgrep-cli", "ripgrep-cli", method)
                rec = load_ledger()["ripgrep-cli"]
        self.assertEqual(rec["pkg"], "ripgrep-cli")
        self.assertEqual(rec["bin"], "ripgrep-cli")
        self.assertNotEqual(rec["pkg"], "global")

    def test_uninstall_command_uses_real_package_name(self):
        rec = {"kind": "yarn", "pkg": "ripgrep-cli", "bin": "ripgrep-cli"}
        self.assertEqual(uninstall_command(rec), "yarn global remove ripgrep-cli")
        self.assertNotIn("global remove global", uninstall_command(rec))

    def test_update_command_uses_real_package_name(self):
        rec = {"kind": "yarn", "pkg": "ripgrep-cli", "bin": "ripgrep-cli"}
        cmd = update_command(rec)
        self.assertIsNotNone(cmd)
        self.assertIn("ripgrep-cli", cmd)
        self.assertNotIn("global add global", cmd)


class UpdateCommandTest(unittest.TestCase):
    """update_command() must return a real command for every kind that
    installer.py actually offers as an install method — a kind with a
    working install + uninstall but no entry here silently falls through
    to None ("no update command") even though the tool is on the box."""

    def test_yarn_update_command(self) -> None:
        rec = {"kind": "yarn", "pkg": "ripgrep-cli"}
        self.assertEqual(update_command(rec), "yarn global upgrade ripgrep-cli")

    def test_bun_update_command(self) -> None:
        rec = {"kind": "bun", "pkg": "ripgrep-cli"}
        self.assertEqual(update_command(rec), "bun update -g ripgrep-cli")

    def test_bun_update_is_not_bun_runtime_upgrade(self) -> None:
        # `bun upgrade` upgrades the bun runtime itself, not a globally
        # installed package -- make sure we never emit that by accident.
        rec = {"kind": "bun", "pkg": "ripgrep-cli"}
        cmd = update_command(rec)
        self.assertNotEqual(cmd, "bun upgrade")
        self.assertNotIn("bun upgrade", cmd)

    def test_emerge_update_command(self) -> None:
        rec = {"kind": "emerge", "pkg": "ripgrep"}
        self.assertEqual(update_command(rec), "sudo emerge --update ripgrep")


class UninstallCommandTest(unittest.TestCase):
    def test_yarn_uninstall_command(self) -> None:
        rec = {"kind": "yarn", "pkg": "ripgrep-cli", "bin": "ripgrep-cli"}
        self.assertEqual(uninstall_command(rec), "yarn global remove ripgrep-cli")

    def test_bun_uninstall_command(self) -> None:
        rec = {"kind": "bun", "pkg": "ripgrep-cli", "bin": "ripgrep-cli"}
        self.assertEqual(uninstall_command(rec), "bun remove -g ripgrep-cli")


class UpgradeManagersTest(unittest.TestCase):
    def test_bun_and_yarn_are_included_when_present(self) -> None:
        env = Env("linux", "ubuntu", {"debian"}, tools={"bun", "yarn"})
        managers = upgrade_managers(env)
        self.assertIn("bun", managers)
        self.assertIn("yarn", managers)

    def test_bun_and_yarn_are_excluded_when_absent(self) -> None:
        env = Env("linux", "ubuntu", {"debian"}, tools=set())
        managers = upgrade_managers(env)
        self.assertNotIn("bun", managers)
        self.assertNotIn("yarn", managers)

    def test_emerge_still_included_when_present(self) -> None:
        env = Env("linux", "gentoo", {"gentoo"}, tools={"emerge"})
        self.assertIn("emerge", upgrade_managers(env))

    def test_system_upgrade_command_includes_bun_and_yarn(self) -> None:
        env = Env("linux", "ubuntu", {"debian"}, tools={"bun", "yarn"})
        script = system_upgrade_command(env)
        self.assertIn("bun update -g", script)
        self.assertIn("yarn global upgrade", script)


if __name__ == "__main__":
    unittest.main()
