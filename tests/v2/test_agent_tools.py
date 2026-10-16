import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.taskhub.app import app
from support_agent.tools import delivery_tool


def test_service_returns_only_bound_project():
    with TestClient(app) as client:
        assert client.get("/deliveries/d-104").json()["http_status"] == 401
        assert client.get("/deliveries/d-900").status_code == 404
        assert client.get("/deliveries/d-999").status_code == 404
        assert client.get("/deliveries/d-900?project_id=other-project").status_code == 404


def test_tool_does_not_expose_identity_or_url():
    with httpx.Client(trust_env=False) as client:
        schema = delivery_tool(client).args_schema.model_json_schema()
        assert set(schema["properties"]) == {"delivery_id"}
        with pytest.raises(ValidationError):
            delivery_tool(client).invoke({"delivery_id": "../private"})


@pytest.mark.parametrize("status,expected", [(200, "ok"), (404, "not_found"), (503, "unavailable")])
def test_http_status_mapping(status, expected):
    def handler(request):
        assert request.url.path == "/deliveries/d-104"
        return httpx.Response(status, json={"delivery_id": "d-104", "http_status": 401})
    with httpx.Client(base_url="http://taskhub", transport=httpx.MockTransport(handler)) as client:
        assert delivery_tool(client).invoke({"delivery_id": "d-104"})["status"] == expected


def test_timeout_is_not_a_delivery_status():
    def handler(request):
        raise httpx.ReadTimeout("injected timeout")
    with httpx.Client(base_url="http://taskhub", transport=httpx.MockTransport(handler)) as client:
        result = delivery_tool(client).invoke({"delivery_id": "d-104"})
        assert result == {"status": "unavailable", "error_type": "ReadTimeout"}
