"""Start the loopback application and synthetic service, or run the same use case in CLI."""
import argparse
import asyncio
from functools import partial
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from dotenv import load_dotenv
import httpx
import uvicorn

from repo_agent.application import investigate, RunManager
from repo_agent.output import consume_answer
from repo_agent.web import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--business-port', type=int, default=8765)
    parser.add_argument('--question', help='CLI mode; runs the same investigation use case')
    parser.add_argument('--business-mode', choices=['normal', 'forbidden', 'slow'], default='normal')
    args = parser.parse_args()
    if (not 1024 <= args.port <= 65535 or not 1024 <= args.business_port <= 65535
            or args.port == args.business_port):
        parser.error('Choose different unprivileged ports')
    for path in ('fixtures/order-service', 'fixtures/order-policy', 'fixtures/business-api/server.py'):
        if not Path(path).exists():
            parser.error('Run from the harness-engineering-lab repository root')
    load_dotenv(Path('.env'), override=False)
    for name in ('MODEL_API_KEY', 'MODEL_BASE_URL', 'MODEL_NAME'):
        if not os.environ.get(name):
            parser.error(f'Set {name} in root .env or process environment')
    directory = Path('runs') / f'interactive-{uuid4()}'
    directory.mkdir(parents=True)
    token = 'teaching-only-local'
    service_env = {k: v for k, v in os.environ.items() if not k.startswith(('MODEL_', 'OPENAI_', 'BUSINESS_'))}
    service_env['BUSINESS_API_TOKEN'] = token
    # Fail on a busy port; never attach silently to an unrelated service.
    import socket
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.business_port))
    with (directory / 'service-stdout.txt').open('w') as service_log:
        child = subprocess.Popen([sys.executable, 'fixtures/business-api/server.py', '--port', str(args.business_port),
            '--mode', args.business_mode, '--log', str(directory / 'service.jsonl')], env=service_env,
            stdout=service_log, stderr=service_log)
        try:
            url = f'http://127.0.0.1:{args.business_port}'
            for _ in range(50):
                if child.poll() is not None:
                    raise RuntimeError('Synthetic service failed to start; inspect run logs')
                try:
                    httpx.get(url + '/api/v1/orders/o-999', headers={'Authorization': f'Bearer {token}'},
                              timeout=.2, trust_env=False)
                    break
                except httpx.TransportError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Synthetic service did not become ready')
            worker = partial(investigate, {'code': Path('fixtures/order-service'), 'policy': Path('fixtures/order-policy')},
                             business_url=url, business_token=token)
            print('Run logs:', directory, flush=True)
            if args.question:
                record = asyncio.run(worker(args.question, directory / 'cli.json'))
                result = consume_answer(record)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                if result['kind'] == 'unavailable':
                    raise SystemExit(1)
            else:
                uvicorn.run(create_app(RunManager(directory, worker)), host='127.0.0.1', port=args.port)
        finally:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    main()
