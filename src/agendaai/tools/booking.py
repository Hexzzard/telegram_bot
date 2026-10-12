
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

logger = logging.getLogger(__name__)


class BookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    start_at: datetime

    @field_validator("start_at")
    @classmethod
    def validate_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "La fecha debe incluir offset horario"
            )
        return value


BOOKING_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="create_booking",
            description=(
                "Crea inmediatamente una reserva real "
                "en la agenda compartida. Úsala únicamente "
                "cuando el usuario solicite explícitamente "
                "reservar una fecha y hora concretas. "
                "No la uses para consultas de disponibilidad. "
                "Si faltan datos, pregunta primero. "
                "No necesita una segunda confirmación."
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
                        "description": (
                            "Fecha y hora ISO 8601 con offset "
                            "correspondiente a America/Santiago"
                        ),
                    },
                },
                "required": ["title", "start_at"],
            },
        )
    ]
)


class BookingToolExecutor:
    def __init__(self, service: BookingService):
        self._service = service

    async def execute(
        self,
        arguments: dict,
        *,
        user_id: int,
        telegram_update_id: int,
    ) -> str:
        try:
            request = BookingRequest.model_validate(
                arguments
            )
        except ValidationError:
            return (
                "No pude validar la fecha o el título. "
                "Indica los datos nuevamente."
            )

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
            if (
                result.state == "already_exists"
                and result.reason != "confirmed"
            ):
                return (
                    "La solicitud ya fue procesada y "
                    "su reserva no está activa."
                )

            tz = ZoneInfo("America/Santiago")
            start = result.start_at.astimezone(tz)
            end = result.end_at.astimezone(tz)

            logger.info(
                "Reserva registrada o recuperada: %s",
                result.booking_id,
            )

            return (
                "RESERVA CONFIRMADA\n\n"
                f"Código: AG-{result.booking_id:06d}\n"
                f"Servicio: Asesoría general\n"
                f"Motivo: {request.title}\n"
                f"Fecha: {start:%d/%m/%Y}\n"
                f"Horario: {start:%H:%M}–{end:%H:%M}\n"
                "Zona horaria: America/Santiago\n"
                "Estado: Confirmada"
            )

        if result.state == "unavailable":
            return (
                "NO SE CREÓ LA RESERVA.\n"
                f"Motivo: {result.reason}.\n"
                "Solicita otro horario disponible."
            )

        if result.state == "retry":
            return (
                "No pude determinar el resultado del "
                "intento duplicado. Consulta tus reservas "
                "antes de volver a solicitar la misma hora."
            )

        return (
            "No se creó la reserva. "
            f"Motivo: {result.reason or 'Datos inválidos'}."
        )
