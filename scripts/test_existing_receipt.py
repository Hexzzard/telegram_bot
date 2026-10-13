import asyncio
import sys

from agendaai.config import Settings
from agendaai.conversation_state import (
    ConversationStateRepository,
)

USER_ID = 8098006883
BOOKING_ID = 4


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
