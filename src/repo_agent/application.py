"""One investigation use case and bounded, process-local run ownership."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from repo_agent.business import BusinessClient, BUSINESS_INSTRUCTIONS
from repo_agent.execution import run_repository_async
from repo_agent.output import consume_answer


async def investigate(sources, question, output, *, business_url, business_token, run_id=None, emit=None):
    # Each attempt owns a new client and observation ledger. No HTTP request selects resources.
    return await run_repository_async(sources, question, output, run_id=run_id, emit=emit,
        instructions=BUSINESS_INSTRUCTIONS, business_client=BusinessClient(business_url, business_token),
        structured_output=True)


class CapacityExceeded(RuntimeError):
    pass


@dataclass
class ActiveRun:
    run_id: str
    question: str
    state: str = 'queued'
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    task: asyncio.Task | None = field(default=None, repr=False)
    execution: asyncio.Task | None = field(default=None, repr=False)
    cancel_requested: bool = False

    def emit(self, kind, data=None):
        self.events.append({'seq': len(self.events) + 1, 'run_id': self.run_id,
            'at': datetime.now(timezone.utc).isoformat(), 'kind': kind, 'data': data or {}})

    def view(self):
        return {'run_id': self.run_id, 'question': self.question, 'state': self.state,
                'cancel_requested': self.cancel_requested, 'result': self.result}


class RunManager:
    """Single event loop, no queues or persistence; reject excess work explicitly."""
    def __init__(self, directory: Path, worker, *, active_limit=2, retained_limit=64):
        self.directory, self.worker = directory, worker
        self.active_limit, self.retained_limit = active_limit, retained_limit
        self.runs: dict[str, ActiveRun] = {}
        self.closed = False

    def submit(self, question):
        question = question.strip()
        if not 1 <= len(question) <= 2000:
            raise ValueError('Question must contain 1–2000 characters')
        if (self.closed or len(self.runs) >= self.retained_limit or
                sum(r.state in ('queued', 'running', 'cancelling') for r in self.runs.values()) >= self.active_limit):
            raise CapacityExceeded()
        run = ActiveRun(str(uuid4()), question)
        self.runs[run.run_id] = run
        run.emit('queued')
        run.task = asyncio.create_task(self._execute(run))
        return run

    async def _execute(self, run):
        if run.cancel_requested:
            run.state = 'cancelled'
            run.emit('cancelled')
            return
        run.state = 'running'
        run.emit('running')
        try:
            run.execution = asyncio.create_task(self.worker(run.question,
                self.directory / f'{run.run_id}.json', run_id=run.run_id, emit=run.emit))
            record = await run.execution
            run.result = consume_answer(record)
            run.state = ('cancelled' if record['stop_reason'] == 'cancelled' else
                         'failed' if run.result['kind'] == 'unavailable' else 'completed')
        except asyncio.CancelledError:
            run.state = 'cancelled'
        except Exception as exc:
            # Never expose exception messages, credentials, or arbitrary upstream response bodies.
            run.state = 'failed'
            run.result = {'kind': 'unavailable', 'error_type': type(exc).__name__}
        finally:
            run.emit(run.state)

    def cancel(self, run):
        if run.state in ('queued', 'running'):
            run.cancel_requested = True
            run.state = 'cancelling'
            run.emit('cancel_requested')
            if run.execution is not None:
                run.execution.cancel()
        return run

    async def close(self):
        self.closed = True
        for run in self.runs.values():
            self.cancel(run)
        await asyncio.gather(*(r.task for r in self.runs.values() if r.task), return_exceptions=True)
