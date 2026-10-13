import logging
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

    title: str = Field(min_length=1, max_length=200)
    start_at: datetime

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
                "Nunca reserves ante una simple pregunta "
                "de disponibilidad."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Motivo de la reserva",
                    },
                    "start_at": {
                        "type": "string",
                        "description": ("Fecha ISO 8601 con offset de America/Santiago"),
                    },
                },
                "required": ["title", "start_at"],
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
            return "No pude validar la fecha o el motivo. Indica los datos nuevamente."

        logger.info(
            "Intento de reserva: usuario=%s update=%s",
            user_id,
            telegram_update_id,
        )

        result = await self._service.create_booking(
            user_id=user_id,
            telegram_update_id=telegram_update_id,
            title=request.title,
            start_at=request.start_at,
        )

        if result.state in ("created", "already_exists"):
            if result.state == "already_exists" and result.reason != "confirmed":
                return "La solicitud ya fue procesada, pero su reserva no está activa."

            tz = ZoneInfo("America/Santiago")

            start = result.start_at.astimezone(tz)
            end = result.end_at.astimezone(tz)

            message = (
                "RESERVA CONFIRMADA\n\n"
                f"Código: AG-{result.booking_id:06d}\n"
                "Servicio: Asesoría general\n"
                f"Motivo: {request.title}\n"
                f"Fecha: {start:%d/%m/%Y}\n"
                f"Horario: {start:%H:%M}–{end:%H:%M}\n"
                "Zona horaria: America/Santiago\n"
                "Estado: Confirmada"
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
