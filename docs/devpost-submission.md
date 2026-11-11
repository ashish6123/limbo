# Devpost submission: Limbo

Copy each section into the matching Devpost field.

## Tagline

An AI agent that finds PayPal payments merchants never saw and recovers them before they turn into disputes.

## Inspiration

A buyer pays with PayPal and PayPal captures the money. Then the merchant's webhook handler crashes, so the order is cancelled and nobody issues a refund. Weeks later the buyer opens a dispute, and the merchant pays the dispute fee and loses the sale. This happens to every payment system that relies on webhooks, and today the cleanup is manual.

## What it does

Limbo continuously reconciles PayPal captures against the merchant's orders. When the two disagree (paid but cancelled, refund stuck, duplicate payment), it:

1. **Detects** the mismatch and classifies it into one of six states.
2. **Investigates** with a Gemini agent that calls PayPal's APIs, read-only, to confirm the capture, check linked refunds and look for open disputes.
3. **Explains** with a structured root cause and confidence, a recommended action and a draft message to the buyer.
4. **Acts** through a deterministic policy engine, never the AI: it replays the webhook to complete the order, issues a real PayPal refund, or escalates to a human. Idempotency keys, amount caps, retry limits, a circuit breaker and dry-run mode apply.
5. **Escalates disputes:** a `CUSTOMER.DISPUTE.CREATED` webhook on a tracked payment goes straight to a human, with a Gemini-drafted response built only from the audit trail.

Every step is written to an append-only audit log, which the live demo streams in real time.

## How we built it

- **PayPal (sandbox):** Orders v2 (create, capture), Payments v2 (capture lookup, refunds with `PayPal-Request-Id` idempotency, refund status), Disputes API (read), Webhooks with verify-webhook-signature. `PAYPAL_BASE_URL` is locked to the sandbox in code.
- **AI:** Gemini (`gemini-2.5-flash`) through the Gemini API (`google-genai`). A bounded function-calling loop (six turns at most) with three read-only PayPal tools, then a separate tool-free call with a strict JSON schema (Pydantic) for the diagnosis. A separate call drafts dispute responses. A deterministic fallback keeps everything working without an API key.
- **Backend:** FastAPI, SQLAlchemy async, Postgres; the reconciliation loop runs inside the API process.
- **Frontend:** Next.js 14; the Limbo queue is built on AG Grid Community; live demo over server-sent events.
- **Hosting:** Render (API and dashboard via `render.yaml`), Supabase Postgres.

## Challenges we ran into

- PayPal sandbox payments need a buyer to approve them, so the demo does a real approve-then-capture round trip instead of faking a capture.
- Giving an LLM payment-API access safely. The answer: the agent's PayPal access refuses anything but GET, each tool is scoped to the one capture under investigation, and only the policy engine can move money.
- Making the metrics honest: the eval reports detection and diagnosis against injected ground truth, and the 0 revenue-recovered figure in one batch is explained rather than tuned away.

## Accomplishments we're proud of

- A full real sandbox lifecycle: real PayPal order → capture → dropped webhook → agent investigation through PayPal APIs → policy decision → real PayPal refund, all visible step by step.
- On a 500-transaction synthetic batch: 100% detection, 100% diagnosis accuracy, 0% false interventions.
- An agent design where the AI adds evidence and explanation but never authority.

## What we learned

The most useful agent in payments is often one that protects money rather than spends it, and the safety case only works if the guardrails are deterministic and auditable.

## What's next

Transaction Search for bulk backfills, Venmo and Pay Later flows, and merchant-configurable policies per amount band.

## Built with

paypal-orders-v2, paypal-payments-v2, paypal-webhooks, paypal-disputes, gemini, google-genai, python, fastapi, postgresql, sqlalchemy, nextjs, typescript, ag-grid, tailwindcss, render, supabase

## AI tools used

- **Gemini API (`gemini-2.5-flash`)** inside the product: function calling with read-only PayPal tools, structured JSON output for diagnosis, dispute-response drafting.
- **Claude Code** as a coding assistant during development.

## Testing instructions (for judges)

- Live demo: https://limbo-ochre.vercel.app/demo (the API wakes from Render free-tier sleep in about 40 s on the first click).
- **Make Test Payment** opens a real PayPal sandbox checkout. Log in with this sandbox buyer account: `<sandbox buyer email>` / `<sandbox buyer password>` (sandbox only, no real money), click Pay, return to the demo tab and click **I've completed the payment**.
- **Run Failure Scenario** runs the same pipeline on a scripted mock payment, with no login.
- Run locally: see "Run PayPal mode locally" in the README.

## Video outline (under 3:00, no copyrighted music)

1. **0:00–0:25** The problem: PayPal says paid, the merchant says failed.
2. **0:25–1:30** Live run on /demo: approve the sandbox payment, the webhook is dropped, detection, the agent's PayPal tool calls in the stream, the diagnosis, the policy decision, then the real refund id.
3. **1:30–2:10** Safety: the agent has no money tools; show the caps, idempotency, circuit breaker and an escalation to a human.
4. **2:10–2:45** Impact: eval results (100% detection, 0% false interventions), and recovery in minutes instead of 21 days.
5. **2:45–3:00** Who it's for, plus the repo and live links.
