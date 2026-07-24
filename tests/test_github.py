import asyncio
import sys
import unittest
from unittest.mock import patch

from tuistore.github import _gh


class GithubTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_kills_process(self):
        process = None
        create_subprocess_exec = asyncio.create_subprocess_exec

        async def create_sleeper(*args, **kwargs):
            nonlocal process
            process = await create_subprocess_exec(
                sys.executable, "-c", "import time; time.sleep(30)", **kwargs,
            )
            return process

        with patch(
            "tuistore.github.asyncio.create_subprocess_exec",
            create_sleeper,
        ):
            result = await _gh("api", "user", timeout=0.05)

        returncode = process.returncode
        if returncode is None:
            process.kill()
            await process.communicate()

        self.assertEqual(result, (1, b"", b"timeout"))
        self.assertIsNotNone(returncode)
