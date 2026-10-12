
import asyncio
import logging
import sys

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from agendaai.availability_service import AvailabilityService
from agendaai.booking_repository import BookingRepository
from agendaai.booking_service import BookingService
from agendaai.config import Settings
from agendaai.database import ConversationRepository
from agendaai.gemini_service import GeminiService
from agendaai.handlers import build_handlers
from agendaai.tools.availability import AvailabilityToolExecutor
from agendaai.tools.booking import BookingToolExecutor

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s %(levelname)s "
            "%(name)s %(message)s"
        ),
    )

    logging.getLogger("httpx").setLevel(
        logging.WARNING
    )
    logging.getLogger("httpcore").setLevel(
        logging.WARNING
    )

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy()
        )

    settings = Settings()

    if not settings.allowed_users:
        logger.warning(
            "AgendaAI permite acceso público."
        )

    database_url = (
        settings.database_url.get_secret_value()
    )

    conversation_repository = ConversationRepository(
        database_url
    )

    booking_repository = BookingRepository(
        database_url
    )

    availability_service = AvailabilityService(
        booking_repository
    )

    availability_executor = AvailabilityToolExecutor(
        service=availability_service,
        resource_id=settings.booking_resource_id,
    )

    booking_service = BookingService(
        repository=booking_repository,
        resource_id=settings.booking_resource_id,
    )

    booking_executor = BookingToolExecutor(
        service=booking_service
    )

    service = GeminiService(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_model,
        repository=conversation_repository,
        availability_executor=availability_executor,
        booking_executor=booking_executor,
        max_turns=10,
    )

    start, whoami, reset, on_message = build_handlers(
        service,
        settings.allowed_users,
    )

    async def on_startup(
        application: Application,
    ) -> None:
        await conversation_repository.check_connection()

        resource = await booking_repository.get_resource(
            settings.booking_resource_id
        )

        if resource is None:
            raise RuntimeError(
                "La agenda configurada no existe o está inactiva."
            )

        logger.info(
            "Supabase conectado. Agenda: %s",
            resource.name,
        )

    async def error_handler(
        update: object,
        context: object,
    ) -> None:
        error = getattr(context, "error", None)

        if error is not None:
            logger.error(
                "Error de Telegram",
                exc_info=(
                    type(error),
                    error,
                    error.__traceback__,
                ),
            )

    app = (
        ApplicationBuilder()
        .token(
            settings.telegram_bot_token.get_secret_value()
        )
        .post_init(on_startup)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("reset", reset))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            on_message,
        )
    )

    app.add_error_handler(error_handler)

    logger.info("Iniciando AgendaAI...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
