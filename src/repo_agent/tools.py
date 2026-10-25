"""Tools see a bounded, frozen text snapshot, never a model-selected directory."""

import hashlib
import json
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


def snapshot_hash(sources: dict[str, str]) -> str:
    manifest = {path: source_hash(content) for path, content in sources.items()}
    return source_hash(json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def repository_tools(sources: dict[str, str]):
    sources = sources.copy()
    snapshot_id = snapshot_hash(sources)

    def observation(status, **fields):
        return {"status": status, "scope": "snapshot_only", "snapshot_id": snapshot_id, **fields}

    @function_tool(failure_error_function=None)
    def search_code(keyword: str) -> dict:
        """只搜索绑定快照的文件内容，不搜索文件名。不支持正则。空 matches 只说明内容无匹配，不能证明仓库没有文件或依赖。
        最多20处，每处最多500字符；truncated 表示还有命中，excerpt_truncated 表示该行片段不完整。缩小关键词或用 read_file 读上下文；查路径用 list_source_files。
        """
        if not keyword.strip() or len(keyword) > 200 or "\n" in keyword or "\r" in keyword:
            return observation("invalid_keyword", matches=[])
        matches = []
        for path, content in sources.items():
            result = subprocess.run(
                ["rg", "--no-config", "--color", "never", "-n", "-F", "-m", "21", "--", keyword],
                input=content, text=True, capture_output=True, timeout=3, check=False,
            )
            if result.returncode not in (0, 1):
                return observation("unavailable", matches=[], reason="search_failed")
            for match in result.stdout.splitlines():
                number, _, text = match.partition(":")
                matches.append({"path": path, "line": int(number), "text": text[:500],
                                "excerpt_truncated": len(text) > 500, "sha256": source_hash(content)})
                if len(matches) > 20:
                    return observation("ok", matches=matches[:20], truncated=True)
        return observation("ok", matches=matches, truncated=False)

    @function_tool(failure_error_function=None)
    def read_file(path: str, start_line: Annotated[int, Field(ge=1)] = 1,
                  end_line: Annotated[int, Field(ge=1)] = 120) -> dict:
        """按快照相对路径读取带行号的内容；行号从1开始，起止均包含，每次最多120行和12000字符。
        not_found 仅表示快照未收录，不代表磁盘不存在。denied 是范围禁止，invalid_range 是行段不合法。
        next_start_line 非空时可续读；content_truncated 表示字符截断，不能用下一行跳过丢失部分，应缩小行段。路径来源可先查 list_source_files。
        """
        if not path.strip():
            return observation("invalid_path")
        relative = PurePosixPath(path)
        if (relative.is_absolute() or ".." in relative.parts or "\\" in path
                or any(part.startswith(".") for part in relative.parts)):
            return observation("denied")
        name = relative.as_posix()
        if name not in sources:
            return observation("not_found", path=name)
        lines = sources[name].splitlines()
        if end_line < start_line or end_line - start_line >= 120 or start_line > len(lines):
            return observation("invalid_range", path=name, total_lines=len(lines))
        end = min(end_line, len(lines))
        content = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start_line - 1, end))
        content_truncated = len(content) > 12_000
        return observation("ok", path=name, sha256=source_hash(sources[name]),
            start_line=start_line, end_line=end, total_lines=len(lines), content=content[:12_000],
            truncated=end < len(lines) or content_truncated, content_truncated=content_truncated,
            next_start_line=end + 1 if end < len(lines) and not content_truncated else None)

    @function_tool(failure_error_function=None)
    def list_source_files() -> dict:
        """列出绑定只读文本快照的全部已收录路径和哈希，供后续 read_file 使用，无须参数。
        这不是完整磁盘目录：隐藏路径、符号链接和非支持后缀未收录。清单中没有文件，只能说明快照未收录，不能断言仓库不存在该文件或依赖。
        """
        return observation("ok", files=[{"path": path, "sha256": source_hash(sources[path])}
                                        for path in sorted(sources)],
            supported_suffixes=sorted(SOURCE_SUFFIXES), repository_complete=False, truncated=False)

    return [search_code, read_file, list_source_files]
