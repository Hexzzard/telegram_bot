import asyncio
import sys

from agendaai.config import Settings
from agendaai.conversation_state import (
    ConversationStateRepository,
)

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

USER_ID = int(os.environ["TELEGRAM_TEST_USER_ID"])
BOOKING_ID = int(os.environ["TEST_BOOKING_ID"])

async def main() -> None:
    settings = Settings()

    repository = ConversationStateRepository(settings.database_url.get_secret_value())

    started = await repository.begin_receipt_offer(
        user_id=USER_ID,
        booking_id=BOOKING_ID,
    )

    if not started:
        print(
            "No se pudo activar la oferta. "
            "Comprueba que la reserva esté "
            "confirmada y pertenezca al usuario."
        )
        return

    state = await repository.get_state(USER_ID)

    print("OFERTA DE COMPROBANTE ACTIVADA")
    print(f"Estado: {state.state}")
    print(f"Reserva: {state.booking_id}")
    print("Ahora puedes responder 'sí' en Telegram para continuar.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
