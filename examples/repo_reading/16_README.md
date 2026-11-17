# Local read-only MCP business bridge

Fixed release: `lesson-16-mcp-20261117`. See the root README for clone/configuration prerequisites.

From the repository root with the existing MODEL_* configuration:

```bash
uv sync --frozen
uv run --frozen python -m pytest -q tests/repo_agent/test_mcp_business.py
uv run --frozen python examples/repo_reading/12_app.py \
  --conversation --data-dir runs/mcp-agent --business-transport mcp \
  --port 18040 --business-port 18800
```

Open http://127.0.0.1:18040 and create a conversation. Query the synthetic order o-999. For CLI add `--question '查询o-999数据时点的状态，不推断过去原因。'`; stop the page server first if reusing the same ports. CLI prints conversation/Run IDs; later add `--conversation-id ID` to resume the saved conversation.

The application still uses the same SDK Runner and fixed business function tools. For every MCP Run it opens a local stdio child (`python -m repo_agent.mcp_business`), discovers its tools, checks the required names/input contract, and retains the allowed remote declarations in the Run JSON `mcp.tools`. Unknown remote tools and descriptions are not promoted into model instructions. The two vetted application tools route through MCP to the same BusinessClient/HTTP service. This is a fixed bridge, not automatic arbitrary-MCP-tool exposure.

MCP Python is pinned to 2.3.0 (MCPServer and snake_case result fields), Agents SDK to 0.24.0. The transport endpoint and credentials are configured by the application. There is no arbitrary server URL/command in model tool arguments. Only the business endpoint/token are passed in child environment; MODEL_*/OPENAI_* credentials are filtered. The stdio child is local/trusted, not a sandbox.

Inspect the Run JSON under `runs/mcp-agent/records/`: `mcp` captures the protocol/allowed declarations, `business.observations` the validated API observations, `model_inputs` actual application tool declarations and returned content, and `answer_checks` schema/source associations. Run the existing offline checker:

```bash
uv run --frozen python examples/repo_reading/05_check.py runs/mcp-agent/records/RUN_ID.json
```

Replace RUN_ID with the actual ID. Check passing does not grade semantic correctness. A protocol error, missing/malformed result or unavailable connection does not become business absence. HTTP business errors retain their existing statuses. There are no automatic retries/replays. Cancellation propagates and the owning Run closes the connection. A new Run opens a new connection; it does not resume a paused tool call.

HTTP remains the default. Changing from HTTP to MCP changes conversation scope; old results remain viewable, continuing the old conversation is rejected, and new work requires a new conversation. Skill loading is unchanged and can be combined using `--skills-dir fixtures/skills`.

The application and the independent test client in `test_mcp_business.py` both consume the same MCP service. Real subprocess/HTTP checks substitute only the model for deterministic behavior. A real-model failure remains `unavailable`; inspect retained error type/status, not only a successful connection. No user identity/OAuth integration or remote MCP deployment is claimed here.

## Reproduce a real permission failure

After configuring the model in the repository root, this single command starts the real synthetic HTTP service in forbidden mode and calls it through MCP. It does not substitute the model or the protocol client:

```bash
uv run --frozen python examples/repo_reading/12_app.py \
  --business-transport mcp --conversation --data-dir runs/mcp-forbidden \
  --business-mode forbidden --port 18042 --business-port 18802 \
  --question '查询o-999数据时点的状态，不推断过去原因。'
```

Inspect the actual Run JSON, not just the exit code: if the model called the status tool, `business.observations` must contain `status: forbidden`, `http_status: 403` and no data. No current status fact can be accepted from this observation; the result is unavailable and CLI exits 1. A model error before a tool call is a different failure and does not demonstrate the 403 path. Neither failure means the order does not exist. No tool call is retried automatically.
