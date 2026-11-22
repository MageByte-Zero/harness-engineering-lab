"""Turn one approved handoff into a verified local workspace."""
import asyncio
import csv
import io
import json
import logging
from pathlib import Path, PurePosixPath
import shutil
import subprocess
from tempfile import mkdtemp
from threading import Lock
from uuid import UUID, uuid4

from repo_agent.access import AccessDenied
from repo_agent.persistence import ApprovalConflict
from repo_agent.tools import load_source_bundle, snapshot_hash


PROBE_IMAGE = 'python@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527'
LOGGER = logging.getLogger(__name__)
PROBE = """
import errno
import json
import os
from pathlib import Path
import sys

targets = sys.argv[1:]
for name in targets:
    path = Path('/work') / name
    original = path.read_bytes()
    with path.open('ab') as target:
        target.write(b'workspace probe')
    path.write_bytes(original)

neighbor = next((path for path in sorted(Path('/work/code/src').rglob('*'))
                 if path.is_file() and path.relative_to('/work').as_posix() not in targets), None)
denied = {}
protected = [Path('/work/__workspace_probe__')]
if neighbor is not None:
    protected.append(neighbor)
for path in protected:
    try:
        with path.open('ab'):
            pass
    except OSError as exc:
        if exc.errno != errno.EROFS:
            raise
        denied[str(path)] = exc.errno
    else:
        raise RuntimeError('An unapproved path is writable')

interfaces = sorted(os.listdir('/sys/class/net'))
if (interfaces != ['lo'] or os.getuid() != 65534
        or Path('/var/run/docker.sock').exists()
        or any(key.startswith('MODEL_') for key in os.environ)):
    raise RuntimeError('Unexpected runtime scope')
print(json.dumps({'writable': targets, 'readonly_denials': denied,
                  'interfaces': interfaces, 'uid': os.getuid()}))
"""


class WorkspaceFailure(RuntimeError):
    def __init__(self, code: str, status: int, detail: str):
        super().__init__(detail)
        self.code = code
        self.status = status


def _bind(source: Path, destination: str, *, readonly: bool = False) -> str:
    # Docker parses --mount as CSV, including host paths with commas.
    buffer = io.StringIO()
    values = ['type=bind', f'src={source}', f'dst={destination}']
    if readonly:
        values.append('readonly')
    csv.writer(buffer, lineterminator='').writerow(values)
    return buffer.getvalue()


def _docker(args: list[str], timeout: float, *, payload: str | None = None) -> str:
    return subprocess.run(['docker', *args], check=True, capture_output=True,
                          encoding='utf-8', input=payload, timeout=timeout).stdout


OPERATION_LABEL = 'io.magebyte.harness.run'


def container_args(name: str, *, memory: str, pids: int, run_id: str | None = None) -> list[str]:
    args = ['create', '--name', name, '--pull=never', '--read-only', '--network=none',
            '--user=65534:65534', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--pids-limit', str(pids), '--memory', memory, '--cpus=1']
    if run_id is not None:
        args.extend(['--label', OPERATION_LABEL + '=' + run_id])
    # The Docker client can otherwise forward proxy variables from its own config.
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'FTP_PROXY', 'NO_PROXY'):
        args.extend(['--env', key + '=', '--env', key.lower() + '='])
    return args


def run_container(args: list[str], name: str, timeout: float, *, payload: str | None = None) -> dict:
    created = False
    try:
        try:
            _docker(args, 15)
            created = True
        except subprocess.TimeoutExpired as exc:
            # The daemon might have received create even though the CLI timed out.
            LOGGER.error('Docker create outcome unknown for %s', name)
            raise WorkspaceFailure('workspace_cleanup_unconfirmed', 503,
                                   'Docker container creation could not be confirmed') from exc
        command = ['start', '--attach']
        if payload is not None:
            command.append('--interactive')
        report = json.loads(_docker([*command, name], timeout, payload=payload))
        if not isinstance(report, dict):
            raise ValueError('Expected a JSON object from container')
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceFailure('workspace_timeout', 504,
                               'Workspace command timed out; container removed') from exc
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        LOGGER.warning('Workspace command failed for %s (%s)', name, type(exc).__name__)
        raise WorkspaceFailure('workspace_runtime_error', 503,
                               'Docker image or workspace command unavailable') from exc
    finally:
        if created:
            try:
                _docker(['rm', '--force', name], 10)
            except (OSError, subprocess.SubprocessError) as exc:
                LOGGER.error('Docker cleanup unconfirmed for %s', name)
                raise WorkspaceFailure('workspace_cleanup_unconfirmed', 503,
                                       'Workspace container cleanup could not be confirmed') from exc
    return {**report, 'container_removed': True}


class DockerProbe:
    def __init__(self, timeout: float = 8):
        self.timeout = timeout

    def verify(self, directory: Path, targets: tuple[str, ...]) -> dict:
        return self.run(directory / 'base', directory / 'write', targets, PROBE)

    def run(self, base: Path, writable: Path, targets: tuple[str, ...], program: str,
            payload: str | None = None, *, reservation: tuple[str, str] | None = None) -> dict:
        name, owner = reservation or ('harness-workspace-' + uuid4().hex, None)
        args = container_args(name, memory='96m', pids=32, run_id=owner)
        args.extend(['--entrypoint', 'python', '--mount', _bind(base, '/work', readonly=True)])
        if payload is not None:
            args.append('--interactive')
        for target in targets:
            args.extend(['--mount', _bind(writable / target, '/work/' + target)])
        args.extend([PROBE_IMAGE, '-I', '-B', '-c', program, *targets])
        return run_container(args, name, self.timeout, payload=payload)


def _source_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (len(parts) > 2 and parts[:2] == ('code', 'src')
            and '/'.join(parts) == path and all(part != '..' for part in parts)
            and '\\' not in path)


class WorkspacePreparation:
    def __init__(self, approvals, probe: DockerProbe):
        self.approvals = approvals
        self.probe = probe
        self.root = approvals.store.directory / 'workspaces'
        self.preparing = Lock()

    def _handoff(self, principal: str, approval_id: str) -> dict:
        try:
            if str(UUID(approval_id)) != approval_id:
                raise ValueError('Noncanonical approval id')
        except ValueError as exc:
            raise AccessDenied('Resource unavailable') from exc
        record = self.approvals.get(principal, approval_id)
        self.approvals.access.require_owner(principal, record)
        if (record['state'] != 'approved' or record['handoff_id'] != approval_id + '/handoff'
                or record['action'] != 'prepare_isolated_change'):
            raise ApprovalConflict('No approved workspace handoff')
        return record

    def get(self, principal: str, approval_id: str) -> dict | None:
        record = self._handoff(principal, approval_id)
        return self._read_workspace(self.root / approval_id, record)

    def edit_inputs(self, principal: str, approval_id: str, revision: str) -> tuple[Path, dict, dict]:
        record = self._handoff(principal, approval_id)
        if revision != record['plan_revision']:
            raise ApprovalConflict('Plan revision changed')
        directory = self.root / approval_id
        ready = self._read_workspace(directory, record)
        if ready is None:
            raise ApprovalConflict('Prepare the workspace first')
        project = self.approvals.access.project(principal, record['project_id'])
        self.approvals._require_snapshot(project, record['snapshot_id'])
        binding = {**self._binding(record), 'workspace_id': ready['workspace_id']}
        return directory, binding, record['plan']

    async def prepare(self, principal: str, approval_id: str, revision: str) -> dict:
        record = self._handoff(principal, approval_id)
        if revision != record['plan_revision']:
            raise ApprovalConflict('Plan revision changed')
        project = self.approvals.access.project(principal, record['project_id'])
        return await asyncio.to_thread(self._prepare, record, project.sources)

    @staticmethod
    def _binding(record: dict) -> dict:
        return {key: record[key] for key in ('approval_id', 'handoff_id', 'project_id',
                'owner_id', 'plan_revision', 'snapshot_id', 'targets')}

    def _read_workspace(self, directory: Path, record: dict) -> dict | None:
        try:
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise WorkspaceFailure('workspace_record_unavailable', 503,
                                   'Saved workspace record could not be read') from exc
        if (not isinstance(manifest, dict) or manifest.get('state') != 'ready'
                or any(manifest.get(key) != value for key, value in self._binding(record).items())):
            raise ApprovalConflict('Saved workspace does not match the approved handoff')
        return manifest

    def _prepare(self, record: dict, roots: dict[str, Path]) -> dict:
        if not self.preparing.acquire(blocking=False):
            raise WorkspaceFailure('workspace_busy', 409, 'Another workspace is being prepared')
        try:
            try:
                sources = load_source_bundle(roots)
            except (OSError, ValueError) as exc:
                raise ApprovalConflict('Plan sources unavailable') from exc
            if snapshot_hash(sources) != record['snapshot_id']:
                raise WorkspaceFailure('workspace_source_changed', 409, 'Plan source snapshot changed')
            base = {path: content for path, content in sources.items() if _source_path(path)}
            targets = tuple(record['targets'])
            if not targets or any(target not in base for target in targets):
                raise WorkspaceFailure('workspace_target_denied', 403,
                                       'Approved targets must be existing files under code/src')

            directory = self.root / record['approval_id']
            existing = self._read_workspace(directory, record)
            if existing is not None:
                return existing
            return self._materialize(directory, record, base, targets)
        finally:
            self.preparing.release()

    def _materialize(self, directory: Path, record: dict, base: dict[str, str],
                     targets: tuple[str, ...]) -> dict:
        self.root.mkdir(mode=0o700, exist_ok=True)
        prefix = '.prepare-' + directory.name + '-'
        if next(self.root.glob(prefix + '*'), None) is not None:
            raise WorkspaceFailure('workspace_cleanup_unconfirmed', 503,
                                   'Check leftover workspace staging before retrying')
        staging = Path(mkdtemp(prefix=prefix, dir=self.root))
        preserve_for_cleanup = False
        try:
            for path, content in base.items():
                file = staging / 'base' / path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(content.encode('utf-8'))
            for target in targets:
                file = staging / 'write' / target
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(base[target].encode('utf-8'))
                # The host parent is private; a non-root worker mounts only this one file.
                file.chmod(0o666)
            report = self.probe.verify(staging, targets)
            if any((staging / 'write' / path).read_bytes() != base[path].encode('utf-8') for path in targets):
                raise WorkspaceFailure('workspace_probe_changed_files', 503,
                                       'Workspace probe did not restore the original bytes')
            manifest = {**self._binding(record), 'workspace_id': record['approval_id'],
                        'state': 'ready', 'image': PROBE_IMAGE, 'probe': report}
            (staging / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
            staging.rename(directory)
            return manifest
        except WorkspaceFailure as exc:
            preserve_for_cleanup = exc.code == 'workspace_cleanup_unconfirmed'
            raise
        finally:
            if not preserve_for_cleanup and staging.exists():
                shutil.rmtree(staging)
