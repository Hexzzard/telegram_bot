import hashlib
import hmac
from datetime import datetime, timezone
from whatsapp_gateway.utils import verify_signature, month_key


def test_valid_signature():
    body, secret = b'{"object":"whatsapp_business_account"}', 'test-only-secret'
    sig = 'sha256=' + hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
    assert verify_signature(body, sig, secret)
    assert not verify_signature(body+b' ',sig,secret)
    assert not verify_signature(body,None,secret)


def test_billing_timezone_month():
    instant = datetime(2026,11,1,1,0,tzinfo=timezone.utc)
    assert month_key(instant,'America/Santiago') == '2026-10'
