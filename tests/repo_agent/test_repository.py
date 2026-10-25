"""Boundary and framework checks. Scripted messages do not measure model quality."""

import asyncio
import json
from pathlib import Path
import runpy
import subprocess
import sys

from agents import Model, ModelResponse, Runner, RunConfig, Usage
from agents.exceptions import MaxTurnsExceeded
from agents.tool_context import ToolContext
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
import pytest

from repo_agent.agent import build_agent
from repo_agent.tools import load_sources, repository_tools

FIXTURE = Path("fixtures/order-service")


def invoke(tool, args):
    context = ToolContext(context=None, tool_call_id="boundary-test", tool_name=tool.name,
                          tool_arguments=json.dumps(args))
    return asyncio.run(tool.on_invoke_tool(context, json.dumps(args)))



def test_snapshot_excludes_secrets_links_and_freezes_content(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.java"
    outside.write_text("OUTSIDE_SECRET")
    (root / "Real.java").write_text("class Real {}\n")
    (root / ".env").write_text("MODEL_API_KEY=SECRET")
    (root / ".private").mkdir()
    (root / ".private" / "Hidden.java").write_text("HIDDEN_SECRET")
    (root / "escape.java").symlink_to(outside)
    (root / "linked").symlink_to(tmp_path, target_is_directory=True)
    sources = load_sources(root)
    assert sources == {"Real.java": "class Real {}\n"}
    search, read = repository_tools(sources)
    (root / "Real.java").write_text("CHANGED")
    sources["Real.java"] = "MUTATED"
    assert "class Real" in invoke(read, {"path": "Real.java"})["content"]
    assert not invoke(search, {"keyword": "SECRET"})["matches"]
    assert invoke(read, {"path": "escape.java"})["status"] == "not_found"


@pytest.mark.parametrize("path", ["../outside.java", "/etc/passwd", ".env", "src/../../secret", "..\\secret"])
def test_path_escape_is_denied(path):
    _, read = repository_tools(load_sources(FIXTURE))
    assert invoke(read, {"path": path}) == {"status": "denied"}


def test_real_read_search_and_missing_are_distinct():
    search, read = repository_tools(load_sources(FIXTURE))
    result = invoke(read, {"path": "src/OrderController.java", "start_line": 17, "end_line": 20})
    assert result["status"] == "ok" and '17:     @GetMapping("/{id}")' in result["content"]
    assert result["truncated"] is True
    assert len(result["sha256"]) == 64
    found = invoke(search, {"keyword": "/orders"})
    assert found["matches"][0]["path"] == "src/OrderController.java"
    assert found["matches"][0]["line"] == 9
    assert invoke(search, {"keyword": "absent-unique-value"})["matches"] == []
    assert invoke(read, {"path": "src/Absent.java"})["status"] == "not_found"
    assert invoke(read, {"path": "src/Order.java", "start_line": 8})["status"] == "invalid_range"
    assert invoke(read, {"path": "src/Order.java", "end_line": 121})["status"] == "invalid_range"


def test_literal_search_and_output_bound():
    search, _ = repository_tools({"a.java": "\n".join([".* --help"] * 25)})
    result = invoke(search, {"keyword": ".*"})
    assert len(result["matches"]) == 20 and result["truncated"]
    assert invoke(search, {"keyword": "--help"})["status"] == "ok"
    assert invoke(search, {"keyword": ""})["status"] == "invalid_keyword"


def test_snapshot_limits_fail_explicitly(tmp_path):
    (tmp_path / "Huge.java").write_bytes(b"a" * 100_001)
    with pytest.raises(ValueError, match="size limit"):
        load_sources(tmp_path)


def test_missing_input_only_removes_exception_mapping():
    full = load_sources(FIXTURE)
    missing = load_sources(Path("fixtures/order-service-missing"))
    assert set(full) - set(missing) == {"src/ApiExceptionHandler.java"}
    assert all(full[path] == content for path, content in missing.items())


class ScriptedModel(Model):
    model = "scripted-test"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.inputs = []

    async def get_response(self, system_instructions, input, *args, **kwargs):
        self.inputs.append(input)
        item = next(self.responses)
        return ModelResponse(output=item if isinstance(item, list) else [item],
                             usage=Usage(requests=1), response_id=None)

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("This application uses Runner lifecycle hooks, not token streaming")
        yield


def request(name, args, number):
    return ResponseFunctionToolCall(name=name, arguments=json.dumps(args),
                                    call_id=f"call-{number}", type="function_call")


def answer(text):
    return ResponseOutputMessage(id="message-test", role="assistant", status="completed",
        type="message", content=[ResponseOutputText(text=text, annotations=[], type="output_text")])


def test_framework_returns_actual_tools_and_matching_ids():
    model = ScriptedModel(responses=[
        request("search_code", {"keyword": "/orders"}, 1),
        request("read_file", {"path": "src/OrderController.java"}, 2),
        answer("测试替身回答，不代表语义验收"),
    ])
    result = Runner.run_sync(build_agent(model, load_sources(FIXTURE)), "定位查询订单入口",
                             max_turns=12, run_config=RunConfig(tracing_disabled=True))
    tools = [m for m in result.to_input_list() if m.get("type") == "function_call_output"]
    assert [m["call_id"] for m in tools] == ["call-1", "call-2"]
    # The SDK feeds actual tool observations to the next model call.
    assert model.inputs[-1] == result.to_input_list()[:-1]
    assert "service.find(id)" in str(tools[1]["output"])
    assert result.final_output == "测试替身回答，不代表语义验收"


@pytest.mark.parametrize("read_source,stop", [(True, "answer_returned"), (False, "no_source_read")])
def test_cli_retains_events_and_refuses_unread_answer(monkeypatch, tmp_path, read_source, stop):
    from repo_agent import model as model_module

    responses = ([request("read_file", {"path": "src/Order.java"}, 1)] if read_source else [])
    model = ScriptedModel(responses=[*responses, answer("替身回答")])
    monkeypatch.setattr(model_module, "create_model", lambda: model)
    output = tmp_path / "run.json"
    monkeypatch.setattr(sys, "argv", ["04_agent.py", "--repo", str(FIXTURE), "--output", str(output)])
    if read_source:
        runpy.run_path("examples/repo_reading/04_agent.py", run_name="__main__")
    else:
        with pytest.raises(SystemExit):
            runpy.run_path("examples/repo_reading/04_agent.py", run_name="__main__")
    record = json.loads(output.read_text())
    assert record["stop_reason"] == stop and record["review"] is None
    messages = [m for e in record["events"] for m in (e["state"] or {}).get("messages", [])]
    assert messages[-1]["content"] == "替身回答"
    assert record["runtime"] == "openai-agents"
    assert record["sources"]["src/Order.java"]["sha256"]
    with pytest.raises(FileExistsError):
        runpy.run_path("examples/repo_reading/04_agent.py", run_name="__main__")


def test_model_limit_stops_instead_of_looping():
    model = ScriptedModel(responses=[request("search_code", {"keyword": "absent"}, i) for i in range(13)])
    with pytest.raises(MaxTurnsExceeded):
        Runner.run_sync(build_agent(model, load_sources(FIXTURE)), "找不存在的东西",
                        max_turns=12, run_config=RunConfig(tracing_disabled=True))
    assert len(model.inputs) == 12


def test_search_failure_is_not_empty_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 2, "", "private error"))
    search, _ = repository_tools({"a.java": "class A {}"})
    assert invoke(search, {"keyword": "class"}) == {"status": "unavailable", "matches": [], "reason": "search_failed"}


def run_scripted(monkeypatch, tmp_path, responses):
    from repo_agent.execution import run_repository
    from repo_agent import model as model_module

    monkeypatch.setattr(model_module, "create_model", lambda: ScriptedModel(responses=responses))
    output = tmp_path / "run.json"
    record = run_repository(FIXTURE, "检查订单实现", output, task_id="test-only")
    assert json.loads(output.read_text()) == record
    assert record["review"] is None
    return record


def test_model_budget_retains_observations_without_claiming_completion(monkeypatch, tmp_path):
    record = run_scripted(monkeypatch, tmp_path, [
        request("search_code", {"keyword": "absent"}, i) for i in range(13)
    ])
    assert record["stop_reason"] == "model_budget_exhausted"
    messages = [m for e in record["events"] for m in (e["state"] or {}).get("messages", [])]
    assert len([m for m in messages if m["type"] == "tool"]) == 12
    assert record["answer"] is None and record["finished_at"]


def test_tool_budget_blocks_batch_before_unrequested_disk_reads(monkeypatch, tmp_path):
    calls = [request("read_file", {"path": "src/Order.java"}, i) for i in range(17)]
    record = run_scripted(monkeypatch, tmp_path, [calls])
    assert record["stop_reason"] == "tool_budget_exhausted"
    messages = [m for e in record["events"] for m in (e["state"] or {}).get("messages", [])]
    assert not [m for m in messages if m["type"] == "tool"]
    assert record["answer"] is None


def test_tool_failure_remains_visible_after_agent_returns_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 2, "", "SECRET"))
    record = run_scripted(monkeypatch, tmp_path, [
        request("search_code", {"keyword": "Order"}, 1),
        request("read_file", {"path": "src/Order.java"}, 2),
        answer("搜索不可用，读取了已知文件，其他关系尚未确认"),
    ])
    assert record["stop_reason"] == "answer_returned"
    assert record["tool_issues"] == [{"call_id": "call-1", "tool": "search_code", "status": "unavailable"}]
    assert "SECRET" not in json.dumps(record)


@pytest.mark.parametrize("failure,stop", [("search_timeout", "tool_timeout"), ("search_error", "tool_error"),
                                         ("model_timeout", "model_timeout")])
def test_failure_is_saved_and_never_mistaken_for_empty_search(monkeypatch, tmp_path, failure, stop):
    from repo_agent.execution import run_repository
    from repo_agent import model as model_module
    import httpx
    from openai import APITimeoutError

    def fail_search(*args, **kwargs):
        if failure == "search_timeout":
            raise subprocess.TimeoutExpired("rg", 3, output="SECRET")
        raise OSError("SECRET")

    class TimeoutModel(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            raise APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))

    model = (TimeoutModel(responses=[answer("unused")]) if failure == "model_timeout" else
             ScriptedModel(responses=[request("search_code", {"keyword": "Order"}, 1)]))
    monkeypatch.setattr(model_module, "create_model", lambda: model)
    monkeypatch.setattr(subprocess, "run", fail_search)
    output = tmp_path / "run.json"
    record = run_repository(FIXTURE, "检查订单", output)
    assert record["stop_reason"] == stop and record["error"]["type"]
    assert record["answer"] is None and "SECRET" not in output.read_text()
    assert json.loads(output.read_text())["events"] == record["events"]


def test_record_checks_detect_corruption_and_do_not_grade_semantics(monkeypatch, tmp_path):
    from repo_agent.checks import check_record
    record = run_scripted(monkeypatch, tmp_path, [
        request("read_file", {"path": "src/Order.java"}, 1),
        answer("这是故意写错的回答：订单存储在 MySQL"),
    ])
    report = check_record(record)
    assert report["ok"] and report["semantic_review"] == "pending"
    messages = [m for e in record["events"] for m in (e["state"] or {}).get("messages", [])]
    tool_message = next(m for m in messages if m["type"] == "tool")
    tool_message["tool_call_id"] = "fabricated"
    assert not check_record(record)["ok"]
    tool_message["tool_call_id"] = "call-1"
    record["sources"]["src/Order.java"]["content"] = "CORRUPTED"
    assert not check_record(record)["ok"]


def test_tool_budget_is_cumulative_and_keeps_completed_observations(monkeypatch, tmp_path):
    first = [request("read_file", {"path": "src/Order.java"}, i) for i in range(9)]
    second = [request("read_file", {"path": "src/Order.java"}, i) for i in range(9, 17)]
    record = run_scripted(monkeypatch, tmp_path, [first, second])
    from repo_agent.checks import check_record
    report = check_record(record)
    assert record["stop_reason"] == "tool_budget_exhausted"
    assert report["ok"] and report["observed_tool_results"] == 9
    assert len(report["pending_tool_ids"]) == 8 and record["answer"] is None


def test_cancel_saves_record_and_releases_output_file(monkeypatch, tmp_path):
    from repo_agent import model as model_module
    from repo_agent.execution import run_repository

    monkeypatch.setattr(model_module, "create_model", lambda: ScriptedModel([]))
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(Runner, "run_sync", interrupt)
    path = tmp_path / "cancelled.json"
    record = run_repository(FIXTURE, "check", path)
    assert record["stop_reason"] == "cancelled" and record["finished_at"]
    assert json.loads(path.read_text()) == record


@pytest.mark.parametrize("bad_arguments", ['{"keyword":', '[]'])
def test_malformed_call_retains_entire_received_batch_before_stopping(monkeypatch, tmp_path, bad_arguments):
    from repo_agent.checks import check_record
    bad = ResponseFunctionToolCall(name="search_code", arguments=bad_arguments,
                                   call_id="call-malformed", type="function_call")
    record = run_scripted(monkeypatch, tmp_path, [[
        request("read_file", {"path": "src/Order.java"}, 1), bad,
    ]])
    messages = [m for e in record["events"] for m in e["state"]["messages"]]
    assert len(messages) == 1
    calls = messages[0]["tool_calls"]
    assert [c["id"] for c in calls] == ["call-1", "call-malformed"]
    assert calls[1]["arguments_raw"] == bad_arguments and calls[1]["argument_error"]
    report = check_record(record)
    assert report["ok"] and report["observed_tool_results"] == 0
    assert record["stop_reason"] == "model_error" and record["answer"] is None
