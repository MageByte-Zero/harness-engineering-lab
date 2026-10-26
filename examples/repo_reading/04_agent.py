"""Run from the lab root. Keep every observable message, including failed attempts."""

import argparse
from pathlib import Path

from repo_agent.execution import run_repository

QUESTION = "查询单个订单的 HTTP 接口在哪里？它如何取得订单，订单不存在时返回什么？请给出源码依据。"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--question", default=QUESTION)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--context-char-limit", type=int, default=60000, help="Maximum prepared SDK input characters, not tokens")
    args = parser.parse_args()
    if not args.question.strip():
        parser.error("Question must not be empty")
    record = run_repository(args.repo, args.question, args.output, context_char_limit=args.context_char_limit)
    if record["stop_reason"] != "answer_returned":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
