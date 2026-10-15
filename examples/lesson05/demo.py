"""Run from the repository root. Scripted mode tests plumbing, not model ability."""
import argparse
import asyncio
import json
from pathlib import Path

from team_harness.investigation import OpenAIResponses, investigate

ROOT = Path(__file__).resolve().parent


class ScriptedModel:
    model = "scripted-protocol-test-double"

    def __init__(self, case):
        self.case = case

    def __call__(self, payload):
        observations = [json.loads(item["output"]) for item in payload["input"]
                        if item.get("type") == "function_call_output"]
        if len(observations) < 2 and payload["tools"]:
            number = len(observations)
            output = [{"type": "function_call", "call_id": "call-" + str(number),
                       "name": ("read_logs", "run_tests")[number], "arguments": "{}"}]
        else:
            missing = self.case["id"] == "missing" or not observations
            summary = {"boundary": "value=100 的日志与测试表明边界行为不符；未检查源码，无法确认具体修复。",
                       "normal": "value=101 符合范围，固定四边界测试通过。",
                       "missing": "缺失输入值与规则版本，无法判断原因。"}[self.case["id"]]
            if not observations:
                summary = "未取得日志和测试证据，无法判断。"
            citations = [] if missing else [
                {"evidence_id": item["evidence_id"], "quote": item["content"]}
                for item in observations]
            answer = {"status": "unknown" if missing else "known", "summary": summary,
                      "citations": citations}
            output = [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}]
        return {"status": "completed", "output": output, "model": self.model, "usage": None}


async def run(args):
    case = next(case for case in json.loads((ROOT / "cases.json").read_text()) if case["id"] == args.case)
    model = ScriptedModel(case) if args.mode == "scripted" else OpenAIResponses()
    record = await investigate(case, model, ROOT, direct=args.mode == "direct", max_turns=args.max_turns)
    if args.mode == "scripted":
        record["mode"] = "scripted"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / (record["run_id"] + ".json")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"run_id": record["run_id"], "stop_reason": record["stop_reason"],
                      "answer": record["answer"], "record": str(path)}, ensure_ascii=False, indent=2))
    return 0 if record["stop_reason"] == "answered" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["boundary", "normal", "missing"], default="boundary")
    parser.add_argument("--mode", choices=["scripted", "agent", "direct"], default="scripted")
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--output-dir", type=Path, default=Path("runs/lesson05"))
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyError:
        parser.exit(2, "Set OPENAI_MODEL and OPENAI_API_KEY for agent/direct mode; never paste the key into source.\n")
