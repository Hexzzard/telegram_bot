
import asyncio
from unittest.mock import patch

from agendaai.receipt_service import (
    ReceiptResult,
    ReceiptService,
)


def test_receipt_result():
    result = ReceiptResult(
        status="queued",
        message="Comprobante preparado",
    )

    assert result.status == "queued"
    assert result.message == "Comprobante preparado"


def test_invalid_email_is_rejected():
    service = ReceiptService(
        database_url="postgresql://unused:unused@localhost/unused",
    )

    # Un correo inválido debe rechazarse antes
    # de realizar cualquier consulta a Supabase.
    with patch(
        "agendaai.receipt_service.open_connection"
    ) as mock_connection:
        result = asyncio.run(
            service.enqueue_receipt(
                user_id=12345,
                email="correo-invalido",
            )
        )

    assert result.status == "invalid_email"
    mock_connection.assert_not_called()


def test_receipt_service_does_not_require_otp():
    # La configuración heredada puede seguir pasando
    # verification_secret, sin utilizar códigos OTP.
    service = ReceiptService(
        database_url="postgresql://unused:unused@localhost/unused",
        verification_secret="",
    )

    assert callable(service.enqueue_receipt)
