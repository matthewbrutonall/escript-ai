# Hosted AI budget reservations

This note specifies how hosted AI spend is held, recorded, and capped. It
covers `ai_transcribe`, `ai_seg_review`, and the synchronous `ai_fix` line
re-read. Local backends (`provider = local`) are outside this accounting:
they create no reservations and no ledger rows, and a dollar cap does not
block them. Their token counts are still added to `AIJob`.

## Caps

| Scope | Cap | Counts |
|---|---|---|
| Instance | `AI_INSTANCE_MONTHLY_BUDGET_USD` (new) | every user's hosted spend, and jobs with no user |
| User | `AIUserQuota.monthly_usd`, else `AI_MONTHLY_BUDGET_USD` | that user's hosted spend |

Unset or negative means unlimited. `0` means blocked. A value that does not
parse as a number (including `nan` and `inf`) is treated as `0` (blocked),
not unlimited. Caps are read fresh at every check, so lowering a cap stops a
running job at its next call. A call or hold must fit under every scope
that applies to it.

## Three kinds of amount

| Kind | Where | Meaning |
|---|---|---|
| Confirmed | `AIUsageLedger.actual_cost` | the provider reported token usage for the call; cost is tokens × configured price |
| Estimated | `AIBillableCall.estimated_usd` (state `estimated`) | the provider answered but did not report usage; the amount is our estimate |
| Unknown | `AIBillableCall` (state `unknown`) | the request may have reached the provider and we do not know whether it was billed |

A reservation is never written to the ledger. The ledger only receives a
row when a call is confirmed, or when an operator resolves an estimated or
unknown call with an amount.

## Tables

`AIBudgetLock` has one row per scope (`instance`, `user:<pk>`). It holds no
money. Reservations lock these rows with `SELECT ... FOR UPDATE`.

`AIBudgetHold` is a job-level reservation. One per job (`job` is one-to-one).
It holds the job's estimate while the job runs. States: `open`, `closed`.

`AIBillableCall` is one provider request. States:

| State | Counted against the cap as |
|---|---|
| `reserved` | `reserved_usd` (in flight) |
| `confirmed` | the linked ledger row's cost (not counted again here) |
| `estimated` | `reserved_usd` until resolved |
| `unknown` | `reserved_usd` until resolved |
| `released` | nothing |

`AIJob` gains `claim` and `heartbeat_at` for execution ownership.

## Month usage

For a scope and the current calendar month (UTC):

```
usage = confirmed ledger rows created this month
      + reserved_usd of calls in reserved / estimated / unknown created this month
      + for each open hold: max(0, hold.amount_usd - drawn(hold))
```

`drawn(hold)` is what the hold's calls already account for: a confirmed
call's ledger cost, a held call's `reserved_usd`, and nothing for a released
call. Each call is therefore counted exactly once: either through the
ledger or as a held call, and the job hold's remainder shrinks by the same
amount.

## Reservation ownership and locking

Every transaction that touches more than one billing row first locks the
`AIBudgetLock` rows for its scopes, always `instance` before `user:<pk>`.
That covers opening a hold, reserving a call, confirming a call,
reconciliation, claiming a job, and operator resolution. Lock rows are
created with `get_or_create`, then locked in one ordered
`select_for_update`. Job rows are locked next, then call and hold rows.

Every such transaction includes the `instance` row, so it acts as one mutex
for multi-row billing writes. That rules out lock-order deadlocks between,
for example, a claim (job, then calls) and a confirmation (call, then job).
The transactions are short and contain no provider request.

Under the lock, usage is recomputed from committed rows and checked against
each cap. Single-row updates of one call (marking it sent, settling it as
`estimated`, `unknown`, or `released`) and closing a hold take no other lock.
They only keep or lower usage.

## Lifecycle of a job

1. **Submission (serializer).** The API estimates the job's cost and stores it
   on `AIJob.est_cost`. It refuses the request if current usage plus the
   estimate is over a cap. This check is advisory: nothing is held while the
   job waits in the queue.
2. **Claim (task start).** The task locks the job row. It proceeds only if the
   job has no live claim. It writes a fresh `claim` token, `heartbeat_at`, and
   `status = running`. See duplicate delivery below.
3. **Hold.** Under the budget locks, the task re-validates and opens an
   `AIBudgetHold` for `est_cost`. If that does not fit, the job fails with a
   budget error before any provider call.
4. **Each billable call.** Under the budget locks and the job row lock, the
   task checks that the job is still running under its own claim, that its
   task was not cancelled, and that its hold is open. It refreshes
   `heartbeat_at`. It then reserves the call's upper-bound cost:

   ```
   need     = call upper bound
   from_job = max(0, hold.amount_usd - drawn(hold))
   extra    = max(0, need - from_job)
   ```

   `extra` must fit under every cap. The call row is created as `reserved`
   and committed before the request is sent.
5. **Retries and fallbacks.** Per-line fallbacks and any later call reserve in
   the same way. When the job's hold is used up, `extra` comes from the free
   budget. If it does not fit, the call is not sent and the job stops with a
   budget error. Lines already written stay written.
6. **Usage capture.** Each backend reports usage as soon as it has decoded the
   response body, before it extracts text or parses JSON. Reporting usage
   confirms the call immediately in its own transaction, writes one ledger
   row, and adds tokens and cost to `AIJob`. A later parse failure cannot lose
   that charge.
7. **Call outcome when usage was not reported.**

   | What happened | State |
   |---|---|
   | exception before the request was sent | `released` |
   | `requests.ConnectTimeout` (no connection was made) | `released` |
   | HTTP 401 or 403 (no authenticated account to bill) | `released` |
   | response received without usage | `estimated` |
   | any other exception after sending | `unknown` |

8. **End.** On success or failure, the hold is closed, which returns its unused
   remainder. Held calls stay held.

## Partial-page failures

Every call is confirmed when it returns, not at the end of the page. If a
page fails after some calls succeeded, those calls are already in the
ledger, and the failing call is `confirmed`, `estimated`, `unknown`, or
`released` as above. The task no longer writes one ledger row per page.

## Cancellation

`TaskReport.cancel` revokes the Celery task with `terminate=True`. The
worker process is killed and runs no cleanup. So:

- Before each call, the task checks its own `TaskReport`. A cancelled report
  stops the job before sending.
- Reconciliation treats a job whose report is cancelled as stopped. It closes
  the hold and marks its `reserved` calls `unknown`. They are not released:
  the killed process may have sent them.

## Worker crashes

A crashed worker leaves a `reserved` call and an open hold. Reconciliation
runs in its own committed transaction before every hold and reservation,
and in `python manage.py reconcile_ai_budget`. A refused reservation
therefore cannot roll it back. It acts on open holds whose job
heartbeat is older than `AI_JOB_LEASE_SECONDS` (default 1800, longer than the
600-second provider timeout). It marks the job's `reserved` calls `unknown`,
closes the hold, and marks a running job `error`. A `reserved` call with no
job (from `ai_fix`) older than the lease becomes `unknown`.

If a process that was presumed dead later gets its response, it may still
confirm its own call from `unknown`, because that is better information.
Its next reservation fails because the hold is closed.

## Duplicate delivery

`CELERY_ACKS_LATE` means a task can be delivered again after a crash, and the
Redis broker can redeliver a long task while the first worker still runs.
The claim decides:

| Job state at claim | Action |
|---|---|
| `done` or `error` | no-op |
| running, heartbeat fresh | no-op (another worker owns it) |
| running, heartbeat stale, hold open | reconcile as a crash, mark `error`, no-op |
| running, heartbeat stale, billing finished | no-op |
| pending | claim and run |

A redelivered job is never resumed automatically, because resuming would
re-bill pages already transcribed. The user can submit it again.

The AI tasks no longer use `autoretry_for=(MemoryError,)`. A retry would
re-run every page and bill it again.

A raw `ai_transcribe.delay()` without `job_pk` creates its own `AIJob`. It
gets cap protection. Duplicate delivery of such a raw call cannot be
detected.

## Upper bounds and estimates

Per call, the upper bound is:

```
input  = image token bound (crop after downscale) + UTF-8 bytes of prompt + 64
output = the backend's output cap
cost   = input × input price + output × output price
```

The image token bound takes the maximum of four tokenizer schemes: 768-px
tiles × 258, 32-px patches, 16-px patches, and pixels / 750. It is
deliberately generous. It is an assumption, not a figure verified per
provider.

Output caps: Anthropic sends `max_tokens` (default 8192) and OpenAI sends
`max_output_tokens` (default 2048), so those are real limits. Gemini, Azure,
and Mistral requests send no output limit. Their bound is an assumed maximum
(`params.max_output_tokens_bound`, default 65536 for Gemini, 16384 otherwise).
A confirmed cost above the bound is recorded truthfully and logged.

The job estimate uses expected rather than worst-case output: 256 tokens
plus 96 per line for transcription, 1024 for a segmentation review. Fallbacks
are not estimated in advance. They draw from the hold and then from free
budget.

## Operator resolution

An `estimated` or `unknown` call stays held until an operator resolves it in
Django admin with the provider's billing data. The admin actions are
**Resolve as charged** (enter the amount; writes a ledger row) and **Resolve
as not charged** (releases it). Both record the user, time, and a note.

## Limitations

- Prices are per backend class, not per model. Confirmed cost is tokens ×
  those prices, not the provider's invoice.
- Gemini, Azure, and Mistral output bounds are assumptions because those
  requests carry no output limit.
- A `local` backend whose endpoint is a paid hosted service is not billed
  here.
- Months are calendar months in UTC.
- A job interrupted after its billable calls (for example during the kraken
  comparison pass) or before its hold opened (during part validation) can
  stay `running`. Nothing is held for it, and a redelivery does nothing.
- Estimates use a fixed expected output per line. They decide whether a
  job may start, not what it costs.
