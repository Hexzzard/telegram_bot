import asyncio
import sys

from telegram import Bot
from telegram.request import HTTPXRequest

from agendaai.config import Settings


async def main() -> None:
    settings = Settings()

    print("1. Cargando configuración...")

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    print("2. Conectando mediante python-telegram-bot...")

    async with Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        request=request,
    ) as bot:
        account = await bot.get_me()

        print("3. Conexión establecida.")
        print(f"Nombre: {account.first_name}")
        print(f"Usuario: @{account.username}")
        print(f"ID del bot: {account.id}")

    print("\nPRUEBA EXITOSA")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
