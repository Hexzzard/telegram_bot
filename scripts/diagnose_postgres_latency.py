import asyncio
import sys
import time
from urllib.parse import urlsplit

import psycopg

from agendaai.config import Settings

ATTEMPTS = 5
CONNECT_TIMEOUT = 12


async def test_connection(
    database_url: str,
    number: int,
) -> None:
    print(f"\n--- PRUEBA {number} ---", flush=True)

    start = time.perf_counter()

    try:
        connection = await psycopg.AsyncConnection.connect(
            database_url,
            connect_timeout=CONNECT_TIMEOUT,
        )

        connection_time = time.perf_counter() - start

        print(
            f"Conexión establecida en {connection_time:.2f} segundos",
            flush=True,
        )

        try:
            query_start = time.perf_counter()

            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                row = await cursor.fetchone()

            query_time = time.perf_counter() - query_start

            print(
                f"Consulta SQL: {query_time:.3f} segundos",
                flush=True,
            )

            print(f"Resultado: {row[0]}", flush=True)

        finally:
            await connection.close()

    except psycopg.Error as error:
        elapsed = time.perf_counter() - start

        print(
            f"CONEXIÓN FALLIDA tras {elapsed:.2f} s",
            flush=True,
        )
        print(
            f"Tipo: {type(error).__name__}",
            flush=True,
        )

    total = time.perf_counter() - start

    print(
        f"Tiempo total: {total:.2f} segundos",
        flush=True,
    )


async def main() -> None:
    settings = Settings()

    url = settings.database_url.get_secret_value()
    parsed = urlsplit(url)

    print("=" * 45)
    print("DIAGNÓSTICO DE LATENCIA POSTGRESQL")
    print("=" * 45)
    print(f"Host: {parsed.hostname}")
    print(f"Puerto: {parsed.port or 5432}")
    print(f"Intentos: {ATTEMPTS}")
    print(f"Timeout: {CONNECT_TIMEOUT} segundos")
    print("=" * 45)

    for number in range(1, ATTEMPTS + 1):
        await test_connection(url, number)

        if number < ATTEMPTS:
            await asyncio.sleep(1)

    print("\nDIAGNÓSTICO FINALIZADO")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
