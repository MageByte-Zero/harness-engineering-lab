# Read-only task methods

The same application can expose a trusted local Agent Skills directory. SDK 0.24.0 still owns the execution loop. This implementation supports `SKILL.md` plus UTF-8 `.md`/`.txt` files below `references/` or `assets/`; scripts are not exposed or executed.

From the repository root:

```bash
uv sync --frozen
uv run --frozen python -m pytest -q tests/repo_agent/test_skills.py
uv run --frozen python examples/repo_reading/12_app.py \
  --conversation --data-dir runs/method-agent --skills-dir fixtures/skills \
  --port 8035 --business-port 8795
```

Set the existing `MODEL_API_KEY`, `MODEL_BASE_URL`, and `MODEL_NAME` in the root `.env` or process environment. Open `http://127.0.0.1:8035`, create a conversation, then ask:

> 追踪查询订单功能，说明入口、数据来源及未找到时如何处理；区分代码设计与实际运行。

The initial prompt contains only method names/descriptions. The model can call `load_skill(name)` and then `read_skill_resource(name, path)` for supporting text. The bundled `trace-feature` method helps code investigation; a simple live order-status query does not need it. The model chooses whether to activate a method; the application records what actually happened rather than assuming a selection.

CLI uses the same worker:

```bash
uv run --frozen python examples/repo_reading/12_app.py \
  --conversation --data-dir runs/method-agent --skills-dir fixtures/skills \
  --port 8035 --business-port 8795 \
  --question '追踪查询订单功能，说明入口、数据来源及未找到时如何处理；区分代码设计与实际运行。'
```

Stop the page server before using these same ports in CLI. Copy the printed conversation ID into a later CLI invocation with `--conversation-id ID` to resume. Keep the same directory, source bindings, method content, and business endpoint. `--skills-dir` is also supported without persistent conversations.

## What to inspect

Run JSON files are under the selected data directory's `records/`:

- `skills.catalog`: offered names and descriptions.
- `skills.files`: retained input files and SHA-256 hashes. These author-side records are not all sent to the model.
- `skills.snapshot_id`: digest of the supported file paths and contents, independent of the metadata's version label.
- `skills.loads`: successful first loads in this Run, including the tool call ID, package name, path, and SHA-256.
- `model_inputs`: actual inputs before each model call, including method tool results; the existing 60,000-character budget still applies.
- `answer_checks`: method outputs cannot be fact citations. A valid source association still requires current file/API evidence; meaning is subject to review.

Repeated loads of a file in the same Run return `already_loaded: true` with its hash, without resending the body. Calls count toward the shared tool budget. Each new Run starts a fresh activation state, including after restart. Prior complete turns may still contain old method text, bounded by the existing history selection.

Changing supported method files while the application runs fails the next attempt before a model call. Restart binds the new snapshot; old conversation attempts return HTTP 409 while old results remain viewable. Create a new conversation to use the changed methods. Adding/removing method support also changes the conversation binding. There is no hot migration of old conversations.

Only explicitly trusted local packages are supported: at most 8 packages, 64 supported files, 128,000 UTF-8 bytes total, and 12,000 characters per file. Symlinks and unsafe relative resource paths are rejected. This is not an installer, sandbox, signature verifier, authorization system, or full Agent Skills runtime.

## Verification boundary

The tests exercise real SDK calls, method files, input captures, source checks, SQLite persistence, cancellation and parallel function calls, substituting the remote model to make failure timing reproducible. They do not measure a model's ability to select or follow a method. Real-model runs can return `unavailable`; inspect `stop_reason` and the retained error type/status. Do not fill in a fabricated answer or label the method successful when no model call completed.
