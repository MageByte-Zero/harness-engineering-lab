"""Record framework events and mechanical stop reasons; business review stays separate."""

import json

from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError


def run_agent(agent, message):
    record = {"input": message, "events": [], "answer": None, "stop_reason": None,
              "error": None, "business_review": "pending"}
    tool_issue = None
    try:
        for update in agent.stream({"messages": [message]}, stream_mode="updates"):
            for node, state in update.items():
                for item in (state or {}).get("messages", []):
                    record["events"].append({"node": node, "message": item.model_dump(mode="json")})
                    if item.type == "tool":
                        result = json.loads(item.content)
                        if result.get("status") in ("unavailable", "not_found"):
                            tool_issue = result["status"]
                    if item.type == "ai" and not item.tool_calls:
                        record["answer"] = item.content
        record["stop_reason"] = tool_issue or ("answer_returned" if record["answer"] else "no_answer")
    except (ModelCallLimitExceededError, ToolCallLimitExceededError) as exc:
        record["stop_reason"] = "budget_exhausted"
        record["error"] = {"type": type(exc).__name__}
    except Exception as exc:
        # Do not persist upstream exception bodies: they may contain secrets.
        record["stop_reason"] = "execution_error"
        record["error"] = {"type": type(exc).__name__}
    return record
