from datetime import datetime

import pytest
from pydantic import ValidationError

from agendaai.tools.calendar import (
    CalendarEventRequest,
    prepare_calendar_event,
)


def test_prepare_event():
    request = CalendarEventRequest(
        title="Reunión de planificación",
        start_at=datetime.fromisoformat("2026-10-15T16:00:00-03:00"),
        duration_minutes=45,
        timezone="America/Santiago",
    )

    event = prepare_calendar_event(request)

    assert event.title == "Reunión de planificación"
    assert event.start_at.hour == 16
    assert event.end_at.hour == 16
    assert event.end_at.minute == 45
    assert event.requires_confirmation is True


def test_reject_negative_duration():
    with pytest.raises(ValidationError):
        CalendarEventRequest(
            title="Reunión",
            start_at=datetime.fromisoformat("2026-10-15T16:00:00-03:00"),
            duration_minutes=-30,
        )


def test_reject_naive_datetime():
    with pytest.raises(ValidationError):
        CalendarEventRequest(
            title="Reunión",
            start_at=datetime(2026, 10, 15, 16, 0),
            duration_minutes=45,
        )
