"""Tools see a bounded, frozen text snapshot, never a model-selected directory."""

import hashlib
from pathlib import Path, PurePosixPath
import subprocess
from typing import Annotated

from agents import function_tool
from pydantic import Field

SOURCE_SUFFIXES = {".java", ".go", ".py", ".sql", ".md"}


def load_sources(root: Path) -> dict[str, str]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Repository root must be a directory")
    sources = {}
    size = 0
    # shortcut: small trusted local teaching input; use an isolated intake service for hostile repositories.
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (any(part.startswith(".") for part in relative.parts)
                or path.suffix not in SOURCE_SUFFIXES
                or any(part.is_symlink() for part in (path, *path.parents) if part != root)
                or not path.is_file()):
            continue
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("Source escaped repository root")
        with path.open("rb") as source:
            content = source.read(100_001)
        size += len(content)
        if len(content) > 100_000 or size > 2_000_000 or len(sources) >= 100:
            raise ValueError("Teaching snapshot exceeds its size limit")
        sources[relative.as_posix()] = content.decode("utf-8")
    if not sources:
        raise ValueError("No supported source files")
    return sources


def source_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def repository_tools(sources: dict[str, str]):
    sources = sources.copy()

    @function_tool(failure_error_function=None)
    def search_code(keyword: str) -> dict:
        """在绑定的源码快照中做字面关键词搜索，返回路径、行号和片段。无匹配就换线索；需要完整逻辑时用 read_file。"""
        if not keyword.strip() or len(keyword) > 200 or "\n" in keyword or "\r" in keyword:
            return {"status": "invalid_keyword", "matches": []}
        matches = []
        for path, content in sources.items():
            result = subprocess.run(
                ["rg", "--no-config", "--color", "never", "-n", "-F", "-m", "21", "--", keyword],
                input=content, text=True, capture_output=True, timeout=3, check=False,
            )
            if result.returncode not in (0, 1):
                return {"status": "unavailable", "matches": [], "reason": "search_failed"}
            for match in result.stdout.splitlines():
                number, _, text = match.partition(":")
                matches.append({"path": path, "line": int(number), "text": text[:500],
                                "excerpt_truncated": len(text) > 500, "sha256": source_hash(content)})
                if len(matches) > 20:
                    return {"status": "ok", "matches": matches[:20], "truncated": True}
        return {"status": "ok", "matches": matches, "truncated": False}

    @function_tool(failure_error_function=None)
    def read_file(path: str, start_line: Annotated[int, Field(ge=1)] = 1,
                  end_line: Annotated[int, Field(ge=1)] = 120) -> dict:
        """读取绑定快照中的相对路径，返回带行号的源码。每次最多120行；不存在、禁止与成功是不同结果。"""
        relative = PurePosixPath(path)
        if (relative.is_absolute() or ".." in relative.parts or "\\" in path
                or any(part.startswith(".") for part in relative.parts)):
            return {"status": "denied"}
        name = relative.as_posix()
        if name not in sources:
            return {"status": "not_found", "path": name}
        lines = sources[name].splitlines()
        if end_line < start_line or end_line - start_line >= 120 or start_line > len(lines):
            return {"status": "invalid_range", "path": name, "total_lines": len(lines)}
        end = min(end_line, len(lines))
        content = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start_line - 1, end))
        return {"status": "ok", "path": name, "sha256": source_hash(sources[name]),
                "start_line": start_line, "end_line": end, "total_lines": len(lines),
                "content": content[:12_000], "truncated": end < len(lines) or len(content) > 12_000}

    return [search_code, read_file]
