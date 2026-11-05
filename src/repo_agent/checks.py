"""Check retained observations against their snapshot; semantic review stays pending."""

import json
from pathlib import PurePosixPath

from agents import FunctionTool

from repo_agent.tools import SOURCE_SUFFIXES, repository_tools, snapshot_hash, source_hash
from repo_agent.retrieval import fixed_input, validate_queries


def check_prefetch(record):
    """Check the fixed plan using retained observations, without rerunning tools."""
    state = record['retrieval']
    pending = [('search_code', {'keyword': q}) for q in state['queries']]
    observations = [m for e in record['events'] for m in e['state']['messages'] if m['type'] == 'prefetch']
    matches, covered, read_phase = [], [], False
    limited, failed = False, False

    def schedule_reads():
        for match in matches:
            path, line = match['path'], match['line']
            if any(p == path and start <= line <= end for p, start, end in covered):
                continue
            start = max(1, line - 3)
            end = min(len(record['sources'][path]['content'].splitlines()), line + 3)
            pending.append(('read_file', {'path': path, 'start_line': start, 'end_line': end}))
            covered.append((path, start, end))

    for observation in observations:
        if not pending and not read_phase:
            schedule_reads()
            read_phase = True
        if failed or not pending or (observation['name'], observation['args']) != pending.pop(0):
            return False
        result = json.loads(observation['content'])
        failed = result['status'] != 'ok'
        if not failed and observation['name'] == 'search_code':
            matches.extend(result['matches'])
            limited |= result['truncated'] or any(m['excerpt_truncated'] for m in result['matches'])
        elif not failed:
            limited |= result['content_truncated']
    if not pending and not read_phase and not failed:
        schedule_reads()
    complete = not failed and (not pending or len(observations) == 16)
    limited |= bool(pending) and len(observations) == 16
    if state['complete'] is True:
        return complete and state['limited'] is bool(limited)
    return (state['complete'] is False and state['limited'] is False
            and record['stop_reason'] in ('tool_error', 'tool_timeout', 'cancelled')
            and not record['model_inputs'])


def check_record(record):
    errors = []
    requests, results, signatures = {}, set(), set()
    call_batches, tool_outputs = [], {}
    model_messages = reads = repeated = prefetched = 0
    prepared_inputs, blocked_inputs = None, 0
    api_reads, api_observations, observation_count = 0, [], 0
    try:
        sources = record["sources"]
        if not sources or any(source_hash(s["content"]) != s["sha256"] for s in sources.values()):
            errors.append("snapshot_hash")
        snapshot_id = snapshot_hash({path: source["content"] for path, source in sources.items()})
        fixed = record.get('retrieval', {}).get('strategy') == 'fixed'
        if fixed:
            validate_queries(record['retrieval']['queries'])
            if not check_prefetch(record):
                errors.append('prefetch_contract')
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
                    if 'output_contract' in record:
                        observation_count += 1
                        if result.get('observation_id') != f'obs-{observation_count}':
                            errors.append('observation_identity')
                    # SDK 0.24.0 serializes this app's dictionary results with str(), not JSON.
                    if call_id is not None:
                        tool_outputs[call_id] = str(result)
                    if message['name'] in ('get_order_status', 'get_order_events'):
                        from repo_agent.business import validate_data
                        business = record['business']
                        kind = 'status' if message['name'] == 'get_order_status' else 'events'
                        oid = call['args']['order_id']
                        path = f'/api/v1/orders/{oid}' + ('/events' if kind == 'events' else '')
                        if (business['contract'] != 'business-readonly-v1'
                                or result.get('scope') != 'business_readonly'
                                or result.get('kind') != kind or result.get('order_id') != oid
                                or not result.get('observed_at')
                                or result.get('status') not in ('ok', 'denied', 'invalid_argument',
                                    'unauthenticated', 'forbidden', 'not_found', 'rate_limited',
                                    'timeout', 'unavailable', 'service_error', 'invalid_response')):
                            errors.append('api_observation')
                        status = result['status']
                        http_status = result.get('http_status')
                        expected_codes = {'ok': 200, 'unauthenticated': 401, 'forbidden': 403,
                                          'not_found': 404, 'rate_limited': 429}
                        if ((status in expected_codes and http_status != expected_codes[status])
                                or (status in ('denied', 'invalid_argument') and
                                    (http_status is not None or result['source'] is not None))
                                or (status not in ('denied', 'invalid_argument') and
                                    (oid not in business['allowed_orders'] or result['source'] != path))):
                            errors.append('api_observation')
                        if result['status'] == 'ok':
                            if (oid not in business['allowed_orders'] or result['source'] != path
                                    or result['http_status'] != 200
                                    or validate_data(kind, result['data'], oid) != result['data']):
                                errors.append('api_observation')
                            api_reads += 1
                        elif 'data' in result:
                            errors.append('api_observation')
                        api_observations.append({k: v for k, v in result.items() if k != 'observation_id'})
                        continue
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
                if 'business' in record:
                    from repo_agent.business import BusinessClient, business_tools
                    expected_tools.extend({"name": t.name, "description": t.description,
                        "parameters": t.params_json_schema, "strict": t.strict_json_schema}
                        for t in business_tools(BusinessClient('http://127.0.0.1', 'offline-check')))
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
                    if 'output_contract' in record:
                        from repo_agent.output import output_contract
                        if payload.get('output_schema') != output_contract():
                            errors.append('context_output_schema')
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
                        or (record["stop_reason"] in ("answer_returned", "no_source_read", "no_observation", "no_answer", "output_invalid")
                            and allowed != model_messages)
                        or (blocked and decisions[-1] != "blocked")
                        or bool(blocked) != (record["stop_reason"] == "context_budget_exhausted")):
                    errors.append("context_sequence")
        if 'business' in record and sorted(json.dumps(x, sort_keys=True) for x in api_observations) != sorted(
                json.dumps(x, sort_keys=True) for x in record['business']['observations']):
            errors.append('api_observation_ledger')
        if not record.get("finished_at") or not record.get("stop_reason"):
            errors.append("unfinished_record")
        if record["stop_reason"] == "answer_returned":
            if not record.get("answer") or not (reads or api_reads) or set(requests) != results:
                errors.append("incomplete_answer_run")
        if 'output_contract' in record:
            from pydantic import ValidationError
            from repo_agent.output import InvestigationAnswer, assess_answer, output_contract
            if record['output_contract'] != output_contract():
                errors.append('output_contract')
            if record.get('answer_checks') != assess_answer(record):
                errors.append('answer_checks')
            if record.get('answer') is not None:
                last = record['events'][-1]['state']['messages'][-1]
                try:
                    parsed = InvestigationAnswer.model_validate_json(last['content'])
                    if (last['type'] != 'ai' or last.get('tool_calls')
                            or parsed.model_dump(mode='json') != record['answer']):
                        errors.append('final_output')
                except ValidationError:
                    errors.append('final_output')
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        errors.append("invalid_record")
    report = {"ok": not errors, "errors": sorted(set(errors)), "run_id": record.get("run_id"),
            "stop_reason": record.get("stop_reason"), "observed_model_messages": model_messages,
            "requested_tools": len(requests), "observed_tool_results": len(results),
            "prefetched_tools": prefetched,
            "successful_reads": reads, "successful_api_reads": api_reads, "repeated_requests": repeated,
            "prepared_model_inputs": prepared_inputs, "blocked_model_inputs": blocked_inputs,
            "pending_tool_ids": sorted(set(requests) - results), "semantic_review": "pending"}
    if 'output_contract' in record:
        from repo_agent.output import assess_answer
        try:
            report['answer_checks'] = assess_answer(record)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            report['ok'] = False
            report['errors'] = sorted(set([*report['errors'], 'invalid_answer_record']))
    return report
