r"""Cross-platform paths for tuistore data.

On Windows we use %LOCALAPPDATA%\tuistore (the standard roaming-free app-data
spot). On Unix we keep the existing XDG-style ~/.local/state/tuistore and
~/.cache/tuistore locations so existing users are unaffected.
"""

import json
import os
from pathlib import Path


def _is_windows() -> bool:
    return os.name == "nt"


def _windows_local_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local)
    return Path.home() / "AppData" / "Local"


def user_data_dir() -> Path:
    """Directory for persistent state (ledger, catalog)."""
    if _is_windows():
        return _windows_local_dir() / "tuistore"
    return Path.home() / ".local/state/tuistore"


def user_cache_dir() -> Path:
    """Directory for cached data (scraped READMEs)."""
    if _is_windows():
        return _windows_local_dir() / "tuistore" / "cache"
    return Path.home() / ".cache/tuistore"


class StoreDirs:
    """UTF-8, cross-platform state and cache storage for the TUI.

    ``ricekit.AppDirs`` deliberately follows XDG paths.  Keep that behaviour
    on Unix, but use Local AppData on Windows and always specify UTF-8 so a
    catalog or cached README containing emoji is not decoded with the active
    Windows ANSI code page.
    """

    def __init__(self) -> None:
        self.state_file = user_data_dir() / "state.json"
        self.cache_dir = user_cache_dir()

    def load_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_state(self, patch: dict) -> None:
        try:
            data = self.load_state()
            data.update(patch)
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    def read_cache(self, name: str) -> dict | None:
        try:
            return json.loads((self.cache_dir / f"{name}.json").read_text(encoding="utf-8"))
        except Exception:
            return None

    def write_cache(self, name: str, data: dict) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{name}.json").write_text(
                json.dumps(data), encoding="utf-8"
            )
        except OSError:
            pass

    def clear_cache(self) -> int:
        count = 0
        try:
            for file in self.cache_dir.glob("*.json"):
                file.unlink()
                count += 1
        except OSError:
            pass
        return count
