from pathlib import Path
from unittest.mock import Mock

import pytest

from support_agent.models import ask, build_input, create_client, load_documents


def test_material_and_no_material_are_distinct():
    docs = load_documents(Path("data/docs"))
    assert len(docs) == 2
    assert "HMAC-SHA256" in build_input("签名如何验证？", docs)
    assert "HMAC-SHA256" not in build_input("签名如何验证？", [])
    assert all(len(doc["sha256"]) == 64 for doc in docs)


def test_empty_inputs_fail(tmp_path):
    with pytest.raises(ValueError):
        build_input("  ", [])
    with pytest.raises(ValueError):
        load_documents(tmp_path)


def test_request_and_raw_response_are_retained():
    client = Mock()
    client.chat.completions.create.return_value.choices = [Mock(message=Mock(content="这是测试替身输出"))]
    client.chat.completions.create.return_value.model_dump.return_value = {"id": "mock-response"}
    record = ask(client, "test-model", "问题", [], instructions="测试指令")
    client.chat.completions.create.assert_called_once_with(**record["request"])
    assert record["request"]["messages"][0]["content"] == "测试指令"
    assert record["response"] == {"id": "mock-response"}
    assert record["review"] is None


def test_missing_key_fails_before_network(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="MODEL_API_KEY"):
        create_client()


def test_base_url_is_required(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODEL_API_KEY", "test-only")
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="MODEL_BASE_URL"):
        create_client()


def test_client_uses_explicit_endpoint(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODEL_API_KEY", "test-only")
    monkeypatch.setenv("MODEL_BASE_URL", "https://example.invalid/v1")
    for name in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    with create_client() as client:
        assert str(client.base_url) == "https://example.invalid/v1/"
        assert client.max_retries == 0
