# Limbo

Agentic reconciliation and recovery for payments stuck between "the gateway says it worked" and "the merchant says it didn't."

## The problem

A customer pays on a merchant app. The payment gateway marks the payment successful and debits the money. But the success webhook to the merchant is lost, delayed, or fails mid-processing. The merchant's system shows "payment gateway failure," cancels the order, and — because its own records say the payment failed — never triggers a refund.

The money sits in limbo: successful on the gateway, non-existent on the merchant. The gateway thinks it was paid. The merchant thinks it failed. The customer thinks they were charged for nothing. Today, resolving this takes weeks of manual customer escalation, if it gets resolved at all.

This is not a hypothetical. It's a real failure mode of asynchronous payment systems, and it happens at scale on every payment platform that relies on webhooks.

## What Limbo does

Limbo is an agentic system that continuously reconciles gateway payment records against merchant order records, classifies every mismatch into one of six states, diagnoses the root cause from observable evidence, and executes a bounded recovery action — complete the order, replay the webhook, auto-refund, or escalate to a human — with hard guardrails: idempotency, amount ceilings, retry limits, a circuit breaker, and an immutable audit trail.

It reports measured money recovered across a synthetic 500-transaction batch, and has been verified end to end against one real Razorpay test-mode payment — real order, real webhook-drop simulation, real reconciliation, real refund executed via the live API.

## Architecture

![Architecture](docs/diagrams/architecture.png)

Full diagrams, with narration, in `docs/`:

- [`docs/architecture.md`](docs/architecture.md) — components and data flow
- [`docs/state-machine.md`](docs/state-machine.md) — the six-state classification and guardrail-forced escalations
- [`docs/recovery-flow.md`](docs/recovery-flow.md) — one full `LIMBO_PAID_CANCELLED` lifecycle, sequence diagram
- [`docs/failure-injection-map.md`](docs/failure-injection-map.md) — where each of the six chaos classes strikes and what footprint it leaves
- [`docs/decisions.md`](docs/decisions.md) — ADR-style log of every non-obvious design decision, including real bugs found while building and how they were fixed

**Where the LLM is used, and where it deliberately isn't:** deterministic code owns reconciliation diffing, guardrail enforcement, idempotency, and metrics. The LLM (Claude) is used only for root-cause narration and ambiguous-case triage — with a deterministic fallback that runs identically offline, and a hard rule that the LLM path can never route directly to a money-moving action. See `docs/decisions.md` ADR-017.

## Quickstart

Requires Docker and Python 3.11+. An `ANTHROPIC_API_KEY` is optional — every LLM call site degrades to a deterministic fallback without one.

```bash
cp .env.example .env
make setup
make up          # starts Postgres
make demo        # runs the 500-transaction eval batch in mock mode
```

Then, optionally:

```bash
make api         # FastAPI backend on :8000
make dashboard   # Next.js dashboard on :3000
```

Run the full verification suite (unit tests + all seven phase scripts):

```bash
make verify-all
```

Regenerate the eval report on demand:

```bash
make eval        # writes evals/results/report.md and report.json
```

Switch to real Razorpay test-mode calls (order creation, refunds, webhook signature verification are all real API calls; see `docs/decisions.md` ADR-018 for why payment *creation* still requires one manual checkout):

```bash
# in .env: GATEWAY_MODE=razorpay, RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET
make razorpay-checkout   # prints a real test-mode payment link
# pay it with a Razorpay test card, then:
cd backend && python -m scripts.verify_phase6 <order_id> <payment_id>
```

## Results

Latest 500-transaction eval batch (mock mode, `make eval`):

| Metric | Result |
|---|---|
| Detection accuracy (recon vs. ground truth) | 100.0% |
| Diagnosis accuracy (vs. chaos ground truth) | 100.0% |
| Loss prevented (refunds executed) | ₹28,366.00 |
| Revenue recovered (webhook replay) | ₹0.00 — see note below |
| False-intervention rate | 0.00% (circuit breaker threshold: 2%) |
| MTTR baseline (documented real-world manual escalation) | 21 days |
| MTTR, this run | same reconciliation cycle (minutes, mock mode) |
| Escalated exceptions | 0 |

Full breakdown, including per-state precision/recall: [`evals/results/report.md`](evals/results/report.md).

**On the ₹0 revenue-recovered figure:** this batch's timeline always reconciles after the fulfillable window has already elapsed, so `LIMBO_PAID_CANCELLED` cases in this particular run resolve via `AUTO_REFUND`, not `REPLAY_WEBHOOK`. `REPLAY_WEBHOOK` is real and verified separately — Phase 4's dedicated within-window scenario proves it end to end. This is reported honestly (`docs/decisions.md` ADR-021) rather than engineered to populate both buckets artificially. Honest metrics over massaged ones was a standing rule for this build.

One real Razorpay test-mode recovery lifecycle was also completed: a real payment (`pay_TSrHOuMZY7szwc`), a simulated webhook drop, correct detection and `AUTO_REFUND` decision, and a real refund (`rfnd_TTAg77RyCFBQrL`) executed and confirmed against the live API. See ADR-018/019/020 for the two real API constraints and one real bug that surfaced along the way.

## Project layout

```
limbo/
├── backend/           FastAPI + SQLAlchemy + Postgres. app/{merchant,gateway,chaos,recon,diagnosis,policy,audit,api}/
├── dashboard/          Next.js 14 single-page dashboard
├── evals/              generator.py, runner.py, scenarios/, results/
├── docs/                architecture, state machine, recovery flow, failure map, decisions
└── docker-compose.yml
```

## Guardrails

Every recovery action is idempotent (keyed on `{order_id}:{action_type}`), capped at `MAX_AUTO_AMOUNT_PAISE` (default ₹5,000) above which it escalates to a human, retried at most `MAX_RETRY_ATTEMPTS` times with exponential cooldown before escalating, and gated by a circuit breaker that halts all auto-actions if the false-intervention rate exceeds `CIRCUIT_BREAKER_FALSE_INTERVENTION_RATE` (default 2%). `DRY_RUN=true` is the default in `.env.example` — every action is proposed and audit-logged but not executed until explicitly turned off. Every detection, diagnosis, decision, action, and outcome is written append-only to the audit log.
