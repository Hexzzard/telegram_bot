import asyncio
import logging
import sys

import psycopg

from agendaai.config import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)


async def main() -> None:
    settings = Settings()

    print("Conectando con Supabase...")

    try:
        async with (
            await psycopg.AsyncConnection.connect(
                settings.database_url,
                connect_timeout=15,
            ) as conn,
            conn.cursor() as cursor,
        ):
            await cursor.execute("SELECT current_database(), current_user")

            result = await cursor.fetchone()

            if result:
                database_name, user_name = result

                print("Conexión exitosa")
                print(f"Base de datos: {database_name}")
                print(f"Usuario: {user_name}")

            await cursor.execute(
                """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name IN ('bot_users', 'messages')
                    ORDER BY table_name
                    """
            )

            tables = await cursor.fetchall()

            print("\nTablas encontradas:")

            for table in tables:
                print(f"- {table[0]}")

    except psycopg.Error as error:
        print("\nERROR DE CONEXIÓN")
        print(f"Tipo: {type(error).__name__}")
        print(f"Detalle: {error}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
