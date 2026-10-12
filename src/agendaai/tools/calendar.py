from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator


class CalendarEventRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_at: datetime
    duration_minutes: int = Field(ge=5, le=480)
    timezone: str = "America/Santiago"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Zona horaria no válida") from exc

        return value

    @field_validator("start_at")
    @classmethod
    def validate_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("La fecha debe incluir zona horaria")
        return value


class PreparedEvent(BaseModel):
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    requires_confirmation: bool = True


def prepare_calendar_event(
    request: CalendarEventRequest,
) -> PreparedEvent:
    local_start = request.start_at.astimezone(ZoneInfo(request.timezone))

    end_at = local_start + timedelta(minutes=request.duration_minutes)

    return PreparedEvent(
        title=request.title,
        start_at=local_start,
        end_at=end_at,
        timezone=request.timezone,
    )
