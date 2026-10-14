"""Envío asíncrono de avisos de cancelación mediante Resend.

La creación de la tarea sucede en PostgreSQL, en la misma transacción de cancelación.
El mensaje no incluye información clínica. No se envía si falta correo de ESTA reserva.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
from psycopg_pool import AsyncConnectionPool
from agendaai.db_connection import open_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CancellationEmail:
    id: int
    recipient: str
    subject: str
    body: str
    attempts: int


class CancellationEmailWorker:
    def __init__(self, *, pool: AsyncConnectionPool, api_key: str, sender: str):
        self._pool = pool
        self._api_key = api_key
        self._sender = sender

    async def claim_next(self) -> CancellationEmail | None:
        async with open_connection(self._pool) as conn:
            async with conn.transaction():
                async with conn.cursor() as cursor:
                    await cursor.execute("""
                        WITH candidate AS (
                            SELECT id FROM public.booking_cancellation_emails
                            WHERE (status='pending' AND next_attempt_at <= now())
                               OR (status='processing' AND locked_until < now())
                            ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
                        )
                        UPDATE public.booking_cancellation_emails AS n
                        SET status='processing', attempts=n.attempts+1,
                            locked_until=now()+interval '5 minutes'
                        FROM candidate WHERE n.id=candidate.id
                        RETURNING n.id,n.recipient,n.subject,n.body,n.attempts
                    """)
                    row = await cursor.fetchone()
        return CancellationEmail(*row) if row else None

    async def process_one(self) -> bool:
        notification = await self.claim_next()
        if notification is None:
            return False
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    'https://api.resend.com/emails',
                    headers={
                        'Authorization': f'Bearer {self._api_key}',
                        'Content-Type': 'application/json',
                        'Idempotency-Key': f'agendaai-cancel-email-{notification.id}',
                    },
                    json={
                        'from': self._sender,
                        'to': [notification.recipient],
                        'subject': notification.subject,
                        'text': notification.body,
                    },
                )
                response.raise_for_status()
                provider_id = response.json()['id']
            async with open_connection(self._pool) as conn:
                await conn.execute("""
                    UPDATE public.booking_cancellation_emails
                    SET status='sent',sent_at=now(),provider_message_id=%s,
                        locked_until=NULL,last_error=NULL
                    WHERE id=%s AND status='processing'
                """, (str(provider_id),notification.id))
            logger.info('Aviso de cancelación aceptado por Resend: tarea=%s',notification.id)
        except Exception as exc:
            retryable = not isinstance(exc,httpx.HTTPStatusError) or (
                exc.response.status_code >= 500 or exc.response.status_code in (408,409,429)
            )
            should_retry = retryable and notification.attempts < 5
            delay = min(30 * (2 ** (notification.attempts-1)),600)
            async with open_connection(self._pool) as conn:
                await conn.execute("""
                    UPDATE public.booking_cancellation_emails
                    SET status=%s,locked_until=NULL,
                        next_attempt_at=now()+(%s*interval '1 second'),
                        last_error=%s
                    WHERE id=%s AND status='processing'
                """, ('pending' if should_retry else 'failed',delay,str(exc)[:500],notification.id))
            logger.warning('No se pudo enviar cancelación tarea=%s: %s',notification.id,type(exc).__name__)
        return True

    async def run_worker(self) -> None:
        logger.info('Worker de correos de cancelación iniciado')
        while True:
            try:
                if not await self.process_one():
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Error en el worker de cancelaciones')
                await asyncio.sleep(10)
