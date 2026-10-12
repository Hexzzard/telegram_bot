
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg import errors


@dataclass(frozen=True)
class BookingResource:
    id: int
    name: str
    duration_minutes: int
    timezone: str


@dataclass(frozen=True)
class AvailabilityRule:
    weekday: int
    start_time: time
    end_time: time


@dataclass(frozen=True)
class BookedInterval:
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class BookingResult:
    state: str
    booking_id: int | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    reason: str | None = None


class BookingRepository:
    def __init__(self, database_url: str):
        self._database_url = database_url

    async def get_resource(
        self,
        resource_id: int,
    ) -> BookingResource | None:
        async with (
            await psycopg.AsyncConnection.connect(
                self._database_url,
                connect_timeout=10,
            ) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                SELECT id, name, duration_minutes, timezone
                FROM public.booking_resources
                WHERE id = %s AND is_active = TRUE
                """,
                (resource_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            return None

        return BookingResource(*row)

    async def get_rules(
        self,
        resource_id: int,
    ) -> list[AvailabilityRule]:
        async with (
            await psycopg.AsyncConnection.connect(
                self._database_url,
                connect_timeout=10,
            ) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                SELECT weekday, start_time, end_time
                FROM public.availability_rules
                WHERE resource_id = %s
                ORDER BY weekday, start_time
                """,
                (resource_id,),
            )
            rows = await cursor.fetchall()

        return [AvailabilityRule(*row) for row in rows]

    async def get_bookings(
        self,
        resource_id: int,
        start_at: datetime,
        end_at: datetime,
    ) -> list[BookedInterval]:
        async with (
            await psycopg.AsyncConnection.connect(
                self._database_url,
                connect_timeout=10,
            ) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                SELECT start_at, end_at
                FROM public.bookings
                WHERE resource_id = %s
                  AND status = 'confirmed'
                  AND start_at < %s
                  AND end_at > %s
                ORDER BY start_at
                """,
                (resource_id, end_at, start_at),
            )
            rows = await cursor.fetchall()

        return [BookedInterval(*row) for row in rows]

    async def create_booking(
        self,
        *,
        resource_id: int,
        user_id: int,
        title: str,
        start_at: datetime,
        idempotency_key: UUID,
    ) -> BookingResult:
        if start_at.tzinfo is None or start_at.utcoffset() is None:
            raise ValueError("La fecha necesita zona horaria")

        if not title.strip() or len(title) > 200:
            raise ValueError("Título inválido")

        try:
            async with await psycopg.AsyncConnection.connect(
                self._database_url,
                connect_timeout=10,
            ) as conn, conn.transaction(), conn.cursor() as cursor:

                # Todas las reservas de este recurso
                # siguen el mismo turno transaccional.
                await cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s::bigint)",
                    (resource_id,),
                )

                # Antes de escribir, comprobar si
                # este mismo intento ya se procesó.
                await cursor.execute(
                    """
                            SELECT
                                id, telegram_user_id,
                                resource_id, title,
                                start_at, end_at, status
                            FROM public.bookings
                            WHERE idempotency_key = %s
                            """,
                    (idempotency_key,),
                )
                previous = await cursor.fetchone()

                if previous is not None:
                    (
                        booking_id,
                        owner_id,
                        saved_resource,
                        saved_title,
                        saved_start,
                        saved_end,
                        saved_status,
                    ) = previous

                    if (
                        owner_id != user_id
                        or saved_resource != resource_id
                        or saved_title != title.strip()
                        or saved_start != start_at
                    ):
                        return BookingResult(
                            state="invalid",
                            reason="Clave reutilizada con datos distintos",
                        )

                    return BookingResult(
                        state="already_exists",
                        booking_id=booking_id,
                        start_at=saved_start,
                        end_at=saved_end,
                        reason=saved_status,
                    )

                await cursor.execute(
                    """
                            SELECT duration_minutes, timezone
                            FROM public.booking_resources
                            WHERE id = %s AND is_active = TRUE
                            """,
                    (resource_id,),
                )
                resource = await cursor.fetchone()

                if resource is None:
                    return BookingResult(
                        state="invalid",
                        reason="Servicio inexistente o inactivo",
                    )

                duration_minutes, timezone_name = resource
                tz = ZoneInfo(timezone_name)

                local_start = start_at.astimezone(tz)
                start_utc = start_at.astimezone(UTC)
                end_utc = start_utc + timedelta(
                    minutes=duration_minutes
                )
                local_end = end_utc.astimezone(tz)

                # No reinterpretar silenciosamente un
                # offset incorrecto enviado por el modelo.
                if (
                    local_start.replace(tzinfo=None)
                    != start_at.replace(tzinfo=None)
                    or local_start.utcoffset()
                    != start_at.utcoffset()
                ):
                    return BookingResult(
                        state="invalid",
                        reason="Offset o zona horaria incorrectos",
                    )

                if start_utc <= datetime.now(UTC):
                    return BookingResult(
                        state="unavailable",
                        reason="La hora ya pasó",
                    )

                if local_start.date() != local_end.date():
                    return BookingResult(
                        state="unavailable",
                        reason="Fuera del horario de atención",
                    )

                # Validación de horario dentro
                # de la misma transacción.
                await cursor.execute(
                    """
                            SELECT EXISTS (
                                SELECT 1
                                FROM public.availability_rules
                                WHERE resource_id = %s
                                  AND weekday = %s
                                  AND start_time <= %s
                                  AND end_time >= %s
                            )
                            """,
                    (
                        resource_id,
                        local_start.isoweekday(),
                        local_start.time().replace(tzinfo=None),
                        local_end.time().replace(tzinfo=None),
                    ),
                )

                inside_hours = (await cursor.fetchone())[0]

                if not inside_hours:
                    return BookingResult(
                        state="unavailable",
                        reason="Fuera del horario de atención",
                    )

                await cursor.execute(
                    """
                            SELECT EXISTS (
                                SELECT 1
                                FROM public.bookings
                                WHERE resource_id = %s
                                  AND status = 'confirmed'
                                  AND start_at < %s
                                  AND end_at > %s
                            )
                            """,
                    (resource_id, end_utc, start_utc),
                )

                occupied = (await cursor.fetchone())[0]

                if occupied:
                    return BookingResult(
                        state="unavailable",
                        reason="Horario ya ocupado",
                    )

                # La identidad del usuario proviene
                # de Telegram, nunca de Gemini.
                await cursor.execute(
                    """
                            INSERT INTO public.bot_users (
                                telegram_user_id
                            )
                            VALUES (%s)
                            ON CONFLICT (telegram_user_id)
                            DO UPDATE SET updated_at = NOW()
                            """,
                    (user_id,),
                )

                await cursor.execute(
                    """
                            INSERT INTO public.bookings (
                                resource_id,
                                telegram_user_id,
                                title,
                                start_at,
                                end_at,
                                status,
                                idempotency_key
                            )
                            VALUES (
                                %s, %s, %s, %s, %s,
                                'confirmed', %s
                            )
                            RETURNING id
                            """,
                    (
                        resource_id,
                        user_id,
                        title.strip(),
                        start_utc,
                        end_utc,
                        idempotency_key,
                    ),
                )

                booking_id = (await cursor.fetchone())[0]

                return BookingResult(
                    state="created",
                    booking_id=booking_id,
                    start_at=start_utc,
                    end_at=end_utc,
                )

        except errors.ExclusionViolation:
            # SQLSTATE 23P01:
            # Protección final ante escrituras concurrentes.
            return BookingResult(
                state="unavailable",
                reason="Otro usuario ocupó el horario",
            )

        except errors.UniqueViolation:
            # SQLSTATE 23505:
            # Otro proceso utilizó la misma clave.
            # No afirmamos éxito sin consultar el registro.
            return BookingResult(
                state="retry",
                reason="Operación ya procesada o en conflicto",
            )
