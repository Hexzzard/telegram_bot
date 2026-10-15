"""Funciones independientes de la red y la base de datos."""
import hashlib
import hmac
from datetime import datetime
from zoneinfo import ZoneInfo


def verify_signature(body: bytes, provided: str | None, secret: str) -> bool:
    if not provided or not provided.startswith('sha256=') or not secret:
        return False
    expected = 'sha256=' + hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def month_key(now: datetime, tz_name: str) -> str:
    return now.astimezone(ZoneInfo(tz_name)).strftime('%Y-%m')
