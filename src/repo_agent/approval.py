"""Pause the plan handoff in the application after the planning run finishes."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from repo_agent.access import AccessDenied
from repo_agent.persistence import ApprovalConflict
from repo_agent.tools import load_source_bundle, snapshot_hash


class PlanApprovals:
    def __init__(self, manager, store, access, *, clock=None):
        self.manager = manager
        self.store = store
        self.access = access
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def submit(self, principal, run_id, revision, reviewer_id):
        run = self.manager.get(run_id, principal=principal)
        result = run.result or {}
        if run.state != 'completed' or result.get('kind') != 'plan_for_review':
            raise ApprovalConflict('Only a complete plan can be submitted for review')
        artifact = result['artifact']
        if artifact['revision'] != revision:
            raise ApprovalConflict('Plan revision changed')
        if principal == reviewer_id:
            raise AccessDenied('Resource unavailable')
        project_id = run.ownership['project_id']
        self.access.require_approver(reviewer_id, project_id)
        project = self.access.project(principal, project_id)
        self._require_snapshot(project, artifact['snapshot_id'])
        now = self.clock()
        # Copy review material from the saved result. The browser supplies neither plan nor action.
        binding = {
            'owner_id': principal,
            'project_id': project_id,
            'reviewer_id': reviewer_id,
            'plan_id': artifact['id'],
            'plan_revision': revision,
            'snapshot_id': artifact['snapshot_id'],
            'action': 'prepare_isolated_change',
            'targets': sorted({step['target'] for step in result['steps']}),
            'requested_at': now.isoformat(),
            'expires_at': (now + timedelta(hours=24)).isoformat(),
            'plan': {key: result[key] for key in
                     ('summary', 'request', 'facts', 'steps', 'checks', 'tradeoffs', 'questions')},
        }
        approval_id = str(uuid4())
        self.store.add_approval(approval_id, run_id, binding)
        return self.get(principal, approval_id)

    @staticmethod
    def _require_snapshot(project, expected):
        try:
            current = snapshot_hash(load_source_bundle(project.sources))
        except (OSError, ValueError) as exc:
            raise ApprovalConflict('Plan sources unavailable') from exc
        if current != expected:
            raise ApprovalConflict('Plan source snapshot changed')

    def _get(self, approval_id):
        rows = self.store.approvals(approval_id)
        if not rows:
            raise AccessDenied('Resource unavailable')
        return rows[0]

    def _view(self, principal, record):
        self.access.project(principal, record['project_id'])
        is_owner = principal == record['owner_id']
        if not is_owner:
            if principal != record['reviewer_id']:
                raise AccessDenied('Resource unavailable')
            self.access.require_approver(principal, record['project_id'])
        pending = record['state'] == 'pending'
        expired = self.clock() >= datetime.fromisoformat(record['expires_at'])
        return {**record, 'expired': pending and expired,
                'can_decide': not is_owner and pending and not expired,
                'can_withdraw': is_owner and pending,
                'can_prepare_workspace': is_owner and record['state'] == 'approved'
                    and record['handoff_id'] is not None}

    def get(self, principal, approval_id):
        return self._view(principal, self._get(approval_id))

    def visible(self, principal):
        result = []
        for record in self.store.approvals():
            try:
                result.append(self._view(principal, record))
            except AccessDenied:
                continue
        return result

    def decide(self, principal, approval_id, revision, decision):
        record = self._get(approval_id)
        if principal != record['reviewer_id']:
            raise AccessDenied('Resource unavailable')
        project_id = record['project_id']
        self.access.require_approver(principal, project_id)
        project = self.access.project(record['owner_id'], project_id)
        now = self.clock()
        self._require_pending(record, revision)
        if now >= datetime.fromisoformat(record['expires_at']):
            raise ApprovalConflict('Approval expired')
        if decision == 'approve':
            self._require_snapshot(project, record['snapshot_id'])
        elif decision != 'reject':
            raise ValueError('Invalid decision')
        state = 'approved' if decision == 'approve' else 'rejected'
        self.store.decide_approval(approval_id, state, principal, now.isoformat())
        return self.get(principal, approval_id)

    @staticmethod
    def _require_pending(record, revision):
        if revision != record['plan_revision']:
            raise ApprovalConflict('Plan revision changed')
        if record['state'] != 'pending':
            raise ApprovalConflict('This approval is no longer pending')

    def withdraw(self, principal, approval_id, revision):
        record = self._get(approval_id)
        self.access.require_owner(principal, record)
        self._require_pending(record, revision)
        self.store.decide_approval(approval_id, 'withdrawn', principal, self.clock().isoformat())
        return self.get(principal, approval_id)
