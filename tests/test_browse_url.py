import unittest

from tuistore.catalog import Entry, load

REPO_URL = "https://github.com/a/b"


def make_entry(homepage: str | None) -> Entry:
    return Entry(name="x", url=REPO_URL, homepage=homepage)


class BrowseUrlTest(unittest.TestCase):
    def test_https_homepage_passes_through(self) -> None:
        self.assertEqual(
            make_entry("https://example.org").browse_url, "https://example.org"
        )

    def test_http_homepage_passes_through(self) -> None:
        self.assertEqual(
            make_entry("http://example.org").browse_url, "http://example.org"
        )

    def test_non_http_scheme_falls_back_to_repo_url(self) -> None:
        # ssh:// is a real value in the shipped catalog; the rest are the
        # classes of scheme xdg-open would happily dispatch to a non-browser
        # handler if we handed them straight to webbrowser.open().
        hostile_homepages = [
            "ssh://slides.tseivan.com",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "data:text/html,x",
            "vscode://x",
        ]
        for homepage in hostile_homepages:
            with self.subTest(homepage=homepage):
                self.assertEqual(make_entry(homepage).browse_url, REPO_URL)

    def test_schemeless_homepage_is_upgraded_to_https(self) -> None:
        self.assertEqual(make_entry("urwid.org").browse_url, "https://urwid.org")
        self.assertEqual(
            make_entry("www.coderholic.com/pyradio").browse_url,
            "https://www.coderholic.com/pyradio",
        )

    def test_missing_or_blank_homepage_falls_back_to_repo_url(self) -> None:
        for homepage in (None, "", "   "):
            with self.subTest(homepage=homepage):
                self.assertEqual(make_entry(homepage).browse_url, REPO_URL)

    def test_case_insensitive_scheme_check(self) -> None:
        self.assertEqual(
            make_entry("HTTPS://Example.org").browse_url, "HTTPS://Example.org"
        )

    def test_shipped_catalog_yields_only_http_browse_urls(self) -> None:
        # Regression net: a future catalog refresh that introduces a new
        # hostile scheme must fail here, not surface at "press o" time.
        for entry in load().entries:
            with self.subTest(entry=entry.name):
                self.assertTrue(
                    entry.browse_url.lower().startswith(("http://", "https://"))
                )


if __name__ == "__main__":
    unittest.main()
