from app.policy.enums import ActionType
from app.recon.enums import TransactionState

# States that need no recovery action at all.
_NO_ACTION_STATES = (TransactionState.MATCHED_HEALTHY, TransactionState.PENDING_WINDOW)


def base_action_for_state(state: TransactionState, within_fulfillable_window: bool) -> ActionType | None:
    """The action a transaction's state calls for, ignoring amount ceiling and
    retry-limit guardrails — those are applied separately by apply_guardrails,
    since checking them requires attempt history keyed on this base action."""
    if state in _NO_ACTION_STATES:
        return None
    if state == TransactionState.LIMBO_PAID_CANCELLED:
        return ActionType.REPLAY_WEBHOOK if within_fulfillable_window else ActionType.AUTO_REFUND
    if state == TransactionState.REFUND_STUCK:
        return ActionType.RETRIGGER_REFUND
    if state == TransactionState.DUPLICATE_PAYMENT:
        return ActionType.DEDUP_REFUND
    # INVERSE_LEAK, and anything else: ambiguous by nature, never routed to a
    # money-moving action without a human.
    return ActionType.ESCALATE_HUMAN


def apply_guardrails(
    base_action: ActionType | None,
    amount_paise: int,
    attempt_count: int,
    max_auto_amount_paise: int,
    max_retry_attempts: int,
) -> ActionType | None:
    if base_action is None:
        return None
    if amount_paise > max_auto_amount_paise:
        return ActionType.ESCALATE_HUMAN
    if attempt_count >= max_retry_attempts:
        return ActionType.ESCALATE_HUMAN
    return base_action


def select_action(
    state: TransactionState,
    amount_paise: int,
    attempt_count: int,
    within_fulfillable_window: bool,
    max_auto_amount_paise: int,
    max_retry_attempts: int,
) -> ActionType | None:
    base = base_action_for_state(state, within_fulfillable_window)
    return apply_guardrails(base, amount_paise, attempt_count, max_auto_amount_paise, max_retry_attempts)
