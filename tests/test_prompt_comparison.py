import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx2 as httpx
import pytest
from openai import APITimeoutError


spec = importlib.util.spec_from_file_location("prompt_example", Path(__file__).parents[1] / "examples/v2/02_prompt.py")
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def test_comparison_changes_only_instructions_and_keeps_failed_trial(tmp_path):
    calls = []
    def create(**request):
        calls.append(request)
        if len(calls) == 2:
            raise APITimeoutError(request=httpx.Request("POST", "https://example.test"))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))],
                               model_dump=lambda **kwargs: {"model": "test"})
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    output = tmp_path / "comparison"
    assert example.compare(client, "test", [], [{"id": "case", "question": "question"}], "new instructions", output) == 1
    assert len(calls) == 4
    assert all(call["messages"][1] == calls[0]["messages"][1] for call in calls)
    records = [json.loads(p.read_text()) for p in output.glob("*.json")]
    assert len(records) == 4
    assert sum(record.get("error", {}).get("type") == "APITimeoutError" for record in records) == 1
    with pytest.raises(FileExistsError):
        example.compare(client, "test", [], [], "new", output)
    assert len(calls) == 4
