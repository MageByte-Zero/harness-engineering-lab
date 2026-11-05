# Typed answers from the existing investigation Agent

Run from the repository root with Python 3.12 and `uv sync --frozen`. Keep the existing root `.env` model configuration (`MODEL_API_KEY`, `MODEL_BASE_URL`, `MODEL_NAME`). No new dependencies are required.

This entry uses the same Agent, tools and SDK Runner with `output_type=InvestigationAnswer`. It records the output contract, serializes the typed result, and builds source metadata from actual observations. First-turn tool choice is required for this read-only investigation application; SDK reset after tool use allows the model to continue or produce a final answer.

The model-call budget remains 12 and the tool-request budget remains 16. Schema and input preparation share the existing character budget. There is no JSON repair loop or silent fallback. The configured model's complex-task behavior remains a separate acceptance question; passing shape and source checks does not certify the answer.

Terminal one:

```bash
export BUSINESS_API_TOKEN=teaching-only-local
uv run --frozen python fixtures/business-api/server.py --port 8765 --log runs/answer-practice/service.jsonl
```

Terminal two:

```bash
export BUSINESS_API_URL=http://127.0.0.1:8765
export BUSINESS_API_TOKEN=teaching-only-local
uv run --frozen python examples/repo_reading/11_answer.py --task status --output runs/answer-practice/status.json
uv run --frozen python examples/repo_reading/11_answer.py --task history --output runs/answer-practice/history.json
uv run --frozen python examples/repo_reading/11_answer.py --record runs/answer-practice/status.json
uv run --frozen python examples/repo_reading/05_check.py runs/answer-practice/status.json
uv run --frozen python -m pytest -q
```

Use a fresh output filename for every attempt. The service uses frozen synthetic inputs; `as_of` is their data time, not the query's `observed_at`. Stop the service when finished. Do not use the teaching token with real systems.

Other tasks: `review` (code and policy), `migrate` (o-1000). Read `11_reference.md` after completing a task; do not send it to the model. For an authorization failure, restart the service with a fresh log filename and `--mode forbidden`, then save a fresh run.

Result states:

- `answer_for_review`: execution, shape and source associations passed. The answer's meaning still needs review.
- `needs_information`: the model reports a gap; inspect whether it is real and whether available tools could still resolve it.
- `unavailable` (exit 1): execution failed, output is invalid, the record was altered, or a cited source was not an actual successful file/API observation. A well recorded bad answer can still pass the record-integrity check; inspect `answer_checks` separately.

`source_ids` refer to `observation_id` values assigned inside actual tool results. The application builds file ranges/content and API data/time metadata from those results. Source association checks do not prove that each statement follows from its sources. The semantic status remains `pending`; a false statement citing a real observation is a deliberate test counterexample.

Structured mode currently uses Agent-selected tools, not the fixed retrieval entry. The earlier free-text commands remain supported. No web service, persistent session, write action or approval flow is added here.
