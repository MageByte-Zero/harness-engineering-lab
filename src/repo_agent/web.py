"""Loopback-only teaching API; routes delegate execution to the existing use case."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from repo_agent.application import CapacityExceeded
from repo_agent.conversation import ConversationBusy


class Submission(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)


def create_app(manager):
    @asynccontextmanager
    async def lifespan(app):
        yield
        await manager.close()

    app = FastAPI(title='研发 Agent', lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

    @app.middleware('http')
    async def same_origin(request: Request, call_next):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': 'Cross-origin access denied'}, status_code=403)
        return await call_next(request)

    def find(run_id):
        if run_id not in manager.runs:
            raise HTTPException(404, 'Run not found in this process')
        return manager.runs[run_id]

    @app.get('/capabilities')
    async def capabilities():
        return {'conversations':hasattr(manager.worker, 'create')}

    @app.get('/')
    def home():
        return FileResponse(Path(__file__).with_name('templates') / 'investigation.html')

    @app.post('/conversations', status_code=201)
    async def new_conversation():
        if not hasattr(manager.worker, 'create'):
            raise HTTPException(404, 'Conversation mode is not enabled')
        try:
            return {'conversation_id':manager.worker.create().conversation_id}
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.post('/runs', status_code=202)
    async def submit(body: Submission):
        try:
            if body.conversation_id and (not hasattr(manager.worker, 'conversations') or body.conversation_id not in manager.worker.conversations):
                raise HTTPException(404, 'Conversation not found')
            if hasattr(manager.worker, 'conversations') and not body.conversation_id:
                raise HTTPException(422, 'Create a conversation first')
            return manager.submit(body.question, conversation_id=body.conversation_id).view()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ConversationBusy as exc:
            raise HTTPException(409, 'This conversation already has an active run') from exc
        except CapacityExceeded as exc:
            raise HTTPException(429, 'Run capacity reached; inspect existing runs or restart for a fresh session') from exc

    @app.get('/runs/{run_id}')
    async def status(run_id: str):
        return find(run_id).view()

    @app.post('/runs/{run_id}/cancel', status_code=202)
    async def cancel(run_id: str):
        return manager.cancel(find(run_id)).view()

    @app.get('/runs/{run_id}/events')
    async def events(run_id: str, request: Request, after: int = Query(0, ge=0)):
        run = find(run_id)
        # Browser reconnects supply Last-Event-ID. Manual readers can use ?after=N.
        try:
            cursor = int(request.headers.get('last-event-id', after))
        except ValueError as exc:
            raise HTTPException(422, 'Invalid event cursor') from exc
        if not 0 <= cursor <= len(run.events):
            raise HTTPException(422, 'Invalid event cursor')

        async def stream():
            nonlocal cursor
            while True:
                if await request.is_disconnected():
                    return  # The manager owns execution; a reader owns only this subscription.
                for event in run.events[cursor:]:
                    cursor = event['seq']
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if run.state in ('completed', 'failed', 'cancelled'):
                    return
                await asyncio.sleep(0.1)

        return StreamingResponse(stream(), media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    return app
