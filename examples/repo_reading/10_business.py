"""Query directly first, then use the existing Agent and record checker."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from repo_agent.business import BusinessClient, BUSINESS_INSTRUCTIONS
from repo_agent.execution import run_repository


def main():
    tasks = json.loads(Path(__file__).with_name('10_tasks.json').read_text())['tasks']
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', choices=[t['id'] for t in tasks], default='status')
    parser.add_argument('--direct', choices=['status', 'events'])
    parser.add_argument('--order-id', default='o-999')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(Path('.env'), override=False)
    client = BusinessClient(os.environ['BUSINESS_API_URL'], os.environ['BUSINESS_API_TOKEN'])
    if args.direct:
        # Reserve first; a paid/network request must never overwrite existing evidence.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as output:
            result = asyncio.run(client.query(args.direct, args.order_id))
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write('\n')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result['status'] != 'ok':
            raise SystemExit(1)
    else:
        task = next(t for t in tasks if t['id'] == args.task)
        record = run_repository({'policy': Path('fixtures/order-policy')}, task['question'], args.output,
            task_id=task['id'], instructions=BUSINESS_INSTRUCTIONS, business_client=client)
        if record['stop_reason'] != 'answer_returned':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
