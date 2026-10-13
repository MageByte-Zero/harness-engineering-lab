import argparse
import asyncio
import json
from pathlib import Path

from pydantic import ValidationError
from team_harness.tools.java_tests import ToolRequest, run_command, run_java_tests


async def main(args) -> int:
    request = ToolRequest(timeout_seconds=args.timeout)
    if args.backend_dir and args.scenario != "pass":
        raise ValueError("failure scenarios only run the teaching fixture")
    if args.backend_dir:
        result = await run_java_tests(request, args.backend_dir)
    else:
        cwd = Path(__file__).resolve().parent
        mode = "sleep" if args.scenario in ("timeout", "cancel") else args.scenario
        argv = ["java", str(cwd / "ToolFixture.java"), mode]
        if args.scenario == "cancel":
            task = asyncio.create_task(run_command(argv, cwd, request.timeout_seconds))
            await asyncio.sleep(1.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                print(json.dumps({"status": "cancelled", "cleanup": "completed"}))
                return 130
        result = await run_command(argv, cwd, request.timeout_seconds)
    print(result.model_dump_json(indent=2))
    return {"passed": 0, "failed": 1, "timeout": 124, "execution_error": 2}[result.status]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lesson 04: run fixed Java tests, without a model")
    parser.add_argument("--scenario", choices=["pass", "fail", "timeout", "cancel"], default="pass")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--backend-dir", type=Path)
    try:
        raise SystemExit(asyncio.run(main(parser.parse_args())))
    except (ValidationError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "invalid_request", "error": type(exc).__name__}))
        raise SystemExit(2)
