"""Check retained observations against their snapshot; semantic review stays pending."""

import json
from pathlib import PurePosixPath

from repo_agent.tools import SOURCE_SUFFIXES, snapshot_hash, source_hash


def check_record(record):
    errors = []
    requests, results, signatures = {}, set(), set()
    model_messages = reads = repeated = 0
    try:
        sources = record["sources"]
        if not sources or any(source_hash(s["content"]) != s["sha256"] for s in sources.values()):
            errors.append("snapshot_hash")
        snapshot_id = snapshot_hash({path: source["content"] for path, source in sources.items()})
        for event in record["events"]:
            for message in (event["state"] or {}).get("messages", []):
                if message["type"] == "ai":
                    model_messages += 1
                    for call in message.get("tool_calls", []):
                        if call["id"] in requests:
                            errors.append("duplicate_call_id")
                        requests[call["id"]] = call
                        signature = (call["name"], json.dumps(call["args"], sort_keys=True))
                        repeated += signature in signatures
                        signatures.add(signature)
                elif message["type"] == "tool":
                    call_id = message["tool_call_id"]
                    call = requests.get(call_id)
                    if not call or call["name"] != message["name"] or call_id in results:
                        errors.append("tool_call_id")
                        continue
                    results.add(call_id)
                    result = json.loads(message["content"])
                    if record.get("tool_contract_version") == 2 or "snapshot_id" in result:
                        if result.get("snapshot_id") != snapshot_id:
                            errors.append("snapshot_identity")
                        if result.get("scope") != "snapshot_only":
                            errors.append("tool_scope")
                    if result["status"] != "ok":
                        continue
                    if message["name"] == "read_file":
                        source = sources[result["path"]]
                        start, end = result["start_line"], result["end_line"]
                        lines = source["content"].splitlines()
                        expected = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start - 1, end))[:12_000]
                        if (not 1 <= start <= end <= len(lines) or result["content"] != expected
                                or result["sha256"] != source["sha256"]
                                or result["path"] != PurePosixPath(call["args"]["path"]).as_posix()):
                            errors.append("read_observation")
                        if record.get("tool_contract_version") == 2:
                            full = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start - 1, end))
                            cut = len(full) > 12_000
                            next_line = end + 1 if end < len(lines) and not cut else None
                            if (result["total_lines"] != len(lines) or result["content_truncated"] != cut
                                    or result["next_start_line"] != next_line
                                    or result["truncated"] != (end < len(lines) or cut)):
                                errors.append("read_continuation")
                        reads += 1
                    elif message["name"] == "search_code":
                        for match in result["matches"]:
                            source = sources[match["path"]]
                            line = source["content"].splitlines()[match["line"] - 1]
                            if (match["text"] != line[:500] or match["sha256"] != source["sha256"]
                                    or call["args"]["keyword"] not in line):
                                errors.append("search_observation")
                    elif message["name"] == "list_source_files":
                        expected_files = [{"path": path, "sha256": sources[path]["sha256"]}
                                          for path in sorted(sources)]
                        if (result["files"] != expected_files or result["truncated"] is not False
                                or result["repository_complete"] is not False
                                or result["supported_suffixes"] != sorted(SOURCE_SUFFIXES)):
                            errors.append("list_observation")
        if not record.get("finished_at") or not record.get("stop_reason"):
            errors.append("unfinished_record")
        if record["stop_reason"] == "answer_returned":
            if not record.get("answer") or not reads or set(requests) != results:
                errors.append("incomplete_answer_run")
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        errors.append("invalid_record")
    return {"ok": not errors, "errors": sorted(set(errors)), "run_id": record.get("run_id"),
            "stop_reason": record.get("stop_reason"), "observed_model_messages": model_messages,
            "requested_tools": len(requests), "observed_tool_results": len(results),
            "successful_reads": reads, "repeated_requests": repeated,
            "pending_tool_ids": sorted(set(requests) - results), "semantic_review": "pending"}
