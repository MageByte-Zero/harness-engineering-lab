"""Loopback-only teaching API; routes delegate execution to the existing use case."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from repo_agent.application import CapacityExceeded
from repo_agent.conversation import ConversationBusy, ConversationScopeChanged
from repo_agent.planning import PlanningRequest
from repo_agent.access import AccessDenied
from repo_agent.approval import PlanApprovals
from repo_agent.persistence import ApprovalConflict


class Submission(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)
    planning_request: PlanningRequest | None = None
    project_id: str | None = Field(default=None, min_length=1, max_length=64)


class ApprovalRevision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    plan_revision: str = Field(pattern=r'^[0-9a-f]{64}$')


class ReviewSubmission(ApprovalRevision):
    reviewer_id: str = Field(min_length=1, max_length=64)


class ReviewDecision(ApprovalRevision):
    decision: Literal['approve', 'reject']


def create_app(manager):
    access = getattr(manager.worker, 'access', None)
    approvals = PlanApprovals(manager, manager.store, access) if access is not None and manager.store is not None else None
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
        request.state.principal = None
        if access is not None and request.url.path not in ('/', '/session', '/capabilities'):
            request.state.principal = identity(request)
            if request.state.principal is None:
                return JSONResponse({'detail': 'Authentication required'}, status_code=401,
                    headers={'WWW-Authenticate': 'Bearer', 'Cache-Control': 'no-store'})
        response = await call_next(request)
        if access is not None:
            response.headers['Cache-Control'] = 'no-store'
        return response

    def identity(request):
        # Bearer and HttpOnly cookie verify the SAME pre-issued credential. No user_id header.
        header = request.headers.get('authorization')
        credential = header[7:] if header and header[:7].lower() == 'bearer ' else (
            None if header else request.cookies.get('agent_session'))
        return access.authenticate(credential) if access is not None else None

    @app.exception_handler(AccessDenied)
    async def denied(request, exc):
        return JSONResponse({'detail': 'Resource unavailable'}, status_code=403)

    @app.exception_handler(ApprovalConflict)
    async def approval_conflict(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    def approval_service():
        if approvals is None:
            raise HTTPException(404, 'Approvals require persistent project access')
        return approvals

    def find(run_id, principal=None):
        if access is not None:
            return manager.get(run_id, principal=principal)
        if run_id not in manager.runs:
            raise HTTPException(404, 'Run not found')
        return manager.runs[run_id]

    @app.get('/capabilities')
    async def capabilities():
        return {'conversations':hasattr(manager.worker, 'create'), 'persistence':manager.store is not None,
                'authentication': access is not None, 'approvals': approvals is not None}

    @app.post('/session')
    async def login(request: Request):
        if access is None:
            raise HTTPException(404)
        try:
            body = await request.json()
            credential = body.get('credential') if isinstance(body, dict) and set(body) == {'credential'} else None
        except ValueError:
            credential = None
        if not isinstance(credential, str) or access.authenticate(credential) is None:
            raise HTTPException(401, 'Invalid credential')
        response = JSONResponse({'authenticated': True})
        response.set_cookie('agent_session', credential, httponly=True, samesite='strict',
            secure=request.url.scheme == 'https', path='/')
        return response

    @app.delete('/session')
    async def logout():
        response = JSONResponse({'authenticated': False})
        response.delete_cookie('agent_session', path='/')
        return response

    @app.get('/session')
    async def session(request: Request):
        principal = identity(request)
        if principal is None or access is None:
            raise HTTPException(401, 'Authentication required')
        return {'principal': principal, 'projects': sorted(access.memberships.get(principal, ()))}

    @app.get('/')
    def home():
        return FileResponse(Path(__file__).with_name('templates') / 'investigation.html')

    @app.get('/conversations')
    async def conversations(request: Request):
        principal = request.state.principal
        return [{'conversation_id': c.conversation_id, 'turn_count': len(c.turns),
                 'project_id': c.scope.get('ownership', {}).get('project_id'),
                 'scope_matches': c.scope == (manager.worker.bound_scope(principal, c.scope['ownership']['project_id'])
                    if access is not None else manager.worker.scope)}
                for c in (manager.worker.visible(principal) if hasattr(manager.worker, 'visible') else [])]

    @app.get('/conversations/{identity}/runs')
    async def conversation_runs(identity: str, request: Request):
        if access is not None:
            manager.worker.find(identity, principal=request.state.principal)
        if identity not in getattr(manager.worker, 'conversations', {}):
            raise HTTPException(404, 'Conversation not found')
        return [r.view() for r in manager.visible(principal=request.state.principal, conversation_id=identity)]

    @app.post('/conversations', status_code=201)
    async def new_conversation(request: Request, project_id: str | None = None):
        if not hasattr(manager.worker, 'create'):
            raise HTTPException(404, 'Conversation mode is not enabled')
        try:
            return {'conversation_id':manager.worker.create(**({'principal': request.state.principal,
                'project_id': project_id} if access is not None else {})).conversation_id}
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.post('/runs', status_code=202)
    async def submit(body: Submission, request: Request):
        try:
            if access is not None:
                access.project(request.state.principal, body.project_id)
                manager.worker.find(body.conversation_id, principal=request.state.principal)
            if body.conversation_id and (not hasattr(manager.worker, 'conversations') or body.conversation_id not in manager.worker.conversations):
                raise HTTPException(404, 'Conversation not found')
            if hasattr(manager.worker, 'conversations') and not body.conversation_id:
                raise HTTPException(422, 'Create a conversation first')
            return manager.submit(body.question, conversation_id=body.conversation_id,
                planning_request=body.planning_request, principal=request.state.principal, project_id=body.project_id).view()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ConversationScopeChanged as exc:
            raise HTTPException(409, 'Bound resources changed; create a new conversation') from exc
        except ConversationBusy as exc:
            raise HTTPException(409, 'This conversation already has an active run') from exc
        except CapacityExceeded as exc:
            raise HTTPException(429, 'Run capacity reached; inspect existing runs or use another local data directory') from exc

    @app.get('/runs/{run_id}')
    async def status(run_id: str, request: Request):
        return find(run_id, request.state.principal).view()

    @app.post('/runs/{run_id}/approval', status_code=201)
    async def submit_review(run_id: str, body: ReviewSubmission, request: Request):
        return approval_service().submit(request.state.principal, run_id, body.plan_revision, body.reviewer_id)

    @app.get('/approvals')
    async def list_approvals(request: Request):
        return approval_service().visible(request.state.principal)

    @app.get('/approvals/{approval_id}')
    async def get_approval(approval_id: str, request: Request):
        return approval_service().get(request.state.principal, approval_id)

    @app.post('/approvals/{approval_id}/decision')
    async def decide_approval(approval_id: str, body: ReviewDecision, request: Request):
        return approval_service().decide(request.state.principal, approval_id, body.plan_revision, body.decision)

    @app.post('/approvals/{approval_id}/withdraw')
    async def withdraw_approval(approval_id: str, body: ApprovalRevision, request: Request):
        return approval_service().withdraw(request.state.principal, approval_id, body.plan_revision)

    @app.post('/runs/{run_id}/cancel', status_code=202)
    async def cancel(run_id: str, request: Request):
        return manager.cancel(find(run_id, request.state.principal), principal=request.state.principal).view()

    @app.get('/runs/{run_id}/events')
    async def events(run_id: str, request: Request, after: int = Query(0, ge=0)):
        run = find(run_id, request.state.principal)
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
                # Recheck ownership/grants on every batch; policy itself changes at restart.
                if access is not None:
                    access.require_owner(identity(request), run.ownership)
                if await request.is_disconnected():
                    return  # The manager owns execution; a reader owns only this subscription.
                for event in run.events[cursor:]:
                    cursor = event['seq']
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if run.state in ('completed', 'failed', 'cancelled', 'interrupted'):
                    return
                await asyncio.sleep(0.1)

        return StreamingResponse(stream(), media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    return app
