
import logging
from typing import Protocol

from telegram import Update
from telegram.ext import ContextTypes

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
):
    async def start(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not is_authorized(user_id, allowed_users):
            await update.message.reply_text(
                "No tienes acceso a este bot."
            )
            return

        await update.message.reply_text(
            "¡Hola! Soy AgendaAI.\n\n"
            "Puedo consultar horarios y gestionar "
            "reservas automáticas de Asesoría general.\n\n"
            "Puedes preguntarme por horarios disponibles "
            "o solicitar una reserva concreta.\n\n"
            "Comandos:\n"
            "/start - Información inicial\n"
            "/whoami - Tu identificador\n"
            "/reset - Borrar historial conversacional"
        )

    async def whoami(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if update.effective_user and update.message:
            await update.message.reply_text(
                f"Tu Telegram ID es "
                f"{update.effective_user.id}."
            )

    async def reset(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not is_authorized(user_id, allowed_users):
            await update.message.reply_text(
                "No tienes acceso a este bot."
            )
            return

        await service.reset(user_id)

        await update.message.reply_text(
            "Tu historial conversacional fue eliminado. "
            "Las reservas realizadas se conservan."
        )

    async def on_message(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if (
            not update.effective_user
            or not update.message
            or not update.message.text
        ):
            return

        user_id = update.effective_user.id

        if not is_authorized(user_id, allowed_users):
            await update.message.reply_text(
                "No tienes acceso a este bot."
            )
            return

        try:
            response = await service.answer(
                user_id=user_id,
                message=update.message.text,
                request_id=update.update_id,
            )

            for index in range(0, len(response), 4000):
                await update.message.reply_text(
                    response[index:index + 4000]
                )

        except Exception:
            logger.exception(
                "Error procesando mensaje del usuario %s",
                user_id,
            )

            await update.message.reply_text(
                "No pude completar la operación. "
                "Si solicitaste una reserva, consulta "
                "su estado antes de reintentar."
            )

    return start, whoami, reset, on_message
