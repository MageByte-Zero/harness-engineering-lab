"""Request a JSON Schema response and keep both raw and validated output."""

import argparse
import json
import os
from pathlib import Path

from openai import APIError
from pydantic import ValidationError
from support_agent.contracts import SupportAnswer, parse_answer
from support_agent.models import build_input, create_client, load_documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output already exists; choose a new file")
    documents = load_documents(Path("data/docs"))
    instructions = Path("prompts/support.md").read_text(encoding="utf-8")
    instructions += "\n把回答写入 answer；evidence 只列本次资料 ID。需要补充用户信息时 needs_clarification 为 true 并填写 clarification_question，否则问题字段为 null。只返回符合 schema 的 JSON。"
    with create_client() as client:
        model = os.environ.get("MODEL_NAME")
        if not model:
            raise ValueError("Set MODEL_NAME before making a model request")
        request = {"model": model, "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": build_input(args.question, documents)}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "support_answer", "strict": True,
                "schema": SupportAnswer.model_json_schema()}}}
        record = {"question": args.question, "documents": documents, "request": request,
                  "response": None, "validated_answer": None, "error": None, "review": None}
        try:
            response = client.chat.completions.create(**request)
            record["response"] = response.model_dump(mode="json")
            record["validated_answer"] = parse_answer(record["response"], documents).model_dump()
        except APIError as exc:
            record["error"] = {"type": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}
        except ValidationError:
            record["error"] = {"type": "schema_validation"}
        except ValueError as exc:
            record["error"] = {"type": str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print("Saved response; validation:", "failed" if record["error"] else "passed")
    if record["error"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
