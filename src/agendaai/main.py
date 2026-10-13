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
from telegram.request import HTTPXRequest

from agendaai.availability_service import AvailabilityService
from agendaai.booking_repository import BookingRepository
from agendaai.booking_service import BookingService
from agendaai.config import Settings
from agendaai.conversation_state import (
    ConversationStateRepository,
)
from agendaai.database import ConversationRepository
from agendaai.db_pool import DatabasePool
from agendaai.email_service import EmailService
from agendaai.gemini_service import GeminiService
from agendaai.handlers import build_handlers
from agendaai.receipt_flow import ReceiptFlow
from agendaai.receipt_service import ReceiptService
from agendaai.tools.availability import AvailabilityToolExecutor
from agendaai.tools.booking import BookingToolExecutor

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s %(levelname)s %(name)s %(message)s"),
    )

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    settings = Settings()

    if not settings.allowed_users:
        logger.warning(
            "AgendaAI permite acceso público. Restringe los usuarios durante estas pruebas."
        )

    database_url = settings.database_url.get_secret_value()

    database_pool = DatabasePool(
        database_url,
        min_size=1,
        max_size=3,
    )

    shared_pool = database_pool.pool

    conversation_repository = ConversationRepository(shared_pool)

    booking_repository = BookingRepository(shared_pool)

    conversation_states = ConversationStateRepository(shared_pool)

    availability_service = AvailabilityService(booking_repository)

    availability_executor = AvailabilityToolExecutor(
        service=availability_service,
        resource_id=settings.booking_resource_id,
    )

    booking_service = BookingService(
        repository=booking_repository,
        resource_id=settings.booking_resource_id,
    )

    booking_executor = BookingToolExecutor(
        service=booking_service,
        conversation_states=conversation_states,
    )

    email_service = EmailService(
        database_url=shared_pool,
        api_key=settings.resend_api_key.get_secret_value(),
        sender=settings.email_from,
    )

    receipt_service = ReceiptService(
        database_url=shared_pool,
        resend_api_key=(settings.resend_api_key.get_secret_value()),
        email_from=settings.email_from,
        verification_secret=(settings.email_verification_secret.get_secret_value()),
    )

    receipt_flow = ReceiptFlow(
        states=conversation_states,
        receipts=receipt_service,
    )

    gemini_service = GeminiService(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_model,
        repository=conversation_repository,
        availability_executor=availability_executor,
        booking_executor=booking_executor,
        max_turns=10,
    )

    start, whoami, reset, on_message = build_handlers(
        gemini_service,
        settings.allowed_users,
        receipt_flow,
    )

    email_worker_task = None

    async def on_startup(
        application: Application,
    ) -> None:
        nonlocal email_worker_task

        logger.info("INICIO 1: Telegram inicializado.")

        try:
            logger.info("INICIO 2: Comprobando conexión con Supabase...")
            await database_pool.open()
            await conversation_repository.check_connection()

            logger.info("INICIO 3: Conexión con Supabase exitosa.")

            logger.info("INICIO 4: Consultando recurso de reservas...")

            resource = await booking_repository.get_resource(settings.booking_resource_id)

            if resource is None:
                raise RuntimeError("La agenda configurada no existe o está inactiva.")

            logger.info(
                "INICIO 5: Recurso encontrado: %s",
                resource.name,
            )

            logger.info("INICIO 6: Iniciando trabajador de correos...")

            email_worker_task = asyncio.create_task(
                email_service.run_worker(),
                name="agendaai-email-worker",
            )

            logger.info("INICIO 7: AgendaAI inicializado correctamente.")

        except Exception:
            await database_pool.close()
            logger.exception("FALLO DURANTE EL INICIO DE AGENDAAI")
            raise

    async def on_shutdown(
        application: Application,
    ) -> None:
        if email_worker_task is not None:
            email_worker_task.cancel()

            await asyncio.gather(
                email_worker_task,
                return_exceptions=True,
            )
        await database_pool.close()

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

    # Configuración de conexiones a Telegram.
    # Damos más tiempo para establecer HTTPS.
    telegram_request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    # Configuración independiente para long polling.
    polling_request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=40.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token.get_secret_value())
        .request(telegram_request)
        .get_updates_request(polling_request)
        .post_init(on_startup)
        .post_stop(on_shutdown)
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

    logger.info("Aplicación construida correctamente.")
    logger.info("Iniciando conexión con Telegram...")

    app.run_polling(
        drop_pending_updates=True,
        bootstrap_retries=2,
    )


if __name__ == "__main__":
    main()
