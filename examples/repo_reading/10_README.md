# Business queries in the existing Agent

Requires Python3.12, uv, rg, the locked SDK0.24.0, and a function-tool-capable model configured in the repository-root .env with MODEL_API_KEY, MODEL_BASE_URL, MODEL_NAME. Run commands from the repository root after uv sync --frozen.

In two terminals, set BUSINESS_API_TOKEN=teaching-only-local; set BUSINESS_API_URL=http://127.0.0.1:8765 in the client terminal. This intentionally public token is for the local fixture only. Real tokens belong in secret management and must never enter model inputs.

```bash
# Terminal1; choose a new log path for each start
uv run --frozen python fixtures/business-api/server.py --log runs/business/service.jsonl
# Terminal2
uv run --frozen python examples/repo_reading/10_business.py --direct status --output runs/business/direct.json
uv run --frozen python examples/repo_reading/10_business.py --task status --output runs/business/status.json
uv run --frozen python examples/repo_reading/05_check.py runs/business/status.json
```

Tasks: status, policy, history, migrate. Files are exclusive-create; change output paths when repeating. Inspect events and model_inputs; compare API observations with fixtures/business-api/records.json and service logs. Read10_reference.md after forming your answer, never as model input.

The service returns fixed synthetic times, not wall-clock business state. observed_at is retrieval time; as_of/coverage fields describe fixture data. The checker checks correspondence and schema, not external authenticity or answer meaning. answer_returned means some successful file/API observation plus an answer; no_observation can still include an explanation of failed queries.

For failures restart with --mode forbidden|limited|slow|invalid and a new log path. Wrong client token gives401; o-404 gives404. The default client deadline is3 seconds; tests exercise slow mode with a shorter client deadline. The loopback-only, read-only service is not production authorization. The adapter enforces a fixed allow-list before network access; real authorization requires an explicit policy.

Run uv run --frozen python -m pytest -q for errors, boundaries, real HTTP integration and regressions. Model quality requires actual model runs and separate semantic review. There is no automatic HTTP retry; API tools share existing model/tool budgets and context limits.
