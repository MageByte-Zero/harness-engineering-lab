# Follow-up investigations in the same application

Base: lesson-12-app-20261109. Python 3.12, `uv`, `rg`, and root `.env` MODEL_* configuration remain unchanged. From the repository root:

```bash
uv sync --frozen
uv run --frozen python examples/repo_reading/12_app.py --conversation
```

The existing launcher now supports conversation mode; it starts/stops the local synthetic service and web application together. The page creates a conversation, accepts follow-ups, and creates a new run per submission. Copy the conversation ID to return to it while this process remains alive. The new-conversation button separates tasks. Existing single-turn mode still works without the flag.

CLI uses the same conversation worker:

```bash
uv run --frozen python examples/repo_reading/12_app.py --conversation \
  --question '查询合成订单 o-999 的当前状态，只报告数据时点的状态，不推断过去原因。' \
  --follow-up '那它在数据时点是什么状态？请重新查询，不解释过去原因。'
```

Use `--port 8015 --business-port 8775` if the web app is still running. Model capability and task quality require their own acceptance; failures are retained, not repaired silently.

API additions:

- `POST /conversations` creates an ID with server-bound sources/tools.
- In conversation mode, `POST /runs` requires question and conversation_id. Unknown conversations return 404; same-conversation overlap returns 409 (the global capacity bound still returns 429). Independent conversations may run concurrently.
- Existing run queries, SSE and cancellation remain available. Run results include conversation_id and selection metadata; full selected history is only in the local run JSON.

SDK SQLiteSession is used explicitly with its in-memory default as a staging session for one attempt. The worker preloads selected history and lets Runner manage messages. After execution and shape/source checks, only the new turn's items are committed to the process-local conversation. A cancelled or unavailable run does not commit partial tool calls. This is not durable SQLite storage, restart recovery or a replacement Agent loop.

Selection retains up to three recent whole accepted turns with a 24,000-character history bound. Tool calls and their responses remain together. Historical transport IDs are rebased per originating run so provider-reused IDs do not cause SDK deduplication. The existing 60,000-character prepared model-input bound still applies to instructions, history, tools and schema together. Oversized history is omitted with a visible count; unresolved references require clarification, never invented memory. A history bound is not proof of model understanding.

Historical text helps resolve references, but accepted facts must cite fresh observations from this run. Observation IDs use `<run_id>/obs-N`; the consumer rejects an old run's evidence ID even if it appears in history. This conservative policy re-reads facts rather than importing a historical evidence ledger. It does not certify claim meaning or data freshness.

Sources are bound to a snapshot when the worker starts. Changes require restarting to bind a fresh scope. There are at most 32 conversations and the previous 64 retained runs/two active runs limit. Keep one worker and loopback binding; no identity authorization or cross-restart history is provided.

```bash
uv run --frozen python -m pytest -q
```

Deterministic tests use the real SDK with scripted model responses, including repeated provider IDs, real history inputs, old-evidence rejection, isolated sessions, cancellation rollback, long history, input budget rejection and HTTP conflict handling. Real model runs must be assessed separately. Raw run records can contain user questions and public fixture contents; keep them local unless reviewed.
