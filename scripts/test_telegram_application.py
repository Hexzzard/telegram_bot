import asyncio
import sys

from telegram.ext import ApplicationBuilder
from telegram.request import HTTPXRequest

from agendaai.config import Settings


async def main() -> None:
    settings = Settings()

    print("1. Creando solicitudes HTTP...")

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    updates_request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=40.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    print("2. Construyendo la aplicación...")

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token.get_secret_value())
        .request(request)
        .get_updates_request(updates_request)
        .build()
    )

    print("3. Inicializando ApplicationBuilder...")

    try:
        await app.initialize()

        print("4. ApplicationBuilder inicializado.")
        print(f"Bot: @{app.bot.username}")
        print("\nPRUEBA EXITOSA")

    finally:
        if app._initialized:
            await app.shutdown()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
