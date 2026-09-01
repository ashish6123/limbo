# Limbo Eval Report

Batch size: 500 transactions

## Detection (reconciliation accuracy vs ground truth)

Overall accuracy: 100.0%

| State | Count | Correct | Precision | Recall |
|---|---|---|---|---|
| REFUND_STUCK | 25 | 25 | 100.0% | 100.0% |
| MATCHED_HEALTHY | 400 | 400 | 100.0% | 100.0% |
| LIMBO_PAID_CANCELLED | 75 | 75 | 100.0% | 100.0% |

## Diagnosis accuracy

Accuracy vs chaos ground truth: 100.0%
Sources used: {'deterministic': 500}

## Money

Revenue recovered (webhook replay): Rs 0.00
Loss prevented (refunds executed): Rs 28366.00

Note: this batch's timeline always reconciles after the fulfillable window
has already elapsed (see docs/decisions.md ADR-010), so LIMBO_PAID_CANCELLED
cases in this particular run resolve via AUTO_REFUND, not REPLAY_WEBHOOK.
REPLAY_WEBHOOK is exercised and verified separately in Phase 4's dedicated
within-window scenario. This is reported honestly rather than engineered
to populate both buckets artificially.

## MTTR vs baseline

Baseline (documented real-world manual escalation): 21 days
Agent resolution in this run: same reconciliation cycle (minutes, mock mode)

## False-intervention rate

0 / 100 executed actions = 0.00% (circuit breaker threshold: 2%)

## Exceptions

0 escalated case(s).

Audit log entries written: 33109
