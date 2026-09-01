import hashlib
import hmac

import razorpay

from app.gateway.razorpay_adapter import RazorpayAdapter

SECRET = "test_webhook_secret_not_real"


def make_adapter() -> RazorpayAdapter:
    client = razorpay.Client(auth=("rzp_test_fake", "fake_secret"))
    return RazorpayAdapter(session=None, client=client, webhook_sink=lambda payload: None)


def sign(body: str, secret: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def test_valid_signature_is_accepted():
    adapter = make_adapter()
    body = '{"event": "payment.captured"}'
    signature = sign(body, SECRET)

    assert adapter.verify_webhook_signature(body, signature, SECRET) is True


def test_tampered_body_is_rejected():
    adapter = make_adapter()
    body = '{"event": "payment.captured"}'
    signature = sign(body, SECRET)

    tampered_body = '{"event": "payment.failed"}'

    assert adapter.verify_webhook_signature(tampered_body, signature, SECRET) is False


def test_wrong_secret_is_rejected():
    adapter = make_adapter()
    body = '{"event": "payment.captured"}'
    signature = sign(body, SECRET)

    assert adapter.verify_webhook_signature(body, signature, "a_different_secret") is False
