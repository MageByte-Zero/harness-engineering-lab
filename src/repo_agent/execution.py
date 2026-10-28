"""Observe the SDK loop and retain mechanical outcomes, never semantic grades."""

import asyncio
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
from urllib.parse import urlsplit
from uuid import uuid4

from agents import FunctionTool, RunConfig, RunHooks, Runner
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from agents.tool_context import ToolContext
import httpx
from openai import APITimeoutError

from repo_agent.agent import INSTRUCTIONS, build_agent
from repo_agent.tools import load_source_bundle, load_sources, source_hash
from repo_agent.retrieval import collect_fixed, fixed_input, validate_queries
from repo_agent import model as model_module

MODEL_LIMIT = 12
TOOL_LIMIT = 16


class ToolBudgetExceeded(RuntimeError):
    """The observed batch would exceed this application's tool-request budget."""


class ContextBudgetExceeded(RuntimeError):
    """The prepared SDK input exceeds the application's character budget."""


def run_repository(repo: Path | dict[str, Path], question: str, output_path: Path, *, task_id=None,
                   instructions: str = INSTRUCTIONS, context_char_limit: int = 60000,
                   retrieval_queries: tuple[str, ...] | None = None):
    if not question.strip():
        raise ValueError("Question must not be empty")
    if not instructions.strip():
        raise ValueError("Instructions must not be empty")
    if context_char_limit <= 0:
        raise ValueError("Context character limit must be positive")
    if retrieval_queries is not None:
        validate_queries(retrieval_queries)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"run_id": str(uuid4()), "started_at": datetime.now(timezone.utc).isoformat(),
              "runtime": "openai-agents", "event_format": "normalized-lifecycle-v1", "tool_contract_version": 2,
              "question": question, "task_id": task_id, "instructions": instructions, "events": [],
              "answer": None, "stop_reason": None, "error": None, "review": None, "tool_issues": [],
              "context_format": "sdk-input-v1", "context_char_limit": context_char_limit, "model_inputs": [],
              "python": platform.python_version(),
              "versions": {p: version(p) for p in ("openai-agents", "openai", "pydantic")}}
    # Reserve before calling a paid API; a typo must not overwrite a previous run.
    with output_path.open("x", encoding="utf-8") as output:
        def save():
            output.seek(0)
            json.dump(record, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.truncate()
            output.flush()

        save()
        pending = set()
        phase = "input"

        class ObservationHooks(RunHooks):
            requested = 0
            read_observed = False

            async def on_llm_start(self, context, agent, system_prompt, input_items):
                tools = []
                # Dynamic/MCP tools would need their resolved declarations captured here.
                for tool in agent.tools:
                    if not isinstance(tool, FunctionTool):
                        raise TypeError("Repository input capture requires static function tools")
                    tools.append({"name": tool.name, "description": tool.description,
                                  "parameters": tool.params_json_schema, "strict": tool.strict_json_schema})
                payload = {"instructions": system_prompt, "input": input_items, "tools": tools}
                serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                blocked = len(serialized) > context_char_limit
                record["model_inputs"].append({"call_index": len(record["model_inputs"]) + 1,
                    "payload": json.loads(serialized), "chars": len(serialized),
                    "decision": "blocked" if blocked else "allowed"})
                save()
                if blocked:
                    raise ContextBudgetExceeded()

            async def on_llm_end(self, context, agent, response):
                calls, text = [], []
                for item in response.output:
                    if item.type == "function_call":
                        call = {"id": item.call_id, "name": item.name, "args": None}
                        try:
                            call["args"] = json.loads(item.arguments)
                            if not isinstance(call["args"], dict):
                                call["argument_error"] = "not_an_object"
                        except json.JSONDecodeError:
                            call["argument_error"] = "invalid_json"
                        if "argument_error" in call:
                            call["arguments_raw"] = item.arguments
                        calls.append(call)
                    elif item.type == "message":
                        text.extend(part.text for part in item.content if part.type == "output_text")
                record["events"].append({"node": "model", "state": {"messages": [
                    {"type": "ai", "content": "\n".join(text), "tool_calls": calls}]}})
                for call in calls:
                    pending.add(call["id"])
                    print(f"[request] {call['id']} {call['name']} {call['args']}", flush=True)
                self.requested += len(calls)
                save()
                if any("argument_error" in call for call in calls):
                    raise ModelBehaviorError("Function arguments must be a JSON object")
                # SDK calls this hook before executing the returned batch.
                if self.requested > TOOL_LIMIT:
                    raise ToolBudgetExceeded()

            async def on_tool_end(self, context, agent, tool, result):
                if not isinstance(context, ToolContext) or not isinstance(result, dict):
                    raise TypeError("Repository tools require a tool-call context and a dictionary result")
                pending.discard(context.tool_call_id)
                self.read_observed |= tool.name == "read_file" and result.get("status") == "ok"
                if result.get("status") != "ok":
                    record["tool_issues"].append({"call_id": context.tool_call_id,
                                                "tool": tool.name, "status": result.get("status")})
                record["events"].append({"node": "tools", "state": {"messages": [
                    {"type": "tool", "name": tool.name, "tool_call_id": context.tool_call_id,
                     "content": json.dumps(result, ensure_ascii=False)}]}})
                print(f"[result] {context.tool_call_id} {tool.name}: {result.get('status')}", flush=True)
                save()

        try:
            sources = load_source_bundle(repo) if isinstance(repo, dict) else load_sources(repo)
            record["sources"] = {p: {"sha256": source_hash(c), "content": c} for p, c in sources.items()}
            hooks = ObservationHooks()
            sdk_input = question
            if retrieval_queries is not None:
                phase = 'retrieval'
                record['retrieval'] = {'strategy': 'fixed', 'queries': list(retrieval_queries),
                                       'complete': False, 'limited': False}

                def retain(observation):
                    hooks.read_observed |= (observation['name'] == 'read_file'
                                            and observation['result']['status'] == 'ok')
                    record['events'].append({'node': 'retrieval', 'state': {'messages': [
                        {'type': 'prefetch', 'name': observation['name'], 'args': observation['args'],
                         'content': json.dumps(observation['result'], ensure_ascii=False)}]}})
                    save()

                save()
                record['retrieval']['limited'] = asyncio.run(collect_fixed(
                    sources, retrieval_queries, limit=TOOL_LIMIT, emit=retain))
                record['retrieval']['complete'] = True
                sdk_input = fixed_input(question, record)
            phase = "model"
            model = model_module.create_model()
            record["model_config"] = {"requested_model": model.model,
                "endpoint_host": urlsplit(os.environ.get("MODEL_BASE_URL", "")).hostname,
                "protocol": "chat_completions", "timeout_seconds": 90, "max_retries": 0,
                "model_limit": MODEL_LIMIT, "tool_limit": TOOL_LIMIT, "remote_tracing": False}
            agent = build_agent(model, sources, instructions=instructions)
            if retrieval_queries is not None:
                agent.tools = []
            save()
            result = Runner.run_sync(agent, sdk_input,
                max_turns=MODEL_LIMIT, hooks=hooks, run_config=RunConfig(tracing_disabled=True))
            record["answer"] = result.final_output
            record["stop_reason"] = ("answer_returned" if record["answer"] and hooks.read_observed
                                     else "no_source_read" if not hooks.read_observed else "no_answer")
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, MaxTurnsExceeded):
                stop = "model_budget_exhausted"
            elif isinstance(exc, ToolBudgetExceeded):
                stop = "tool_budget_exhausted"
            elif isinstance(exc, ContextBudgetExceeded):
                stop = "context_budget_exhausted"
            elif isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
                stop = "model_timeout"
            elif isinstance(exc, subprocess.TimeoutExpired) or isinstance(exc.__cause__, subprocess.TimeoutExpired):
                stop = "tool_timeout"
            elif isinstance(exc, KeyboardInterrupt):
                stop = "cancelled"
            elif isinstance(exc, ModelBehaviorError):
                stop = "model_error"
            else:
                stop = ("tool_error" if pending or phase == 'retrieval'
                        else "input_error" if phase == "input" else "model_error")
            # Error text/HTTP bodies can contain secrets; retain only safe identifiers.
            record["error"] = {"type": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}
            record["stop_reason"] = stop
        finally:
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            save()
    print("Stop:", record["stop_reason"], "Record:", output_path)
    if record["answer"]:
        print(record["answer"])
    return record
