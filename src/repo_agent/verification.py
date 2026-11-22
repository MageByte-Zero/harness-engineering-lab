"""Verify a stored candidate and permit one repair under the original approval."""
import asyncio
from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import mkdtemp
from uuid import uuid4

from repo_agent.editing import edit_workspace
from repo_agent.observability import child
from repo_agent.persistence import ApprovalConflict
from repo_agent.workspace import WorkspaceFailure, _bind, container_args, run_container


VERIFY_IMAGE = 'harness-order-check:17.0.20-6.1.14'
TARGET = 'code/src/ApiExceptionHandler.java'
CHECKS = ('missing_status', 'missing_body', 'found_status', 'found_body')


def authorized_contract(binding: dict, plan: dict, required: dict) -> None:
    request = plan['request']
    def texts(items):
        return sorted(item['text'] for item in items)

    if (binding['project_id'] != 'alpha' or binding['targets'] != [TARGET]
            or any(texts(request[key]) != texts(required[key]) for key in ('constraints', 'acceptance'))):
        raise ApprovalConflict('This verifier covers the approved order error contract only')


def _materialize(base: Path, target: Path, artifact: dict) -> None:
    # git must treat this directory as its root, even when the data directory is inside a checkout.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CEILING_DIRECTORIES=str(target.parent), GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM='1')
    shutil.copytree(base, target)
    patch = artifact['patch'].encode('utf-8')

    def apply(*args):
        return subprocess.run(['git', 'apply', *args, '-'], cwd=target, env=env,
            input=patch, capture_output=True, timeout=5, check=True).stdout

    stats = apply('--numstat', '-z').decode('utf-8').split('\0')
    changed = [line.split('\t', 2)[-1] for line in stats if line]
    if not changed or sorted(changed) != sorted(artifact['changed_paths']):
        raise ValueError('Patch paths differ from the stored candidate')
    if len(set(changed)) != len(changed) or any(path not in artifact['targets'] for path in changed):
        raise ValueError('Patch paths outside approval')
    apply('--check')
    apply()
    files = {p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()}
    baseline = {p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file()}
    if files != baseline or any(p.is_symlink() for p in target.rglob('*')):
        raise ValueError('Only edits to existing approved files are allowed')
    actual = sorted(path for path in files if (target / path).read_bytes() != (base / path).read_bytes())
    if actual != sorted(changed):
        raise ValueError('Actual changes differ from the approved patch')


class OrderVerifier:
    def __init__(self, image: str = VERIFY_IMAGE):
        self.image = image

    def resolve_image(self) -> str:
        try:
            return subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', self.image],
                                                text=True, stderr=subprocess.DEVNULL, timeout=6).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkspaceFailure('verification_image_unavailable', 503, 'Verifier image unavailable') from exc
    def check(self, source: Path, *, image_id: str | None = None, reservation: tuple[str, str] | None = None) -> dict:
        image_id = image_id if image_id is not None else self.resolve_image()
        name, owner = reservation or ('harness-verify-' + uuid4().hex, None)
        args = container_args(name, memory='384m', pids=96, run_id=owner)
        args.extend(['--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=96m',
                     '--mount', _bind(source, '/candidate', readonly=True), image_id])
        return {**run_container(args, name, 35), 'verifier_image_id': image_id}


async def _run_check(verifier: OrderVerifier, source: Path, **options) -> dict:
    task = asyncio.create_task(asyncio.to_thread(verifier.check, source, **options))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # The worker still owns the mount until Docker has stopped.
        try:
            await asyncio.shield(task)
        except WorkspaceFailure as exc:
            if exc.code == 'workspace_cleanup_unconfirmed':
                raise
        raise


def _checked_rows(report: dict) -> list[dict]:
    rows = report.get('checks')
    if (not isinstance(rows, list) or len(rows) != len(CHECKS)
            or any(not isinstance(row, dict) or row.get('id') != key
                   or type(row.get('passed')) is not bool
                   or any(type(row.get(field)) not in (int, str) or len(str(row[field])) > 120
                          for field in ('expected', 'actual'))
                   for key, row in zip(CHECKS, rows))):
        raise ValueError('Invalid verification report')
    return [{key: row[key] for key in ('id', 'passed', 'expected', 'actual')} for row in rows]


async def verify_patch(directory: Path, binding: dict, plan: dict, docker, verifier: OrderVerifier,
                       original: dict, repair: bool, run_id: str, emit, *, journal=None) -> dict:
    if journal is None:
        stage = Path(mkdtemp(prefix='.verify-' + run_id + '-', dir=directory.parent))
    else:
        stage = directory.parent / ('.verify-' + run_id)
        stage.mkdir(mode=0o700)
    attempts = []
    candidate = original
    preserve = False
    try:
        for index in range(2 if repair else 1):
            check_source = stage / str(index)
            receipt = {'patch': candidate['patch'],
                       'patch_sha256': sha256(candidate['patch'].encode('utf-8')).hexdigest()}
            attempts.append(receipt)
            try:
                _materialize(directory / 'base', check_source, candidate)
            except (OSError, ValueError, subprocess.SubprocessError):
                receipt['outcome'] = 'candidate_unavailable'
                break
            emit('verification_started', {'attempt': index + 1})
            span = child('agent.verification', **{'harness.check.attempt': index + 1,
                                                  'harness.source_run_id': original['run_id']})
            try:
                options = {}
                if journal is not None:
                    image_id = await asyncio.to_thread(verifier.resolve_image)
                    options = {'image_id': image_id, 'reservation': await journal.reserve('harness-verify-')}
                    journal.save(check_input={'patch_sha256': receipt['patch_sha256'],
                                              'image_id': image_id, 'attempt': index + 1})
                report = await _run_check(verifier, check_source, **options)
                if options and report['verifier_image_id'] != options['image_id']:
                    raise ValueError('Verifier image differs from saved attempt')
                image_id = report['verifier_image_id']
                if index and image_id != attempts[0]['verifier_image_id']:
                    raise ValueError('Verifier image changed between attempts')
                receipt['verifier_image_id'] = image_id
                checks = _checked_rows(report)
                failures = [row for row in checks if not row['passed']]
                if span is not None:
                    span.set_attribute('harness.check.count', len(checks))
                    span.set_attribute('harness.check.failed_count', len(failures))
                    span.set_attribute('harness.check.outcome', 'requirements_failed' if failures else 'verified')
            except (WorkspaceFailure, ValueError) as exc:
                receipt['outcome'] = 'environment_failure'
                receipt['reason'] = exc.code if isinstance(exc, WorkspaceFailure) else 'invalid_verifier_output'
                preserve = receipt['reason'] == 'workspace_cleanup_unconfirmed'
                emit('verification_finished', {'attempt': index + 1, 'status': receipt['outcome']})
                if span is not None:
                    span.set_attribute('harness.check.outcome', 'environment_failure')
                break
            finally:
                if span is not None:
                    span.end()
            receipt['checks'] = checks
            receipt['outcome'] = 'requirements_failed' if failures else 'verified'
            emit('verification_finished', {'attempt': index + 1, 'status': receipt['outcome']})
            if not failures or index == 1 or not repair:
                if journal is not None:
                    journal.ready(_result(original, binding, attempts))
                break
            if journal is not None:
                journal.save(repair_started=True, checks=[{key: value for key, value in receipt.items()
                                                          if key != 'patch'}])
            emit('repair_started', {'attempt': index + 1, 'failed_checks': [row['id'] for row in failures]})
            try:
                candidate = await edit_workspace(directory, binding, plan, docker, run_id, emit,
                                                 initial=check_source, feedback=failures, journal=journal)
            except Exception as exc:
                receipt['repair_stop_reason'] = type(exc).__name__
                break
            if candidate['kind'] != 'patch_for_review':
                receipt['repair_stop_reason'] = candidate['stop_reason']
                if candidate['stop_reason'] == 'cancelled':
                    raise asyncio.CancelledError
                if candidate['stop_reason'] == 'workspace_cleanup_unconfirmed':
                    receipt['outcome'] = 'environment_failure'
                    receipt['reason'] = candidate['stop_reason']
                break
    finally:
        if not preserve:
            shutil.rmtree(stage)
    return _result(original, binding, attempts)


def _result(original: dict, binding: dict, attempts: list[dict]) -> dict:
    final = attempts[-1]
    result = {'source_run_id': original['run_id'], **binding, 'attempts': attempts}
    if final['outcome'] == 'verified':
        return {'kind': 'verification_for_review', **result, 'patch': final['patch']}
    stop = 'workspace_cleanup_unconfirmed' if final.get('reason') == 'workspace_cleanup_unconfirmed' else final['outcome']
    return {'kind': 'unavailable', **result, 'stop_reason': stop}
