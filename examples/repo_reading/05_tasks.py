"""Run one fixed task from the lab root; reference answers never enter the agent input."""

import argparse
import json
from pathlib import Path

from repo_agent.agent import INSTRUCTIONS
from repo_agent.execution import run_repository


def main():
    tasks = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, choices=[t["id"] for t in tasks])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--instruction-note", default="", help="Append an application instruction; saved in the run record")
    args = parser.parse_args()
    task = next(t for t in tasks if t["id"] == args.task)
    instructions = INSTRUCTIONS + ("\n" + args.instruction_note if args.instruction_note else "")
    record = run_repository(Path(task["repo"]), task["question"], args.output,
                            task_id=task["id"], instructions=instructions)
    if record["stop_reason"] != "answer_returned":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
