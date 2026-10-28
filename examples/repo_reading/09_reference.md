# Retrieval comparison: independent acceptance reference

Never include this file in the source bundle or model input. All data is synthetic; no Java service is started.

## Shared criteria

Check necessary evidence separately from the final answer. A valid citation must name a file and line range actually present in that run's read observations. A search hit alone is a lead; verify adjacent conditions before asserting a rule. Do not demand a fixed tool order or wording. `05_check.py` checks record consistency, not answer semantics.

The fixed baseline uses three publicly configured task-family keywords, then reads three lines before and after each uncovered hit, with at most16 total search/read operations. It does not select a query from each question or cover every possible future task. The agent uses the same snapshot and literal search/read tools but can choose queries and supplement evidence. Both have the same configured model and upper budgets; fixed mode exposes no tools to the answering model. This is a comparison of two application configurations, not an isolation of a single prompt word or a benchmark of all retrieval systems.

## review and paraphrase

- Querying an absent order throws `OrderNotFound` in `code/src/OrderService.java`; `code/src/ApiExceptionHandler.java` maps it to404 and `ORDER_NOT_FOUND`. These are source facts, not HTTP measurements.
- Current policy only permits suggesting closure for synthetic rehearsal copies whose status isUNPAID and age is over30minutes. PAID/REFUNDED/UNKNOWN are excluded. Missing status or creation time requires reporting a gap. Even eligible cases are suggestions for human review; no direct closure.
- Use `guidance/source-index.md` and archive applicability when discussing competing policy versions. Never use the historical15-minute automatic-closure rule as the current rule.

## versions

The index declares `closure-current` in `policy/closure-policy.md` applicable and `closure-archive` invalid. Explain why the15-minute automatic action is inapplicable, and preserve current30-minute/UNPAID/rehearsal/manual-review restrictions. A path containing “archive” is not by itself the authority; the source declares its status.

## missing

`code/src/OrderService.java` supports throwing `OrderNotFound`. No supplied exception mapping supports a definitive HTTP status or body. Do not import404 or `ORDER_NOT_FOUND` from another run or a reference. Current policy conclusions are the same as inreview. The index points to supplied code, not a guarantee that all relevant files were included.

## live

There is no actual status or execution evidence for o-999. Do not assignPAID/UNPAID, invent a job failure, or infer yesterday's cause from the policy. Explain the gap and request current order/payment records, execution history/logs, and applicable deployment/configuration information as needed. A correct limited answer or an appropriate request for evidence can be semantically acceptable even if the mechanical stop is`no_source_read`.

## payment

Necessary evidence is`guidance/payment-status.md`: check payment receipt, merchant order identifier, and order-system state synchronization records. A page remaining unchanged does not prove payment failed, and it does not justify another debit. No supplied actual records determine an individual cause.

The fixed keywords were configured for exception mapping and closure policy before this task was added. Failure here illustrates uncovered query intent, not proof that embeddings are necessary. The agent may bridge wording by listing files or changing queries; record any failure without replacing it with a selected successful run.
