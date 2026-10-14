"""Cola PostgreSQL de sincronización eventual de reservas a Calendar."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from agendaai.public_calendar_service import PublicCalendarService

from googleapiclient.errors import HttpError
from psycopg_pool import AsyncConnectionPool

from agendaai.google_calendar_service import GoogleCalendarService, GoogleEvent

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 8


@dataclass(frozen=True)
class CalendarJob:
    booking_id: int
    title: str
    client_name: str
    start_at: datetime
    end_at: datetime
    attempts: int
    desired_action: str


class CalendarSyncService:
    def __init__(self, *, pool: AsyncConnectionPool, calendar: GoogleCalendarService,
                 public_calendar: PublicCalendarService | None = None, resource_id: int = 1):
        self._pool = pool
        self._calendar = calendar
        self._public_calendar = public_calendar
        self._resource_id = resource_id

    async def claim_next(self) -> CalendarJob | None:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    WITH candidate AS (
                        SELECT j.booking_id FROM public.calendar_sync_jobs j
                        JOIN public.bookings b ON b.id=j.booking_id
                        WHERE ((j.desired_action='upsert' AND b.status='confirmed')
                            OR (j.desired_action='delete' AND b.status='cancelled'))
                          AND ((j.state='pending' AND j.next_attempt_at <= NOW())
                            OR (j.state='processing' AND j.locked_until < NOW()))
                        ORDER BY j.next_attempt_at, j.booking_id
                        FOR UPDATE OF j SKIP LOCKED LIMIT 1
                    )
                    UPDATE public.calendar_sync_jobs AS j
                    SET state='processing', attempts=j.attempts+1,
                        locked_until=NOW()+INTERVAL '3 minutes', updated_at=NOW()
                    FROM candidate, public.bookings AS b
                    WHERE j.booking_id=candidate.booking_id AND b.id=j.booking_id
                    RETURNING b.id,b.title,b.client_name,b.start_at,b.end_at,j.attempts,j.desired_action
                    """
                )
                row = await cursor.fetchone()
        return CalendarJob(*row) if row else None

    async def mark_synced(self, job: CalendarJob, event: GoogleEvent) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE public.calendar_sync_jobs
                SET state='synced', google_event_id=%s,
                    google_event_link=%s, synced_at=NOW(),
                    locked_until=NULL, last_error=NULL, updated_at=NOW()
                WHERE booking_id=%s AND state='processing' AND attempts=%s AND desired_action='upsert'
                """,
                (event.event_id, event.html_link, job.booking_id, job.attempts),
            )

    async def mark_deleted(self, job: CalendarJob) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE public.calendar_sync_jobs
                SET state='synced', google_event_id=NULL, google_event_link=NULL,
                    synced_at=NOW(), locked_until=NULL, last_error=NULL,
                    updated_at=NOW()
                WHERE booking_id=%s AND state='processing'
                  AND attempts=%s AND desired_action='delete'
                """, (job.booking_id, job.attempts)
            )

    async def mark_error(self, job: CalendarJob, error: Exception) -> None:
        retryable = True
        if isinstance(error, HttpError):
            code = error.resp.status
            retryable = code in (408, 409, 429) or code >= 500
        should_retry = retryable and job.attempts < MAX_ATTEMPTS
        delay = min(20 * (2 ** (job.attempts - 1)), 1800)
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE public.calendar_sync_jobs
                SET state=%s,
                    next_attempt_at=NOW()+(%s * INTERVAL '1 second'),
                    locked_until=NULL, last_error=%s, updated_at=NOW()
                WHERE booking_id=%s AND state='processing' AND attempts=%s AND desired_action=%s
                """,
                (
                    "pending" if should_retry else "failed",
                    delay,
                    f"{type(error).__name__}: {str(error)[:300]}",
                    job.booking_id,
                    job.attempts,
                    job.desired_action,
                ),
            )
        logger.warning(
            "Google Calendar: reserva=%s intento=%s reintento=%s error=%s",
            job.booking_id, job.attempts, should_retry, type(error).__name__,
        )

    async def sync_public_blocks(self, job: CalendarJob) -> None:
        """Vuelve a comprobar ocupación en BD tras operación privada.
        Nunca publica nombre o motivo. Reintento de la cola es idempotente.
        """
        if self._public_calendar is None:
            return
        local = job.start_at.astimezone(ZoneInfo('America/Santiago'))
        end_local = job.end_at.astimezone(ZoneInfo('America/Santiago'))
        step = timedelta(minutes=self._public_calendar.slot_minutes)
        start_minutes = local.hour * 60 + local.minute
        slot_minutes = self._public_calendar.slot_minutes
        aligned = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            minutes=(start_minutes // slot_minutes) * slot_minutes
        )
        cursor_at = aligned
        while cursor_at < end_local:
            slot_end = cursor_at + step
            async with self._pool.connection() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        """SELECT EXISTS (
                           SELECT 1 FROM public.bookings
                           WHERE resource_id = %s AND status = 'confirmed'
                             AND start_at < %s AND end_at > %s
                        )""", (self._resource_id, slot_end, cursor_at)
                    )
                    occupied = (await cursor.fetchone())[0]
            await self._public_calendar.sync_block(cursor_at, occupied)
            cursor_at = slot_end

    async def process_one(self) -> bool:
        job = await self.claim_next()
        if job is None:
            return False
        try:
            if job.desired_action == 'delete':
                await self._calendar.remove_event(booking_id=job.booking_id)
                await self.sync_public_blocks(job)
                await self.mark_deleted(job)
                logger.info("Google Calendar eliminado: reserva=%s", job.booking_id)
            else:
                event = await self._calendar.ensure_event(
                    booking_id=job.booking_id, title=job.title, client_name=job.client_name,
                    start_at=job.start_at, end_at=job.end_at,
                )
                await self.sync_public_blocks(job)
                await self.mark_synced(job, event)
                # Un worker paralelo puede haber procesado el borrado mientras
                # terminaba la inserción: verificar el estado real tras el I/O.
                async with self._pool.connection() as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute(
                            "SELECT status FROM public.bookings WHERE id=%s",
                            (job.booking_id,),
                        )
                        state_row = await cursor.fetchone()
                if state_row is not None and state_row[0] == 'cancelled':
                    logger.warning("Reserva cancelada durante inserción en Calendar: %s", job.booking_id)
                    # Reponer job de borrado incluso si otro worker ya lo marcó synced.
                    async with self._pool.connection() as conn:
                        await conn.execute(
                            """UPDATE public.calendar_sync_jobs
                               SET desired_action='delete', state='pending',
                                   next_attempt_at=NOW(), locked_until=NULL,
                                   attempts=0, updated_at=NOW()
                               WHERE booking_id=%s AND desired_action='delete'""",
                            (job.booking_id,),
                        )
                else:
                    logger.info("Google Calendar sincronizado: reserva=%s", job.booking_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("Error sincronizando reserva %s", job.booking_id)
            await self.mark_error(job, error)
        return True

    async def run_worker(self) -> None:
        logger.info("Worker de Google Calendar iniciado")
        while True:
            try:
                if not await self.process_one():
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error inesperado worker Google Calendar")
                await asyncio.sleep(10)

    async def last_booking_status(self, *, user_id: int) -> tuple | None:
        # La propiedad siempre se determina desde Telegram, no desde un argumento del usuario.
        async with self._pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT b.id, b.start_at, j.state
                    FROM public.bookings b
                    LEFT JOIN public.calendar_sync_jobs j ON j.booking_id=b.id
                    WHERE b.telegram_user_id=%s AND b.status='confirmed'
                    ORDER BY b.id DESC LIMIT 1
                    """,
                    (user_id,),
                )
                return await cursor.fetchone()
