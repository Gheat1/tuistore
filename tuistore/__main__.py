"""Entry point: `tuistore` / `python -m tuistore`."""

from __future__ import annotations

import sys


def main() -> None:
    if "--version" in sys.argv or "-V" in sys.argv:
        from . import __version__
        print(f"tuistore {__version__}")
        return
    if "--doctor" in sys.argv:
        from .platform import detect
        e = detect()
        print(f"tuistore doctor — {e.label}")
        print(f"  package managers: {' '.join(sorted(e.tools)) or '(none found)'}")
        return
    from .app import main as run
    run()


if __name__ == "__main__":
    main()
