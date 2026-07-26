import unittest

from tuistore.installer import is_interactive_command


class SudoDetectionTest(unittest.TestCase):
    """Every distro package-manager template tuistore ships already prefixes
    the command with a literal `sudo` (see installed.py's `_UNINSTALL` /
    `_UPDATE` dicts) -- catching that token is what covers pacman, apt, dnf,
    zypper, emerge, eopkg, etc. all at once (github.com/Gheat1/tuistore/
    issues/31)."""

    def test_sudo_pacman_is_interactive(self):
        self.assertTrue(is_interactive_command("sudo pacman -S ripgrep"))

    def test_sudo_apt_is_interactive(self):
        self.assertTrue(is_interactive_command("sudo apt install ripgrep"))

    def test_sudo_dnf_is_interactive(self):
        self.assertTrue(is_interactive_command("sudo dnf install ripgrep"))

    def test_sudo_zypper_is_interactive(self):
        self.assertTrue(is_interactive_command("sudo zypper install ripgrep"))

    def test_sudo_emerge_is_interactive(self):
        self.assertTrue(is_interactive_command("sudo emerge ripgrep"))

    def test_sudo_eopkg_is_interactive(self):
        self.assertTrue(is_interactive_command("sudo eopkg install ripgrep"))

    def test_sudo_xbps_is_interactive(self):
        self.assertTrue(is_interactive_command("sudo xbps-install ripgrep"))

    def test_sudo_apk_is_interactive(self):
        self.assertTrue(is_interactive_command("sudo apk add ripgrep"))

    def test_sudo_yum_is_interactive(self):
        self.assertTrue(is_interactive_command("sudo yum install ripgrep"))

    def test_sudo_with_flags_is_interactive(self):
        self.assertTrue(is_interactive_command("sudo -E apt install ripgrep"))

    def test_sudo_mid_chain_is_interactive(self):
        self.assertTrue(is_interactive_command("brew update && sudo apt install ripgrep"))

    def test_sudo_as_substring_of_another_word_is_not_flagged(self):
        # "sudo" must be its own token -- a package/flag that merely
        # contains the substring must not trip the detector.
        self.assertFalse(is_interactive_command("cargo install sudoku-solver"))
        self.assertFalse(is_interactive_command("npm install -g pseudo-tool"))


class YayParuDetectionTest(unittest.TestCase):
    """AUR helpers are deliberately never run with sudo (pacman is; yay/paru
    are not), so they need their own leading-verb check independent of the
    sudo one -- and they prompt for their own confirmations (cleanbuild,
    PKGBUILD review, etc.) regardless of sudo."""

    def test_bare_yay_install_is_interactive(self):
        self.assertTrue(is_interactive_command("yay -S ripgrep-all"))

    def test_bare_paru_install_is_interactive(self):
        self.assertTrue(is_interactive_command("paru -S ripgrep-all"))

    def test_yay_uninstall_is_interactive(self):
        self.assertTrue(is_interactive_command("yay -R ripgrep-all"))

    def test_yay_behind_sudo_dash_e_is_interactive(self):
        self.assertTrue(is_interactive_command("sudo -E yay -S ripgrep-all"))

    def test_yay_behind_env_var_assignment_is_interactive(self):
        self.assertTrue(is_interactive_command("env FOO=bar yay -S ripgrep-all"))

    def test_yay_behind_bare_var_assignment_is_interactive(self):
        self.assertTrue(is_interactive_command("FOO=bar yay -S ripgrep-all"))

    def test_yay_behind_arch_wrapper_is_interactive(self):
        self.assertTrue(is_interactive_command("arch -arm64 yay -S ripgrep-all"))

    def test_yay_after_chained_command_is_interactive(self):
        self.assertTrue(is_interactive_command("brew update && yay -S ripgrep-all"))

    def test_yay_as_a_later_statement_in_a_multi_tool_script_is_interactive(self):
        # shape of the "update all" action: several `echo "== name =="; cmd;
        # echo` lines joined with newlines. The yay entry isn't the first
        # statement, and none of the other entries need sudo -- this must
        # still be caught, or a machine with only AUR-installed tools would
        # never get the terminal handoff at all.
        script = (
            'echo "== foo =="; cargo install foo --force; echo\n'
            'echo "== bar =="; yay -S bar; echo'
        )
        self.assertTrue(is_interactive_command(script))

    def test_yay_as_substring_of_package_name_is_not_flagged(self):
        self.assertFalse(is_interactive_command("cargo install yay-cli"))
        self.assertFalse(is_interactive_command("npm install -g yay"))

    def test_paru_as_substring_of_package_name_is_not_flagged(self):
        self.assertFalse(is_interactive_command("cargo install paru-helper"))


class NonInteractiveCommandTest(unittest.TestCase):
    """The common case -- cargo/npm/uv/brew/pipx/go rarely prompt -- must
    keep using the normal streamed-log UI, not the terminal handoff."""

    def test_cargo_install_is_not_interactive(self):
        self.assertFalse(is_interactive_command("cargo install ripgrep"))

    def test_cargo_install_force_is_not_interactive(self):
        self.assertFalse(is_interactive_command("cargo install ripgrep --force"))

    def test_npm_install_global_is_not_interactive(self):
        self.assertFalse(is_interactive_command("npm install -g typescript"))

    def test_uv_tool_install_is_not_interactive(self):
        self.assertFalse(is_interactive_command("uv tool install ruff"))

    def test_uv_tool_upgrade_is_not_interactive(self):
        self.assertFalse(is_interactive_command("uv tool upgrade ruff"))

    def test_brew_install_is_not_interactive(self):
        self.assertFalse(is_interactive_command("brew install ripgrep"))

    def test_pipx_install_is_not_interactive(self):
        self.assertFalse(is_interactive_command("pipx install black"))

    def test_go_install_is_not_interactive(self):
        self.assertFalse(is_interactive_command("go install github.com/x/y@latest"))

    def test_gem_install_is_not_interactive(self):
        self.assertFalse(is_interactive_command("gem install bundler"))

    def test_bare_clone_is_not_interactive(self):
        self.assertFalse(is_interactive_command("git clone https://github.com/a/b && cd b"))


if __name__ == "__main__":
    unittest.main()
