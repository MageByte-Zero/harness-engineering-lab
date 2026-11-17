"""Fixed local MCP bridge. The application keeps its business/result contract."""
import asyncio
from datetime import datetime, timezone
import json
import os
import re
import sys

from agents.mcp import MCPServerStdio
from mcp.server.mcpserver import MCPServer

from repo_agent.business import BusinessClient, validate_data

NAMES = {'status': 'get_order_status', 'events': 'get_order_events'}
CONTRACT = 'business-mcp-stdio-v1'


def checked_catalog(tools):
    """Unknown capabilities are not exposed; required tools must have the expected input."""
    catalog = []
    for name in sorted(NAMES.values()):
        matches = [t for t in tools if t.name == name]
        if len(matches) != 1:
            raise ValueError('Required MCP tool missing or duplicated')
        tool = matches[0]
        schema = tool.input_schema
        properties = schema.get('properties')
        if not isinstance(properties, dict) or set(properties) != {'order_id'}:
            raise ValueError('MCP tool parameters changed')
        def annotations_removed(value):
            return {k: v for k, v in value.items() if k not in ('title', 'description')}
        normalized = annotations_removed(schema)
        normalized['properties'] = {'order_id': annotations_removed(properties['order_id'])}
        expected = {'type': 'object', 'properties': {'order_id': {'type': 'string'}},
                    'required': ['order_id']}
        if normalized != expected:
            raise ValueError('MCP tool input contract changed')
        # Remote descriptions are retained for inspection, not promoted to system instructions.
        catalog.append({'name': name, 'input_schema': schema})
    return catalog


class MCPBusinessClient:
    def __init__(self, base_url, token):
        bound = BusinessClient(base_url, token)
        self.base_url, self.allowed_orders = bound.base_url, bound.allowed_orders
        self.observations = []
        self.catalog = []
        env = {k: v for k, v in os.environ.items() if not k.startswith(('MODEL_', 'OPENAI_', 'BUSINESS_'))}
        env.update(BUSINESS_API_URL=bound.base_url, BUSINESS_API_TOKEN=token)
        self.server = MCPServerStdio(params={'command': sys.executable,
            'args': ['-m', 'repo_agent.mcp_business'], 'env': env},
            name='teaching-business', client_session_timeout_seconds=5, max_retry_attempts=0)

    async def __aenter__(self):
        await self.server.__aenter__()
        try:
            self.catalog = checked_catalog(await self.server.list_tools())
        except BaseException:
            await self.server.__aexit__(*sys.exc_info())
            raise
        return self

    async def __aexit__(self, *exc):
        return await self.server.__aexit__(*exc)

    async def query(self, kind, order_id):
        if kind not in NAMES:
            raise ValueError('Unknown query kind')
        result = {'status': 'denied', 'scope': 'business_readonly', 'kind': kind,
            'order_id': order_id, 'source': None,
            'observed_at': datetime.now(timezone.utc).isoformat(), 'http_status': None}
        if not re.fullmatch(r'o-[0-9]{1,8}', order_id):
            result['status'] = 'invalid_argument'
        elif order_id in self.allowed_orders:
            result['source'] = f'/api/v1/orders/{order_id}' + ('/events' if kind == 'events' else '')
            try:
                try:
                    response = await self.server.call_tool(NAMES[kind], {'order_id': order_id})
                except Exception:
                    result['status'] = 'unavailable'
                    self.observations.append(result.copy())
                    return result
                if response.is_error:
                    raise ValueError('MCP tool returned an error')
                remote = response.structured_content
                if remote is None:
                    if (len(response.content) != 1 or response.content[0].type != 'text'
                            or len(response.content[0].text) > 20000):
                        raise ValueError('Invalid MCP content')
                    remote = json.loads(response.content[0].text)
                if (remote.get('scope') != 'business_readonly' or remote.get('kind') != kind
                        or remote.get('order_id') != order_id or remote.get('source') != result['source']):
                    raise ValueError('MCP response scope mismatch')
                status = remote.get('status')
                codes = {'ok':200,'unauthenticated':401,'forbidden':403,'not_found':404,'rate_limited':429}
                if status not in (*codes, 'timeout','unavailable','service_error','invalid_response'):
                    raise ValueError('MCP response status invalid')
                code = remote.get('http_status')
                if code is not None and (type(code) is not int or not 100 <= code <= 599):
                    raise ValueError('MCP HTTP status must be an integer or None')
                if status == 'service_error' and (code is None or code in codes.values()):
                    raise ValueError('MCP service error code mismatch')
                if status in ('timeout', 'unavailable', 'invalid_response') and code not in (None, 200):
                    raise ValueError('MCP failure code mismatch')
                if status in codes and code != codes[status]:
                    raise ValueError('MCP response code mismatch')
                stamp = remote['observed_at']
                if datetime.fromisoformat(stamp.replace('Z','+00:00')).tzinfo is None:
                    raise ValueError('MCP observation time invalid')
                result.update(status=status, http_status=remote.get('http_status'), observed_at=stamp)
                if status == 'ok':
                    result['data'] = validate_data(kind, remote['data'], order_id)
                elif 'data' in remote:
                    raise ValueError('Failed MCP observation contains data')
            except asyncio.CancelledError:
                raise
            except Exception:
                # No arbitrary remote error text or response body is trusted as a business observation.
                result.update(status='invalid_response', http_status=None)
                result.pop('data', None)
        self.observations.append(result.copy())
        return result


def serve():
    client = BusinessClient(os.environ['BUSINESS_API_URL'], os.environ['BUSINESS_API_TOKEN'])
    app = MCPServer('teaching-business')

    @app.tool()
    async def get_order_status(order_id: str) -> dict:
        """Read the permitted synthetic order's current status."""
        return await client.query('status', order_id)

    @app.tool()
    async def get_order_events(order_id: str) -> dict:
        """Read the permitted synthetic order's available events."""
        return await client.query('events', order_id)

    app.run(transport='stdio')


if __name__ == '__main__':
    serve()
