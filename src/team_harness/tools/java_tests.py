import asyncio
import os
from pathlib import Path
import signal
import tempfile
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal


OUTPUT_LIMIT = 8000


class ToolRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    tool: Literal["java_tests"] = "java_tests"
    timeout_seconds: float = Field(default=30.0, gt=0, le=300, allow_inf_nan=False)


class ToolResult(BaseModel):
    status: Literal["passed", "failed", "timeout", "execution_error"]
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""


def read_excerpt(stream, secrets: list[str]) -> str:
    stream.seek(0)
    lookahead = max((len(secret.encode("utf-8")) for secret in secrets), default=1)
    text = stream.read(OUTPUT_LIMIT + lookahead).decode("utf-8", errors="replace")
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    return text[:OUTPUT_LIMIT] + ("\n[truncated]" if stream.tell() > OUTPUT_LIMIT else "")


async def run_command(argv: list[str], cwd: Path, timeout_seconds: float) -> ToolResult:
    # shortcut: POSIX process groups only; add a Windows Job Object before supporting Windows.
    if os.name != "posix":
        raise RuntimeError("This lesson supports macOS/Linux only")
    env = {k: os.environ[k] for k in ("PATH", "JAVA_HOME", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
    secrets = [v for k, v in os.environ.items() if any(word in k.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH")) and len(v) >= 4]
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        launch = asyncio.create_task(asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, start_new_session=True,
        ))
        proc = None
        try:
            async with asyncio.timeout(timeout_seconds):
                proc = await asyncio.shield(launch)
                code = await proc.wait()
            status = "passed" if code == 0 else "failed"
        except TimeoutError:
            status, code = "timeout", None
        except OSError as exc:
            return ToolResult(status="execution_error", exit_code=None, stderr=type(exc).__name__)
        finally:
            # Cancellation may arrive after OS spawn but before Python receives its Process.
            if proc is None:
                try:
                    proc = await launch
                except OSError:
                    pass
            # A Java/Maven process can have children; ending the wait must end their work too.
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
        return ToolResult(status=status, exit_code=code,
                          stdout=read_excerpt(stdout, secrets), stderr=read_excerpt(stderr, secrets))


async def run_java_tests(request: ToolRequest, backend_dir: Path) -> ToolResult:
    cwd = backend_dir.resolve(strict=True)
    if not (cwd / "pom.xml").is_file():
        raise ValueError("backend directory must contain pom.xml")
    argv = ["mvn", "-q", "-Dtest=ScorecardEvaluatorTest,RulesetEvaluatorTest,ComparisonOperatorsTest",
            "-DskipTests=false", "-Dmaven.test.skip=false", "test"]
    return await run_command(argv, cwd, request.timeout_seconds)
