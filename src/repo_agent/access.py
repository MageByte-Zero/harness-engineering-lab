"""Trusted startup grants and verified, pre-issued local credentials; no model decisions."""
from dataclasses import dataclass, field
import hmac
import json
import os
from pathlib import Path
import secrets

from pydantic import BaseModel, ConfigDict


class AccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class Project:
    sources: dict[str, Path]
    business_url: str
    business_token: str = field(repr=False)
    allowed_orders: frozenset[str] = frozenset()


class AccessPolicy:
    def __init__(self, projects, memberships, credentials):
        self.projects = projects
        self.memberships = memberships
        self._credentials = credentials

    def authenticate(self, credential):
        if not isinstance(credential, str) or not credential.isascii() or not 1 <= len(credential) <= 256:
            return None
        return next((actor for actor, token in self._credentials.items()
            if hmac.compare_digest(credential, token)), None)

    def project(self, principal, project_id):
        if not principal or project_id not in self.memberships.get(principal, ()) or project_id not in self.projects:
            raise AccessDenied('Resource unavailable')
        return self.projects[project_id]

    def require_owner(self, principal, ownership):
        if not ownership or ownership.get('owner_id') != principal:
            raise AccessDenied('Resource unavailable')
        return self.project(principal, ownership.get('project_id'))

    def can_read(self, principal, ownership):
        try:
            self.require_owner(principal, ownership)
            return True
        except AccessDenied:
            return False


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    sources: dict[str, str]
    allowed_orders: list[str]


class AccessConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    projects: dict[str, ProjectConfig]
    memberships: dict[str, list[str]]


def load_local_access(config_path: Path, credential_path: Path, business_url: str):
    """Demo provisioning, never a public login service. Source paths are relative to the config."""
    config = AccessConfig.model_validate_json(config_path.read_text(encoding='utf-8'))
    if not config.projects or not config.memberships or any(
            p not in config.projects for ids in config.memberships.values() for p in ids):
        raise ValueError('Invalid project grants')
    credential_path.parent.mkdir(parents=True, exist_ok=True)
    if not credential_path.exists():
        credentials = {'users': {actor: secrets.token_urlsafe(32) for actor in config.memberships},
            'services': {pid: secrets.token_urlsafe(32) for pid in config.projects}}
        fd = os.open(credential_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(credentials, stream, indent=2)
    credentials = json.loads(credential_path.read_text(encoding='utf-8'))
    tokens = [credentials['users'][a] for a in config.memberships] + [credentials['services'][p] for p in config.projects]
    if len(set(tokens)) != len(tokens) or any(not isinstance(t, str) or len(t) < 32 or not t.isascii() for t in tokens):
        raise ValueError('Invalid local credentials')
    # This fixture configuration is trusted operator input, not an HTTP body or model argument.
    root = config_path.resolve().parent
    projects = {pid: Project({k: (root / v).resolve() for k, v in cfg.sources.items()},
        business_url, credentials['services'][pid], frozenset(cfg.allowed_orders)) for pid, cfg in config.projects.items()}
    if any(not p.is_dir() for project in projects.values() for p in project.sources.values()):
        raise ValueError('Project source directory missing')
    return AccessPolicy(projects, {a: frozenset(ids) for a, ids in config.memberships.items()},
        {a: credentials['users'][a] for a in config.memberships})
