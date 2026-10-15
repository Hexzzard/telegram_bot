from whatsapp_gateway.worker import attach_public_calendar

URL = "https://calendar.google.com/calendar/embed?src=public%40group.calendar.google.com"


def test_reservation_adds_link():
    result = attach_public_calendar("¿Para qué fecha quieres reservar?", "hola quiero agendar una hora", URL)
    assert URL in result
    assert "📅" in result


def test_hours_adds_link():
    result = attach_public_calendar("Hay horas a las 09:00 y 10:00", "muéstrame las horas disponibles", URL)
    assert URL in result


def test_explicit_link_request_adds_link():
    result = attach_public_calendar("No contamos con un enlace web", "puedes mostrarme el link con las horas", URL)
    assert URL in result


def test_other_topics_do_not_add_link():
    assert attach_public_calendar("La vacuna cuesta $20.000", "cuánto cuesta la vacuna", URL) == "La vacuna cuesta $20.000"


def test_no_duplicate():
    result = attach_public_calendar("Aquí: " + URL, "reservar", URL)
    assert result.count(URL) == 1


def test_no_private_or_untrusted_link():
    assert attach_public_calendar("Hay horarios", "reservar", "https://evil.example/") == "Hay horarios"
