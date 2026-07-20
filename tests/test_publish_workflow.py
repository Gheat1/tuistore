"""Regression test for .github/workflows/publish.yml.

GitHub's `release` event's `published` activity type fires both for a
normal release AND for a release explicitly marked "pre-release" when it
is created (see GitHub's webhook/event docs). Without an explicit guard,
the `publish` job — which uploads to real PyPI via Trusted Publishing —
would run for pre-release/RC GitHub Releases too, with no way for a
maintainer to cut a pre-release without also shipping it to real PyPI.

This test parses the workflow file (kept dependency-free: no PyYAML, just
indentation-aware text parsing, since PyYAML is not a project dependency)
and asserts that the `publish` job carries a guard referencing
`github.event.release.prerelease` that skips the job when the release is
a pre-release, while still permitting non-release triggers (e.g. the
`workflow_dispatch` trigger this workflow also declares).
"""

import re
import unittest
from pathlib import Path

WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "publish.yml"
)


def _job_block(text: str, job_name: str) -> str:
    """Return the raw text of a top-level job's body (2-space-indented job
    key; body is everything indented deeper, up to the next job or EOF)."""
    lines = text.splitlines()
    header = re.compile(rf"^  {re.escape(job_name)}:\s*$")
    start = next((i for i, line in enumerate(lines) if header.match(line)), None)
    assert start is not None, f"job {job_name!r} not found in {WORKFLOW_PATH}"

    body = []
    for line in lines[start + 1 :]:
        if line.strip() == "":
            body.append(line)
            continue
        # A new top-level job (or `jobs:` sibling) starts at <= 2-space
        # indent with a non-blank, non-comment line.
        if re.match(r"^  \S", line):
            break
        body.append(line)
    return "\n".join(body)


class PublishWorkflowPrereleaseGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(
            WORKFLOW_PATH.is_file(), f"workflow file missing: {WORKFLOW_PATH}"
        )
        self.text = WORKFLOW_PATH.read_text()

    def test_release_trigger_uses_published_type(self) -> None:
        # Sanity check the assumption this guard depends on: the trigger is
        # the `published` activity type, which GitHub fires for both
        # regular and pre-release releases.
        self.assertRegex(self.text, r"release:\s*\n\s*types:\s*\[published\]")

    def test_publish_job_is_guarded_against_prerelease(self) -> None:
        publish_block = _job_block(self.text, "publish")

        if_lines = [
            line.strip()
            for line in publish_block.splitlines()
            if re.match(r"^\s*if:", line)
        ]
        self.assertTrue(
            if_lines,
            "publish job has no `if:` guard — a GitHub Release marked "
            "pre-release will be published to real PyPI just like a "
            "stable release",
        )

        guard = if_lines[0]
        self.assertIn(
            "github.event.release.prerelease",
            guard,
            f"publish job guard does not reference the release's prerelease "
            f"flag: {guard!r}",
        )

        # The guard must actively exclude pre-releases (e.g. `!= true`,
        # `== false`, or a leading `!`), not just mention the field.
        negates_prerelease = bool(
            re.search(r"prerelease\s*!=\s*true", guard)
            or re.search(r"prerelease\s*==\s*false", guard)
            or re.search(r"!\s*github\.event\.release\.prerelease\b", guard)
        )
        self.assertTrue(
            negates_prerelease,
            f"publish job guard does not appear to exclude pre-releases: {guard!r}",
        )

    def test_publish_job_guard_permits_non_release_triggers(self) -> None:
        # This workflow also supports `workflow_dispatch`, where
        # `github.event.release` is unset. The guard must not be written in
        # a way (e.g. `== false`) that would also block manual runs.
        publish_block = _job_block(self.text, "publish")
        guard = next(
            line.strip()
            for line in publish_block.splitlines()
            if re.match(r"^\s*if:", line)
        )
        self.assertNotIn(
            "prerelease == false",
            guard,
            "guard uses `== false`, which evaluates to false (skipping the "
            "job) on workflow_dispatch runs where github.event.release is "
            "unset; use `!= true` instead",
        )


if __name__ == "__main__":
    unittest.main()
