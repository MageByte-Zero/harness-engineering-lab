"""Boundary and framework checks. Scripted messages do not measure model quality."""

import json
from pathlib import Path
import runpy
import subprocess
import sys

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
import pytest

from repo_agent.agent import build_agent
from repo_agent.tools import load_sources, repository_tools

FIXTURE = Path("fixtures/order-service")


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
    assert "class Real" in read.invoke({"path": "Real.java"})["content"]
    assert not search.invoke({"keyword": "SECRET"})["matches"]
    assert read.invoke({"path": "escape.java"})["status"] == "not_found"


@pytest.mark.parametrize("path", ["../outside.java", "/etc/passwd", ".env", "src/../../secret", "..\\secret"])
def test_path_escape_is_denied(path):
    _, read = repository_tools(load_sources(FIXTURE))
    assert read.invoke({"path": path}) == {"status": "denied"}


def test_real_read_search_and_missing_are_distinct():
    search, read = repository_tools(load_sources(FIXTURE))
    result = read.invoke({"path": "src/OrderController.java", "start_line": 17, "end_line": 20})
    assert result["status"] == "ok" and '17:     @GetMapping("/{id}")' in result["content"]
    assert result["truncated"] is True
    assert len(result["sha256"]) == 64
    found = search.invoke({"keyword": "/orders"})
    assert found["matches"][0]["path"] == "src/OrderController.java"
    assert found["matches"][0]["line"] == 9
    assert search.invoke({"keyword": "absent-unique-value"})["matches"] == []
    assert read.invoke({"path": "src/Absent.java"})["status"] == "not_found"
    assert read.invoke({"path": "src/Order.java", "start_line": 8})["status"] == "invalid_range"
    assert read.invoke({"path": "src/Order.java", "end_line": 121})["status"] == "invalid_range"


def test_literal_search_and_output_bound():
    search, _ = repository_tools({"a.java": "\n".join([".* --help"] * 25)})
    result = search.invoke({"keyword": ".*"})
    assert len(result["matches"]) == 20 and result["truncated"]
    assert search.invoke({"keyword": "--help"})["status"] == "ok"
    assert search.invoke({"keyword": ""})["status"] == "invalid_keyword"


def test_snapshot_limits_fail_explicitly(tmp_path):
    (tmp_path / "Huge.java").write_bytes(b"a" * 100_001)
    with pytest.raises(ValueError, match="size limit"):
        load_sources(tmp_path)


def test_missing_input_only_removes_exception_mapping():
    full = load_sources(FIXTURE)
    missing = load_sources(Path("fixtures/order-service-missing"))
    assert set(full) - set(missing) == {"src/ApiExceptionHandler.java"}
    assert all(full[path] == content for path, content in missing.items())


class ScriptedModel(FakeMessagesListChatModel):
    model_name: str = "scripted-test"
    openai_api_base: str = "https://example.invalid/v1"

    def bind_tools(self, tools, **kwargs):
        return self


def request(name, args, number):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{number}", "type": "tool_call"}])


def test_framework_returns_actual_tools_and_matching_ids():
    model = ScriptedModel(responses=[
        request("search_code", {"keyword": "/orders"}, 1),
        request("read_file", {"path": "src/OrderController.java"}, 2),
        AIMessage(content="测试替身回答，不代表语义验收"),
    ])
    result = build_agent(model, load_sources(FIXTURE)).invoke(
        {"messages": [{"role": "user", "content": "定位查询订单入口"}]})
    tools = [m for m in result["messages"] if m.type == "tool"]
    assert [m.tool_call_id for m in tools] == ["call-1", "call-2"]
    assert json.loads(tools[0].content)["matches"][0]["line"] == 9
    assert "service.find(id)" in json.loads(tools[1].content)["content"]
    assert result["messages"][-1].content == "测试替身回答，不代表语义验收"


@pytest.mark.parametrize("read_source,stop", [(True, "answer_returned"), (False, "no_source_read")])
def test_cli_retains_events_and_refuses_unread_answer(monkeypatch, tmp_path, read_source, stop):
    from repo_agent import model as model_module

    responses = ([request("read_file", {"path": "src/Order.java"}, 1)] if read_source else [])
    model = ScriptedModel(responses=[*responses, AIMessage(content="替身回答")])
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
    assert any(e["state"] is None for e in record["events"])
    assert record["sources"]["src/Order.java"]["sha256"]
    with pytest.raises(FileExistsError):
        runpy.run_path("examples/repo_reading/04_agent.py", run_name="__main__")


def test_model_limit_stops_instead_of_looping():
    model = ScriptedModel(responses=[request("search_code", {"keyword": "absent"}, i) for i in range(13)])
    with pytest.raises(Exception, match="Model call limits exceeded"):
        build_agent(model, load_sources(FIXTURE)).invoke(
            {"messages": [{"role": "user", "content": "找不存在的东西"}]}, {"recursion_limit": 80})


def test_search_failure_is_not_empty_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 2, "", "private error"))
    search, _ = repository_tools({"a.java": "class A {}"})
    assert search.invoke({"keyword": "class"}) == {"status": "unavailable", "matches": [], "reason": "search_failed"}


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
    calls = [request("read_file", {"path": "src/Order.java"}, i).tool_calls[0] for i in range(17)]
    record = run_scripted(monkeypatch, tmp_path, [AIMessage(content="", tool_calls=calls)])
    assert record["stop_reason"] == "tool_budget_exhausted"
    messages = [m for e in record["events"] for m in (e["state"] or {}).get("messages", [])]
    assert not [m for m in messages if m["type"] == "tool"]
    assert record["answer"] is None


def test_tool_failure_remains_visible_after_agent_returns_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 2, "", "SECRET"))
    record = run_scripted(monkeypatch, tmp_path, [
        request("search_code", {"keyword": "Order"}, 1),
        request("read_file", {"path": "src/Order.java"}, 2),
        AIMessage(content="搜索不可用，读取了已知文件，其他关系尚未确认"),
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
        def _generate(self, *args, **kwargs):
            raise APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))

    model = (TimeoutModel(responses=[AIMessage(content="unused")]) if failure == "model_timeout" else
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
        AIMessage(content="这是故意写错的回答：订单存储在 MySQL"),
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
