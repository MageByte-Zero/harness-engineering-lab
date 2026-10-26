"""Observe the SDK loop and retain mechanical outcomes, never semantic grades."""

from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
from urllib.parse import urlsplit
from uuid import uuid4

from agents import RunConfig, RunHooks, Runner
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
import httpx
from openai import APITimeoutError

from repo_agent.agent import INSTRUCTIONS, build_agent
from repo_agent.tools import load_sources, source_hash
from repo_agent import model as model_module

MODEL_LIMIT = 12
TOOL_LIMIT = 16


class ToolBudgetExceeded(RuntimeError):
    """The observed batch would exceed this application's tool-request budget."""


def run_repository(repo: Path, question: str, output_path: Path, *, task_id=None, instructions: str = INSTRUCTIONS):
    if not question.strip():
        raise ValueError("Question must not be empty")
    if not instructions.strip():
        raise ValueError("Instructions must not be empty")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"run_id": str(uuid4()), "started_at": datetime.now(timezone.utc).isoformat(),
              "runtime": "openai-agents", "event_format": "normalized-lifecycle-v1", "tool_contract_version": 2,
              "question": question, "task_id": task_id, "instructions": instructions, "events": [],
              "answer": None, "stop_reason": None, "error": None, "review": None, "tool_issues": [],
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
            sources = load_sources(repo)
            record["sources"] = {p: {"sha256": source_hash(c), "content": c} for p, c in sources.items()}
            phase = "model"
            model = model_module.create_model()
            record["model_config"] = {"requested_model": model.model,
                "endpoint_host": urlsplit(os.environ.get("MODEL_BASE_URL", "")).hostname,
                "protocol": "chat_completions", "timeout_seconds": 90, "max_retries": 0,
                "model_limit": MODEL_LIMIT, "tool_limit": TOOL_LIMIT, "remote_tracing": False}
            hooks = ObservationHooks()
            save()
            result = Runner.run_sync(build_agent(model, sources, instructions=instructions), question,
                max_turns=MODEL_LIMIT, hooks=hooks, run_config=RunConfig(tracing_disabled=True))
            record["answer"] = result.final_output
            record["stop_reason"] = ("answer_returned" if record["answer"] and hooks.read_observed
                                     else "no_source_read" if not hooks.read_observed else "no_answer")
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, MaxTurnsExceeded):
                stop = "model_budget_exhausted"
            elif isinstance(exc, ToolBudgetExceeded):
                stop = "tool_budget_exhausted"
            elif isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
                stop = "model_timeout"
            elif isinstance(exc, subprocess.TimeoutExpired) or isinstance(exc.__cause__, subprocess.TimeoutExpired):
                stop = "tool_timeout"
            elif isinstance(exc, KeyboardInterrupt):
                stop = "cancelled"
            elif isinstance(exc, ModelBehaviorError):
                stop = "model_error"
            else:
                stop = "tool_error" if pending else "input_error" if phase == "input" else "model_error"
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
