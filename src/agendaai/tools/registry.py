import logging
from datetime import datetime

from google.genai import types
from pydantic import ValidationError

from agendaai.tools.calendar import (
    CalendarEventRequest,
    prepare_calendar_event,
)

logger = logging.getLogger(__name__)


CALENDAR_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="prepare_calendar_event",
            description=(
                "Prepara una solicitud de evento de calendario. "
                "No crea el evento. Úsala cuando el usuario "
                "quiera agendar una reunión o actividad. "
                "Solicita los datos que falten antes de llamarla."
            ),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Título del evento",
                    },
                    "start_at": {
                        "type": "string",
                        "description": (
                            "Fecha y hora ISO 8601 con offset, "
                            "por ejemplo "
                            "2026-10-15T16:00:00-03:00"
                        ),
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": ("Duración del evento en minutos"),
                    },
                    "timezone": {
                        "type": "string",
                        "description": ("Zona horaria IANA, normalmente America/Santiago"),
                    },
                },
                "required": [
                    "title",
                    "start_at",
                    "duration_minutes",
                    "timezone",
                ],
            },
        )
    ]
)


def execute_tool(
    name: str,
    arguments: dict,
    *,
    now: datetime | None = None,
) -> str:
    if name != "prepare_calendar_event":
        raise ValueError(f"Herramienta no autorizada: {name}")

    logger.info(
        "Ejecutando herramienta: %s",
        name,
    )

    try:
        request = CalendarEventRequest.model_validate(arguments)

        event = prepare_calendar_event(request)

        current_time = now or datetime.now(event.start_at.tzinfo)

        if event.start_at <= current_time.astimezone(event.start_at.tzinfo):
            return "No puedo preparar un evento en el pasado. Indícame otra fecha u hora."

        logger.info("Evento validado correctamente")

        return (
            "Evento preparado:\n\n"
            f"Título: {event.title}\n"
            f"Inicio: {event.start_at:%d/%m/%Y %H:%M}\n"
            f"Fin: {event.end_at:%d/%m/%Y %H:%M}\n"
            f"Zona horaria: {event.timezone}\n\n"
            "Este evento NO se ha creado en "
            "Google Calendar.\n"
            "La creación real estará disponible "
            "cuando implementemos OAuth y confirmaciones."
        )

    except ValidationError as error:
        logger.warning(
            "Argumentos inválidos: %s",
            error,
        )
        return "No pude validar los datos del evento. Revisa la fecha, zona horaria y duración."
