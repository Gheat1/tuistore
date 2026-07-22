import unittest
from types import SimpleNamespace

from tuistore.app import StoreApp
from tuistore.catalog import Entry, _bundled_text, _parse, _prefer_uv
from tuistore.installed import pkg_from_command, uninstall_command, update_command
from tuistore.installer import KINDS, classify, force_variant, make
from tuistore.platform import Env


class TestDnfYumClassification(unittest.TestCase):
    """dnf and yum are distinct binaries with independent availability —
    legacy RHEL-family boxes (RHEL7, CentOS7, Amazon Linux 2) only ship
    yum, and a `yum install` line must be gated on `yum` being present,
    not `dnf`."""

    def test_yum_install_classifies_as_yum_not_dnf(self):
        self.assertEqual(classify("sudo yum install mytool"), "yum")

    def test_dnf_install_still_classifies_as_dnf(self):
        self.assertEqual(classify("sudo dnf install mytool"), "dnf")

    def test_yum_only_environment_reports_yum_method_available(self):
        # RHEL7 / CentOS7 / Amazon Linux 2: yum present, dnf absent.
        env = Env("linux", "centos", {"rhel"}, "x86_64", tools={"yum"})
        cmd = "sudo yum install mytool"
        kind = classify(cmd)
        method = make(kind, cmd, source="readme")
        self.assertTrue(method.available(env))
        self.assertEqual(method.why_unavailable(env), "")

    def test_yum_only_environment_does_not_offer_dnf_method(self):
        env = Env("linux", "centos", {"rhel"}, "x86_64", tools={"yum"})
        method = make("dnf", "sudo dnf install mytool", source="readme")
        self.assertFalse(method.available(env))
        self.assertEqual(method.why_unavailable(env), "needs dnf")

    def test_dnf_only_environment_does_not_offer_yum_method(self):
        env = Env("linux", "fedora", {"fedora", "rhel"}, "x86_64", tools={"dnf"})
        method = make("yum", "sudo yum install mytool", source="readme")
        self.assertFalse(method.available(env))
        self.assertEqual(method.why_unavailable(env), "needs yum")

    def test_dnf_environment_still_offers_dnf_method(self):
        env = Env("linux", "fedora", {"fedora", "rhel"}, "x86_64", tools={"dnf"})
        method = make("dnf", "sudo dnf install mytool", source="readme")
        self.assertTrue(method.available(env))


class TestClassifyUvPip(unittest.TestCase):
    """`uv pip install X` runs the `uv` binary, not pip3 — it must not be
    tagged as a plain `pip` install (github.com/Gheat1/tuistore issue: wrong
    `requires` sends users who lack pip3 but have uv down a dead end)."""

    def test_uv_pip_install_classifies_as_uv_pip_not_pip(self):
        self.assertEqual(classify("uv pip install ruff"), "uv-pip")

    def test_uv_pip_install_requires_uv_not_pip3(self):
        method = make(classify("uv pip install ruff"), "uv pip install ruff", source="readme")
        self.assertIn("uv", method.requires)
        self.assertNotIn("pip3", method.requires)

    def test_uv_pip_install_label_mentions_uv_not_bare_pip(self):
        method = make(classify("uv pip install ruff"), "uv pip install ruff", source="readme")
        self.assertIn("uv", method.label)
        self.assertNotEqual(method.label, KINDS["pip"]["label"])

    def test_plain_pip_install_is_unaffected(self):
        # regression guard: the generic pip pattern must still win for real
        # pip invocations once the more specific uv-pip pattern is checked
        # first and doesn't match.
        self.assertEqual(classify("pip install ruff"), "pip")
        self.assertEqual(classify("pip3 install ruff"), "pip")
        self.assertEqual(classify("python3 -m pip install ruff"), "pip")

    def test_uv_tool_install_and_uvx_are_unaffected(self):
        self.assertEqual(classify("uv tool install ruff"), "uv")
        self.assertEqual(classify("uvx install ruff"), "uv")
        self.assertEqual(classify("uv install ruff"), "uv")

    def test_uv_pip_install_with_flags_and_sudo_prefix(self):
        self.assertEqual(classify("uv pip install --system ruff"), "uv-pip")
        self.assertEqual(classify("sudo uv pip install ruff"), "uv-pip")

    def test_pkg_name_extracted_from_uv_pip_command(self):
        self.assertEqual(pkg_from_command("uv-pip", "uv pip install ruff"), "ruff")

    def test_force_variant_uses_reinstall_flag(self):
        forced = force_variant("uv-pip", "uv pip install ruff")
        self.assertIn("--reinstall", forced)

    def test_uninstall_and_update_commands_use_uv_not_pip(self):
        rec = {"kind": "uv-pip", "pkg": "ruff", "bin": "ruff"}
        uninstall = uninstall_command(rec)
        update = update_command(rec)
        self.assertIsNotNone(uninstall)
        self.assertIsNotNone(update)
        self.assertTrue(uninstall.startswith("uv "))
        self.assertTrue(update.startswith("uv "))
        self.assertNotIn("pip3", uninstall)
        self.assertNotIn("pip3", update)

    def test_prefer_uv_still_canonicalizes_from_uv_pip_readme_method(self):
        # a README that recommends `uv pip install X` should still feed the
        # catalog's "make uv tool install the default" canonicalization.
        entry = Entry(name="Ruff", url="https://github.com/astral-sh/ruff")
        entry.methods = [make("uv-pip", "uv pip install ruff", source="readme")]
        _prefer_uv(entry)
        uv_methods = [m for m in entry.methods if m.kind == "uv"]
        self.assertEqual(len(uv_methods), 1)
        self.assertEqual(uv_methods[0].command, "uv tool install ruff")


class TestPackageParsingPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = _bundled_text()
        assert raw is not None
        cls.entries = {entry.name: entry for entry in _parse(raw).entries}

    def test_posting_preserves_pinned_uv_install_and_lifecycle_target(self):
        method = self.entries["posting"].methods[0]
        self.assertEqual(method.command, "uv tool install --python 3.13 posting")
        rec = {"kind": method.kind, "command": method.command, "pkg": "3.13"}
        self.assertEqual(uninstall_command(rec), "uv tool uninstall posting")
        self.assertEqual(update_command(rec), "uv tool upgrade posting")

    def test_thefuck_quirk_keeps_lifecycle_target(self):
        method = self.entries["thefuck"].methods[0]
        self.assertEqual(method.command, "uv tool install --python 3.11 thefuck")
        rec = {"kind": method.kind, "command": method.command, "pkg": "3.11"}
        self.assertEqual(uninstall_command(rec), "uv tool uninstall thefuck")
        self.assertEqual(update_command(rec), "uv tool upgrade thefuck")

    def test_yt_dlp_keeps_quoted_extras_and_winner_metadata(self):
        method = self.entries["yt-dlp"].methods[0]
        self.assertEqual(method.command, "uv tool install 'yt-dlp[default]'")
        self.assertEqual(method.source, "readme")
        self.assertEqual(method.note, "from README")
        self.assertEqual(method.requires, ["uv"])
        rec = {"kind": method.kind, "command": method.command, "pkg": '"yt-dlp[default]"'}
        self.assertEqual(uninstall_command(rec), "uv tool uninstall yt-dlp")
        self.assertEqual(update_command(rec), "uv tool upgrade yt-dlp")

    def test_manage_candidates_compares_extras_as_bare_name(self):
        entry = Entry(name="yt-dlp", url="https://github.com/yt-dlp/yt-dlp")
        entry.methods = [make("pip", 'pip install "yt-dlp[default]"', source="readme")]
        app = SimpleNamespace(env=Env("linux", "ubuntu", {"debian"}, tools={"pip3"}))
        self.assertEqual(
            StoreApp._manage_candidates(app, entry, "update"),
            [("pip", "python3 -m pip install -U yt-dlp")],
        )


class TestPreferUvIntegrity(unittest.TestCase):
    def test_uv_winner_object_and_metadata_are_preserved(self):
        winner = make("uv", "uv tool install --python 3.13 posting", source="readme", note="pinned")
        winner.os, winner.families = ["linux"], ["debian"]
        inferred = make("uv", "uv tool install posting", note="guess")
        entry = Entry(name="posting", url="https://github.com/darrenburns/posting",
                      methods=[winner, inferred])
        _prefer_uv(entry)
        self.assertIs(entry.methods[0], winner)
        self.assertEqual([m for m in entry.methods if m.kind == "uv"], [winner])
        self.assertEqual((winner.note, winner.os, winner.families),
                         ("pinned", ["linux"], ["debian"]))

    def test_pip_winner_translation_uses_uv_requirements_and_metadata(self):
        winner = make("pip", 'pip install "yt-dlp[default]"', source="readme", note="from README")
        winner.os, winner.families = ["linux"], ["debian"]
        entry = Entry(name="yt-dlp", url="https://github.com/yt-dlp/yt-dlp", methods=[winner])
        _prefer_uv(entry)
        method = entry.methods[0]
        self.assertEqual(method.command, "uv tool install 'yt-dlp[default]'")
        self.assertEqual(method.requires, ["uv"])
        self.assertEqual((method.source, method.note, method.os, method.families),
                         ("readme", "from README", ["linux"], ["debian"]))

    def test_invalid_pip_target_is_not_synthesized(self):
        winner = make("pip", "pip install 'bad;target'", source="readme")
        entry = Entry(name="bad", url="https://github.com/owner/bad", methods=[winner])
        _prefer_uv(entry)
        self.assertEqual(entry.methods, [winner])


class ClassifyScriptTest(unittest.TestCase):
    def test_curl_pipe_sudo_bash_is_a_script(self) -> None:
        self.assertEqual(
            classify("curl -fsSL https://example.com/install.sh | sudo bash"),
            "script",
        )

    def test_wget_pipe_sudo_bash_is_a_script(self) -> None:
        # Regression: the wget branch of the script regex previously lacked
        # the optional `sudo` that the curl branch already allowed, so a
        # documented `wget ... | sudo bash` install line silently failed to
        # classify (returned None) while the equivalent curl form worked.
        self.assertEqual(
            classify("wget -qO- https://example.com/install.sh | sudo bash"),
            "script",
        )

    def test_wget_pipe_sh_without_sudo_is_still_a_script(self) -> None:
        self.assertEqual(
            classify("wget -qO- https://example.com/install.sh | sh"),
            "script",
        )


if __name__ == "__main__":
    unittest.main()
