"""Compare fixed prefetch with agent-directed retrieval over the same source bundle."""

import argparse
import json
from pathlib import Path

from repo_agent.agent import RETRIEVAL_INSTRUCTIONS
from repo_agent.execution import run_repository


def main():
    config = json.loads(Path(__file__).with_name('09_tasks.json').read_text(encoding='utf-8'))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True, choices=[t['id'] for t in config['tasks']])
    parser.add_argument('--strategy', required=True, choices=['fixed', 'agent'])
    parser.add_argument('--context-char-limit', type=int, default=60000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    task = next(t for t in config['tasks'] if t['id'] == args.task)
    roots = {'code': Path(task['code']), 'policy': Path('fixtures/order-policy'),
             'guidance': Path('fixtures/order-guidance')}
    record = run_repository(roots, task['question'], args.output, task_id=task['id'],
        instructions=RETRIEVAL_INSTRUCTIONS, context_char_limit=args.context_char_limit,
        retrieval_queries=tuple(config['fixed_queries']) if args.strategy == 'fixed' else None)
    if record['stop_reason'] != 'answer_returned':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
