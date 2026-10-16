"""Run from the repository root: uv run python examples/v2/01_ask.py --help."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from support_agent.models import ask, build_input, create_client, load_documents


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", default="TaskHub Webhook 返回 401 时，我应该检查什么？")
    parser.add_argument("--without-docs", action="store_true")
    parser.add_argument("--prepare-only", action="store_true", help="Save input without calling a model")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(Path(".env"), override=False)
    if args.output.exists():
        parser.error("Output exists; choose a new path to retain previous attempts")
    documents = [] if args.without_docs else load_documents(Path("data/docs"))
    if args.prepare_only:
        record = {"mode": "prepare_only", "question": args.question,
                  "documents": documents, "input": build_input(args.question, documents),
                  "response": None, "review": None}
    else:
        model = os.environ.get("MODEL_NAME")
        if not model:
            parser.error("Set MODEL_NAME to an model available at MODEL_BASE_URL")
        try:
            with create_client() as client:
                record = ask(client, model, args.question, documents)
        except ValueError as exc:
            parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(record, target, ensure_ascii=False, indent=2)
        target.write("\n")
    if args.prepare_only:
        print(f"Input saved; no model called: {args.output}")
    else:
        print(record.get("answer") or f"Response saved without text; inspect: {args.output}")


if __name__ == "__main__":
    main()
