"""Check retained observations against their snapshot; semantic review stays pending."""

import json
from pathlib import PurePosixPath

from agents import FunctionTool

from repo_agent.tools import SOURCE_SUFFIXES, repository_tools, snapshot_hash, source_hash
from repo_agent.retrieval import fixed_input, validate_queries


def check_record(record):
    errors = []
    requests, results, signatures = {}, set(), set()
    call_batches, tool_outputs = [], {}
    model_messages = reads = repeated = prefetched = 0
    prepared_inputs, blocked_inputs = None, 0
    try:
        sources = record["sources"]
        if not sources or any(source_hash(s["content"]) != s["sha256"] for s in sources.values()):
            errors.append("snapshot_hash")
        snapshot_id = snapshot_hash({path: source["content"] for path, source in sources.items()})
        fixed = record.get('retrieval', {}).get('strategy') == 'fixed'
        if fixed:
            validate_queries(record['retrieval']['queries'])
        for event in record["events"]:
            for message in (event["state"] or {}).get("messages", []):
                if message["type"] == "ai":
                    model_messages += 1
                    call_batches.append(message.get("tool_calls", []))
                    for call in message.get("tool_calls", []):
                        if call["id"] in requests:
                            errors.append("duplicate_call_id")
                        requests[call["id"]] = call
                        signature = (call["name"], json.dumps(call["args"], sort_keys=True))
                        repeated += signature in signatures
                        signatures.add(signature)
                elif message["type"] in ("tool", "prefetch"):
                    if message['type'] == 'prefetch':
                        prefetched += 1
                        if not fixed or message['name'] not in ('search_code', 'read_file') or prefetched > 16:
                            errors.append('prefetch_contract')
                        call = {'name': message['name'], 'args': message['args']}
                        call_id = None
                    else:
                        call_id = message["tool_call_id"]
                        call = requests.get(call_id)
                        if not call or call["name"] != message["name"] or call_id in results:
                            errors.append("tool_call_id")
                            continue
                        results.add(call_id)
                    result = json.loads(message["content"])
                    # SDK 0.24.0 serializes this app's dictionary results with str(), not JSON.
                    if call_id is not None:
                        tool_outputs[call_id] = str(result)
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
        if "context_format" in record:
            if record["context_format"] != "sdk-input-v1":
                errors.append("context_format")
            snapshots, limit = record["model_inputs"], record["context_char_limit"]
            if not isinstance(snapshots, list) or not isinstance(limit, int) or limit <= 0:
                errors.append("context_budget")
            else:
                prepared_inputs = len(snapshots)
                expected_tools = [{"name": t.name, "description": t.description,
                                   "parameters": t.params_json_schema, "strict": t.strict_json_schema}
                                  for t in repository_tools({p: s["content"] for p, s in sources.items()})
                                  if isinstance(t, FunctionTool)]
                if fixed:
                    expected_tools = []
                expected_input = (fixed_input(record['question'], record) if fixed else
                                  [{'role': 'user', 'content': record['question']}])
                decisions = []
                for index, snapshot in enumerate(snapshots, 1):
                    payload = snapshot["payload"]
                    chars = len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                    decision = "blocked" if chars > limit else "allowed"
                    decisions.append(snapshot["decision"])
                    if (snapshot["call_index"] != index or snapshot["chars"] != chars
                            or snapshot["decision"] != decision):
                        errors.append("context_budget")
                    items = payload["input"]
                    if (payload["instructions"] != record["instructions"]
                            or payload["tools"] != expected_tools
                            or (index == 1 and items != expected_input)
                            or [i for i in items if i.get("role") == "user"]
                            != expected_input):
                        errors.append("context_input")
                    expected = {c["id"]: c for batch in call_batches[:index - 1] for c in batch}
                    calls = [i for i in items if i.get("type") == "function_call"]
                    outputs = [i for i in items if i.get("type") == "function_call_output"]
                    if ({c["call_id"] for c in calls} != set(expected) or len(calls) != len(expected)
                            or {o["call_id"] for o in outputs} != set(expected) or len(outputs) != len(expected)):
                        errors.append("context_history")
                    for call in calls:
                        original = expected.get(call["call_id"])
                        if (not original or original["name"] != call["name"]
                                or original["args"] != json.loads(call["arguments"])):
                            errors.append("context_history")
                    for output in outputs:
                        if output["output"] != tool_outputs.get(output["call_id"]):
                            errors.append("context_observation")
                allowed, blocked = decisions.count("allowed"), decisions.count("blocked")
                blocked_inputs = blocked
                if (allowed not in (model_messages, model_messages + 1) or blocked > 1
                        or (record["stop_reason"] in ("answer_returned", "no_source_read", "no_answer")
                            and allowed != model_messages)
                        or (blocked and decisions[-1] != "blocked")
                        or bool(blocked) != (record["stop_reason"] == "context_budget_exhausted")):
                    errors.append("context_sequence")
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
            "prefetched_tools": prefetched,
            "successful_reads": reads, "repeated_requests": repeated,
            "prepared_model_inputs": prepared_inputs, "blocked_model_inputs": blocked_inputs,
            "pending_tool_ids": sorted(set(requests) - results), "semantic_review": "pending"}
