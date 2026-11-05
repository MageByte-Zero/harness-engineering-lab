"""Run the existing Agent with a typed final answer, or consume a retained run offline."""
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from repo_agent.business import BusinessClient, BUSINESS_INSTRUCTIONS
from repo_agent.execution import run_repository
from repo_agent.output import consume_answer


def main():
    tasks = json.loads(Path(__file__).with_name('11_tasks.json').read_text())['tasks']
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--task', choices=[t['id'] for t in tasks])
    mode.add_argument('--record', type=Path, help='Consume offline; no model or business request')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.record:
        record = json.loads(args.record.read_text(encoding='utf-8'))
    else:
        if args.output is None:
            parser.error('--task requires --output')
        task = next(t for t in tasks if t['id'] == args.task)
        load_dotenv(Path('.env'), override=False)
        client = BusinessClient(os.environ['BUSINESS_API_URL'], os.environ['BUSINESS_API_TOKEN'])
        record = run_repository({'code': Path('fixtures/order-service'), 'policy': Path('fixtures/order-policy')},
            task['question'], args.output, task_id=task['id'], instructions=BUSINESS_INSTRUCTIONS,
            business_client=client, structured_output=True)
    view = consume_answer(record)
    print(json.dumps(view, ensure_ascii=False, indent=2))
    if view['kind'] == 'unavailable':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
