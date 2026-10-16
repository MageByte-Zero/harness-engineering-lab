"""Scripted models verify control paths, not live model quality."""

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from services.taskhub.app import app
from support_agent.agent import build_bounded_agent
from support_agent.execution import run_agent
from support_agent.tools import service_status_tool


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def request(tool, args, number):
    return AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": f"call-{number}", "type": "tool_call"}])


def test_service_status_endpoint():
    with TestClient(app) as client:
        assert client.get("/services/webhook-delivery/status").json()["status"] == "degraded"
        assert client.get("/services/unknown/status").status_code == 404


@pytest.mark.parametrize("model_limit,tool_limit,error,executed", [(1, 3, "ModelCallLimitExceededError", 1), (4, 1, "ToolCallLimitExceededError", 1)])
def test_repeated_no_progress_calls_are_bounded(model_limit, tool_limit, error, executed):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"delivery_id": "d-105", "http_status": 429})
    model = ScriptedModel(responses=[request("get_delivery", {"delivery_id": "d-105"}, n) for n in range(5)])
    with httpx.Client(base_url="http://taskhub", transport=httpx.MockTransport(handler)) as client:
        result = run_agent(build_bounded_agent(model, client, model_limit=model_limit, tool_limit=tool_limit), {"role": "user", "content": "查询 d-105"})
    assert result["stop_reason"] == "budget_exhausted"
    assert result["error"]["type"] == error
    assert len(calls) == executed
    assert result["events"] and result["business_review"] == "pending"


@pytest.mark.parametrize("failure,expected", [("timeout", "unavailable"), ("missing", "not_found")])
def test_failed_query_cannot_become_success_by_model_claim(failure, expected):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("injected timeout")
        return httpx.Response(404)
    model = ScriptedModel(responses=[request("get_service_status", {"service_id": "webhook-delivery"}, 1), AIMessage(content="任务成功")])
    with httpx.Client(base_url="http://taskhub", transport=httpx.MockTransport(handler)) as client:
        result = run_agent(build_bounded_agent(model, client), {"role": "user", "content": "查服务状态"})
    assert result["stop_reason"] == expected
    assert result["business_review"] == "pending"


def test_two_tools_then_answer_still_requires_business_review():
    def handler(request):
        return httpx.Response(200, json={"service_id": "webhook-delivery", "status": "degraded"})
    model = ScriptedModel(responses=[request("get_delivery", {"delivery_id": "d-105"}, 1), request("get_service_status", {"service_id": "webhook-delivery"}, 2), AIMessage(content="证据不足以确认根因")])
    with httpx.Client(base_url="http://taskhub", transport=httpx.MockTransport(handler)) as client:
        result = run_agent(build_bounded_agent(model, client), {"role": "user", "content": "查 d-105"})
    assert result["stop_reason"] == "answer_returned"
    assert len([event for event in result["events"] if event["message"]["type"] == "tool"]) == 2
    assert result["business_review"] == "pending"
