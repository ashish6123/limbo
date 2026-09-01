from app.policy.enums import ActionType
from app.policy.selector import apply_guardrails, base_action_for_state, select_action
from app.recon.enums import TransactionState

MAX_AUTO_AMOUNT_PAISE = 500_000
MAX_RETRY_ATTEMPTS = 3


def test_matched_healthy_needs_no_action():
    assert base_action_for_state(TransactionState.MATCHED_HEALTHY, within_fulfillable_window=True) is None


def test_pending_window_needs_no_action():
    assert base_action_for_state(TransactionState.PENDING_WINDOW, within_fulfillable_window=True) is None


def test_limbo_paid_cancelled_within_window_replays_webhook():
    assert base_action_for_state(TransactionState.LIMBO_PAID_CANCELLED, within_fulfillable_window=True) == ActionType.REPLAY_WEBHOOK


def test_limbo_paid_cancelled_past_window_auto_refunds():
    assert base_action_for_state(TransactionState.LIMBO_PAID_CANCELLED, within_fulfillable_window=False) == ActionType.AUTO_REFUND


def test_refund_stuck_retriggers_refund():
    assert base_action_for_state(TransactionState.REFUND_STUCK, within_fulfillable_window=True) == ActionType.RETRIGGER_REFUND


def test_duplicate_payment_dedups_refund():
    assert base_action_for_state(TransactionState.DUPLICATE_PAYMENT, within_fulfillable_window=True) == ActionType.DEDUP_REFUND


def test_inverse_leak_always_escalates():
    assert base_action_for_state(TransactionState.INVERSE_LEAK, within_fulfillable_window=True) == ActionType.ESCALATE_HUMAN


def test_amount_above_ceiling_escalates_regardless_of_state():
    action = apply_guardrails(
        ActionType.AUTO_REFUND,
        amount_paise=MAX_AUTO_AMOUNT_PAISE + 1,
        attempt_count=0,
        max_auto_amount_paise=MAX_AUTO_AMOUNT_PAISE,
        max_retry_attempts=MAX_RETRY_ATTEMPTS,
    )
    assert action == ActionType.ESCALATE_HUMAN


def test_amount_at_ceiling_is_still_allowed():
    action = apply_guardrails(
        ActionType.AUTO_REFUND,
        amount_paise=MAX_AUTO_AMOUNT_PAISE,
        attempt_count=0,
        max_auto_amount_paise=MAX_AUTO_AMOUNT_PAISE,
        max_retry_attempts=MAX_RETRY_ATTEMPTS,
    )
    assert action == ActionType.AUTO_REFUND


def test_attempt_count_at_retry_limit_escalates():
    action = apply_guardrails(
        ActionType.RETRIGGER_REFUND,
        amount_paise=1000,
        attempt_count=MAX_RETRY_ATTEMPTS,
        max_auto_amount_paise=MAX_AUTO_AMOUNT_PAISE,
        max_retry_attempts=MAX_RETRY_ATTEMPTS,
    )
    assert action == ActionType.ESCALATE_HUMAN


def test_attempt_count_below_retry_limit_proceeds():
    action = apply_guardrails(
        ActionType.RETRIGGER_REFUND,
        amount_paise=1000,
        attempt_count=MAX_RETRY_ATTEMPTS - 1,
        max_auto_amount_paise=MAX_AUTO_AMOUNT_PAISE,
        max_retry_attempts=MAX_RETRY_ATTEMPTS,
    )
    assert action == ActionType.RETRIGGER_REFUND


def test_no_action_state_ignores_guardrails():
    action = select_action(
        TransactionState.MATCHED_HEALTHY,
        amount_paise=MAX_AUTO_AMOUNT_PAISE + 1,
        attempt_count=99,
        within_fulfillable_window=True,
        max_auto_amount_paise=MAX_AUTO_AMOUNT_PAISE,
        max_retry_attempts=MAX_RETRY_ATTEMPTS,
    )
    assert action is None
