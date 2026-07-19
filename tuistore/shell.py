"""Cross-platform shell invocation helpers.

Provides a single source of truth for "which shell should run a user-facing
install command" so that the TUI stream and the CLI `_run` helper behave
identically on Windows, macOS, and Linux.
"""

from __future__ import annotations

import shutil
import sys


def shell_command() -> tuple[str, list[str]]:
    """Return the executable and argument list to run a command in the user's
    preferred shell.

    On Windows we prefer PowerShell Core, then Windows PowerShell, then
    cmd.exe. On Unix we use a POSIX login shell (bash, zsh, or /bin/sh).
    """
    if sys.platform == "win32":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if pwsh:
            # -NoProfile avoids slow startup and prompt/profile side effects.
            return pwsh, ["-NoProfile", "-Command"]
        return "cmd.exe", ["/c"]
    shell = shutil.which("bash") or shutil.which("zsh") or "/bin/sh"
    return shell, ["-lc"]
