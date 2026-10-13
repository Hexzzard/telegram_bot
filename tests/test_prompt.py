from agendaai.gemini_service import SYSTEM_INSTRUCTION


def test_agent_can_create_bookings():
    assert "create_booking" in SYSTEM_INSTRUCTION


def test_agent_checks_availability():
    assert "check_availability" in SYSTEM_INSTRUCTION
    assert "get_available_slots" in SYSTEM_INSTRUCTION


def test_agent_does_not_book_on_inquiry():
    assert "Nunca reserves por una simple consulta" in SYSTEM_INSTRUCTION


def test_agent_does_not_claim_unverified_booking():
    assert "sin recibir un resultado exitoso" in SYSTEM_INSTRUCTION
