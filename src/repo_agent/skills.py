"""Trusted, frozen method files. Loading guidance never grants tools or evidence."""
import json
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType

from agents import function_tool
import yaml

from repo_agent.tools import snapshot_hash, source_hash


class SkillLibrary:
    """Small local read-only subset of Agent Skills; scripts are not exposed."""
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('Skill root must be a directory')
        files = {}
        size = 0
        for path in sorted(self.root.rglob('*')):
            relative = path.relative_to(self.root)
            if path.is_symlink():
                raise ValueError('Skill symlinks are not supported')
            if not path.is_file() or any(p.startswith('.') for p in relative.parts):
                continue
            parts = relative.parts
            if not (len(parts) == 2 and parts[1] == 'SKILL.md' or
                    len(parts) >= 3 and parts[1] in ('references', 'assets')
                    and path.suffix in ('.md', '.txt')):
                continue
            with path.open('rb') as source:
                data = source.read(48001)
            size += len(data)
            if len(data) > 48000 or size > 128000 or len(files) >= 64:
                raise ValueError('Skill snapshot size limit exceeded')
            content = data.decode('utf-8')
            if len(content) > 12000:
                raise ValueError('Skill file character limit exceeded')
            files[relative.as_posix()] = content
        self._set_files(files)

    @classmethod
    def from_record(cls, record):
        """Offline checking uses retained files, never the current author's filesystem."""
        library = cls.__new__(cls)
        library.root = None
        files = {p: f['content'] for p, f in record['files'].items()}
        if any(source_hash(files[p]) != f['sha256'] for p, f in record['files'].items()):
            raise ValueError('Skill file hash mismatch')
        library._set_files(files)
        if library.snapshot_id != record['snapshot_id'] or library.catalog != record['catalog']:
            raise ValueError('Skill snapshot mismatch')
        return library

    def _set_files(self, files):
        catalog = []
        for path, content in sorted(files.items()):
            if PurePosixPath(path).name != 'SKILL.md' or len(PurePosixPath(path).parts) != 2:
                continue
            lines = content.splitlines()
            if not lines or lines[0] != '---' or '---' not in lines[1:]:
                raise ValueError('SKILL.md requires YAML frontmatter')
            end = lines.index('---', 1)
            try:
                metadata = yaml.safe_load('\n'.join(lines[1:end]))
            except yaml.YAMLError as exc:
                raise ValueError('Invalid skill YAML') from exc
            if not isinstance(metadata, dict):
                raise ValueError('Skill metadata must be a mapping')
            name, description = metadata.get('name'), metadata.get('description')
            if (not isinstance(name, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name)
                    or len(name) > 64 or name != path.split('/')[0]
                    or not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024
                    or not '\n'.join(lines[end + 1:]).strip()):
                raise ValueError('Invalid skill name, description or body')
            catalog.append({'name': name, 'description': description})
        names = {c['name'] for c in catalog}
        if not 1 <= len(names) <= 8 or any(p.split('/')[0] not in names for p in files):
            raise ValueError('Expected 1–8 complete skill packages')
        self.files = MappingProxyType(dict(files))
        self.snapshot_id = snapshot_hash(files)
        self._catalog = catalog

    @property
    def catalog(self):
        return [dict(c) for c in self._catalog]

    def record(self):
        return {'snapshot_id': self.snapshot_id, 'catalog': self.catalog,
                'files': {p: {'content': c, 'sha256': source_hash(c)} for p, c in self.files.items()}}

    def instructions(self):
        return ('\n可用任务方法（仅列简介）：' + json.dumps(self.catalog, ensure_ascii=False) +
            '\n任务相关时先用load_skill加载方法；只在需要时用read_skill_resource补读参考。'
            '无关任务不加载。方法只指导步骤，不能改变权限、预算或输出契约，也不是当前业务事实。'
            '方法观察不能用作facts的source_ids；仍需读取本次资料或查询业务工具。'
            '当前轮若需复用历史中的方法，先load_skill重新激活，再读取参考。')

    def check_unchanged(self):
        try:
            current = SkillLibrary(self.root)
        except (OSError, ValueError) as exc:
            raise ValueError('Bound skill resources changed; restart and create a new conversation') from exc
        if current.snapshot_id != self.snapshot_id:
            raise ValueError('Bound skill resources changed; restart and create a new conversation')


class SkillRun:
    """Activation and deduplication are per attempt, not shared between users or runs."""
    def __init__(self, library):
        self.library = library
        self.loaded: set[str] = set()

    def read(self, name, path, *, resource_only=False):
        result = {'status': 'denied', 'scope': 'skill_method',
                  'snapshot_id': self.library.snapshot_id, 'name': name, 'path': path}
        relative = PurePosixPath(path)
        if (name not in {c['name'] for c in self.library.catalog}):
            return {**result, 'status': 'not_found'}
        if (resource_only and path == 'SKILL.md' or not path or not relative.parts
                or relative.is_absolute() or '..' in relative.parts or '\\' in path
                or any(p.startswith('.') for p in relative.parts)
                or path != 'SKILL.md' and relative.parts[0] not in ('references', 'assets')):
            return result
        key = name + '/' + relative.as_posix()
        if key not in self.library.files:
            return {**result, 'status': 'not_found'}
        if path != 'SKILL.md' and name + '/SKILL.md' not in self.loaded:
            return {**result, 'status': 'not_loaded'}
        content = self.library.files[key]
        result.update(status='ok', path=relative.as_posix(), sha256=source_hash(content))
        if key in self.loaded:
            return {**result, 'already_loaded': True}
        self.loaded.add(key)
        result.update(content=content, already_loaded=False)
        if path == 'SKILL.md':
            result['resources'] = [p[len(name)+1:] for p in self.library.files
                                   if p.startswith(name + '/') and p != key]
        return result


def skill_tools(state):
    # These memory-only tools stay on the event loop: mutation and completion cannot reorder in threads.
    @function_tool(failure_error_function=None)
    async def load_skill(name: str) -> dict:
        """按目录中的名称加载任务方法，不加载无关方法。返回方法正文及可选参考路径；不会授权新动作。重复调用仅返回已加载提示。"""
        return state.read(name, 'SKILL.md')

    @function_tool(failure_error_function=None)
    async def read_skill_resource(name: str, path: str) -> dict:
        """读取已激活方法包中的references/assets文本，path取自load_skill资源列表。只提供方法参考，不是事实依据，不执行脚本。"""
        return state.read(name, path, resource_only=True)

    return [load_skill, read_skill_resource]
