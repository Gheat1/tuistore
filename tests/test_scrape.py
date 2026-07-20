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


if __name__ == "__main__":
    unittest.main()
