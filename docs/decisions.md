# Architecture Decisions

ADR-style log of design decisions made before and during the build. Newest at the bottom.

## ADR-001: Money stored as integer paise

All amount columns are `amount_paise integer`, never floating point. Matches Razorpay's own API convention (amounts in paise) and eliminates rounding error in reconciliation diffs.

## ADR-002: Minimal core schema

Four tables carry the domain: `orders`, `payments`, `refunds`, `webhook_events`, plus `audit_log` for the append-only trail.

- `orders`: id, amount_paise, currency, state (`CREATED / AWAITING_PAYMENT / CONFIRMED / CANCELLED / FULFILLED`), created_at, updated_at
- `payments`: id, order_id (FK), amount_paise, gateway_status (`created / authorized / captured / failed / refunded`), method, created_at, captured_at
- `refunds`: id, payment_id (FK), amount_paise, gateway_status (`initiated / processed / failed`), initiated_at, settled_at (nullable — null past SLA means stuck)
- `webhook_events`: id, payment_id, event_type, delivered, delivered_at, merchant_response_code, attempt_number

No `many orders per payment` modeling — real-world messiness observed in the field was a UI artifact of the merchant app, not a payment-rail concept, and adding it would complicate the schema for no demo value. One order can have many payment attempts (captures the retry-until-success flow).

## ADR-003: SLA windows are config-driven and time-scalable

Two config values govern state transitions:

- `ORDER_FULFILLABLE_WINDOW_MINUTES` (default 30) — past this, a `LIMBO_PAID_CANCELLED` order can no longer be completed via webhook replay; the correct action flips from `REPLAY_WEBHOOK` to `AUTO_REFUND`.
- `REFUND_SETTLEMENT_SLA_HOURS` (default 72) — past this, an `initiated` refund with no `settled_at` becomes `REFUND_STUCK`.

A `TimeProvider` abstraction (see ADR-006) lets eval/mock mode compress these windows via a time-scale factor so a 500-transaction batch runs in seconds, not days.

## ADR-004: Mock gateway mirrors real Razorpay webhook payload shape

The mock gateway emits `payment.captured`, `payment.failed`, and `refund.processed` events using Razorpay's actual webhook envelope (`payload.payment.entity` / `payload.refund.entity` nesting, signature header), cribbed from Razorpay's public webhook docs. Only these three event types are implemented — no others are needed for the demo.

Reason: the merchant webhook receiver is written once against this shape. When Phase 6 swaps `GATEWAY_MODE=razorpay`, the receiver needs zero changes — only the adapter underneath changes.

## ADR-005: Honest baseline, not a simulated one

The "no-agent" baseline (`BASELINE_RESOLUTION_DAYS`, default 21) is a stated constant assumption based on a documented real-world incident, not a simulated manual-resolution process. Per resolved transaction: `time_saved = BASELINE_RESOLUTION_DAYS − actual_agent_resolution_time`. The eval report states this baseline is an assumption, not a measurement, and shows agent MTTR (expected: minutes) alongside it.

Reason: a fabricated manual-process simulation would be less credible to judges than a clearly labeled, defensible assumption.

## ADR-006: Interval-based reconciliation, with an injectable clock

Reconciliation runs on a fixed interval scheduler (default every 60s in mock mode), not event-driven. This is a deliberate simplicity choice — "continuous background reconciliation" is the intended story, and event-driven triggering adds complexity with no demo-visible benefit.

Time itself is abstracted behind a `TimeProvider` interface so the eval harness can fast-forward the clock deterministically. Without this, SLA-based states (`REFUND_STUCK`, the fulfillable-window flip) would need real hours/days to elapse, making the 500-transaction eval batch non-reproducible and slow.

## ADR-007: Diagnosis agent has no access to chaos ground truth

The chaos engine's injection metadata (which failure class was deliberately injected) is written to a separate eval-only table that the `diagnosis/` module has no read access to. The diagnosis agent — LLM or deterministic fallback — sees only observable evidence: `webhook_events` rows, timestamps, and state history.

Reason: without this separation, "diagnosis accuracy vs ground truth" would be measuring an agent that can see the answer key. The eval harness compares diagnosis output against the injection table only at scoring time, never at inference time.

## ADR-008: Two definitions of "false intervention" for the circuit breaker

The circuit-breaker stopping rule (default 2% false-intervention rate) uses different signals depending on mode:

- **Eval/mock mode:** ground truth exists (from chaos injection). A false intervention is an executed action whose ground-truth label says it was wrong (e.g. refunding a fulfillable order).
- **Live/real mode:** no ground truth exists. A false intervention is instead any action that failed at the gateway (API error, refund rejection) — the closest observable proxy for "this action should not have been attempted."

Both definitions are implemented explicitly and selected by mode; the live-mode proxy is documented as a proxy, not equated with the eval-mode definition.

## ADR-009: Dashboard scope is locked to the spec panel list

Exactly seven panels, no more: limbo queue, money at risk, ₹ recovered, ₹ loss prevented, MTTR vs baseline, exception queue, audit trail. No additional panels are added except as explicit Phase 8 polish, and only if time remains.

## ADR-010: The merchant times itself out; chaos only touches webhook delivery

Chaos classes only manipulate whether/how/when a webhook reaches the merchant (dropped, delayed, mid-processing 500, out-of-order, duplicated) or whether a refund settles. None of them directly cancel an order. Order cancellation is a separate, independent piece of ordinary merchant behavior: `MerchantService.expire_unconfirmed_orders(cutoff)` cancels any order still `AWAITING_PAYMENT` past `ORDER_FULFILLABLE_WINDOW_MINUTES`, regardless of why.

This is what actually produces `LIMBO_PAID_CANCELLED`: a captured payment left orphaned once its order's window lapses with no confirmation. It also means `WEBHOOK_DELAYED` and `WEBHOOK_DROPPED` naturally converge to the same terminal footprint (captured payment, cancelled order) — they differ only in whether a late webhook_events row exists at all, which is exactly the diagnostic signal Phase 5 needs to tell them apart.

Reason: modeling cancellation as a merchant-owned, chaos-independent process is closer to how real merchant timeout logic works, and keeps the chaos engine's job narrowly about webhook/settlement mechanics rather than owning business-state transitions.

## ADR-011: Verification runs chaos in two passes, not real-time delay

`WEBHOOK_DELAYED` webhooks are held back and delivered only after `verify_phase2.py` fast-forwards `FakeTimeProvider` past the fulfillable window and runs the timeout sweep — so the late webhook arrives at an already-cancelled order, matching real-world "too late to matter." This avoids simulating actual elapsed time and keeps the 50-transaction batch deterministic and fast, consistent with the `TimeProvider` rationale in ADR-006.

## ADR-012: Orders with no payment and no fulfillment have nothing to reconcile

`classify()` returns `None` (not one of the six states) for an order that never received a captured payment and was never fulfilled — an honestly abandoned cart. The six-state machine describes mismatches between the gateway and the merchant; an order with nothing on either side is not a mismatch. `ReconciliationEngine.run()` skips these entirely: only orders with at least one payment row, or that reached `CONFIRMED`/`FULFILLED`, are recon candidates.

Reason: shoehorning "nothing happened, nothing to do" into one of the six named states (most plausibly `MATCHED_HEALTHY`, whose definition is explicitly "gateway success, order fulfilled") would misrepresent it and pollute recall metrics with cases that were never gateway/merchant mismatches to begin with.

## ADR-013: REFUND_STUCK requires crossing the refund SLA, not just the order window

The Phase 2 chaos batch only fast-forwards the clock past `ORDER_FULFILLABLE_WINDOW_MINUTES` (default 30 min) to produce `LIMBO_PAID_CANCELLED`. A refund injected as stuck is genuinely not "stuck" from recon's point of view until `REFUND_SETTLEMENT_SLA_HOURS` (default 72h) has also elapsed — classifying it as `REFUND_STUCK` any earlier would be premature by the domain's own definition. `verify_phase3.py` advances the clock past the refund SLA as a separate step, after the order-window sweep, before running reconciliation. This was caught by the first verification run (2/50 mismatches) and is recorded here rather than silently patched, per the honesty rules on flagging what broke and why.

## ADR-014: Idempotency key is (order_id, action_type); execution is per-attempt rows

Every `PolicyAction` row is one attempt, not one action. The idempotency key stays constant across retries of the same base action (`f"{order_id}:{action_type.value}"`); `attempt_number` tracks retries within that key. Before executing, `PolicyEngine.act()` checks whether any prior attempt for the key already reached `EXECUTED` or `DRY_RUN` — if so, it returns `SKIPPED_IDEMPOTENT` without calling the executor. This is what guarantees a re-run of the agent never double-refunds or double-completes an order, which is exercised directly by `verify_phase4.py`'s two-pass idempotency check and the unit tests in `test_policy_engine.py`.

The amount ceiling and retry-limit guardrails are checked separately from execution, in `PolicyEngine.decide()`, because they need to know the base action (state + fulfillable window) before they can look up its idempotency key's attempt history — see `policy/selector.py`'s split between `base_action_for_state` and `apply_guardrails`.

## ADR-015: Refunding a payment must mark it REFUNDED, or recon reclassifies it forever

Building and exercising the Phase 4 verification script surfaced two real bugs, both fixed rather than worked around:

1. `MockGatewayAdapter.create_refund` and `retry_refund_settlement` created/settled the `Refund` row but never updated the underlying `Payment.gateway_status` to `REFUNDED`. Since `classify()` only excludes `REFUNDED` payments (not `CAPTURED`) from the "money still outstanding" set, an already-refunded `LIMBO_PAID_CANCELLED` order would be reclassified as `LIMBO_PAID_CANCELLED` again on every subsequent reconciliation pass — never actually resolving. Fixed by marking the payment `REFUNDED` at the point of refund; a refunded payment against a cancelled order now correctly returns `None` (nothing left to reconcile) from `classify()`.
2. `ReconciliationEngine.run()` inserts a new result row on every run rather than replacing the prior one for an order — a deliberate choice (ADR-010's "append-only" spirit, useful for showing how a transaction's classification evolved). But this means any consumer that reads *all* rows for an order, rather than the most recent one, can act on a stale, already-superseded classification. Added `recon.service.latest_results_by_order()` as the one correct way to read current state; both `verify_phase4.py` and, later, the dashboard's limbo queue must use it instead of a raw `select(ReconciliationResult)`.

Neither bug was visible from the classifier's own unit tests (which construct in-memory objects and never call the gateway) or from Phase 3's single-pass verification — they only showed up once Phase 4 ran the recon → policy → recon loop repeatedly, which is exactly what the real scheduled system does. Both are now covered: bug 1 by `test_refunded_payment_with_cancelled_order_has_nothing_left_to_reconcile`, bug 2 by `verify_phase4.py`'s multi-round structure itself.

## ADR-016: Model choice — claude-opus-5, not claude-sonnet-4-6

The original spec named `claude-sonnet-4-6` for the diagnosis LLM. That model id predates this build and is not the current default; per house policy (the `claude-api` reference this build follows) the default model for new code is `claude-opus-5` unless the user explicitly names a different one. Used `claude-opus-5` in `diagnosis/llm.py`. Flagging this explicitly rather than silently swapping it in, since it's a deviation from the literal original spec — say so if `claude-sonnet-4-6` (or another specific model) is actually wanted for cost reasons.

Separately, `anthropic==0.34.2` (the version originally pinned in `requirements.txt`) predates the `client.messages.parse()` / `output_format` structured-outputs helper this phase depends on for JSON-constrained diagnosis output. Upgraded to `anthropic==1.0.0` in `requirements.txt`; the full pytest suite and all five phase verification scripts were re-run afterward with no regressions.

## ADR-017: The deterministic fallback classifies from evidence, not from chaos metadata

The original build prompt's `<ai_judgment>` section describes the deterministic fallback as mapping "failure-injection metadata directly to root cause... usable offline and as ground truth for evaluating LLM diagnosis accuracy." Read literally, that would mean the fallback itself reads `chaos_injections` — directly contradicting decision #8 (diagnosis must never see injection ground truth, only observable evidence).

Resolution: the deterministic fallback in `diagnosis/deterministic.py` is a genuine rule-based classifier over the same `EvidenceBundle` the LLM path sees — webhook delivery records, timestamps, payment/refund status — and never imports or queries `ChaosInjection`. The phrase "usable... as ground truth for evaluating LLM diagnosis accuracy" is honored at the *scoring* layer instead: `verify_phase5.py` (and later `evals/runner.py`) is the only place that reads `chaos_injections`, and only after diagnosis has already run, to score both the deterministic and LLM paths against it. This keeps the no-leakage guarantee intact while still giving the eval harness a ground truth to score against.

On the Phase 2/3 synthetic 50-case batch this deterministic classifier reaches 100% accuracy against ground truth — expected, not impressive, since the batch's evidence patterns are unambiguous by construction (each chaos class produces a distinct, clean footprint). This is an honest number for clean synthetic data, not a claim about real-world diagnosis accuracy, where evidence is noisier and is exactly why the LLM path exists.

## ADR-018: RazorpayAdapter cannot create payments; a human completes one real checkout

Razorpay's only documented, non-deprecated way to move a payment to "captured" is a customer completing real Checkout (hosted page, Payment Link, or Checkout.js) with a test card — verified by fetching the S2S UPI Collect docs directly: that flow, the one way to create a captured payment purely server-side, was deprecated 2026-02-28 for all but a few exempted use cases (IPO/secondary-market MCCs, iOS app transactions, UPI mandates, eRupi, PACB cross-border) that Limbo does not qualify for.

Consequence: `RazorpayAdapter.capture_payment` raises `NotImplementedError` with a clear message rather than pretending to support it — this method exists only to satisfy the `GatewayAdapter` interface; nothing calls it in real mode. Order creation, refunds, refund status lookup, and webhook signature verification are all real, working API calls. To get a real captured payment into Limbo at all: `scripts/razorpay_manual_checkout.py` creates a real Razorpay Payment Link and a local order, a human pays it once with a Razorpay test card, and `scripts/verify_phase6.py <order_id> <payment_id>` takes that real payment id and runs it through one full real recovery lifecycle — simulated webhook drop, merchant timeout sweep, reconciliation, and a real `AUTO_REFUND` executed against the real Razorpay API.

This was a deliberate choice offered to and confirmed by the user rather than assumed: the alternative was browser-automating the actual Checkout flow, which was not chosen.

Real webhook *delivery* is also not exercised end-to-end here — there is no public HTTPS endpoint for Razorpay to reach in this environment. `RazorpayAdapter.verify_webhook_signature` wraps the real SDK's `Utility.verify_webhook_signature` and is unit-tested against the real algorithm with a known secret; it has not been exercised against an actual webhook Razorpay delivered.

## ADR-019: Razorpay validates refunds against real account balance, even in test mode

A real `AUTO_REFUND` call against the Phase 6 test payment (₹100 captured) failed with a generic `400 BAD_REQUEST_ERROR: "invalid request sent"` — reproduced identically via the SDK, raw HTTP with several payload variations, and the Razorpay Dashboard's own "Issue Full refund" button, ruling out a request-format bug. The Dashboard's refund dialog and the account's Balances page (`Account & Settings → Balances`) revealed the real mechanism: Razorpay deducts its own fee (₹2.20 on this ₹100 payment) from the merchant's balance immediately on capture, and a refund cannot exceed that net balance — a full ₹100 refund against a ₹97.80 balance is rejected. Test mode does not allow adding funds (`Add Funds` is explicitly disabled there, per the Balances page); only live mode can top up balance.

Practical consequence for `scripts/verify_phase6.py`: the real `AUTO_REFUND` action refunds an amount capped by what the account can actually support (`min(remaining unrefunded amount, 500 paise)`), not the full captured amount, with the reason printed explicitly rather than silently substituted. This is a real, external account constraint being worked around for demo purposes — not a limitation of Limbo's own logic, which would request the full amount in a properly funded account.

Diagnostic note: before this was understood, one raw-API test refund of ₹90 (`rfnd_TTAcxoi8qfyLOP`) was made directly against the live Razorpay test API to isolate whether the failure was amount-dependent — a real but harmless test-mode side effect, disclosed to the user as it happened rather than after. The real refund actually executed through Limbo's own policy engine for this transaction is `rfnd_TTAg77RyCFBQrL` (₹5).

## ADR-021: The eval batch reports Rs 0 revenue recovered, honestly

`run_scenario_batch` always advances the clock past the fulfillable window before reconciliation ever runs (ADR-010's sweep). Every `LIMBO_PAID_CANCELLED` case in the 500-transaction eval batch is therefore already past its window by the time the policy engine decides an action, so it always resolves via `AUTO_REFUND` (loss prevented), never `REPLAY_WEBHOOK` (revenue recovered) — `REPLAY_WEBHOOK`'s within-window path is architecturally impossible in this batch design, since the global simulated clock advances uniformly for every transaction before any decision is made.

`REPLAY_WEBHOOK` is not untested: Phase 4's dedicated scenario (an order cancelled by ordinary business logic moments after capture, well within the window) proves it end to end, with its own unit and integration coverage. Rather than contriving an artificial "early cancellation" scenario into the 500-txn batch just to make the revenue-recovered figure nonzero, `evals/results/report.md` states this plainly. Per the project's own quality bar ("honest metrics: never massage numbers"), a batch design limitation reported honestly is preferable to a manufactured number.

## ADR-022: Dashboard money-at-risk and limbo-queue exclude already-resolved orders

Reconciliation runs on an interval (ADR-006) and is not re-triggered after each policy action, so an order's most recent `ReconciliationResult` can still show an actionable state (e.g. `LIMBO_PAID_CANCELLED`) even after it has already been resolved by an executed `AUTO_REFUND`/`REPLAY_WEBHOOK`/etc. A first pass at `/api/summary` and `/api/limbo-queue` counted these as still "at risk," inflating both figures with resolved history (caught immediately by testing against real accumulated data: money at risk read ₹1,42,398 before the fix, dropping to a correct ₹33,000 after).

Fixed by excluding any order with an `EXECUTED` `PolicyAction` for a resolving action type (`REPLAY_WEBHOOK`, `AUTO_REFUND`, `RETRIGGER_REFUND`, `DEDUP_REFUND` — not `ESCALATE_HUMAN`, which routes to a human rather than resolving the underlying issue) from both the limbo queue and the money-at-risk sum. `_resolved_order_ids()` in `app/api/routes.py` is the shared filter both endpoints use.

## ADR-023: Dashboard polls the API client-side; no separate live/websocket layer

The dashboard is a single Next.js client component that fetches all six API endpoints on mount and every 5 seconds (`REFRESH_INTERVAL_MS`), rather than using server-side rendering with revalidation or a websocket/SSE push channel. This satisfies "live audit trail view" from the spec with the least infrastructure — no extra server-sent-events endpoint, no polling-vs-push tradeoff analysis needed for a single-user local demo tool. If genuine multi-second-latency-sensitive live updates were ever needed at scale, this would be the first thing to revisit.

## ADR-024: The interval scheduler was never actually wired up until Phase 8

`RECON_INTERVAL_SECONDS` was defined in `config.py` since Phase 1 and referenced in ADR-006's design rationale, but nothing ever read it — reconciliation only ever ran when a script explicitly called `ReconciliationEngine.run()`. The system as built through Phase 7 was not actually "continuously reconciling" as the original problem story describes; it only reconciled on demand. This was a real gap between the documented design and the running system, not a deliberate simplification, and it went unnoticed because every phase's verification drove reconciliation explicitly.

Fixed in `app/scheduler.py`: `run_scheduler()` runs `run_reconciliation_cycle()` (classify -> diagnose -> decide -> act, over whatever the current DB state shows) on a loop every `RECON_INTERVAL_SECONDS`, started as a background `asyncio.Task` from `main.py`'s lifespan and cancelled on shutdown. One cycle's exception is logged and does not kill the loop — the scheduler's contract is explicitly best-effort continuous operation, so a single stuck transaction must not stop reconciliation for every other one (the one broad `except Exception` in this codebase that is intentional, not a caught-and-ignored bug). `DRY_RUN=true` remains the default, so the live scheduler proposes actions without executing them unless explicitly turned off.

## ADR-025: Two real dashboard bugs, found by a user screenshot and fixed properly

The user spotted the top summary tile showing "Revenue recovered: Rs 2,400" while the "Latest eval batch" panel on the same page showed "Rs 0.00" for the same label, and asked for both this and any related issues to be found and fixed rather than just patched cosmetically. Investigating surfaced two real, distinct bugs, not one:

**1. Unlabeled scope mismatch.** The top tiles are live cumulative totals across every transaction this instance has ever processed (including every verification script and eval run in this dev session); the eval panel is one isolated 500-txn benchmark scored against its own ground truth. These are genuinely different populations and were never going to show the same number — the bug was presenting them with identical unqualified labels ("Revenue recovered") with no indication they measure different things. Fixed by labeling both sections explicitly: "Live totals — every transaction this instance has ever processed (includes test/eval runs)" above the top tiles, and an explanatory line plus a renamed panel title ("Latest eval batch (isolated 500-txn benchmark)") below.

**2. A fabricated MTTR number.** The live "agent MTTR" tile displayed "2.6d" — investigated by pulling the actual `PolicyAction.created_at - Order.created_at` deltas from the DB, which clustered at exactly ~73 hours: `REFUND_SETTLEMENT_SLA_HOURS` (72) + 1 hour, the simulated clock advance every verification script and eval run applies via `FakeTimeProvider` before checking stuck refunds. The number was not measuring agent latency at all — it was replaying a simulated wait baked into test scripts, averaged in with genuine (fast) resolutions from any real scheduler activity, because both write to the same `Order`/`PolicyAction` tables with no way to distinguish real wall-clock timestamps from simulated ones. This is worse than an unlabeled-scope issue: no label could have made it honest. Fixed by removing `agent_mttr_seconds` from `/api/summary` entirely rather than trying to caveat it, and surfacing `evals/results/report.json`'s `mttr.agent_resolution` (computed within one single-clock eval run, and already correctly written as descriptive text, e.g. "same reconciliation cycle (minutes, mock mode)") in the eval batch panel instead, where it had existed in the API response all along but was never actually rendered.

**A third, unrelated issue surfaced while verifying the fix live:** `/api/summary` and `/api/limbo-queue` intermittently failed in the browser preview with `ERR_CONNECTION_REFUSED` even though `curl` against the same endpoint always returned 200. Timing revealed why: `reconciliation_results` had grown to 109,176 rows from repeated verification/eval runs across the session, and `latest_results_by_order()` pulled every row into Python to dedupe by hand — 2.2-2.6 seconds per request, and under the dashboard's 5 concurrent polling requests every 5 seconds, that occasionally tipped over into a dropped connection. Root cause was actually two compounding problems: the query itself (fixed by rewriting `latest_results_by_order` as a SQL `ROW_NUMBER() OVER (PARTITION BY order_id ...)` window-function query instead of a full-table Python dedup — verified byte-for-byte identical output against the old logic before replacing it, 14x faster, 2.6s to 0.18s), and `ReconciliationEngine.run()` writing a brand-new row for every candidate order on every single cycle even when the classification hadn't changed — which, now that Phase 8 wired up a live scheduler running every 60 seconds, would have grown this table forever. Fixed by skipping the write when the new classification matches the prior latest one for that order. Both fixes verified: identical query results before/after, full pytest + all phase scripts re-run clean, and the dashboard reloaded repeatedly in the live browser preview with zero further connection errors.

## ADR-026: Dashboard visual redesign — no fabricated numbers for the sake of a chart

Rebuilt the presentation layer only (dark palette, hero metric tiles, a donut chart for limbo-queue-by-state, a resolution-time comparison chart, a sortable/searchable/paginated queue table, a healthy-state exception panel, and an audit trail that collapses idempotency-guard skip pairs into one row) — no backend logic or API response shape changed.

The one design tension worth recording: the requested "MTTR: agent resolution vs. baseline" chart needs a numeric numbers for both sides, but ADR-025 already established that no honest per-transaction agent MTTR figure exists (the live one was fabricated from mixed real/simulated clocks, and even the eval batch's `mttr.agent_resolution` is descriptive text, not a number, because within one shared FakeTimeProvider run the "detection to action" delta really is ~0 by construction). Rather than inventing a plausible-looking number to plot, `MttrChart.tsx` uses `RECON_INTERVAL_SECONDS`'s documented default (60s, from `.env.example`) as the comparison value, labeled explicitly as a system-design bound, not a measurement, with a code comment cross-referencing ADR-025. The chart still makes the intended point emphatically (a log-scale bar comparing 21 days to ~1 minute), without resurrecting the honesty problem the previous fix removed.

## ADR-027: Detection was never actually audit-logged

Guardrail #6 promises "every detection, diagnosis, decision, action, and outcome is written append-only to audit_log." Diagnosis, decision, action, and outcome all were — but `ReconciliationEngine.run()` never wrote a `stage="detection"` entry; it only wrote the `ReconciliationResult` row itself, which is state, not an audit trail entry. This went unnoticed through every phase's own verification, since none of them specifically checked for a detection-stage audit row. Found while building `scripts/trace_single_transaction.py` (a full single-transaction terminal trace for demo/interview purposes) — asked to show "recon engine ka detection log" and there wasn't one to show. Fixed: `run()` now writes an audit entry (`stage="detection"`, `event=<state>`, reasoning noting the prior state if any) alongside every new classification, only on the same state-change path already added in ADR-025's dedup fix, so no extra bloat over the existing write.

## ADR-020: RazorpayAdapter.create_refund must write to Limbo's own ledger, not just call the API

The first real Phase 6 run executed a real refund successfully (`status: EXECUTED`) but then crashed looking up the local `Refund` row — because `RazorpayAdapter.create_refund` only called the real Razorpay API and fired the webhook sink; it never wrote a `Refund` row to Limbo's own database, unlike `MockGatewayAdapter.create_refund`. This meant a real, successful refund would have been invisible to `classify()`, the dashboard, and every other consumer of local state — a real transaction with money genuinely moved, but with Limbo's own audit trail blind to it.

Fixed: `RazorpayAdapter.create_refund` now writes a real `Refund` row using the actual API response (id, amount, status, timestamps), and only marks the underlying `Payment` `REFUNDED` once the cumulative `amount_refunded` from Razorpay actually covers the full captured amount — a partial refund (as happened here, capped by ADR-019) correctly leaves the payment `CAPTURED` with money still outstanding, rather than being marked as if fully resolved.

This bug was only found by running the full real lifecycle end to end — no unit test exercises the real Razorpay API, and the mock adapter's equivalent code path was already correct, so nothing in the existing test suite could have caught this divergence between the two adapters.


## ADR-028: The PayPal investigator reads, the policy engine acts

**Context.** For the PayPal AI Hackathon the diagnosis step became a tool-using agent that checks a suspect transaction against PayPal itself (capture status, linked refunds, open disputes) before concluding. Giving an LLM live payment-API access raises the obvious risk: a confused or manipulated agent moving money.

**Decision.** The agent gets three read-only tools built on `PayPalAdapter.get`, which refuses every method but GET. Each tool is scoped to the capture under investigation; refund ids are allowed only after PayPal itself lists them on that capture. The loop is capped at six turns and the final, structured answer is requested with `tool_choice: none`. Every tool call is appended to the audit log. Refunds, replays and escalations still come only from `PolicyEngine`, under the existing idempotency, amount, retry, circuit-breaker and dry-run guardrails (ADR-017 unchanged). Any LLM failure, refusal or missing key falls back to the deterministic classifier.

**Consequences.** The agent adds evidence and explanation (including a draft buyer message and, for disputes, a draft response for a human), never authority. Judges and merchants can audit exactly which PayPal calls informed each decision.

## ADR-029: The investigator runs on Gemini's free tier, not Claude

The hackathon build first ran the investigator on Claude. The hosted demo needs an always-on API key, and there was no budget for paid API credits, so the agent was ported to the Gemini API's free tier (`google-genai`, default model `gemini-2.5-flash`, overridable with `GEMINI_MODEL`). The design is unchanged: the same three read-only PayPal tools, the same six-turn cap, every tool call audited, and the policy engine alone moves money (ADR-017, ADR-028). One structural change: the concluding structured-output call is a separate request with no tools, and the PayPal lookups are passed into it as data, so it behaves the same on every Gemini model. Free-tier rate limits (HTTP 429) are just another failure: `diagnose_llm` returns None and the deterministic classifier takes over, so the demo never breaks. The Gemini client has a 60 s timeout, low thinking (default thinking ran past the deadline with 504 DEADLINE_EXCEEDED) and no SDK retries, so a 429 falls back at once instead of holding a request open. The background reconciliation loop re-diagnoses every actionable order on every cycle, which would exhaust the free daily quota in minutes, so it uses the deterministic classifier; the Gemini agent runs on the per-order demo pipeline and on dispute webhooks. The default model is `gemini-3.5-flash-lite`: `gemini-3.8-flash` on the free tier still hit 504 DEADLINE_EXCEEDED in production even with low thinking, and `gemini-2.5-flash` returns 404 for new API users.
