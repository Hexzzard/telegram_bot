import asyncio
import sys
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg

from agendaai.booking_repository import BookingRepository
from agendaai.config import Settings

CHILE = ZoneInfo("America/Santiago")


def future_weekday() -> datetime:
    candidate = datetime.now(CHILE).date() + timedelta(days=2)

    while candidate.isoweekday() > 5:
        candidate += timedelta(days=1)

    return datetime(
        candidate.year,
        candidate.month,
        candidate.day,
        15,
        0,
        tzinfo=CHILE,
    )


async def main() -> None:
    settings = Settings()
    database_url = settings.database_url.get_secret_value()

    repository = BookingRepository(database_url)

    resource_id = None

    # IDs ficticios exclusivos para esta prueba.
    base_user = 5_000_000_000 + (uuid4().int % 1_000_000_000)
    user_a = base_user
    user_b = base_user + 1

    start_at = future_weekday()

    try:
        # Preparar recurso independiente.
        async with (
            await psycopg.AsyncConnection.connect(database_url) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                    INSERT INTO public.booking_resources (
                        name,
                        duration_minutes,
                        timezone
                    )
                    VALUES (%s, 30, 'America/Santiago')
                    RETURNING id
                    """,
                (f"Prueba concurrente {uuid4().hex[:8]}",),
            )

            resource_id = (await cursor.fetchone())[0]

            await cursor.execute(
                """
                    INSERT INTO public.availability_rules (
                        resource_id,
                        weekday,
                        start_time,
                        end_time
                    )
                    VALUES (%s, %s, '09:00', '17:00')
                    """,
                (resource_id, start_at.isoweekday()),
            )

        key_a = uuid4()
        key_b = uuid4()

        # Barrera para lanzar las operaciones
        # lo más simultáneamente posible.
        barrier = asyncio.Event()

        async def attempt(user_id, key):
            await barrier.wait()

            result = await repository.create_booking(
                resource_id=resource_id,
                user_id=user_id,
                title=f"Reserva usuario {user_id}",
                start_at=start_at,
                idempotency_key=key,
            )

            print(f"Usuario {user_id}: {result.state} (ID: {result.booking_id})")

            return result

        task_a = asyncio.create_task(attempt(user_a, key_a))

        task_b = asyncio.create_task(attempt(user_b, key_b))

        print("\nEjecutando dos reservas simultáneas...")
        barrier.set()

        result_a, result_b = await asyncio.gather(
            task_a,
            task_b,
        )

        states = sorted(
            [
                result_a.state,
                result_b.state,
            ]
        )

        assert states == ["created", "unavailable"], f"Resultado inesperado: {states}"

        # Comprobar lo que realmente quedó
        # escrito en PostgreSQL.
        async with (
            await psycopg.AsyncConnection.connect(database_url) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute(
                """
                    SELECT id, telegram_user_id, start_at
                    FROM public.bookings
                    WHERE resource_id = %s
                      AND status = 'confirmed'
                    """,
                (resource_id,),
            )

            rows = await cursor.fetchall()

        assert len(rows) == 1, "Se esperaba exactamente una reserva"

        winner_id = rows[0][1]
        winner_key = key_a if winner_id == user_a else key_b

        print("\nProbando idempotencia...")

        repeated = await repository.create_booking(
            resource_id=resource_id,
            user_id=winner_id,
            title=f"Reserva usuario {winner_id}",
            start_at=start_at,
            idempotency_key=winner_key,
        )

        assert repeated.state == "already_exists"
        assert repeated.booking_id == rows[0][0]

        print("\nPRUEBA EXITOSA")
        print("Se confirmó exactamente una reserva.")
        print("El otro usuario recibió unavailable.")
        print("Repetir la operación no creó duplicados.")

    finally:
        # Eliminar los datos temporales,
        # incluso si alguna prueba falla.
        if resource_id is not None:
            async with (
                await psycopg.AsyncConnection.connect(database_url) as conn,
                conn.cursor() as cursor,
            ):
                await cursor.execute(
                    """
                        DELETE FROM public.bookings
                        WHERE resource_id = %s
                        """,
                    (resource_id,),
                )

                await cursor.execute(
                    """
                        DELETE FROM public.booking_resources
                        WHERE id = %s
                        """,
                    (resource_id,),
                )

                await cursor.execute(
                    """
                        DELETE FROM public.bot_users
                        WHERE telegram_user_id IN (%s, %s)
                        """,
                    (user_a, user_b),
                )

            print("\nDatos de prueba eliminados.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
