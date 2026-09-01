# System Architecture

Components and data flow for Limbo's reconciliation and recovery pipeline. Solid arrows are synchronous calls; dashed arrows are webhook deliveries (real or simulated).

```mermaid
flowchart TB
    subgraph gw["Gateway (GATEWAY_MODE)"]
        Mock["MockGatewayAdapter"]
        Real["RazorpayAdapter (real test-mode API)"]
    end

    Merchant["Merchant service<br/>(orders, webhook receiver, timeout sweep)"]
    Chaos["Chaos engine<br/>(6 failure classes)"]
    Recon["Reconciliation engine<br/>(6-state classifier)"]
    Diagnosis["Diagnosis agent<br/>(LLM + deterministic fallback)"]
    Policy["Policy engine<br/>(guardrails: idempotency, ceiling,<br/>retries, circuit breaker, DRY_RUN)"]
    Audit[("Audit log<br/>(append-only)")]
    DB[("Postgres<br/>orders / payments / refunds /<br/>webhook_events / chaos_injections /<br/>reconciliation_results / policy_actions /<br/>diagnosis_records / audit_log")]
    API["FastAPI REST layer"]
    Dashboard["Next.js dashboard"]
    EvalHarness["Eval harness<br/>(generator + runner)"]

    gw -- "payment.captured /<br/>refund.processed" --> Chaos
    Chaos -. "webhook (maybe dropped,<br/>delayed, 500, reordered,<br/>duplicated)" .-> Merchant
    Merchant --> DB
    gw --> DB

    Recon -- reads --> DB
    Recon -- "writes ReconciliationResult" --> DB

    Diagnosis -- "reads webhook_events,<br/>payment, refund evidence" --> DB
    Diagnosis -- "writes DiagnosisRecord" --> DB
    Diagnosis -.->|"never reads"| ChaosGT[("chaos_injections<br/>(eval ground truth)")]

    Policy -- "reads ReconciliationResult" --> DB
    Policy -- "REPLAY_WEBHOOK / AUTO_REFUND /<br/>RETRIGGER_REFUND / DEDUP_REFUND" --> gw
    Policy -- "writes PolicyAction" --> DB
    Policy --> Audit
    Merchant --> Audit
    Recon -.-> Audit
    Diagnosis --> Audit
    Audit --> DB

    API -- reads --> DB
    Dashboard -- "polls every 5s" --> API

    EvalHarness -- "drives batch through<br/>Chaos + Recon + Diagnosis + Policy" --> DB
    EvalHarness -- "scores against chaos_injections<br/>(eval-only)" --> ChaosGT
    EvalHarness -- "writes report.md / report.json" --> Reports[("evals/results/")]
    API -. "serves latest report" .-> Reports
```

Key boundaries:

- **Gateway is swappable** behind one interface (`GatewayAdapter`) — mock and real Razorpay implementations, same call sites. See `docs/decisions.md` ADR-004, ADR-018.
- **Diagnosis never reads chaos ground truth** — only the eval harness does, and only to score results after the fact, never to inform them. See ADR-007, ADR-017.
- **Every stage writes to the audit log** — detection, diagnosis, decision, action, outcome — append-only, never updated or deleted.
