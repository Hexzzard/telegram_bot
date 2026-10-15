import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
import sys
from zoneinfo import ZoneInfo

from google import genai

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
from agendaai.google_calendar_service import GoogleCalendarService
from agendaai.calendar_sync import CalendarSyncService
from agendaai.public_calendar_service import PublicCalendarService
from agendaai.cancellation_email_worker import CancellationEmailWorker
from agendaai.gemini_service import GeminiService
from agendaai.handlers import build_handlers
from agendaai.receipt_flow import ReceiptFlow
from agendaai.receipt_service import ReceiptService
from agendaai.tools.availability import AvailabilityToolExecutor
from agendaai.tools.booking import BookingToolExecutor
from agendaai.tools.knowledge import KnowledgeToolExecutor
from agendaai.tools.cancellation import CancellationToolExecutor

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

    # Cargar .env para la configuración opcional de Calendar.
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")
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

    cancellation_email_worker = CancellationEmailWorker(
        pool=shared_pool,
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

    # Cliente dedicado a embeddings; utiliza las mismas credenciales Gemini.
    # En esta primera integración solo leemos recuerdos del laboratorio.
    embedding_client = genai.Client(
        api_key=settings.gemini_api_key.get_secret_value()
    )
    knowledge_executor = KnowledgeToolExecutor(
        pool=shared_pool,
        client=embedding_client,
        limit=5,
    )

    debug_sources = os.getenv("AGENT_DEBUG_SOURCES", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )

    cancellation_executor = CancellationToolExecutor(pool=shared_pool, minimum_notice_hours=12)

    gemini_service = GeminiService(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_model,
        repository=conversation_repository,
        availability_executor=availability_executor,
        booking_executor=booking_executor,
        max_turns=10,
        knowledge_executor=knowledge_executor,
        cancellation_executor=cancellation_executor,
        debug_sources=debug_sources,
    )

    start, whoami, reset, on_message = build_handlers(
        gemini_service,
        settings.allowed_users,
        receipt_flow,
    )

    # Credenciales de calendario: exclusivas del servidor; no se envían a Gemini.
    calendar_credentials = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "").strip()
    if bool(calendar_credentials) != bool(calendar_id):
        raise RuntimeError(
            "Configura GOOGLE_SERVICE_ACCOUNT_FILE y GOOGLE_CALENDAR_ID juntos"
        )
    calendar_sync = None
    if calendar_credentials:
        credentials_path = Path(calendar_credentials)
        if not credentials_path.is_absolute():
            credentials_path = project_root / credentials_path
        public_id = os.getenv("GOOGLE_PUBLIC_CALENDAR_ID", "").strip()
        if public_id and public_id == calendar_id:
            raise RuntimeError("El calendario público y privado deben ser diferentes")
        public_calendar = (
            PublicCalendarService(credentials_file=credentials_path, calendar_id=public_id)
            if public_id else None
        )
        calendar_sync = CalendarSyncService(
            pool=shared_pool,
            calendar=GoogleCalendarService(
                credentials_file=credentials_path, calendar_id=calendar_id,
            ),
            public_calendar=public_calendar,
            resource_id=settings.booking_resource_id,
        )
    else:
        logger.warning("Sincronización Google Calendar deshabilitada: falta configuración")

    async def calendar_status_command(update, context) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None or chat.type != "private":
            return
        if settings.allowed_users and user.id not in settings.allowed_users:
            await update.effective_message.reply_text("No tienes acceso a este bot.")
            return
        if calendar_sync is None:
            await update.effective_message.reply_text("Google Calendar no está habilitado.")
            return
        row = await calendar_sync.last_booking_status(user_id=user.id)
        if row is None:
            await update.effective_message.reply_text("Aún no tienes reservas confirmadas.")
            return
        booking_id, start_at, status = row
        if status == "synced":
            state_text = "Actualizada en el calendario"
        elif status in ("pending", "processing"):
            state_text = "Confirmada; actualizando el calendario"
        elif status == "failed":
            state_text = "Confirmada; la actualización del calendario está pendiente"
        else:
            state_text = "Confirmada; aún no aparece actualizada en el calendario"
        await update.effective_message.reply_text(
            f"Reserva AG-{booking_id:06d}\n"
            f"Fecha: {start_at.astimezone(ZoneInfo('America/Santiago')):%d/%m/%Y %H:%M}\n"
            f"Google Calendar: {state_text}"
        )

    email_worker_task = None
    calendar_worker_task = None
    cancellation_email_task = None

    async def on_startup(
        application: Application,
    ) -> None:
        nonlocal email_worker_task, calendar_worker_task, cancellation_email_task

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

            # Verificar la migración antes de iniciar el trabajador de cancelaciones.
            async with shared_pool.connection() as conn:
                await conn.execute(
                    "SELECT 1 FROM public.booking_cancellation_emails LIMIT 1"
                )
            cancellation_email_task = asyncio.create_task(
                cancellation_email_worker.run_worker(),
                name="agendaai-cancellation-email-worker",
            )
            logger.info("Trabajador de correos de cancelación programado")

            if calendar_sync is not None:
                # Fallar visiblemente si no se aplicó la migración SQL.
                async with shared_pool.connection() as conn:
                    await conn.execute("SELECT 1 FROM public.calendar_sync_jobs LIMIT 1")
                calendar_worker_task = asyncio.create_task(
                    calendar_sync.run_worker(), name="agendaai-calendar-worker"
                )
            logger.info("INICIO 7: AgendaAI inicializado correctamente.")

        except Exception:
            for task in (cancellation_email_task, calendar_worker_task, email_worker_task):
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await database_pool.close()
            logger.exception("FALLO DURANTE EL INICIO DE AGENDAAI")
            raise

    async def on_shutdown(
        application: Application,
    ) -> None:
        if cancellation_email_task is not None:
            cancellation_email_task.cancel()
            await asyncio.gather(cancellation_email_task, return_exceptions=True)
        if calendar_worker_task is not None:
            calendar_worker_task.cancel()
            await asyncio.gather(calendar_worker_task, return_exceptions=True)
        if email_worker_task is not None:
            email_worker_task.cancel()

            await asyncio.gather(
                email_worker_task,
                return_exceptions=True,
            )
        await database_pool.close()
        await embedding_client.aio.aclose()

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
    app.add_handler(CommandHandler("calendario", calendar_status_command))

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
