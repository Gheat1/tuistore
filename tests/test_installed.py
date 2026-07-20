import unittest

from tuistore.installed import pkg_from_command, uninstall_command, update_command


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


if __name__ == "__main__":
    unittest.main()
