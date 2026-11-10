# Durable local conversations

Continue the same application from `lesson-13-conversation-20261110` (SDK 0.24.0).
This revision stores complete accepted turns, Run status and reviewable results in a
fixed local directory. It does not resume a model request or automatically replay tools.

From the repository root, configure the existing root `.env` (`MODEL_API_KEY`,
`MODEL_BASE_URL`, `MODEL_NAME`), then:

```bash
uv sync --frozen
uv run --frozen python examples/repo_reading/12_app.py --conversation \
  --data-dir runs/persistent-agent --port 8024 --business-port 8784
```

Open http://127.0.0.1:8024. Create a conversation and submit a question. After it
finishes, stop with Ctrl-C and run the same command again. Select the previous
conversation, open a previous Run, then ask a follow-up. Keep both the data directory
and business port unchanged; the business endpoint is part of the conversation scope.
A new data directory creates a separate workspace. Existing transient commands still work.

The API adds `GET /conversations` (ID, committed turn count, scope compatibility) and
`GET /conversations/{id}/runs` (saved Run views including results). Existing Run status,
cancel and events routes include restored records. `interrupted` is terminal.

CLI can create a persistent conversation and print its ID:

```bash
uv run --frozen python examples/repo_reading/12_app.py --conversation \
  --data-dir runs/persistent-agent --port 8024 --business-port 8784 \
  --question '查询合成订单 o-999 的当前状态，只报告数据时点的状态，不推断过去原因。'
```

After that process exits, replace the shell variable below with the printed ID:

```bash
CONVERSATION_ID='replace-with-the-printed-id'
uv run --frozen python examples/repo_reading/12_app.py --conversation \
  --data-dir runs/persistent-agent --port 8024 --business-port 8784 \
  --conversation-id "$CONVERSATION_ID" \
  --question '那它在数据时点是什么状态？请重新查询，不解释过去原因。'
```

Stop the web service before running the CLI against its data directory. A directory
has one owner; a second process fails explicitly instead of sharing in-memory locks.

## Storage ownership

- `application.sqlite3`: conversation resource bindings, Run views/events and ordered
  committed turn references. The final result and its turn reference commit together.
- `messages.sqlite3`: SDK `SQLiteSession` messages, one immutable batch per Run. Only
  batches referenced by a committed application turn are restored. A crash between
  these two writes can leave unreferenced messages, which are ignored, not replayed.
- `records/<run_id>.json`: raw execution records. A killed process may leave an
  incomplete raw record; it is not used to infer a successful Run.
- `owner.lock`: an OS advisory lock held for the application's lifetime.

`ConversationWorker` still executes each attempt using a temporary SDK Session.
After output/source checks, it stages only the new messages in durable SDK storage.
`RunManager` publishes the result and turn reference, then updates in-memory history.
Restart validates the count/digest of referenced batches before selecting complete
turns. Missing/changed history fails explicitly. Digests are local consistency checks,
not authentication of an untrusted database.

On startup, saved `queued/running/cancelling` Runs become `interrupted`; their messages
are not added to committed history. The UI prompts a new attempt. SDK `RunState`
execution resumption, approvals and external side-effect reconciliation are separate
capabilities, not provided here. Shutdown cancellation remains cancellation.

Source roots, source snapshot and business endpoint are bound to a conversation.
If the scope changes, existing results remain viewable but continuing that conversation
returns 409; create a new conversation. Credentials come from current configuration,
not from the conversation store. New attempts still require fresh observations.

This is a loopback-only, single-process teaching application with trusted local storage,
up to 32 conversations and 64 retained Runs per workspace (2 active Runs). Restart does
not reset these limits. Another directory starts a separate workspace; archive/delete,
multi-worker operation and identity authorization are not implemented. Use transient
mode for experiments that do not need retention.

## Verify

```bash
uv run --frozen python -m pytest -q
```

Persistence tests exercise the real SDK/SQLite and substitute only remote model/HTTP.
They include failed/cancelled rounds, scope changes, missing history, transaction
rollback, final-storage failure, and abrupt subprocess exits before messages, after
messages and after the application commit. Model understanding and answer correctness
still require separate real-task checks.
