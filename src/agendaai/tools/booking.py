import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from google.genai import types
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from agendaai.booking_service import BookingService
from agendaai.conversation_state import (
    ConversationStateRepository,
)

logger = logging.getLogger(__name__)


class BookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=8, max_length=200, description="Motivo clínico concreto de atención")
    client_name: str = Field(min_length=3, max_length=120, description="Nombre del cliente, no del animal")
    start_at: datetime

    @field_validator("client_name")
    @classmethod
    def validate_client_name(cls, value: str) -> str:
        name = " ".join(value.split())
        if len(name) < 3 or len(name) > 120 or not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ .'-]+", name) or name.casefold() in {"cliente", "usuario", "no sé", "sin nombre", "por definir"}:
            raise ValueError("Se requiere el nombre real proporcionado por el cliente")
        return name

    @field_validator("title")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        reason = " ".join(value.strip().split())
        generic = {
            "consulta", "consulta general", "reserva", "hora", "cita",
            "atencion", "atención", "veterinaria", "visita", "control",
            "asesoria general", "asesoría general", "sin motivo",
            "no especificado", "pendiente", "por definir",
        }
        if reason.casefold() in generic or not re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}", reason):
            raise ValueError("Se requiere un motivo de atención específico")
        return reason

    @field_validator("start_at")
    @classmethod
    def validate_start(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("La fecha debe incluir offset horario")
        return value


BOOKING_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="create_booking",
            description=(
                "Crea inmediatamente una reserva real "
                "cuando el usuario solicita explícitamente "
                "reservar una fecha y hora concretas. "
                "No necesita una segunda confirmación. "
                "Antes de reservar debes conocer nombre del cliente y motivo concreto; si el usuario ya explicó síntomas o el tipo de control en esta conversación, úsalo sin preguntar otra vez. "
                "Nunca inventes un motivo. Si solo pide reservar una hora sin contexto clínico, pregúntale primero para qué necesita la cita. "
                "Nunca reserves ante una simple pregunta "
                "de disponibilidad."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "client_name": {"type": "string", "description": "Nombre del cliente entregado explícitamente en la conversación. No deducir del perfil de Telegram ni inventarlo."},
                    "title": {
                        "type": "string",
                        "description": "Motivo clínico concreto, explicado por el usuario o inferido con claridad de la conversación previa. No usar valores genéricos como Consulta general, Atención o Asesoría general. Si no se conoce, preguntar antes de llamar la herramienta.",
                    },
                    "start_at": {
                        "type": "string",
                        "description": ("Fecha ISO 8601 con offset de America/Santiago"),
                    },
                },
                "required": ["client_name", "title", "start_at"],
            },
        )
    ]
)


class BookingToolExecutor:
    def __init__(
        self,
        service: BookingService,
        conversation_states: (ConversationStateRepository | None) = None,
    ):
        self._service = service
        self._conversation_states = conversation_states

    async def execute(
        self,
        arguments: dict,
        *,
        user_id: int,
        telegram_update_id: int,
    ) -> str:
        try:
            request = BookingRequest.model_validate(arguments)
        except ValidationError:
            return "🐾 Antes de reservar necesito tu nombre, el motivo de la consulta y la hora. Si ya me contaste alguno de esos datos, no hace falta repetirlo."

        logger.info(
            "Intento de reserva: usuario=%s update=%s",
            user_id,
            telegram_update_id,
        )

        result = await self._service.create_booking(
            user_id=user_id,
            telegram_update_id=telegram_update_id,
            title=request.title,
            client_name=request.client_name,
            start_at=request.start_at,
        )

        if result.state in ("created", "already_exists"):
            if result.state == "already_exists" and result.reason != "confirmed":
                return "La solicitud ya fue procesada, pero su reserva no está activa."

            tz = ZoneInfo("America/Santiago")

            start = result.start_at.astimezone(tz)
            end = result.end_at.astimezone(tz)

            message = (
                "🐾 ¡Listo! Tu hora quedó agendada.\n\n"
                f"🎟 Código: AG-{result.booking_id:06d}\n"
                f"👤 Cliente: {request.client_name}\n"
                "Atención: Veterinaria\n"
                f"🩺 Motivo: {request.title}\n"
                f"📅 Fecha: {start:%d/%m/%Y}\n"
                f"🕒 Horario: {start:%H:%M}–{end:%H:%M} (Chile)\n"
                "✅ Estado: Confirmada en nuestra agenda\n"
                "¡Te esperamos! 🐾"
            )

            # Ofrecemos el comprobante solamente
            # después de confirmar la escritura.
            if self._conversation_states is not None:
                try:
                    started = await self._conversation_states.begin_receipt_offer(
                        user_id=user_id,
                        booking_id=result.booking_id,
                    )

                    if started:
                        message += (
                            "\n\n¿Quieres recibir un "
                            "comprobante por correo electrónico? "
                            "Responde «sí» o «no»."
                        )

                except Exception:
                    # La reserva ya está confirmada.
                    # Un problema en el flujo opcional
                    # no debe presentarse como fallo
                    # de la reserva principal.
                    logger.exception(
                        "No pude iniciar la oferta de comprobante para reserva %s",
                        result.booking_id,
                    )

            logger.info(
                "Reserva confirmada: %s",
                result.booking_id,
            )

            return message

        if result.state == "unavailable":
            return (
                f"NO SE CREÓ LA RESERVA.\nMotivo: {result.reason}.\nPuedes consultar otro horario."
            )

        if result.state == "retry":
            return (
                "No pude determinar el resultado "
                "de esta operación duplicada. "
                "Revisa la reserva antes de reintentar."
            )

        return f"No se creó la reserva. Motivo: {result.reason or 'Datos inválidos'}."
