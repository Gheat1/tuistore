import unittest

from tuistore.scrape import extract_methods


class ScrapeInstallCommandTest(unittest.TestCase):
    def test_accepts_command_prefixes_and_rejects_usage_lines(self) -> None:
        readme = """```sh
sudo arch -arm64 brew install yq
sudo -E apt install yq
or: bun add -g yq
docker run --rm mikefarah/yq
```"""

        methods = extract_methods(readme, "https://github.com/mikefarah/yq")

        self.assertEqual(
            [(method.kind, method.os) for method in methods],
            [("brew", ["macos"]), ("apt", ["linux"])],
        )

    def test_rejects_bare_hash_comment_lines(self) -> None:
        # A bare "# apt install mytool" comment line (no leading distro
        # label or other text) must not be scraped as a real command: "#"
        # is a Markdown comment marker here, not a root-shell prompt glyph,
        # so stripping it before the comment filter runs would otherwise
        # let it masquerade as a verified README install command.
        readme = """```sh
# apt install mytool
# Fedora: dnf install mytool
brew install mytool
```"""

        methods = extract_methods(readme, "https://github.com/someone/mytool")

        self.assertEqual(
            [(method.kind, method.command) for method in methods],
            [("brew", "brew install mytool")],
        )

    def test_still_strips_non_hash_prompt_glyphs(self) -> None:
        # Regression check for the fix above: "#" no longer counts as a
        # prompt glyph, but the other shell-prompt conventions ($, the
        # heavy angle-quote ❯, and the double angle-quote ») must still be
        # stripped from the front of a command line.
        readme = """```sh
$ brew install mytool
❯ cargo install mytool
» pip install mytool
```"""

        methods = extract_methods(readme, "https://github.com/someone/mytool")

        self.assertEqual(
            sorted((method.kind, method.command) for method in methods),
            sorted([
                ("brew", "brew install mytool"),
                ("cargo", "cargo install mytool"),
                ("pip", "pip install mytool"),
            ]),
        )

    def test_accepts_chained_env_and_quoted_var_prefixes(self) -> None:
        # at_start=True must not reject real install lines that happen to
        # have more than a bare sudo/env prefix: a two-step update-then-
        # install chain, an `env VAR=val` wrapper, a quoted VAR='val with
        # spaces', or a decorative leading glyph some READMEs use in place
        # of a shell prompt.
        readme = """```sh
sudo apt update && sudo apt install yq
env GOFLAGS=-mod=mod go install github.com/mikefarah/yq/v4@latest
FOO='-C bar' cargo install yq --locked
▶ brew install yq
```"""

        methods = extract_methods(readme, "https://github.com/mikefarah/yq")

        self.assertEqual(
            sorted((method.kind, method.command) for method in methods),
            sorted([
                ("apt", "sudo apt update && sudo apt install yq"),
                ("go", "env GOFLAGS=-mod=mod go install github.com/mikefarah/yq/v4@latest"),
                ("cargo", "FOO='-C bar' cargo install yq --locked"),
                ("brew", "brew install yq"),
            ]),
        )


if __name__ == "__main__":
    unittest.main()
