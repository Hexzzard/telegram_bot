import logging
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from google.genai import types
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
)

from agendaai.availability_service import (
    AvailabilityService,
)

logger = logging.getLogger(__name__)


class AvailabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_at: datetime

    @field_validator("start_at")
    @classmethod
    def validate_datetime(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("La fecha debe incluir zona horaria")

        return value


class SlotsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_date: date


AVAILABILITY_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="check_availability",
            description=(
                "Consulta si existe disponibilidad real "
                "para una atención veterinaria en una fecha y hora. "
                "La duración depende del servicio "
                "configurado en la agenda. "
                "No crea reservas."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "start_at": {
                        "type": "string",
                        "description": (
                            "Fecha y hora ISO 8601 con offset, ejemplo: 2026-10-15T15:00:00-03:00"
                        ),
                    },
                },
                "required": ["start_at"],
            },
        ),
        types.FunctionDeclaration(
            name="get_available_slots",
            description=(
                "Consulta los horarios disponibles "
                "de un día en la agenda real. "
                "Úsala si el usuario pide opciones "
                "o alternativas de horario. "
                "No crea reservas."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "target_date": {
                        "type": "string",
                        "description": ("Fecha en formato YYYY-MM-DD"),
                    },
                },
                "required": ["target_date"],
            },
        ),
    ]
)


class AvailabilityToolExecutor:
    def __init__(
        self,
        service: AvailabilityService,
        resource_id: int,
    ):
        self._service = service
        self._resource_id = resource_id

    async def execute(
        self,
        name: str,
        arguments: dict,
    ) -> str:
        logger.info(
            "Ejecutando herramienta de disponibilidad: %s",
            name,
        )

        try:
            if name == "check_availability":
                request = AvailabilityRequest.model_validate(arguments)

                available = await self._service.check_availability(
                    resource_id=self._resource_id,
                    start_at=request.start_at,
                )

                return json.dumps(
                    {
                        "type": "availability_check",
                        "start_at": request.start_at.isoformat(),
                        "available": bool(available),
                        "reservation_created": False,
                    },
                    ensure_ascii=False,
                )

            if name == "get_available_slots":
                request = SlotsRequest.model_validate(arguments)

                slots = await self._service.get_available_slots(
                    resource_id=self._resource_id,
                    target_date=request.target_date,
                )

                timezone = ZoneInfo("America/Santiago")
                return json.dumps(
                    {
                        "type": "available_slots",
                        "date": request.target_date.isoformat(),
                        "timezone": "America/Santiago",
                        "slots": [
                            slot.astimezone(timezone).strftime("%H:%M")
                            for slot in slots
                        ],
                        "reservation_created": False,
                    },
                    ensure_ascii=False,
                )

            raise ValueError(f"Herramienta no autorizada: {name}")

        except ValidationError as error:
            logger.warning(
                "Argumentos de disponibilidad inválidos: %s",
                error,
            )

            return "Los argumentos no son válidos. Solicita una fecha y hora correctas."
