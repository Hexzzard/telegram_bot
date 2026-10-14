import logging
import os
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from typing import Protocol

from telegram import Update
from telegram.ext import ContextTypes

from agendaai.receipt_flow import ReceiptFlow

logger = logging.getLogger(__name__)


class AnswerService(Protocol):
    async def answer(
        self,
        user_id: int,
        message: str,
        request_id: int | None = None,
    ) -> str: ...

    async def reset(
        self,
        user_id: int,
    ) -> None: ...


def is_authorized(
    user_id: int,
    allowed_users: set[int],
) -> bool:
    return not allowed_users or user_id in allowed_users


def build_handlers(
    service: AnswerService,
    allowed_users: set[int],
    receipt_flow: ReceiptFlow,
):
    public_calendar_url = os.getenv("GOOGLE_PUBLIC_CALENDAR_URL", "").strip()
    async def start(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not is_authorized(user_id, allowed_users):
            await update.message.reply_text("No tienes acceso a este bot.")
            return

        await update.message.reply_text(
            "¡Hola! Soy AgendaAI.\n\n"
            "Puedo consultar horarios disponibles "
            "y crear reservas automáticamente.\n\n"
            "Después de reservar, puedo enviarte "
            "un comprobante por correo electrónico.\n\n"
            "Comandos:\n"
            "/start - Información inicial\n"
            "/whoami - Tu identificador\n"
            "/reset - Borrar historial conversacional\n\n"
            "Para reservar, puedes escribir:\n"
            "Quiero reservar una asesoría "
            "el jueves a las 15:00."
        )

    async def whoami(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not update.effective_user or not update.message:
            return

        if not is_authorized(
            update.effective_user.id,
            allowed_users,
        ):
            await update.message.reply_text("No tienes acceso a este bot.")
            return

        await update.message.reply_text(f"Tu Telegram ID es {update.effective_user.id}.")

    async def reset(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not is_authorized(user_id, allowed_users):
            await update.message.reply_text("No tienes acceso a este bot.")
            return

        await service.reset(user_id)

        await update.message.reply_text(
            "Historial conversacional eliminado.\n"
            "Tus reservas se conservan.\n"
            "Si tienes una verificación de correo "
            "pendiente, también se conserva."
        )

    async def on_message(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not update.effective_user or not update.message or not update.message.text:
            return

        user_id = update.effective_user.id

        if not is_authorized(user_id, allowed_users):
            await update.message.reply_text("No tienes acceso a este bot.")
            return

        # Los comprobantes contienen datos
        # personales: flujo solo en chat privado.
        if update.effective_chat.type != "private":
            await update.message.reply_text(
                "Para proteger tus reservas y tu correo, utiliza un chat privado conmigo."
            )
            return

        try:
            flow_response = await receipt_flow.handle(
                user_id=user_id,
                message=update.message.text,
            )

            if flow_response.handled:
                response = flow_response.text

            else:
                response = await service.answer(
                    user_id=user_id,
                    message=update.message.text,
                    request_id=update.update_id,
                )

            if not response:
                response = "No pude generar una respuesta."

            # Enlace visual solo cuando se habla del calendario, las horas o una reserva.
            show_calendar = bool(public_calendar_url) and bool(re.search(
                r"calendario|disponib|horario|hora|agend|reserv|cita", 
                update.message.text + " " + response, re.IGNORECASE
            ))
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(
                "📅 Haz clic aquí para ver el calendario", url=public_calendar_url
            )]]) if show_calendar and public_calendar_url.startswith("https://calendar.google.com/") else None
            parts = [response[index:index + 4000] for index in range(0, len(response), 4000)]
            for index, part in enumerate(parts):
                await update.message.reply_text(
                    part, reply_markup=keyboard if index == len(parts)-1 else None
                )

        except Exception:
            logger.exception(
                "Error procesando mensaje del usuario %s",
                user_id,
            )

            await update.message.reply_text(
                "No pude completar la operación. "
                "Tu reserva podría seguir confirmada. "
                "No solicites otra cita sin revisar "
                "primero su estado."
            )

    return start, whoami, reset, on_message
