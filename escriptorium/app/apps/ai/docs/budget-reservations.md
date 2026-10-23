# Hosted AI budget reservations

This note specifies which hosted AI configurations may run, and how their
spend is priced, held, recorded, and capped. It covers `ai_transcribe`,
`ai_seg_review`, and the synchronous `ai_fix` line re-read.

A hosted model runs only when its cost per call can be bounded from
documented provider rules. Anything else is refused before a request is
made. The supported set is deliberately small.

## Deployment impact

- **Drain jobs first.** Drain hosted AI jobs before applying migrations
  `0010` and `0011`, and ship both together. A job that is `running` with no
  claim (started before `0010`) is never started again by a delivery.
  Reconciliation marks it `error` once its lease has passed.
- **Gemini and Mistral stop working.** Backends for these providers are
  refused, including Gemini, which earlier docs called the best on prose.
  Use a supported model below, or a local backend.
- **Unlisted models are refused.** That includes OpenAI or Anthropic models
  not in the table below, such as `gpt-5.6-terra` or `claude-sonnet-5`.
- **Azure backends need two settings.** They must set `price_model_id` to a
  supported model. Their `api_version` must be `2024-10-21`, which is now the
  default.
- **Supported models still need prices.** Every one in use needs an
  `AIModelPrice` row. There are no built-in prices.
- **Local backends and restricted documents.** A `local` backend pointed at a
  non-loopback URL can no longer process documents marked `never_send_offsite`
  (see Local backends).

## Supported billing profiles

Profiles live in `ai/profiles.py`. Each is bound to an exact model id; aliases
and dated snapshots are not matched. Azure is profiled by `price_model_id`,
which also selects the price row.

| Provider | Model ids | Image tokens for the image actually sent | Output limit covers reasoning |
|---|---|---|---|
| OpenAI | `gpt-4o`, `gpt-4.1` | low: 85. high/auto: fit within 2048², shortest side to 768 if longer, then 85 + 170 × 512-px tiles | yes (`max_output_tokens`) |
| OpenAI | `gpt-4o-mini` | as above with 2,833 + 5,667 × tiles (low: 2,833) | yes |
| Azure | `price_model_id` ∈ `gpt-4o`, `gpt-4.1`, `gpt-4o-mini` | same as the OpenAI model | yes (`max_completion_tokens`) |
| Anthropic | `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5` | ceil(w / 28) × ceil(h / 28), at most 4,784 | yes (`max_tokens`) |

Worked examples from the OpenAI guide, all in the tests:
- 1024 × 1024 at high detail costs 765 tokens on `gpt-4o` and 25,501 on
  `gpt-4o-mini`.
- 2048 × 4096 at high detail costs 1,105 tokens.

Anthropic's cap is 1,568 visual tokens on the standard tier and 4,784 on
Claude 4.7 and later. Larger images are scaled down until they fit, so 4,784
bounds either tier.

OpenAI and Azure send `detail` explicitly, default `high`. Only `low`, `high`,
and `auto` are accepted, and the reservation uses the value sent.

### Sources

All retrieved 2026-10-23. The documentation hosts are blocked by this build
environment's network policy, so the pages were not read directly here. Each
figure was taken from an independent reading through web search, and it
agreed with the figures in the second review of this branch. Treat them as
cross-checked, not read from the primary page.

- OpenAI tile table, resize procedure, and low-detail rule:
  https://developers.openai.com/api/docs/guides/images-vision
- Anthropic 28-px patches and tier caps:
  https://platform.claude.com/docs/en/build-with-claude/vision
- Azure `max_completion_tokens` (added in `2024-09-01-preview`) and GA
  `2024-10-21`:
  https://learn.microsoft.com/en-us/azure/ai-foundry/openai/api-version-lifecycle

### Refused configurations

All of these are refused before any request: at submission, at task start,
and in `ai_fix`.

| Configuration | Why |
|---|---|
| Any Gemini model | `maxOutputTokens` is documented as a hard cutoff on thoughts plus output, but measurements reported on `gemini-3.7-flash` and `gemini-3.8-flash` reached about twice the cap, unresolved. Image-token defaults also differ between Gemini docs. No model's combined ceiling could be verified. |
| Any Mistral model | No image-token ceiling is published, and how reasoning relates to `max_tokens` is not documented for every model. |
| OpenAI patch-based models (`gpt-5.x` and later) | Patch budgets and multipliers could not be verified. |
| Any other model id, alias, or dated snapshot | No profile. A price row alone never enables a model. |
| Azure with no or unsupported `price_model_id` | No profile. |
| Azure `api_version` other than `2024-10-21` | Older versions predate `max_completion_tokens`. Whether a resource still serves a dated preview cannot be checked without a paid request. |
| OpenAI/Azure `detail` other than `low`, `high`, `auto` | No documented token rule. |

## Local backends

`provider = local` creates no reservations and no ledger rows, and a dollar
cap does not block it. Its token counts are still added to `AIJob`, and each
call refreshes the job's heartbeat.

The label does not make a URL local. For a document with `never_send_offsite`,
a local backend's endpoint must be a literal loopback address or `localhost`.
With no endpoint set, the default is `http://localhost:11434/v1`. Anything else
is refused before any request, as is an unreadable policy flag. Classification
never resolves DNS. `ai/egress.py` restates the external HTR rule, and a parity
test keeps the two identical.

Operators are responsible for configuring local backends truthfully. A `local`
backend pointed at a paid hosted service is not billed here, and on an
unrestricted document it is not blocked.

## Prices

Prices live in `AIModelPrice`: provider, exact model id, USD per million
input tokens, USD per million output tokens, and a source note. Enter them
from the provider's current price list.

- The price is looked up only after a profile is found, by the same
  `(provider, model)` key. For Azure the model is `price_model_id`.
- Each meter resolves the price once. Every call row stores that snapshot
  (`in_usd_per_1m`, `out_usd_per_1m`). The reservation and the settlement of a
  call both use it, so a later price edit changes neither.
- Rates must be finite and at least 0. A rate of 0 is accepted, and it makes
  that model free for cap purposes.

Output prices include reasoning and thinking tokens, which providers bill as
output.

## Output limits

Every hosted request carries an output limit, and the reservation is built
from the same number (`billing.sent_output_limit`):

| Provider | Body field | Config param | Default |
|---|---|---|---|
| Anthropic | `max_tokens` | `max_tokens` | 8192 |
| OpenAI | `max_output_tokens` | `max_output_tokens` | 2048 |
| Azure | `max_completion_tokens` | `max_completion_tokens` | 8192 |

A limit that is not a positive integer is refused.

## Upper bound per call

```
input  = profile image tokens for the outgoing image
       + UTF-8 bytes of the prompt + 64
output = sent output limit
bound  = input × input price + output × output price   (call's price snapshot)
```

The image is measured after the backend's own downscale
(`backends.outgoing_image`, the function that also encodes the request), so
the reservation sees the same pixels the provider receives. A prompt token is
at least one UTF-8 byte.

## Caps

| Scope | Cap | Counts |
|---|---|---|
| Instance | `AI_INSTANCE_MONTHLY_BUDGET_USD` | every user's hosted spend, and jobs with no user |
| User | `AIUserQuota.monthly_usd`, else `AI_MONTHLY_BUDGET_USD` | that user's hosted spend |

- Unset or negative: unlimited. `0`: blocked.
- A non-finite value is treated as `0` (blocked). That covers a setting that
  does not parse, `nan`, `inf`, and a quota stored as `nan` or `inf`. The quota
  field also rejects non-finite values on save.
- Caps are read fresh at every check, so lowering a cap stops a running job at
  its next call.
- A call or hold must fit under every scope that applies to it.

## Accounting month

A charge belongs to the calendar month, in UTC, in which its call was
reserved.

- Each ledger row written for a call stores that month in `accounting_month`:
  on confirmation, on a late confirmation, and on operator resolution.
- Ledger rows from before `0011` have no `accounting_month` and count in the
  month of `created_at`.

A month's usage for a scope is:

```
usage = ledger rows whose accounting month is this month
      + reserved_usd of every call still reserved / estimated / unknown,
        whatever month it was made in
      + for each open hold: max(0, hold.amount_usd - drawn(hold))
```

An unresolved call therefore stays inside the cap in every month until it is
resolved, and its exposure never drops out at a month boundary. When it is
resolved as charged, the charge moves to the month the call was made. It
never lands in the month of resolution.

`drawn(hold)` is what the hold's calls already account for: a confirmed
call's cost, a held call's `reserved_usd`, or nothing for a released call.
Each call is counted exactly once.

## Three kinds of amount

| Kind | Where | Meaning |
|---|---|---|
| Confirmed | `AIUsageLedger.actual_cost` | the provider reported usage; cost is tokens × the call's price snapshot |
| Estimated | `AIBillableCall.estimated_usd` (state `estimated`) | the provider answered without usage; the amount is an estimate |
| Unknown | `AIBillableCall` (state `unknown`) | the request may have been billed; we do not know |

A reservation is never written to the ledger.

### Usage fields

- **Anthropic:** `input_tokens` excludes cache tokens. Cache writes count as
  2× input tokens (real rate 1.25× or 2×). Cache reads count as 1× (real rate
  0.1×). These requests set no cache breakpoints, so both are normally zero.
- **OpenAI, Azure:** cached input is included in the prompt count and
  charged here at the full input rate.

Every one of these is an overestimate or exact. None under-records a charge.

## Tables

- `AIModelPrice` holds prices, as described above.
- `AIBudgetLock` has one row per scope (`instance`, `user:<pk>`).
- `AIBudgetHold` holds a running job's estimate. States `open` and `closed`.
- `AIBillableCall` is one provider request. Its states are `reserved`,
  `confirmed`, `estimated`, `unknown`, and `released`. It carries the price
  snapshot.
- `AIUsageLedger` gains `accounting_month`. `AIJob` gains `claim` and
  `heartbeat_at`. `AIBackendConfig` gains `price_model_id`.

## Locking

Every transaction that touches more than one billing row locks the
`AIBudgetLock` rows for its scopes first, always `instance` before
`user:<pk>`. Moving a call to "sent" does the same. Job rows come next, then
call and hold rows. Every such transaction includes the `instance` row, so
it acts as one mutex for these writes. That rules out lock-order deadlocks.
The transactions are short and contain no provider request.

Settling one call after its request (`estimated`, `unknown`, `released`) is
a single conditional row update.

## Lifecycle of a job

1. **Submission.** The API resolves the price, estimates the job's cost,
   stores it on `AIJob.est_cost`, and refuses the request if current usage plus
   the estimate is over a cap. Nothing is held while the job waits in the queue.
2. **Claim.** The task needs a `job_pk`. A delivery without one is refused
   before any work, because a redelivered message without a job cannot be
   de-duplicated. The task locks the job and proceeds only if the job is
   `pending`. See Duplicate delivery.
3. **Hold.** Reconciliation runs in its own committed transaction. Then,
   under the budget locks, the task resolves the price and opens an
   `AIBudgetHold` for `est_cost`. If that does not fit, the job fails before
   any request.
4. **Reserve each call.** Reconciliation runs again. Under the locks, the
   task checks that it still owns the running job and that the task was not
   cancelled. It checks that no earlier call in the job cost more than its
   reservation, and that its hold is open. It refreshes the heartbeat, then
   reserves the call's bound. `max(0, bound - hold remainder)` must fit under
   every cap. The call row is committed as `reserved`.
5. **Send.** Immediately before the HTTP request, `mark_sent` locks the call
   and the job. It moves the call to sent only if it is still `reserved` and
   unsent, the job is still owned, running, not cancelled, and within its
   lease, and the hold is open. It refreshes the heartbeat. Otherwise it
   raises, and no request is made.
6. **Usage capture.** Each backend reports usage as soon as the body is
   decoded, before any parsing. That confirms the call immediately, writes one
   ledger row, and adds tokens and cost to `AIJob`.
7. **Overrun.** A confirmed cost above the reservation is recorded in full.
   It should not happen for a supported profile; if a provider exceeds its
   documented rule, it can. The job's next reservation is then refused and the
   job stops. That cannot undo the call that overran.
8. **Outcome when usage was not reported.**

   | What happened | State |
   |---|---|
   | exception before the request was sent | `released` |
   | `requests.ConnectTimeout` (no connection was made) | `released` |
   | HTTP 401 (authentication failed, no account to bill) | `released` |
   | response received without usage | `estimated` |
   | HTTP 403, any other status, read timeout, reset, parse error after send | `unknown` |

   An HTTP 403 is a permission or policy refusal, and does not prove no
   tokens were processed.
9. **End.** On success or failure the hold is closed, returning its unused
   remainder. Held calls stay held.

Retries and per-line fallbacks are ordinary calls. They draw from the hold
and then from free budget. When budget runs out, the next call is not sent
and the job stops. Lines already written are kept.

## Partial-page failures

Every call is confirmed as soon as it returns. A page that fails halfway
keeps every charge already made. The failing call itself ends `confirmed`,
`estimated`, `unknown`, or `released` as above.

## Leases

`AI_JOB_LEASE_SECONDS` (default 1800) is raised to at least twice the
provider read timeout (600 s), whatever it is set to. A request is only sent
with a heartbeat refreshed at that moment, so the lease cannot run out during
one read timeout. Requests' timeout applies per read, not to the whole
request. A provider trickling bytes for longer than the lease would be
reconciled as `unknown`, never released. Its late usage still confirms it,
and its job sends nothing more.

## Cancellation

`TaskReport.cancel` revokes the Celery task with `terminate=True`. The
worker process is killed and runs no cleanup.

- The reservation and send checks both look at the task's report. A
  cancelled task sends nothing more.
- Reconciliation stops a cancelled job at once. It closes the hold, marks
  sent `reserved` calls `unknown`, releases unsent ones, and marks the job
  `error`.

## Worker crashes

Reconciliation runs before every hold and reservation, and in
`python manage.py reconcile_ai_budget`. It acts on:

- an open hold whose job was cancelled, has finished, or whose heartbeat is
  older than the lease;
- a `running` job with no open hold and a stale heartbeat (or, with no
  heartbeat, a stale `updated_at`). That covers a worker that died before its
  hold opened, after it closed, or a local job;
- a `reserved` call with no job (`ai_fix`) older than the lease.

Sent calls become `unknown`, unsent ones `released`, and the job `error`. A
presumed-dead worker that later gets its response may still confirm its own
call from `unknown`. Its next reservation or send is refused.

## Duplicate delivery

| Job state at claim | Action |
|---|---|
| `done` or `error` | no-op |
| `running` with no claim (pre-0010) | no-op |
| `running`, heartbeat fresh | no-op (another worker owns it) |
| `running`, heartbeat stale | stop billing as for a crash, mark `error`, no-op |
| `pending` | claim and run |
| no `job_pk` | refused before any work |

A redelivered job is never resumed, because resuming would re-bill pages
already done. The AI tasks do not auto-retry.

## Estimates

The job estimate uses expected rather than worst-case output: 256 tokens
plus 96 per line for transcription, and 1024 per part for segmentation
review. It decides whether a job may start, not what it costs.

## Operator resolution

An `estimated` or `unknown` call stays held until an operator resolves it in
Django admin from the provider's billing data:

- **Resolve as charged** asks for an amount. That amount must be finite and
  at least 0. It writes a ledger row in the call's accounting month, and adds
  the amount to the job's `actual_cost` in the same transaction.
- **Resolve as not charged** releases the call.

Both need the `change` permission on billable calls and a note. Both record
who resolved the call, and when. A call can be resolved only once.

## Unresolved assumptions

- **Sources were not read directly.** Profile figures come from an
  independent web-search reading of the provider pages, because the build
  environment cannot reach them. Re-check them against the primary pages
  before release.
- **Anthropic model ids.** `claude-opus-5-5`, `claude-sonnet-5-5` and
  `claude-haiku-5-5` are taken as Claude 4.7-or-later models that accept
  images. The 4,784 cap bounds either tier, but the ids themselves were not
  checked against Anthropic's model list here.
- **Azure on `2024-10-21`.** That `max_completion_tokens` is accepted for
  `gpt-4o`, `gpt-4.1` and `gpt-4o-mini` deployments rests on the changelog.
  One community report of a 400 concerned GPT-4 deployments, which are not
  supported here. A rejection after sending leaves that call `unknown` and
  held.
- **Azure tokenization.** Azure tokenizes images like the OpenAI model it
  runs. That is assumed, not separately documented here.
- **Request overhead.** 64 tokens covers message framing. No provider
  publishes this figure, so 64 is an allowance, not a documented number.

## Limitations

- **Wrong prices.** Prices are only as accurate as the `AIModelPrice` rows,
  and a 0/0 row makes a model free for cap purposes. Confirmed cost is tokens
  × those rates, not the provider's invoice.
- **Ledger rounding.** Ledger `actual_cost` is a float; reservations are
  Decimal.
- **No periodic reconciliation.** Reconciliation runs before holds and
  reservations, and from `reconcile_ai_budget`. There is no periodic worker.
