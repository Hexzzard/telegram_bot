import asyncio
import sys

from agendaai.config import Settings
from agendaai.conversation_state import (
    ConversationStage,
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

    print("1. Consultando estado inicial...")

    original = await repository.get_state(TEST_USER_ID)

    print(f"Estado anterior: {original.state}")

    if original.state != ConversationStage.IDLE:
        raise RuntimeError(
            "El usuario ya tiene un flujo activo. Utiliza un usuario de prueba en estado idle."
        )

    try:
        print("\n2. Iniciando oferta de comprobante...")

        started = await repository.begin_receipt_offer(
            user_id=TEST_USER_ID,
            booking_id=TEST_BOOKING_ID,
        )

        assert started, "La reserva no existe, no está confirmada o no pertenece al usuario."

        state = await repository.get_state(TEST_USER_ID)

        assert state.state == ConversationStage.AWAITING_RECEIPT_CHOICE

        assert state.booking_id == TEST_BOOKING_ID

        print(f"Estado: {state.state}")
        print(f"Reserva asociada: {state.booking_id}")

        print("\n3. Simulando respuesta: Sí")

        changed = await repository.move_to_email_input(user_id=TEST_USER_ID)

        assert changed

        state = await repository.get_state(TEST_USER_ID)

        assert state.state == ConversationStage.AWAITING_EMAIL

        print(f"Estado: {state.state}")

        print("\n4. Comprobando persistencia...")

        # Nueva instancia, como si otro componente
        # recuperara la conversación.
        second_repository = ConversationStateRepository(settings.database_url.get_secret_value())

        recovered = await second_repository.get_state(TEST_USER_ID)

        assert recovered.state == ConversationStage.AWAITING_EMAIL

        print("El estado se recuperó desde Supabase.")

    finally:
        print("\n5. Limpiando conversación de prueba...")

        await repository.clear_state(user_id=TEST_USER_ID)

    final = await repository.get_state(TEST_USER_ID)

    assert final.state == ConversationStage.IDLE

    print(f"Estado final: {final.state}")
    print("\nPRUEBA EXITOSA")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
