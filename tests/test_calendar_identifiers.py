"""Pruebas unitarias sencillas de la sincronización, sin llamar a Google."""
from agendaai.google_calendar_service import GoogleCalendarService


def test_event_id_is_stable():
    assert GoogleCalendarService.event_id_for_booking(125) == "ag00000000000000000125"
    assert GoogleCalendarService.event_id_for_booking(125) == GoogleCalendarService.event_id_for_booking(125)


def test_event_id_rejects_nonpositive():
    import pytest

    with pytest.raises(ValueError):
        GoogleCalendarService.event_id_for_booking(0)
