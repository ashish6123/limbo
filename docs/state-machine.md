# Transaction State Machine

The six terminal classifications produced by `recon/classifier.py`, and the guardrail-forced transitions to `ESCALATE_HUMAN` applied by the policy engine on top of them.

```mermaid
stateDiagram-v2
    [*] --> PENDING_WINDOW: order created,<br/>within fulfillable window

    PENDING_WINDOW --> MATCHED_HEALTHY: webhook delivered,<br/>order confirmed
    PENDING_WINDOW --> LIMBO_PAID_CANCELLED: window elapses,<br/>payment captured,<br/>order still unconfirmed

    MATCHED_HEALTHY --> [*]: no action needed

    LIMBO_PAID_CANCELLED --> REPLAY_WEBHOOK: still within<br/>fulfillable window
    LIMBO_PAID_CANCELLED --> AUTO_REFUND: past fulfillable window

    REFUND_STUCK --> RETRIGGER_REFUND
    DUPLICATE_PAYMENT --> DEDUP_REFUND
    INVERSE_LEAK --> ESCALATE_HUMAN: ambiguous by definition,<br/>never a direct money action

    REPLAY_WEBHOOK --> MATCHED_HEALTHY: order force-completed
    AUTO_REFUND --> [*]: loss prevented
    RETRIGGER_REFUND --> [*]: refund settled
    DEDUP_REFUND --> [*]: duplicate refunded

    REPLAY_WEBHOOK --> ESCALATE_HUMAN: amount over ceiling,<br/>or retry limit reached
    AUTO_REFUND --> ESCALATE_HUMAN: amount over ceiling,<br/>or retry limit reached
    RETRIGGER_REFUND --> ESCALATE_HUMAN: amount over ceiling,<br/>or retry limit reached
    DEDUP_REFUND --> ESCALATE_HUMAN: amount over ceiling,<br/>or retry limit reached

    LIMBO_PAID_CANCELLED --> ESCALATE_HUMAN: circuit breaker tripped
    REFUND_STUCK --> ESCALATE_HUMAN: circuit breaker tripped
    DUPLICATE_PAYMENT --> ESCALATE_HUMAN: circuit breaker tripped

    ESCALATE_HUMAN --> [*]: human review queue
```

Notes:

- `PENDING_WINDOW` and `MATCHED_HEALTHY` need no recovery action — the policy engine's `decide()` returns `None` for both.
- The guardrail-forced `ESCALATE_HUMAN` transitions (amount ceiling, retry limit, circuit breaker) apply uniformly across every action-producing state — drawn once here rather than duplicated per state, since the logic in `policy/selector.py` and `policy/engine.py` is identical regardless of which base action triggered it.
- `DUPLICATE_PAYMENT` and `INVERSE_LEAK` are implemented in the classifier and selector but not produced by the current chaos engine — see `docs/decisions.md` for what is and isn't exercised by the eval harness.
