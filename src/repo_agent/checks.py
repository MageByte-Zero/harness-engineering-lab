"""Check retained observations against their snapshot; semantic review stays pending."""

import json

from repo_agent.tools import source_hash


def check_record(record):
    errors = []
    requests, results, signatures = {}, set(), set()
    model_messages = reads = repeated = 0
    try:
        sources = record["sources"]
        if not sources or any(source_hash(s["content"]) != s["sha256"] for s in sources.values()):
            errors.append("snapshot_hash")
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
                    if result["status"] != "ok":
                        continue
                    if message["name"] == "read_file":
                        source = sources[result["path"]]
                        start, end = result["start_line"], result["end_line"]
                        lines = source["content"].splitlines()
                        expected = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start - 1, end))[:12_000]
                        if (not 1 <= start <= end <= len(lines) or result["content"] != expected
                                or result["sha256"] != source["sha256"]
                                or result["path"] != call["args"]["path"]):
                            errors.append("read_observation")
                        reads += 1
                    elif message["name"] == "search_code":
                        for match in result["matches"]:
                            source = sources[match["path"]]
                            line = source["content"].splitlines()[match["line"] - 1]
                            if (match["text"] != line[:500] or match["sha256"] != source["sha256"]
                                    or call["args"]["keyword"] not in line):
                                errors.append("search_observation")
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
