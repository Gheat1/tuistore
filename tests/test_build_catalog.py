import unittest

from tools.build_catalog import dedupe_methods, has_method_kind
from tuistore.installer import make


class GuessedBrewFallbackDedupeTest(unittest.TestCase):
    """add_methods() appends a guessed `brew install <repo>` fallback for
    "essential" catalog entries, but only if a real one wasn't already
    scraped from the README. The scraped command can legitimately differ in
    shape from the naive guess (e.g. it needs a tap: `brew tap owner/tap &&
    brew install name` instead of `brew install reponame`), so the check
    must be "is there already a brew method at all", not "is there already
    a brew method with this exact command string".
    """

    def test_scraped_brew_suppresses_guessed_fallback_even_with_different_command(self) -> None:
        scraped = make(
            "brew", "brew tap owner/tap && brew install name", source="scraped"
        ).to_dict()
        guessed = make(
            "brew", "brew install name", source="inferred", note="homebrew"
        ).to_dict()

        # today's bug: both survive because their command strings differ
        self.assertNotEqual(scraped["command"], guessed["command"])

        # the fixed fallback-append logic must see the scraped brew method
        # and refuse to add the guessed one at all
        self.assertTrue(has_method_kind([scraped], "brew"))

        # sanity: without a scraped brew method, the fallback is still needed
        self.assertFalse(has_method_kind([], "brew"))

        # and the final dedupe pass alone is not sufficient to catch this —
        # exact-string dedupe lets both through
        methods = [scraped, guessed]
        deduped = dedupe_methods(methods)
        self.assertEqual(len(deduped), 2)
        self.assertIn(scraped, deduped)
        self.assertIn(guessed, deduped)

    def test_essential_fallback_logic_end_to_end(self) -> None:
        # mirrors add_methods()'s do(): scraped/inferred methods collected
        # first, then the guessed brew fallback appended only if no brew
        # method exists yet, then the final dedupe pass.
        scraped = make(
            "brew", "brew tap owner/tap && brew install name", source="scraped"
        ).to_dict()
        methods = [scraped]

        if not has_method_kind(methods, "brew"):
            methods.append(
                make("brew", "brew install name", source="inferred", note="homebrew")
                .to_dict()
            )

        result = dedupe_methods(methods)

        brew_methods = [m for m in result if m["kind"] == "brew"]
        self.assertEqual(len(brew_methods), 1)
        self.assertEqual(brew_methods[0]["command"], scraped["command"])
        self.assertEqual(brew_methods[0]["source"], "scraped")

    def test_dedupe_methods_keeps_first_of_exact_duplicates(self) -> None:
        m1 = make("cargo", "cargo install foo", source="scraped").to_dict()
        m2 = make("cargo", "cargo install foo", source="inferred").to_dict()

        deduped = dedupe_methods([m1, m2])

        self.assertEqual(deduped, [m1])


if __name__ == "__main__":
    unittest.main()
