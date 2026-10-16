"""Run bounded multi-step support queries; outputs never assert business success."""

import argparse
import json
from pathlib import Path

import httpx
from support_agent.agent import build_bounded_agent, create_model
from support_agent.execution import run_agent
from support_agent.models import build_input, load_documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-limit", type=int, default=4)
    parser.add_argument("--tool-limit", type=int, default=3)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output already exists; choose a new file")
    model = create_model()
    message = {"role": "user", "content": build_input(args.question, load_documents(Path("data/docs")))}
    with httpx.Client(base_url="http://127.0.0.1:8005", timeout=5, trust_env=False) as client:
        agent = build_bounded_agent(model, client, model_limit=args.model_limit, tool_limit=args.tool_limit)
        record = run_agent(agent, message)
    record["model"] = model.model_name
    record["limits"] = {"model": args.model_limit, "tool": args.tool_limit, "http_timeout_seconds": 5}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print("Saved run:", record["stop_reason"], "business_review:", record["business_review"])
    if record["stop_reason"] != "answer_returned":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
