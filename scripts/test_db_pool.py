import asyncio
import sys
import time

from agendaai.config import Settings
from agendaai.db_pool import DatabasePool


async def main() -> None:
    settings = Settings()

    database_url = settings.database_url.get_secret_value()

    database = DatabasePool(
        database_url,
        min_size=1,
        max_size=3,
    )

    print("DIAGNÓSTICO DEL POOL DE AGENDAAI")
    print("-" * 45)

    try:
        print("1. Abriendo pool...")

        start = time.perf_counter()

        await database.open()

        elapsed = time.perf_counter() - start

        print(f"Pool listo en {elapsed:.3f} segundos")

        print("\n2. Verificando conexión...")

        await database.check_connection()

        print("Conexión correcta.")

        print("\n3. Ejecutando diez consultas...")

        for number in range(1, 11):
            query_start = time.perf_counter()

            async with database.pool.connection() as conn, conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
                result = await cursor.fetchone()

            duration = time.perf_counter() - query_start

            print(f"Consulta {number:02d}: {duration:.3f} segundos (resultado={result[0]})")

        print("\n4. Probando concurrencia...")

        async def query_task(number: int) -> int:
            async with database.pool.connection() as conn, conn.cursor() as cursor:
                await cursor.execute("SELECT %s", (number,))
                row = await cursor.fetchone()
                return row[0]

        results = await asyncio.gather(*(query_task(n) for n in range(1, 6)))

        assert results == [1, 2, 3, 4, 5]

        print("Cinco consultas concurrentes completadas.")

        print("\nPRUEBA EXITOSA")

    finally:
        print("\n5. Cerrando pool...")
        await database.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
