"""GitHub, through the `gh` CLI.

We lean on `gh` rather than raw tokens: if the user has `gh auth login` done
(almost every dev does), starring and live counts just work with zero setup.
Everything degrades gracefully to None/False when gh is missing or unauthed.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from functools import lru_cache


@lru_cache(maxsize=1)
def available() -> bool:
    return shutil.which("gh") is not None


async def _gh(*args: str, timeout: float = 15.0) -> tuple[int, bytes, bytes]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, out, err
    except (FileNotFoundError, asyncio.TimeoutError):
        return 1, b"", b"timeout"
    except Exception as e:  # pragma: no cover
        return 1, b"", str(e).encode()


async def whoami() -> str | None:
    if not available():
        return None
    code, out, _ = await _gh("api", "user", "--jq", ".login")
    return out.decode().strip() if code == 0 and out.strip() else None


async def repo_info(owner: str, repo: str) -> dict | None:
    """One call → stars, language, archived, pushed_at, description, homepage."""
    if not available():
        return None
    code, out, _ = await _gh(
        "api", f"repos/{owner}/{repo}",
        "--jq",
        "{stars: .stargazers_count, language: .language, archived: .archived, "
        "pushed_at: .pushed_at, description: .description, homepage: .homepage, "
        "full_name: .full_name}",
    )
    if code != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


async def is_starred(owner: str, repo: str) -> bool | None:
    """True/False if we can tell, None if unknown (unauthed / offline)."""
    if not available():
        return None
    # 204 = starred, 404 = not starred. gh exits non-zero on 404.
    code, out, err = await _gh("api", f"user/starred/{owner}/{repo}")
    if code == 0:
        return True
    blob = (out + err).decode().lower()
    if "404" in blob or "not found" in blob:
        return False
    return None


async def star(owner: str, repo: str) -> bool:
    if not available():
        return False
    code, _, _ = await _gh("api", "-X", "PUT", f"user/starred/{owner}/{repo}")
    return code == 0


async def unstar(owner: str, repo: str) -> bool:
    if not available():
        return False
    code, _, _ = await _gh("api", "-X", "DELETE", f"user/starred/{owner}/{repo}")
    return code == 0
