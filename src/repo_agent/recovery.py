"""Reconcile a recorded edit or check in the original local workspace."""
import asyncio
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import re
import shutil
import subprocess

from repo_agent.persistence import ApprovalConflict, message_digest
from repo_agent.workspace import OPERATION_LABEL, WorkspaceFailure, _docker


def baseline_digest(base: Path) -> str:
    if not base.is_dir() or base.is_symlink():
        raise ApprovalConflict('Approved baseline is unavailable')
    try:
        paths = sorted(base.rglob('*'))
        if any(path.is_symlink() for path in paths):
            raise ApprovalConflict('Approved baseline contains a symbolic link')
        return message_digest([(path.relative_to(base).as_posix(), path.read_bytes().hex())
                               for path in paths if path.is_file()])
    except OSError as exc:
        raise ApprovalConflict('Approved baseline is unavailable') from exc


def _daemon() -> str:
    try:
        identity = _docker(['info', '--format', '{{.ID}}'], 6).strip()
        if identity:
            return identity
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkspaceFailure('recovery_docker_unavailable', 503, 'Check the original Docker daemon') from exc
    raise WorkspaceFailure('recovery_docker_unavailable', 503, 'Check the original Docker daemon')


class OperationJournal:
    def __init__(self, run, store):
        self.run, self.store = run, store

    def save(self, **fields):
        updated = {**self.run.operation, **fields}
        view = self.run.view(durable=True)
        view['operation'] = updated
        self.store.save_run(view, self.run.events)
        self.run.operation = updated

    async def reserve(self, prefix: str) -> tuple[str, str]:
        operation = self.run.operation
        daemon = await asyncio.to_thread(_daemon)
        if operation.get('docker_id', daemon) != daemon:
            raise WorkspaceFailure('recovery_docker_changed', 503, 'The Docker daemon changed')
        name = prefix + uuid4().hex
        # Commit the name and daemon before the worker can ask Docker to create it.
        self.save(docker_id=daemon, containers=[*operation.get('containers', []), name])
        return name, self.run.run_id

    def ready(self, result: dict):
        self.save(receipt=result, receipt_digest=message_digest(result))


def _reconcile_containers(run_id: str, operation: dict) -> int:
    names = set(operation.get('containers', []))
    if not names:
        return 0
    if _daemon() != operation['docker_id']:
        raise WorkspaceFailure('recovery_docker_changed', 503, 'Reconnect the original Docker daemon')

    def inventory():
        # A failed listing is distinct from a successful listing with no matches.
        template = '{{.ID}}\t{{.Names}}\t{{.Label "' + OPERATION_LABEL + '"}}'
        names_filter = '^/(' + '|'.join(re.escape(name) for name in sorted(names)) + ')$'
        output = _docker(['container', 'ls', '-a', '--no-trunc', '--filter', 'name=' + names_filter,
                          '--format', template], 6)
        owned = []
        for line in output.splitlines():
            identifier, name, label = line.split('\t')
            if name in names:
                if label != run_id:
                    raise ApprovalConflict('A recorded container name no longer belongs to this run')
                owned.append(identifier)
        return owned

    try:
        leftover = inventory()
        for identifier in leftover:
            _docker(['container', 'rm', '--force', identifier], 10)
        if inventory():
            raise WorkspaceFailure('recovery_cleanup_unconfirmed', 503, 'Confirm old container cleanup')
        return len(leftover)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise WorkspaceFailure('recovery_cleanup_unconfirmed', 503, 'Confirm old container cleanup') from exc


def _edit_difference(directory: Path, run_id: str, targets: list[str]) -> tuple[list[str], list[str]]:
    stage = directory.parent / ('.edit-' + run_id)
    changed, unavailable = [], []
    for target in targets:
        staged = stage / target
        try:
            if not staged.resolve().is_relative_to(stage) or stage.is_symlink():
                unavailable.append(target)
            elif staged.read_bytes() != (directory / 'base' / target).read_bytes():
                changed.append(target)
        except (OSError, RuntimeError):
            unavailable.append(target)
    return changed, unavailable


async def reconcile(run, directory: Path) -> tuple[dict, dict | None]:
    operation = run.operation
    if baseline_digest(directory / 'base') != operation['baseline_digest']:
        raise ApprovalConflict('Approved workspace baseline changed')
    receipt = operation.get('receipt')
    if receipt is not None:
        if (message_digest(receipt) != operation.get('receipt_digest')
                or any(receipt.get(key) != value for key, value in operation['binding'].items())):
            raise ApprovalConflict('Saved operation receipt does not match')
        if operation['kind'] == 'edit' and receipt.get('run_id') != run.run_id:
            raise ApprovalConflict('Saved edit receipt belongs to another run')
        if operation['kind'] == 'verification':
            attempts = receipt['attempts']
            last = attempts[-1]
            if (receipt['source_run_id'] != operation['source_run_id']
                    or attempts[0]['patch_sha256'] != operation['candidate_digest']
                    or operation['check_input'] != {'patch_sha256': sha256(last['patch'].encode()).hexdigest(),
                                                    'image_id': last['verifier_image_id'], 'attempt': len(attempts)}):
                raise ApprovalConflict('Saved verification receipt does not match the checked inputs')
    removed = await asyncio.to_thread(_reconcile_containers, run.run_id, operation)
    decision: dict = {'removed_containers': removed}
    if receipt is not None or run.cancel_requested:
        try:
            for prefix in ('.edit-', '.verify-'):
                stage = directory.parent / (prefix + run.run_id)
                if stage.exists() or stage.is_symlink():
                    shutil.rmtree(stage)
        except OSError as exc:
            raise WorkspaceFailure('recovery_cleanup_unconfirmed', 503, 'Confirm old stage cleanup') from exc
        if run.cancel_requested:
            return {**decision, 'status': 'cancelled'}, None
        return {**decision, 'status': 'receipt_restored'}, receipt
    if operation['kind'] == 'edit' or operation.get('repair_started'):
        changed, unavailable = _edit_difference(directory, run.run_id, operation['binding']['targets'])
        decision.update(changed_paths=changed, unavailable_paths=unavailable)
    if operation['kind'] == 'edit':
        return {**decision, 'status': 'edit_needs_review'}, None
    decision.update(source_run_id=operation['source_run_id'],
                    repair_started=operation.get('repair_started', False),
                    saved_checks=operation.get('checks', []))
    return {**decision, 'status': 'verification_retry'}, None
