from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    gateway_mode: str = "mock"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_webhook_id: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_currency: str = "USD"

    gemini_api_key: str = ""

    order_fulfillable_window_minutes: int = 30
    refund_settlement_sla_hours: int = 72

    max_auto_amount_paise: int = 500_000
    max_retry_attempts: int = 3
    circuit_breaker_false_intervention_rate: float = 0.02
    dry_run: bool = True

    baseline_resolution_days: int = 21

    recon_interval_seconds: int = 60
    run_embedded_scheduler: bool = True

    cors_allow_origins: str = "http://localhost:3000"
    dashboard_api_key: str = ""
    # Where PayPal sends the buyer back after approving a demo order.
    public_dashboard_url: str = "http://localhost:3000"

    demo_order_amount_paise: int = 100_000
    demo_max_amount_paise: int = 100_000
    demo_session_ttl_minutes: int = 30
    demo_fulfillable_window_seconds: int = 20
    demo_rate_limit_per_minute: int = 10

    @field_validator("razorpay_key_id")
    @classmethod
    def _reject_live_keys(cls, value: str) -> str:
        if value and not value.startswith("rzp_test_"):
            raise ValueError(
                f"RAZORPAY_KEY_ID must start with 'rzp_test_', got {value[:8]!r}... — "
                "live keys are never permitted in this deployment"
            )
        return value

    @field_validator("paypal_base_url")
    @classmethod
    def _reject_live_paypal(cls, value: str) -> str:
        if value.rstrip("/") != "https://api-m.sandbox.paypal.com":
            raise ValueError(
                f"PAYPAL_BASE_URL must be the sandbox API (https://api-m.sandbox.paypal.com), got {value!r} — "
                "live PayPal is never permitted in this deployment"
            )
        return value.rstrip("/")


settings = Settings()
