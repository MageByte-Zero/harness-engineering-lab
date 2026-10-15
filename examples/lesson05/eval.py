"""Tiny development set, not a benchmark or a held-out quality measurement."""
import argparse
import asyncio
import json
from pathlib import Path

from demo import ROOT, ScriptedModel
from team_harness.investigation import OpenAIResponses, investigate


async def main(args):
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in json.loads((ROOT / "cases.json").read_text()):
        model = ScriptedModel(case) if args.mode == "scripted" else OpenAIResponses()
        record = await investigate(case, model, ROOT, direct=args.mode == "direct")
        if args.mode == "scripted":
            record["mode"] = "scripted"
        path = args.output_dir / (record["run_id"] + ".json")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        answer = record["answer"]
        names = {tool["name"] for turn in record["turns"] for tool in turn["tools"]}
        # This detects only a few failures. Human review still judges whether claims follow evidence.
        passed = bool(answer and answer["status"] == case["expected_status"]
                      and case["required_text"] in answer["summary"]
                      and set(case["required_tools"]).issubset(names))
        rows.append({"case_id": case["id"], "passed": passed, "stop_reason": record["stop_reason"],
                     "run_id": record["run_id"], "record": str(path)})
    summary = {"mode": args.mode, "cases": rows, "passed": sum(row["passed"] for row in rows),
               "total": len(rows), "scope": "3-case development check; scripted is not model evaluation"}
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["scripted", "agent", "direct"], default="scripted")
    parser.add_argument("--output-dir", type=Path, default=Path("runs/lesson05-eval"))
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(main(args)))
    except KeyError:
        parser.exit(2, "Set OPENAI_MODEL and OPENAI_API_KEY for agent/direct mode.\n")
