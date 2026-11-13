"""Start the loopback application and synthetic service, or run the same use case in CLI."""
import argparse
import asyncio
from functools import partial
from contextlib import ExitStack
import json
import os
import signal
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
from repo_agent.conversation import ConversationWorker
from repo_agent.persistence import LocalStore
from repo_agent.skills import SkillLibrary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--business-port', type=int, default=8765)
    parser.add_argument('--question', help='CLI mode; runs the same investigation use case')
    parser.add_argument('--business-mode', choices=['normal', 'forbidden', 'slow'], default='normal')
    parser.add_argument('--conversation', action='store_true', help='Enable multi-turn conversations')
    parser.add_argument('--follow-up', action='append', default=[], help='CLI follow-up; requires --conversation and --question')
    parser.add_argument('--data-dir', type=Path, help='Persistent local directory; requires --conversation, single process only')
    parser.add_argument('--conversation-id', help='Resume an existing conversation in persistent CLI mode')
    parser.add_argument('--skills-dir', type=Path, help='Trusted local read-only method packages; no script execution')
    args = parser.parse_args()
    skills = SkillLibrary(args.skills_dir) if args.skills_dir else None
    if args.data_dir and not args.conversation:
        parser.error('--data-dir requires --conversation')
    if args.conversation_id and not (args.data_dir and args.question):
        parser.error('--conversation-id requires --data-dir and --question')
    if args.follow_up and not (args.conversation and args.question):
        parser.error('--follow-up requires --conversation and --question')
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
    def terminate(signum, frame):
        # Uvicorn re-emits SIGTERM after shutdown; unwind our child/store cleanup too.
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    with ExitStack() as resources:
        store = resources.enter_context(LocalStore(args.data_dir)) if args.data_dir else None
        directory = store.records if store else Path('runs') / f'interactive-{uuid4()}'
        directory.mkdir(parents=True, exist_ok=True)
        token = 'teaching-only-local'
        service_env = {k: v for k, v in os.environ.items() if not k.startswith(('MODEL_', 'OPENAI_', 'BUSINESS_'))}
        service_env['BUSINESS_API_TOKEN'] = token
        # Fail on a busy port; never attach silently to an unrelated service.
        import socket
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', args.business_port))
        service_id = uuid4()
        with (directory / f'service-stdout-{service_id}.txt').open('w') as service_log:
            child = subprocess.Popen([sys.executable, 'fixtures/business-api/server.py', '--port', str(args.business_port),
                '--mode', args.business_mode, '--log', str(directory / f'service-{service_id}.jsonl')], env=service_env,
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
                                 business_url=url, business_token=token, skills=skills)
                if args.conversation:
                    worker = ConversationWorker({'code': Path('fixtures/order-service'), 'policy': Path('fixtures/order-policy')},
                        business_url=url, business_token=token, store=store, skills=skills)
                print('Run logs:', directory, flush=True)
                manager = RunManager(directory, worker)
                if args.question:
                    async def execute_questions():
                        if args.conversation_id and isinstance(worker, ConversationWorker):
                            if args.conversation_id not in worker.conversations:
                                parser.error('Conversation not found in this data directory')
                            conversation = worker.conversations[args.conversation_id]
                        else:
                            conversation = worker.create() if isinstance(worker, ConversationWorker) else None
                        if conversation is not None:
                            print('Conversation:', conversation.conversation_id, flush=True)
                        for index, question in enumerate([args.question, *args.follow_up], 1):
                            options = {'conversation':conversation, 'run_id':str(uuid4()), 'emit':lambda *a: None} if conversation else {}
                            if store is not None and conversation is not None:
                                run = manager.submit(question, conversation_id=conversation.conversation_id)
                                assert run.task is not None
                                await run.task
                                result = run.result
                                print('Run:', run.run_id, flush=True)
                            else:
                                record = await worker(question, directory / (f'cli-{index}.json' if conversation else 'cli.json'), **options)
                                result = consume_answer(record)
                            print(json.dumps(result, ensure_ascii=False, indent=2))
                            if result is None or result['kind'] == 'unavailable':
                                return 1
                        return 0
                    if asyncio.run(execute_questions()):
                        raise SystemExit(1)
                else:
                    uvicorn.run(create_app(manager), host='127.0.0.1', port=args.port)
            finally:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    main()
