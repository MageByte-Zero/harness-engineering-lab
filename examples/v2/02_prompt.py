"""Compare only instructions; retain every attempt for human review."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from openai import APIError
from support_agent.models import BASE_INSTRUCTIONS, ask, build_input, create_client, load_documents


def compare(client, model, documents, cases, instructions, output, repeats=2):
    if repeats < 2:
        raise ValueError("Use at least two repeats")
    output.mkdir(parents=True, exist_ok=False)
    failures = 0
    for case in cases:
        for trial in range(1, repeats + 1):
            for version, prompt in [("before", BASE_INSTRUCTIONS), ("after", instructions)]:
                record = {"case": case, "trial": trial, "version": version,
                          "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
                try:
                    record.update(ask(client, model, case["question"], documents, instructions=prompt))
                except APIError as exc:
                    failures += 1
                    # Store safe error metadata, not provider text that may echo credentials.
                    record.update(question=case["question"], documents=documents,
                                  request={"model": model, "messages": [
                                      {"role": "system", "content": prompt},
                                      {"role": "user", "content": build_input(case["question"], documents)}]},
                                  error={"type": type(exc).__name__,
                                         "status_code": getattr(exc, "status_code", None)},
                                  response=None, answer=None, review=None)
                path = output / f"{case['id']}-{version}-{trial}.json"
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"Saved {path.name}", flush=True)
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory; never overwritten")
    args = parser.parse_args()
    cases = [json.loads(line) for line in Path("evals/cases/prompt.jsonl").read_text().splitlines() if line.strip()]
    instructions = Path("prompts/support.md").read_text(encoding="utf-8")
    documents = load_documents(Path("data/docs"))
    with create_client() as client:
        model = os.environ.get("MODEL_NAME")
        if not model:
            raise ValueError("Set MODEL_NAME before making a model request")
        failures = compare(client, model, documents, cases, instructions, args.output)
    if failures:
        raise SystemExit(f"{failures} API attempts failed; retained all records")


if __name__ == "__main__":
    main()
