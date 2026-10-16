"""Run the agent against the local teaching API and retain observable messages."""

import argparse
import json
from pathlib import Path

import httpx
from support_agent.agent import build_agent, create_model
from support_agent.models import build_input, load_documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output already exists; choose a new file")
    documents = load_documents(Path("data/docs"))
    record = {"question": args.question, "documents": documents, "events": [], "answer": None,
              "error": None, "review": None}
    try:
        with httpx.Client(base_url="http://127.0.0.1:8004", timeout=5, trust_env=False) as client:
            model = create_model()
            record["model"] = model.model_name
            agent = build_agent(model, client)
            message = {"role": "user", "content": build_input(args.question, documents)}
            record["input"] = message
            for update in agent.stream({"messages": [message]}, stream_mode="updates"):
                for node, state in update.items():
                    for item in state.get("messages", []):
                        record["events"].append({"node": node, "message": item.model_dump(mode="json")})
                        if item.type == "ai" and not item.tool_calls:
                            record["answer"] = item.content
    except Exception as exc:
        # API error bodies can contain credentials or private headers.
        record["error"] = {"type": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print("Saved agent messages; run:", "failed" if record["error"] else "returned")
    if record["error"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
