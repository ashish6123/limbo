# Recovery Flow — One Full LIMBO_PAID_CANCELLED Lifecycle

The flagship case: payment succeeds on the gateway, the webhook that would tell the merchant is lost, the merchant cancels the order believing it failed, and Limbo detects and recovers the loss.

```mermaid
sequenceDiagram
    participant Customer
    participant Gateway
    participant Chaos as Chaos engine
    participant Merchant as Merchant service
    participant Recon as Reconciliation engine
    participant Diagnosis as Diagnosis agent
    participant Policy as Policy engine
    participant Audit as Audit log

    Customer->>Gateway: pay ₹499
    Gateway->>Gateway: capture payment (CAPTURED)
    Gateway->>Chaos: emit payment.captured webhook
    Chaos--xMerchant: webhook dropped (never delivered)

    Note over Merchant: order stays AWAITING_PAYMENT

    Merchant->>Merchant: timeout sweep:<br/>fulfillable window elapses
    Merchant->>Merchant: order -> CANCELLED
    Merchant->>Audit: log cancellation

    Note over Gateway,Merchant: gateway thinks: paid.<br/>merchant thinks: failed.<br/>customer thinks: charged for nothing.

    Recon->>Gateway: read payment (CAPTURED)
    Recon->>Merchant: read order (CANCELLED)
    Recon->>Recon: classify() -> LIMBO_PAID_CANCELLED
    Recon->>Audit: log detection

    Diagnosis->>Merchant: read webhook_events (empty)
    Diagnosis->>Diagnosis: no evidence of delivery,<br/>payment captured, order cancelled<br/>-> root_cause: webhook_dropped
    Diagnosis->>Audit: log diagnosis + narrative

    Policy->>Policy: decide(LIMBO_PAID_CANCELLED,<br/>within_window=False) -> AUTO_REFUND
    Policy->>Policy: guardrails: amount under ceiling,<br/>no prior attempts, circuit breaker closed
    Policy->>Audit: log decision

    alt DRY_RUN=true (default)
        Policy->>Audit: log proposed action, do not execute
    else DRY_RUN=false
        Policy->>Gateway: create_refund(payment_id, amount)
        Gateway->>Gateway: refund processed
        Gateway->>Chaos: emit refund.processed webhook
        Policy->>Policy: idempotency_key = order:AUTO_REFUND<br/>recorded EXECUTED
        Policy->>Audit: log outcome: EXECUTED
    end

    Note over Customer: refund received —<br/>loss prevented, not silently absorbed
```

A re-run of the agent over the same transaction is safe: `policy.act()` checks for a prior `EXECUTED` attempt under the same idempotency key (`{order_id}:{action_type}`) before doing anything, and returns `SKIPPED_IDEMPOTENT` without touching the gateway again.
