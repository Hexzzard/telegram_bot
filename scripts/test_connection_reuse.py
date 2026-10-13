import asyncio
import sys
import time

import psycopg

from agendaai.config import Settings


async def main() -> None:
    settings = Settings()
    database_url = settings.database_url.get_secret_value()

    print("Abriendo una única conexión...")

    start = time.perf_counter()

    async with await psycopg.AsyncConnection.connect(
        database_url,
        connect_timeout=15,
    ) as conn:
        elapsed = time.perf_counter() - start

        print(f"Conexión inicial: {elapsed:.3f} s")

        for number in range(1, 11):
            query_start = time.perf_counter()

            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
                row = await cursor.fetchone()

            duration = time.perf_counter() - query_start

            print(f"Consulta {number:02d}: {duration:.3f} s (resultado={row[0]})")

    print("\nPRUEBA FINALIZADA")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
