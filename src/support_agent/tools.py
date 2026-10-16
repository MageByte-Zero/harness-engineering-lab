"""Read-only business tools. The application supplies the HTTP client."""

from typing import Annotated

import httpx
from langchain.tools import tool
from pydantic import Field


def delivery_tool(client: httpx.Client):
    @tool
    def get_delivery(delivery_id: Annotated[str, Field(pattern=r"^d-[0-9]+$")]) -> dict:
        """查询指定投递的当前记录。仅在用户要了解具体投递时使用；参数是 d-数字 格式的投递 ID。查不到不代表投递成功。"""
        try:
            response = client.get(f"/deliveries/{delivery_id}")
            if response.status_code == 404:
                return {"status": "not_found", "delivery_id": delivery_id}
            response.raise_for_status()
            return {"status": "ok", "delivery": response.json()}
        except httpx.HTTPError as exc:
            return {"status": "unavailable", "error_type": type(exc).__name__}

    return get_delivery


def service_status_tool(client: httpx.Client):
    @tool
    def get_service_status(service_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]) -> dict:
        """查询投递记录中 service_id 对应服务的当前运行状态。429 需要结合服务状态判断，不把当前状态当成历史根因。"""
        try:
            response = client.get(f"/services/{service_id}/status")
            if response.status_code == 404:
                return {"status": "not_found", "service_id": service_id}
            response.raise_for_status()
            return {"status": "ok", "service": response.json()}
        except httpx.HTTPError as exc:
            return {"status": "unavailable", "error_type": type(exc).__name__}

    return get_service_status
