"""Observe the framework loop and retain mechanical outcomes, never semantic grades."""

from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import platform
import subprocess
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langgraph.errors import GraphRecursionError
from openai import APITimeoutError

from repo_agent.agent import INSTRUCTIONS, build_agent
from repo_agent.tools import load_sources, source_hash
from team_harness import model as model_module


def run_repository(repo: Path, question: str, output_path: Path, *, task_id=None):
    if not question.strip():
        raise ValueError("Question must not be empty")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"run_id": str(uuid4()), "started_at": datetime.now(timezone.utc).isoformat(),
              "question": question, "task_id": task_id, "instructions": INSTRUCTIONS, "events": [],
              "answer": None, "stop_reason": None, "error": None, "review": None, "tool_issues": [],
              "python": platform.python_version(),
              "versions": {p: version(p) for p in ("langchain", "langchain-openai", "langgraph")}}
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
        try:
            sources = load_sources(repo)
            record["sources"] = {p: {"sha256": source_hash(c), "content": c} for p, c in sources.items()}
            phase = "model"
            model = model_module.create_model()
            record["model_config"] = {"requested_model": model.model_name,
                                      "endpoint_host": urlsplit(str(model.openai_api_base)).hostname,
                                      "protocol": "chat_completions", "timeout_seconds": 90,
                                      "max_retries": 0, "model_limit": 12, "tool_limit": 16}
            agent = build_agent(model, sources)
            read_observed = False
            save()
            for update in agent.stream(
                {"messages": [{"role": "user", "content": question}]},
                config={"recursion_limit": 80}, stream_mode="updates",
            ):
                for node, state in update.items():
                    record["events"].append({"node": node, "state": json.loads(json.dumps(
                        state, default=lambda item: item.model_dump(mode="json"), ensure_ascii=False))})
                    save()
                    for message in (state or {}).get("messages", []):
                        if message.type == "ai":
                            for call in message.tool_calls:
                                pending.add(call["id"])
                                print(f"[request] {call['id']} {call['name']} {call['args']}", flush=True)
                            if not message.tool_calls and message.content:
                                record["answer"] = message.content
                        elif message.type == "tool":
                            pending.discard(message.tool_call_id)
                            result = json.loads(message.content)
                            read_observed |= message.name == "read_file" and result.get("status") == "ok"
                            if result.get("status") != "ok":
                                record["tool_issues"].append({"call_id": message.tool_call_id,
                                    "tool": message.name, "status": result.get("status")})
                            print(f"[result] {message.tool_call_id} {message.name}: {result.get('status')}", flush=True)
                        save()
            record["stop_reason"] = ("answer_returned" if record["answer"] and read_observed
                                     else "no_source_read" if not read_observed else "no_answer")
        except (Exception, KeyboardInterrupt) as exc:
            if isinstance(exc, ModelCallLimitExceededError):
                stop = "model_budget_exhausted"
            elif isinstance(exc, ToolCallLimitExceededError):
                stop = "tool_budget_exhausted"
            elif isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
                stop = "model_timeout"
            elif isinstance(exc, subprocess.TimeoutExpired):
                stop = "tool_timeout"
            elif isinstance(exc, GraphRecursionError):
                stop = "runtime_limit"
            elif isinstance(exc, KeyboardInterrupt):
                stop = "cancelled"
            else:
                stop = "tool_error" if pending else "input_error" if phase == "input" else "model_error"
            # Error text/HTTP bodies can contain secrets; retain only safe error identifiers.
            record["error"] = {"type": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}
            record["stop_reason"] = stop
        finally:
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            save()
    print("Stop:", record["stop_reason"], "Record:", output_path)
    if record["answer"]:
        print(record["answer"])
    return record
