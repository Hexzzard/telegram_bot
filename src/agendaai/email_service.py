import asyncio
import logging
from dataclasses import dataclass

import httpx
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agendaai.db_connection import open_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailNotification:
    id: int
    recipient: str
    subject: str
    body: str
    attempts: int


class EmailService:
    def __init__(
        self,
        database_url: str | AsyncConnectionPool,
        api_key: str,
        sender: str,
    ):
        self._database_url = database_url
        self._api_key = api_key
        self._sender = sender

    async def register_email(
        self,
        user_id: int,
        email: str,
    ) -> None:
        async with open_connection(self._database_url) as conn:
            await conn.execute(
                """
                INSERT INTO public.bot_users (
                    telegram_user_id,
                    email
                )
                VALUES (%s, %s)
                ON CONFLICT (telegram_user_id)
                DO UPDATE SET
                    email = EXCLUDED.email,
                    updated_at = NOW()
                """,
                (user_id, email),
            )

    async def get_email(
        self,
        user_id: int,
    ) -> str | None:
        async with (
            open_connection(self._database_url) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                    SELECT email
                    FROM public.bot_users
                    WHERE telegram_user_id = %s
                    """,
                (user_id,),
            )
            row = await cursor.fetchone()

        return row[0] if row else None

    async def claim_next(
        self,
    ) -> EmailNotification | None:
        # SKIP LOCKED permite varios workers
        # sin reclamar la misma fila.
        async with (
            open_connection(self._database_url) as conn,
            conn.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                """
                    WITH candidate AS (
                        SELECT id
                        FROM public.email_notifications
                        WHERE (
                            status = 'pending'
                            AND next_attempt_at <= NOW()
                        )
                        OR (
                            status = 'processing'
                            AND locked_until < NOW()
                        )
                        ORDER BY id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE public.email_notifications AS n
                    SET status = 'processing',
                        attempts = attempts + 1,
                        locked_until =
                            NOW() + INTERVAL '5 minutes'
                    FROM candidate
                    WHERE n.id = candidate.id
                    RETURNING
                        n.id,
                        n.recipient,
                        n.subject,
                        n.body,
                        n.attempts
                    """
            )
            row = await cursor.fetchone()

        if row is None:
            return None

        return EmailNotification(**row)

    async def send_email(
        self,
        notification: EmailNotification,
    ) -> str:
        # Mismo identificador en cada reintento.
        # Resend conserva claves por 24 horas.
        idempotency_key = f"agendaai-booking-email-{notification.id}"

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": (f"Bearer {self._api_key}"),
                    "Content-Type": "application/json",
                    "Idempotency-Key": idempotency_key,
                },
                json={
                    "from": self._sender,
                    "to": [notification.recipient],
                    "subject": notification.subject,
                    "text": notification.body,
                },
            )

            response.raise_for_status()
            data = response.json()

        return str(data["id"])

    async def mark_sent(
        self,
        notification_id: int,
        provider_id: str,
    ) -> None:
        async with open_connection(self._database_url) as conn:
            await conn.execute(
                """
                UPDATE public.email_notifications
                SET status = 'sent',
                    provider_message_id = %s,
                    sent_at = NOW(),
                    locked_until = NULL,
                    last_error = NULL
                WHERE id = %s
                  AND status = 'processing'
                """,
                (provider_id, notification_id),
            )

    async def mark_failed(
        self,
        notification: EmailNotification,
        error: Exception,
    ) -> None:
        # Los errores HTTP 4xx, salvo 408,
        # 409 y 429, no se reintentan.
        retryable = True

        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            retryable = status >= 500 or status in (408, 409, 429)

        should_retry = retryable and notification.attempts < 5

        new_status = "pending" if should_retry else "failed"

        # Espera exponencial: 30, 60, 120... s
        delay_seconds = min(
            30 * 2 ** (notification.attempts - 1),
            600,
        )

        async with open_connection(self._database_url) as conn:
            await conn.execute(
                """
                UPDATE public.email_notifications
                SET status = %s,
                    locked_until = NULL,
                    next_attempt_at =
                        NOW() + (%s * INTERVAL '1 second'),
                    last_error = %s
                WHERE id = %s
                  AND status = 'processing'
                """,
                (
                    new_status,
                    delay_seconds,
                    str(error)[:500],
                    notification.id,
                ),
            )

        logger.warning(
            "Correo %s: estado=%s, intento=%s",
            notification.id,
            new_status,
            notification.attempts,
        )

    async def process_one(self) -> bool:
        notification = await self.claim_next()

        if notification is None:
            return False

        try:
            provider_id = await self.send_email(notification)
            await self.mark_sent(
                notification.id,
                provider_id,
            )

            logger.info(
                "Correo aceptado por Resend: notificación=%s, proveedor=%s",
                notification.id,
                provider_id,
            )
        except Exception as error:
            logger.exception(
                "Error enviando notificación %s",
                notification.id,
            )
            await self.mark_failed(
                notification,
                error,
            )

        return True

    async def run_worker(self) -> None:
        logger.info("Worker de correo iniciado")

        while True:
            try:
                processed = await self.process_one()

                if not processed:
                    await asyncio.sleep(5)

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception("Error inesperado del worker")
                await asyncio.sleep(10)
