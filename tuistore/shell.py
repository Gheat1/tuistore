"""Cross-platform shell invocation helpers.

Provides a single source of truth for "which shell should run a user-facing
install command" so that the TUI stream and the CLI `_run` helper behave
identically on Windows, macOS, and Linux.
"""

from __future__ import annotations

import os
import shutil
import sys

# Env-var name used to carry *this* process's PATH through a subprocess's
# login-shell startup files (see `shell_env` / `wrap_command` below). Chosen
# to be distinctive enough that it won't collide with anything a user's own
# shell config sets.
_PATH_MARKER = "_TUISTORE_LAUNCH_PATH"


def shell_command() -> tuple[str, list[str]]:
    """Return the executable and argument list to run a command in the user's
    preferred shell.

    On Windows we prefer PowerShell Core, then Windows PowerShell, then
    cmd.exe. On Unix we use a POSIX login shell (bash, zsh, or /bin/sh).

    Callers should run the returned command through `wrap_command` and pass
    `shell_env()` as the subprocess environment — see those functions for
    why a plain login shell isn't sufficient on its own.
    """
    if sys.platform == "win32":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if pwsh:
            # -NoProfile avoids slow startup and prompt/profile side effects.
            return pwsh, ["-NoProfile", "-Command"]
        return "cmd.exe", ["/c"]
    shell = shutil.which("bash") or shutil.which("zsh") or "/bin/sh"
    return shell, ["-lc"]


def shell_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Environment to run a `shell_command()` subprocess in.

    A login shell re-runs the system/user startup files (``/etc/profile``,
    ``~/.profile``, …) before the actual command executes. On Debian and
    Ubuntu, the stock ``/etc/profile`` *overwrites* PATH outright rather
    than merging into it, and the file most tools actually append PATH to
    — ``~/.bashrc``, the exact convention nvm and friends use — starts with
    ``case $- in *i*) ;; *) return;; esac`` and so is skipped entirely by a
    non-interactive login shell. Net effect: a tool this process can
    already find (because *our* PATH has it) can become invisible to the
    very install command we're about to run, even though nothing about the
    user's machine actually changed.

    We smuggle the current PATH through under a differently-named variable
    that those startup files won't touch, so `wrap_command` can restore it
    no matter what the login shell's own startup did or didn't do.
    """
    env = dict(base_env if base_env is not None else os.environ)
    env[_PATH_MARKER] = env.get("PATH", "")
    return env


def wrap_command(command: str) -> str:
    """Prefix `command` so PATH entries visible to *this* process stay
    visible even if the subprocess's login shell resets PATH first.

    Guarded on a non-empty check so a missing/blank marker (e.g. `command`
    run outside of `shell_env()`) can never introduce an empty PATH
    component — POSIX shells treat an empty PATH entry as `.` (cwd), which
    would be a foothold for a directory-planted-binary attack.
    """
    if sys.platform == "win32":
        return command
    return f'if [ -n "${_PATH_MARKER}" ]; then PATH="${_PATH_MARKER}:$PATH"; fi; {command}'
