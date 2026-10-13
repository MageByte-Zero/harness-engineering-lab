import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from team_harness.tools import java_tests as runner


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pass_and_nonzero_exit_are_distinct(self):
        good = await runner.run_command([sys.executable, "-c", "print('4 tests passed')"], Path.cwd(), 2.0)
        bad = await runner.run_command([sys.executable, "-c", "raise SystemExit(3)"], Path.cwd(), 2.0)
        self.assertEqual((good.status, good.exit_code), ("passed", 0))
        self.assertIn("4 tests passed", good.stdout)
        self.assertEqual((bad.status, bad.exit_code), ("failed", 3))

    async def test_timeout_is_not_a_test_failure(self):
        result = await runner.run_command([sys.executable, "-c", "import time; time.sleep(30)"], Path.cwd(), 0.15)
        self.assertEqual(result.status, "timeout")
        self.assertIsNone(result.exit_code)

    async def test_secret_is_not_inherited_or_returned(self):
        secret = "lesson-four-test-secret-123"
        os.environ["LESSON_TEST_API_KEY"] = secret
        try:
            script = "import os; print(os.environ.get('LESSON_TEST_API_KEY', 'absent')); print('" + secret + "')"
            result = await runner.run_command([sys.executable, "-c", script], Path.cwd(), 2.0)
            self.assertIn("absent", result.stdout)
            self.assertNotIn(secret, result.model_dump_json())
        finally:
            del os.environ["LESSON_TEST_API_KEY"]

    async def test_timeout_and_cancel_stop_descendant_work(self):
        # Removing process-group cleanup lets the grandchild write this marker.
        for cancel in (False, True):
            with tempfile.TemporaryDirectory() as folder:
                ready = Path(folder) / "ready"
                marker = Path(folder) / "still-running"
                child = "import time; from pathlib import Path; time.sleep(0.8); Path(" + repr(str(marker)) + ").write_text('alive')"
                parent = "import subprocess,sys,time; from pathlib import Path; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); Path(" + repr(str(ready)) + ").touch(); time.sleep(30)"
                task = asyncio.create_task(runner.run_command([sys.executable, "-c", parent], Path(folder), 3.0 if cancel else 0.4))
                for _ in range(200):
                    if ready.exists():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(ready.exists(), "fixture did not start")
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    self.assertEqual((await task).status, "timeout")
                await asyncio.sleep(1.0)
                self.assertFalse(marker.exists(), "descendant kept working")

    async def test_output_excerpt_is_bounded(self):
        result = await runner.run_command([sys.executable, "-c", "print('x' * 50000)"], Path.cwd(), 2.0)
        self.assertLessEqual(len(result.stdout), runner.OUTPUT_LIMIT + 30)
        self.assertTrue(result.stdout.endswith("[truncated]"))

    async def test_cancel_during_launch_cleans_up_the_real_process(self):
        original = asyncio.create_subprocess_exec
        spawned = asyncio.Event()
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "still-running"
            async def delayed_launch(*args, **kwargs):
                proc = await original(*args, **kwargs)
                spawned.set()
                await asyncio.sleep(0.2)
                return proc
            script = "import time; from pathlib import Path; time.sleep(0.7); Path(" + repr(str(marker)) + ").touch()"
            with patch.object(asyncio, "create_subprocess_exec", delayed_launch):
                task = asyncio.create_task(runner.run_command([sys.executable, "-c", script], Path(folder), 2.0))
                await spawned.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            await asyncio.sleep(0.8)
            self.assertFalse(marker.exists(), "cancelled launch left a real process running")

    async def test_invalid_request_is_rejected_before_execution(self):
        for payload in ({"tool": "shell", "timeout_seconds": 1.0}, {"tool": "java_tests", "timeout_seconds": 0.0}, {"tool": "java_tests", "timeout_seconds": "3"}, {"tool": "java_tests", "timeout_seconds": float("nan")}, {"tool": "java_tests", "timeout_seconds": 1.0, "command": "rm"}):
            with self.assertRaises(runner.ValidationError):
                runner.ToolRequest.model_validate(payload)

    async def test_missing_executable_is_reported_without_raw_error(self):
        result = await runner.run_command(["/missing/lesson-four-executable"], Path.cwd(), 2.0)
        self.assertEqual(result.status, "execution_error")
        self.assertEqual(result.stderr, "FileNotFoundError")


if __name__ == "__main__":
    unittest.main()
