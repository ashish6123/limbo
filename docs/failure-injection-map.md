# Failure Injection Map

Where each of the six chaos classes strikes in the payment flow, and the footprint each one leaves for reconciliation and diagnosis to find.

```mermaid
flowchart LR
    Capture["Gateway captures<br/>payment"] --> Deliver{"Webhook<br/>delivery attempt"}

    Deliver -- "1. dropped" --> Dropped["No webhook_events row.<br/>Merchant never notified."]
    Deliver -- "2. delayed" --> Delayed["Delivered, but after the<br/>fulfillable window closed."]
    Deliver -- "3. merchant 500" --> Err500["Delivered, merchant crashed<br/>mid-processing. code=500,<br/>delivered=false."]
    Deliver -- "4. out of order" --> OOO["2 events; attempt 2 arrives<br/>before attempt 1."]
    Deliver -- "5. duplicated" --> Dup["2 events; same payload,<br/>ascending attempt numbers."]
    Deliver -- "healthy" --> OK["1 event, delivered promptly."]

    Dropped --> Sweep["Merchant timeout sweep"]
    Delayed --> Sweep
    Err500 --> Sweep
    Sweep --> Cancelled["Order CANCELLED,<br/>payment still CAPTURED"]
    Cancelled --> Limbo["recon: LIMBO_PAID_CANCELLED"]

    OOO --> Confirmed1["Order CONFIRMED<br/>(idempotent guard)"]
    Dup --> Confirmed2["Order CONFIRMED<br/>(idempotent guard)"]
    OK --> Confirmed3["Order CONFIRMED"]
    Confirmed1 --> Healthy1["recon: MATCHED_HEALTHY<br/>(anomaly visible only in<br/>webhook_events, for diagnosis)"]
    Confirmed2 --> Healthy2["recon: MATCHED_HEALTHY"]
    Confirmed3 --> Healthy3["recon: MATCHED_HEALTHY"]

    RefundInit["6. refund_stuck:<br/>strikes AFTER a healthy purchase —<br/>refund created INITIATED,<br/>settlement callback never arrives"] --> StuckCheck{"past<br/>REFUND_SETTLEMENT_SLA_HOURS?"}
    StuckCheck -- yes --> Stuck["recon: REFUND_STUCK"]
    StuckCheck -- no --> HealthyRefund["recon: MATCHED_HEALTHY<br/>(not yet overdue)"]
```

Only the first five classes touch webhook delivery for the *capture* event; `refund_stuck` is independent — it strikes the refund settlement callback on an otherwise-healthy transaction. See `docs/decisions.md` ADR-010 for why chaos never directly cancels an order (the merchant's own timeout sweep does), and ADR-013 for why `refund_stuck` needs its own, much longer, SLA window before it is classified as actually stuck.
