# Deployment

Three services, deployed manually (this repo does not deploy anything itself):

| Service | Where | What |
|---|---|---|
| `dashboard` | Vercel | Next.js dashboard, including `/demo` |
| `limbo-api` | Render Web Service (Docker) | FastAPI app — reads and serves; also runs the demo endpoints |
| `limbo-worker` | Render Background Worker (Docker) | The reconciliation/diagnosis/policy loop, as its own process — see below |
| Postgres | Supabase | Replaces the docker-compose Postgres for deployed environments only; local dev is unchanged |

A fresh Supabase database gets its indexes for free from `Base.metadata.create_all` on first startup. If you're pointing at a local dev Postgres that already existed before these indexes were added to the models, `create_all` won't retroactively add them — run `python -m scripts.add_missing_indexes` once against it.

`render.yaml` in the repo root defines both Render services (Blueprint deploy). No `vercel.json` is needed — Vercel auto-detects the Next.js app once the project's Root Directory is set to `dashboard/` in its own settings; there's nothing project-specific to declare beyond that.

## Why the worker is a separate service

`limbo-api` and `limbo-worker` build from the same Docker image but run different commands: the API runs `uvicorn app.main:app`, the worker runs `python -m app.worker`. Both must never run the reconciliation loop at the same time — `RUN_EMBEDDED_SCHEDULER=false` on the web service turns off its local dev convenience of running the loop in-process (which local docker-compose still does, unchanged, since it only runs one `api` service). The loop itself takes a Postgres advisory lock each cycle, so even a brief overlap during a Render deploy (old and new instances both alive momentarily) can't double-execute a recovery action — see `app/scheduler.py`.

## Env vars per service

Names only — set actual values directly in each platform's dashboard, never committed.

### `limbo-api` (Render web service)

`DATABASE_URL`, `GATEWAY_MODE`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `ANTHROPIC_API_KEY`, `ORDER_FULFILLABLE_WINDOW_MINUTES`, `REFUND_SETTLEMENT_SLA_HOURS`, `MAX_AUTO_AMOUNT_PAISE`, `MAX_RETRY_ATTEMPTS`, `CIRCUIT_BREAKER_FALSE_INTERVENTION_RATE`, `DRY_RUN`, `BASELINE_RESOLUTION_DAYS`, `RECON_INTERVAL_SECONDS`, `RUN_EMBEDDED_SCHEDULER` (must be `false`), `CORS_ALLOW_ORIGINS` (the deployed Vercel origin, e.g. `https://limbo-demo.vercel.app`), `DASHBOARD_API_KEY`, `DEMO_ORDER_AMOUNT_PAISE`, `DEMO_MAX_AMOUNT_PAISE`, `DEMO_SESSION_TTL_MINUTES`, `DEMO_FULFILLABLE_WINDOW_SECONDS`, `DEMO_RATE_LIMIT_PER_MINUTE`.

`RAZORPAY_KEY_ID` **must** start with `rzp_test_` — the app refuses to start otherwise (see `app/config.py`). There is no override; a live key is never loadable here.

`DASHBOARD_API_KEY` gates the 5 read-only dashboard endpoints (`/api/summary`, `/api/limbo-queue`, `/api/exceptions`, `/api/audit-log`, `/api/eval-report`) behind a shared secret, checked as the `X-API-Key` header — leave it unset locally to keep dev open. This is a light gate against casual/automated access to a public URL, **not real authentication**: the dashboard ships the same key to the browser via `NEXT_PUBLIC_DASHBOARD_API_KEY`, so anyone who opens the deployed frontend's network tab can read it. It stops a stranger who only has the bare API URL, not a motivated one inspecting the frontend. The `/api/demo/*` endpoints are unaffected — they use their own per-order token model instead.

### `limbo-worker` (Render background worker)

Same list minus `CORS_ALLOW_ORIGINS`, `RUN_EMBEDDED_SCHEDULER`, and the `DEMO_*` vars — the worker never serves HTTP and doesn't need them.

### `dashboard` (Vercel)

`NEXT_PUBLIC_API_URL` env var name used by this project is `NEXT_PUBLIC_API_BASE_URL` (matches the existing local `.env.example`) — set it to the deployed `limbo-api` URL, e.g. `https://limbo-api.onrender.com`. `NEXT_PUBLIC_DASHBOARD_API_KEY` must match `limbo-api`'s `DASHBOARD_API_KEY` if that's set.

## Free-tier caveats

- **Render cold start:** a free-tier web service or worker spins down after ~15 minutes idle and takes roughly 30-50 seconds to wake on the next request. The `/demo` page's first "Make Test Payment" or "Run Failure Scenario" click after a period of inactivity can hang for that long before anything happens — this is Render waking the container, not a bug. If demoing live to an evaluator, hit `limbo-api`'s `/health` a minute beforehand to warm it up, or say so up front.
- **Render worker cold start:** the background worker has the same spin-down behavior, which means the live reconciliation loop can be stopped for a while between demos. The demo's on-demand accelerated cycle (used by both scenarios) does not depend on the worker being awake — it runs synchronously inside the `limbo-api` request itself — so the guided demo works even if the worker is asleep. The worker matters for the *general* dashboard's live totals staying current between demos.
- **Supabase pause:** a free-tier Supabase project pauses after 7 days with no activity and must be manually resumed from the Supabase dashboard before `limbo-api`/`limbo-worker` can connect again.

## Rollback

Neither Render nor Vercel need anything built into this app for rollback — both keep a history of prior deploys and support reverting to one with a couple of clicks in their own dashboards (Render: the service's "Deploys" tab; Vercel: the project's "Deployments" tab, "Promote to Production" on an older one). If a bad deploy ships, roll back there rather than trying to hotfix forward under pressure.

## Manual deployment checklist

1. **Supabase**: create a project, copy its Postgres connection string, convert it to `postgresql+asyncpg://...` form for `DATABASE_URL`.
2. **Render — `limbo-api`**: create the Blueprint from `render.yaml` (or a manual Web Service pointed at `backend/Dockerfile`), fill in the env vars listed above, deploy, confirm `GET /health` returns `200`.
3. **Render — `limbo-worker`**: same Blueprint creates it automatically alongside `limbo-api`; if created manually, set its start command to `python -m app.worker` and fill in its (shorter) env var list.
4. **Vercel**: import the repo, set Root Directory to `dashboard/`, set `NEXT_PUBLIC_API_BASE_URL` to the `limbo-api` URL from step 2, deploy.
5. **Close the loop**: set `CORS_ALLOW_ORIGINS` on `limbo-api` to the Vercel URL from step 4 (Render redeploys on env var change).
6. **Smoke test**: open the Vercel URL's `/demo` page, run "Run Failure Scenario" for both scenarios, confirm the event stream and outcome card. Then try "Make Test Payment" with a real Razorpay test card to confirm the live checkout path end-to-end.
