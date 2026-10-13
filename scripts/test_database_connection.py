import asyncio
import sys
from urllib.parse import urlsplit

import psycopg

from agendaai.config import Settings
from agendaai.database import ConversationRepository


async def main() -> None:
    settings = Settings()

    url = settings.database_url.get_secret_value()
    parsed = urlsplit(url)

    print("Diagnóstico PostgreSQL de AgendaAI")
    print("-" * 40)
    print(f"Host: {parsed.hostname}")
    print(f"Puerto: {parsed.port or 5432}")
    print(f"Base de datos: {parsed.path.lstrip('/')}")
    print("Contraseña: [OCULTA]")

    print("\nIntentando conectar...")

    try:
        async with (
            await psycopg.AsyncConnection.connect(
                url,
                connect_timeout=15,
            ) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute("SELECT current_database(), NOW()")
            result = await cursor.fetchone()

        print("\nCONEXIÓN EXITOSA")
        print(f"Base de datos: {result[0]}")
        print(f"Fecha del servidor: {result[1]}")

        print("\nProbando ConversationRepository...")

        repository = ConversationRepository(url)

        try:
            await repository.check_connection()
            print("REPOSITORY CHECK: EXITOSO")

        except psycopg.Error as error:
            print("REPOSITORY CHECK: FALLIDO")
            print(f"Tipo: {type(error).__name__}")
            print(f"Detalle: {error}")

    except psycopg.Error as error:
        print("\nCONEXIÓN FALLIDA")
        print(f"Tipo: {type(error).__name__}")
        print(f"Detalle: {error}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
