# Deployment

Two services, deployed manually (this repo does not deploy anything itself):

| Service | Where | What |
|---|---|---|
| `dashboard` | Vercel | Next.js dashboard, including `/demo` |
| `limbo-api` | Render Web Service (Docker) | FastAPI app, demo endpoints, and the reconciliation loop, embedded |
| Postgres | Supabase | Replaces the docker-compose Postgres for deployed environments only; local dev is unchanged |

A fresh Supabase database gets its indexes for free from `Base.metadata.create_all` on first startup. If you're pointing at a local dev Postgres that already existed before these indexes were added to the models, `create_all` won't retroactively add them — run `python -m scripts.add_missing_indexes` once against it.

`render.yaml` in the repo root defines the Render service (Blueprint deploy). No `vercel.json` is needed — Vercel auto-detects the Next.js app once the project's Root Directory is set to `dashboard/` in its own settings; there's nothing project-specific to declare beyond that.

## Why there's no separate worker service

The original design ran the reconciliation loop as its own Render Background Worker, separate from the web process. Render's free plan doesn't offer Background Workers at all (paid plans only), so `render.yaml` instead sets `RUN_EMBEDDED_SCHEDULER=true` on `limbo-api`, which runs the loop in-process — the same code path local docker-compose dev already uses via `app/main.py`'s lifespan hook, now just doing double duty in production too. The tradeoff: the loop only runs while `limbo-api` is awake, so it stops during Render's free-tier idle spin-down (see below). This doesn't break the guided demo — its on-demand accelerated cycle runs synchronously inside the request itself, not off the background loop — it only means the *general* dashboard's live totals go stale between visits. If you ever move to a paid Render plan, add back a `type: worker` service running `python -m app.worker` and flip `RUN_EMBEDDED_SCHEDULER` to `false` — `app/scheduler.py`'s Postgres advisory lock already guards against the two ever double-executing a recovery action if you run both at once.

## Env vars per service

Names only — set actual values directly in each platform's dashboard, never committed.

### `limbo-api` (Render web service)

**PayPal mode (hackathon deployment):** `GATEWAY_MODE=paypal`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID` (optional; enables `POST /webhooks/paypal`), `PAYPAL_CURRENCY=USD`, `PUBLIC_DASHBOARD_URL` (where PayPal returns the buyer after approval, e.g. `https://limbo-ochre.vercel.app`), and `DEMO_ORDER_AMOUNT_PAISE` / `DEMO_MAX_AMOUNT_PAISE` / `MAX_AUTO_AMOUNT_PAISE` in cents. `PAYPAL_BASE_URL` defaults to, and is locked to, the sandbox. `render.yaml` also defines an optional `limbo-dashboard` service, so the whole app can run on Render instead of Vercel.

Razorpay mode and shared: `DATABASE_URL`, `GATEWAY_MODE`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `GEMINI_API_KEY`, `ORDER_FULFILLABLE_WINDOW_MINUTES`, `REFUND_SETTLEMENT_SLA_HOURS`, `MAX_AUTO_AMOUNT_PAISE`, `MAX_RETRY_ATTEMPTS`, `CIRCUIT_BREAKER_FALSE_INTERVENTION_RATE`, `DRY_RUN`, `BASELINE_RESOLUTION_DAYS`, `RECON_INTERVAL_SECONDS`, `RUN_EMBEDDED_SCHEDULER` (`true` on the free plan — see above), `CORS_ALLOW_ORIGINS` (the deployed Vercel origin, e.g. `https://limbo-demo.vercel.app`), `DASHBOARD_API_KEY`, `DEMO_ORDER_AMOUNT_PAISE`, `DEMO_MAX_AMOUNT_PAISE`, `DEMO_SESSION_TTL_MINUTES`, `DEMO_FULFILLABLE_WINDOW_SECONDS`, `DEMO_RATE_LIMIT_PER_MINUTE`.

`RAZORPAY_KEY_ID` **must** start with `rzp_test_` — the app refuses to start otherwise (see `app/config.py`). There is no override; a live key is never loadable here.

`RAZORPAY_WEBHOOK_SECRET` and `GEMINI_API_KEY` can both be left blank. Nothing calls webhook signature verification (there's no live webhook receiver — ADR-018), and diagnosis falls back to its deterministic classifier automatically without an LLM key (ADR-017) — recovery still works either way, you just lose the LLM-written narrative sentence in the diagnosis stage.

`DASHBOARD_API_KEY` gates the 5 read-only dashboard endpoints (`/api/summary`, `/api/limbo-queue`, `/api/exceptions`, `/api/audit-log`, `/api/eval-report`) behind a shared secret, checked as the `X-API-Key` header — leave it unset to keep the dashboard open (simplest to start with). This is a light gate against casual/automated access to a public URL, **not real authentication**: the dashboard ships the same key to the browser via `NEXT_PUBLIC_DASHBOARD_API_KEY`, so anyone who opens the deployed frontend's network tab can read it. It stops a stranger who only has the bare API URL, not a motivated one inspecting the frontend. The `/api/demo/*` endpoints are unaffected — they use their own per-order token model instead.

### `dashboard` (Vercel)

`NEXT_PUBLIC_API_URL` env var name used by this project is `NEXT_PUBLIC_API_BASE_URL` (matches the existing local `.env.example`) — set it to the deployed `limbo-api` URL, e.g. `https://limbo-api.onrender.com`. `NEXT_PUBLIC_DASHBOARD_API_KEY` must match `limbo-api`'s `DASHBOARD_API_KEY` if that's set.

## Free-tier caveats

- **Render cold start:** a free-tier web service spins down after ~15 minutes idle and takes roughly 30-50 seconds to wake on the next request. The `/demo` page's first "Make Test Payment" or "Run Failure Scenario" click after a period of inactivity can hang for that long before anything happens — this is Render waking the container, not a bug. If demoing live to an evaluator, hit `limbo-api`'s `/health` a minute beforehand to warm it up, or say so up front. While asleep, the embedded reconciliation loop is also stopped — the guided demo doesn't care (its accelerated cycle runs synchronously in the request), but the *general* dashboard's live totals go stale until the next visit wakes it.
- **Supabase pause:** a free-tier Supabase project pauses after 7 days with no activity and must be manually resumed from the Supabase dashboard before `limbo-api` can connect again.

## Rollback

Neither Render nor Vercel need anything built into this app for rollback — both keep a history of prior deploys and support reverting to one with a couple of clicks in their own dashboards (Render: the service's "Deploys" tab; Vercel: the project's "Deployments" tab, "Promote to Production" on an older one). If a bad deploy ships, roll back there rather than trying to hotfix forward under pressure.

## Manual deployment checklist

1. **Supabase**: create a project, copy its Postgres connection string, convert it to `postgresql+asyncpg://...` form for `DATABASE_URL`.
2. **Render — `limbo-api`**: create the Blueprint from `render.yaml` (or a manual Web Service pointed at `backend/Dockerfile`), fill in the env vars listed above, deploy, confirm `GET /health` returns `200`.
3. **Vercel**: import the repo, set Root Directory to `dashboard/`, set `NEXT_PUBLIC_API_BASE_URL` to the `limbo-api` URL from step 2, deploy.
4. **Close the loop**: set `CORS_ALLOW_ORIGINS` on `limbo-api` to the Vercel URL from step 3 (Render redeploys on env var change).
5. **Smoke test**: open the Vercel URL's `/demo` page, run "Run Failure Scenario" for both scenarios, confirm the event stream and outcome card. Then try "Make Test Payment", approve it with a PayPal sandbox buyer account (Razorpay mode: a Razorpay test card), and confirm the live checkout path end-to-end.
