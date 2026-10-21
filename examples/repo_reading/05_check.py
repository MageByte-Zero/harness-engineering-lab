"""Inspect a saved run offline. Passing this check never means its answer is correct."""

import argparse
import json
from pathlib import Path

from repo_agent.checks import check_record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    report = check_record(json.loads(args.record.read_text(encoding="utf-8")))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
