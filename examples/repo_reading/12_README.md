# Interactive read-only investigation

Python 3.12, `rg`, and `uv` are required. Run from the repository root:

```bash
uv sync --frozen
# Copy .env.example to .env if needed and fill MODEL_API_KEY, MODEL_BASE_URL, MODEL_NAME.
uv run --frozen python examples/repo_reading/12_app.py
```

Open http://127.0.0.1:8000 . One command starts the synthetic service on 8765 and the application on 8000; Ctrl+C stops both. Both ports must be free. Alternate ports: `--port 8012 --business-port 8772`. The teaching service uses a fixed local teaching token. MODEL_* credentials are not passed to its child process. This launcher intentionally uses the local synthetic service; use the earlier business client entry for separately configured endpoints.

Submit a question, e.g. `查询合成订单 o-999 的当前状态，只报告数据时点的状态，不推断过去原因。` Inspect the actual tool events, answer and source details. The synthetic `as_of` date is fixed input data, not the current observation time. A supported model must handle function tools and structured output; MODEL_NAME=auto does not identify the backend model or guarantee complex-task quality.

CLI uses the same `investigate` use case and starts/stops its synthetic service:

```bash
uv run --frozen python examples/repo_reading/12_app.py --question '查询合成订单 o-999 的当前状态，只报告数据时点的状态。'
```

API:

- `POST /runs` with JSON `{"question":"..."}` -> 202 with run_id and state.
- `GET /runs/{run_id}` -> state and checked result, or 404 for an unknown process-local run.
- `GET /runs/{run_id}/events` -> SSE containing id/seq, run_id, time, kind and bounded lifecycle data. `?after=N` or `Last-Event-ID: N` replays events after that sequence; invalid cursors return 422. This is lifecycle progress, not model-token streaming.
- `POST /runs/{run_id}/cancel` -> 202; queued/running becomes cancelling, then cancelled after local cleanup. Repeated requests do not interrupt cleanup. A terminal result stays terminal.

A subscription closing does not stop execution. Keep the run ID and reconnect using the page's field or SSE cursor. The manager owns execution independently from HTTP subscriptions. It permits two active runs and retains at most 64 runs in memory; capacity returns 429. Restart to start a new session after retained capacity is exhausted. Use one server worker and no hot reload. No authentication or cross-process storage is provided; keep loopback binding. Host and Origin checks restrict browser access to the local app but are not user authorization.

Run states: queued/running/cancelling/completed/failed/cancelled. Result kinds reuse `consume_answer`: answer_for_review/needs_information/unavailable. completed means the consumer obtained a reviewable result, never semantic approval. A cancelled model request is closed locally; this cannot confirm that the remote provider stopped computation or billing. Existing read-only HTTP requests may already have reached the service. The search subprocess is killed and reaped before its cancellation propagates. No business write tools are available.

Each run retains an exclusive JSON record under the printed `runs/interactive-<uuid>/` directory; do not publish raw logs without review. On restart, files remain as offline records, but the web manager does not reload or resume them. Earlier CLI entries keep the same synchronous adapter over the async core.

Checks:

```bash
uv run --frozen python -m pytest -q
uv run --frozen python examples/repo_reading/12_app.py --help
```

Deterministic tests use the real SDK with a scripted model and separate HTTP boundary substitutes to test concurrent ownership, cancellation, cleanup, source failure and SSE replay. Real model task quality is a separate acceptance step; a source association cannot prove that a free-form claim follows from that source. `--business-mode forbidden` and `--business-mode slow` provide additional synthetic-service exercises. The README is an implementation guide, not a claim about all model providers.
